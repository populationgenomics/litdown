"""Convert JATS XML — an article, or a BITS book part — to semantically-richer Markdown.

Public entry points: :func:`render` for an ``<article>`` root and
:func:`render_book_part_wrapper` for a ``<book-part-wrapper>`` root.
"""

from __future__ import annotations

import copy
import re
import textwrap
import xml.etree.ElementTree as ET
from collections.abc import Callable

from litdown import common, mathml

__all__ = ['render', 'render_book_part_wrapper']


# Mapping from common <ext-link ext-link-type="..."> values to a URL
# template. PMC encodes accession numbers as ext-links with the bare
# accession in xlink:href; resolving them to the appropriate database
# makes the markdown directly usable downstream.
_EXT_LINK_RESOLVERS = {
    'pmc:entrez-protein': 'https://www.ncbi.nlm.nih.gov/protein/{}',
    'pmc:entrez-nucleotide': 'https://www.ncbi.nlm.nih.gov/nuccore/{}',
    'pmc:entrez-gene': 'https://www.ncbi.nlm.nih.gov/gene/{}',
    'pmc:pubmed': 'https://pubmed.ncbi.nlm.nih.gov/{}',
    'pmc:pmc': 'https://www.ncbi.nlm.nih.gov/pmc/articles/{}/',
    'ddbj-embl-genbank': 'https://www.ncbi.nlm.nih.gov/nuccore/{}',
    'gen-bank': 'https://www.ncbi.nlm.nih.gov/nuccore/{}',
    'genpept': 'https://www.ncbi.nlm.nih.gov/protein/{}',
    'protein': 'https://www.ncbi.nlm.nih.gov/protein/{}',
    'pubmed': 'https://pubmed.ncbi.nlm.nih.gov/{}',
    'pdb': 'https://www.rcsb.org/structure/{}',
    'doi': 'https://doi.org/{}',
    'ec': 'https://enzyme.expasy.org/EC/{}',
    'go': 'https://amigo.geneontology.org/amigo/term/{}',
    'uniprot': 'https://www.uniprot.org/uniprotkb/{}',
}


def _object_id_doi_link(elem: ET.Element) -> str:
    """Return a markdown DOI link for a child <object-id pub-id-type='doi'>.

    Returns empty string if absent. PLOS uses these to attach a per-figure /
    per-table DOI distinct from the article DOI.
    """
    for oid in elem.findall('object-id'):
        if oid.get('pub-id-type') == 'doi':
            val = common.flat(oid.text)
            if val:
                return f'[doi:{val}](https://doi.org/{val})'
    return ''


def _caption_text(caption: ET.Element | None) -> str:
    """Render a <caption>'s contents to markdown.

    JATS <caption> permits a <title> followed by zero or more <p>s.
    Both should appear in the rendered output (title as the lead
    sentence, paragraphs as the body). Render children in document
    order so any title-then-prose structure is preserved.
    """
    if caption is None:
        return ''
    parts = []
    for child in caption:
        tag = common.get_tag(child)
        if tag in ('title', 'p'):
            text = inline_to_md(child).strip()
            if text:
                parts.append(text)
    return ' '.join(parts)


def _extract_tex(tex_math_el: ET.Element) -> str:
    r"""Pull the actual math expression out of a <tex-math> element.

    Springer/Nature publishing toolchains often wrap the expression in a
    full minimal documentclass so the equation can be compiled as a
    standalone PDF for typesetting:

        \\documentclass[12pt]{minimal}
        \\usepackage{...}
        \\begin{document}
        $\\log_2 q_{ij} = \\sum_r x_{jr}\\beta_{ir}$
        \\end{document}

    Strip the wrapping and the outer $/$$ delimiters; return just the
    body. If no documentclass wrapping is present, return the trimmed
    text as-is.
    """
    text = ''.join(tex_math_el.itertext())
    m = re.search(r'\\begin\{document\}(.*?)\\end\{document\}', text, re.DOTALL)
    if m:
        text = m.group(1)
    text = text.strip()
    if text.startswith('$$') and text.endswith('$$'):
        text = text[2:-2].strip()
    elif text.startswith('$') and text.endswith('$'):
        text = text[1:-1].strip()
    return text


def _heading(level: int, text: str) -> str:
    """An ATX heading at ``level``, clamped to the six Markdown has."""
    return f'{"#" * min(level, 6)} {text}'


# ---------------------------------------------------------------------------
# Inline renderer  (elem → markdown string, no trailing newline)
# ---------------------------------------------------------------------------


def inline_to_md(elem: ET.Element | None) -> str:
    """Render an element's mixed content as inline Markdown.

    Output is always a single line: PMC OA JATS is pretty-printed, and
    every markdown construct this feeds (heading, list item, table row,
    caption line) is line-terminated. See :func:`common.norm_ws`.
    """
    if elem is None:
        return ''

    buf = []
    if elem.text:
        buf.append(elem.text)

    for child in elem:
        tag = common.get_tag(child)
        inner = inline_to_md(child)

        # Shared inline typographic leaves (italic/bold/sup/sub/underline/
        # monospace/strike). JATS tag names line up 1:1 with the canonical
        # keys, so no remapping is needed. monospace → backtick code span
        # doubles as the markdown convention for variable names, paths, etc.
        wrapped = common.inline_wrap(tag, inner)
        if wrapped is not None:
            buf.append(wrapped)
        elif tag == 'break':
            buf.append('<br>')
        elif tag in ('sc', 'overline', 'roman', 'sans-serif', 'ruby'):
            # These are font-family / typographic hints with no markdown
            # equivalent worth emitting (sc = small caps, overline = bar
            # above, etc.). Preserve the text content; drop the styling.
            buf.append(inner)
        elif tag == 'xref':
            ref_type = child.get('ref-type', '')
            rid = child.get('rid', '')
            if ref_type == 'bibr':
                # Surrounding document text already provides [...] brackets;
                # just make the number a hyperlink.
                buf.append(f'[{inner}](#{rid})')
            elif ref_type in ('fig', 'table'):
                buf.append(f'[{inner}](#{rid})')
            else:
                buf.append(inner)
        elif tag == 'ext-link':
            href = common.xlink_href(child)
            link_type = child.get('ext-link-type', '')
            if href.startswith(('http://', 'https://', 'ftp://')):
                buf.append(f'[{inner or href}]({href})')
            elif href and link_type in _EXT_LINK_RESOLVERS:
                # Accession number — resolve via the per-database URL
                # template. PMC encodes "pmc:entrez-protein" / "pdb" /
                # "uniprot" / etc. with the bare accession in xlink:href.
                resolved = _EXT_LINK_RESOLVERS[link_type].format(href)
                buf.append(f'[{inner or href}]({resolved})')
            else:
                buf.append(inner or href)
        elif tag == 'inline-formula':
            buf.append(_render_inline_formula(child) or inner)
        elif tag in ('inline-graphic', 'graphic', 'media'):
            buf.append(common.norm_ws(_render_graphic(child, 0).replace('\n', ' ')))
        elif tag == 'math':
            buf.append(mathml.render_mathml(child, display=False))
        elif tag == 'tex-math':
            buf.append(f'${_extract_tex(child)}$')
        else:
            buf.append(inner)

        if child.tail:
            buf.append(child.tail)

    return common.norm_ws(''.join(buf))


# ---------------------------------------------------------------------------
# Front matter
# ---------------------------------------------------------------------------


def _title_group_heading(title_group: ET.Element, title_tag: str) -> str:
    """Compose heading text from a <title-group>: its label, the title, then each <subtitle>.

    ``title_tag`` names the group's main title — ``article-title`` in JATS,
    ``title`` in BITS. A subtitle follows the preceding text after a colon
    unless that text already ends in one of ``.?!:`` (judged on the source
    text, so inline markup does not hide the punctuation).
    """
    title_el = title_group.find(title_tag)
    text = inline_to_md(title_el).strip()
    tail = common.flat_text(title_el)
    for subtitle_el in title_group.findall('subtitle'):
        subtitle = inline_to_md(subtitle_el).strip()
        if not subtitle:
            continue
        sep = '' if tail.endswith(('.', '?', '!', ':')) else ':'
        text = f'{text}{sep} {subtitle}' if text else subtitle
        tail = common.flat_text(subtitle_el)
    label = common.flat(title_group.findtext('label'))
    return f'{label} {text}'.strip()


def render_front(front: ET.Element) -> str:  # noqa: C901, PLR0912, PLR0915
    jmeta = front.find('journal-meta')
    ameta = front.find('article-meta')
    if ameta is None:
        # Every JATS article has <article-meta>; without it there's nothing
        # to render in the front matter beyond the bare journal title.
        return ''
    parts: list[str] = []

    # --- Title ---
    title_group = ameta.find('title-group')
    parts.append(_heading(1, _title_group_heading(title_group, 'article-title') if title_group is not None else ''))

    # --- Authors ---
    # Only look at top-level author contribs: direct children of any
    # <contrib-group> child of <article-meta>, plus any <contrib> children
    # of <article-meta> itself. A descendant search (.//contrib) would
    # also pull in nested consortium members (e.g. gnomAD's 112-author
    # Genome Aggregation Database Consortium), which we'd then render
    # twice — once as a member, once concatenated into the consortium
    # author entry's text content.
    contribs: list[ET.Element] = []
    for cg in ameta.findall('contrib-group'):
        contribs.extend(cg.findall("contrib[@contrib-type='author']"))
    contribs.extend(ameta.findall("contrib[@contrib-type='author']"))
    # JATS Archiving permits <aff> as a direct child of <article-meta>,
    # inside <contrib-group>, or inside an individual <contrib>. Collect
    # from anywhere beneath article-meta so all three encodings work.
    # aff_map is in document order (dict preserves insertion order).
    aff_map: dict[str, ET.Element] = {
        aff.get('id') or '': aff for aff in ameta.iter() if common.get_tag(aff) == 'aff' and aff.get('id')
    }

    # Build referenced-aff order + ordinal map. Some publishers (BMC
    # Genome Biology, e.g. PMC4302049) ship <aff>s with no <label> or
    # <sup>, and <xref ref-type="aff"> with empty text, expecting the
    # consumer to generate ordinal markers (1, 2, 3, ...) from doc
    # order. Build the ordinals once so xref-side and aff-side
    # rendering agree.
    referenced_set: set[str] = set()
    for c in contribs:
        for x in c.findall("xref[@ref-type='aff']"):
            rid = x.get('rid', '')
            if rid and rid in aff_map:
                referenced_set.add(rid)
    aff_ordinal: dict[str, str] = {}
    n = 0
    for aff_id in aff_map:
        if aff_id in referenced_set:
            n += 1
            aff_ordinal[aff_id] = str(n)

    author_lines = []
    for c in contribs:
        name = c.find('name')
        collab = c.find('collab')
        if name is not None:
            sn = common.flat(name.findtext('surname'))
            gn = common.flat(name.findtext('given-names'))
            full = f'{gn} {sn}'.strip()
        elif collab is not None:
            # The collab's lead text is the consortium name; nested
            # <contrib-group> holds individual members which we drop here.
            full = common.flat(collab.text)
            if not full:
                full = common.flat_text(collab)
        else:
            full = common.flat_text(c)

        # Affiliation markers: prefer the xref's own text (the rendered
        # marker in PLOS-style JATS); fall back to the linked <aff>'s
        # <label> when the xref is self-closing or carries only
        # whitespace (a pretty-printing artefact, not a spec issue).
        aff_refs = []
        # Treat <xref ref-type="author-notes"> the same as
        # ref-type="aff": both attach a superscript marker to the
        # author name. Used by some publishers for current-address /
        # present-address footnotes.
        for x in c.findall('xref'):
            rt = x.get('ref-type', '')
            if rt not in ('aff', 'author-notes'):
                continue
            # The marker can be in xref.text directly OR wrapped in a
            # <sup> child (some Oxford journals use <xref><sup>2</sup></xref>).
            marker = common.flat_text(x)
            if not marker and rt == 'aff':
                rid = x.get('rid', '')
                aff = aff_map.get(rid)
                if aff is not None:
                    marker = common.flat(aff.findtext('label'))
                    if not marker:
                        # JATS Archiving permits <sup> as an aff-level
                        # marker alongside <label>. Frontiers uses <sup>;
                        # PLOS uses <label>. Both are spec-valid.
                        sup = aff.find('sup')
                        if sup is not None:
                            marker = common.flat(sup.text)
                    if not marker:
                        # Final fallback: doc-order ordinal. Used when
                        # neither <label> nor <sup> is present and the
                        # publisher expects the consumer to generate
                        # markers (BMC Genome Biology pattern).
                        marker = aff_ordinal.get(rid, '')
            if marker:
                aff_refs.append(marker)
        # JATS Archiving's <contrib> content model allows <email> both
        # as a direct child and inside <address>. Descendant search
        # picks up both encodings.
        email_el = c.find('.//email')
        email = common.flat(email_el.text) if email_el is not None else ''
        # Two ways to mark corresponding authors per the spec: a
        # corresp="yes" attribute on the <contrib>, or an
        # <xref ref-type="corresp"> pointing to <author-notes>/<corresp>.
        # An author can satisfy either; recognise both.
        is_corresp = c.get('corresp') == 'yes' or c.find("xref[@ref-type='corresp']") is not None
        corresp = '\\*' if is_corresp else ''

        # Join all affiliation markers into a single <sup>1,2</sup> with
        # comma separation rather than emitting <sup>1</sup><sup>2</sup>,
        # which renders visually as the number "12" — fusing two
        # distinct affiliation references into one bogus marker.
        sup_str = f'<sup>{",".join(aff_refs)}</sup>' if aff_refs else ''
        email_str = f' <{email}>' if email else ''
        author_lines.append(f'{full}{corresp}{sup_str}{email_str}')

    parts.append(', '.join(author_lines))
    if any(c.get('corresp') == 'yes' or c.find("xref[@ref-type='corresp']") is not None for c in contribs):
        parts.append('\\* Corresponding author')

    # --- Affiliations ---
    # Render only aff entries referenced by an author xref. Article-level
    # <aff>s often include the editor's affiliation alongside the
    # authors' — without this filter the editor's aff would show up in
    # the author affiliation block. The ordinal map computed above
    # provides labels for affs that have no <label> or <sup>.
    aff_parts = []
    for aff_id, aff in aff_map.items():
        if referenced_set and aff_id not in referenced_set:
            continue
        label = common.flat(aff.findtext('label'))
        if not label:
            sup = aff.find('sup')
            if sup is not None:
                label = common.flat(sup.text)
        if not label:
            label = aff_ordinal.get(aff_id, '')
        text_parts = [common.flat(aff.text)]
        for child in aff:
            ctag = common.get_tag(child)
            if ctag in {'label', 'sup'}:
                # Skip label/sup-as-label markers — already extracted above.
                pass
            elif ctag == 'institution-wrap':
                # <institution-wrap> contains <institution> name(s)
                # alongside zero or more <institution-id> children
                # carrying ROR / GRID / ISNI / FundRef identifiers.
                # Render only the <institution> text — the URI-shaped
                # identifiers don't help a markdown reader.
                text_parts.append(common.flat(child.text))
                for sub in child:
                    if common.get_tag(sub) == 'institution':
                        text_parts.append(inline_to_md(sub).strip())
                    text_parts.append(common.flat(sub.tail))
            elif ctag == 'institution-id':
                pass
            else:
                text_parts.append(inline_to_md(child).strip())
            text_parts.append(common.flat(child.tail))
        aff_text = ' '.join(t for t in text_parts if t)
        aff_parts.append(f'<sup>{label}</sup> {aff_text}' if label else aff_text)

    parts.append('\n'.join(aff_parts))

    # --- Editors ---
    editors: list[ET.Element] = []
    for cg in ameta.findall('contrib-group'):
        editors.extend(cg.findall("contrib[@contrib-type='editor']"))
    if editors:
        editor_lines = []
        for c in editors:
            name = c.find('name')
            if name is not None:
                sn = common.flat(name.findtext('surname'))
                gn = common.flat(name.findtext('given-names'))
                full = f'{gn} {sn}'.strip()
            else:
                full = common.flat_text(c)
            role = common.flat(c.findtext('role')) or 'Editor'
            # Editor's affiliation lookup
            aff_text = ''
            for x in c.findall("xref[@ref-type='aff']"):
                rid = x.get('rid', '')
                aff = aff_map.get(rid)
                if aff is not None:
                    aff_text = common.flat_text(aff)
            line = f'**{role}:** {full}'
            if aff_text:
                line += f', {aff_text}'
            editor_lines.append(line)
        parts.append('\n'.join(editor_lines))

    # --- Author notes (corresp emails, fn-typed metadata) ---
    notes = ameta.find('author-notes')
    if notes is not None:
        notes_lines = []
        for child in notes:
            tag = common.get_tag(child)
            if tag == 'corresp':
                # The corresp body is mixed content with <email> children.
                # Render inline so emails appear as text and any prose
                # ("To whom correspondence should be addressed.") is kept.
                text = inline_to_md(child).strip()
                if text:
                    notes_lines.append(text)
            elif tag == 'fn':
                fn_type = child.get('fn-type', '')
                fn_text = ' '.join(inline_to_md(p).strip() for p in child.findall('p')).strip()
                if not fn_text:
                    fn_text = inline_to_md(child).strip()
                if fn_text:
                    # SPEC DEVIATION: Frontiers reuses
                    # fn-type="edited-by" for "Reviewed by:" footnotes
                    # too. JATS only defines edited-by as "the role of
                    # an editor" — there is no reviewed-by fn-type
                    # value. The body text is self-labelling, so skip
                    # our own fn-type prefix to avoid mislabeling.
                    plain = fn_text.lstrip('*_ ')
                    has_inline_label = ':' in plain[:32] and plain[: plain.find(':')].strip().lower() in {
                        'edited by',
                        'reviewed by',
                        'edited',
                        'reviewed',
                        'received',
                        'accepted',
                        'published',
                        'deceased',
                        'current address',
                        'present address',
                        'correspondence',
                    }
                    if fn_type and not has_inline_label:
                        notes_lines.append(f'**{_FN_TYPE_LABELS.get(fn_type, fn_type)}:** {fn_text}')
                    else:
                        notes_lines.append(fn_text)
        if notes_lines:
            parts.append('\n'.join(notes_lines))

    # --- Journal & article IDs ---
    jname = common.flat(jmeta.findtext('.//journal-title')) if jmeta is not None else ''
    issn = common.flat(jmeta.findtext('issn')) if jmeta is not None else ''
    pmcid = common.flat(ameta.findtext("article-id[@pub-id-type='pmcid']"))
    pmid = common.flat(ameta.findtext("article-id[@pub-id-type='pmid']"))
    doi = common.flat(ameta.findtext("article-id[@pub-id-type='doi']"))

    # Publication dates
    epub = ameta.find("pub-date[@pub-type='epub']")
    date_parts = []
    if epub is not None:
        y = common.flat(epub.findtext('year'))
        m = common.flat(epub.findtext('month'))
        d = common.flat(epub.findtext('day'))
        date_parts = [x for x in [y, m.zfill(2) if m else '', d.zfill(2) if d else ''] if x]

    vol = common.flat(ameta.findtext('volume'))
    issue = common.flat(ameta.findtext('issue'))
    fpage = common.flat(ameta.findtext('fpage'))
    lpage = common.flat(ameta.findtext('lpage'))
    pages = f'{fpage}–{lpage}' if fpage and lpage else fpage

    # Article subject / category (e.g. "Research Article", "Plant Science /
    # Review Article"). Some publishers (Frontiers) print this at the top
    # of page 1 alongside the journal title.
    cats = ameta.find('article-categories')
    cat_subjects: list[str] = []
    if cats is not None:
        for sg in cats.findall('subj-group'):
            cat_subjects.extend(
                subject for s in sg.iter() if common.get_tag(s) == 'subject' and (subject := common.flat(s.text))
            )

    meta_lines = []
    if cat_subjects:
        meta_lines.append(f'**Article type:** {" / ".join(cat_subjects)}')
    meta_lines.append(f'**Journal:** {jname}' + (f' (ISSN {issn})' if issn else ''))
    if date_parts:
        meta_lines.append(f'**Published:** {"-".join(date_parts)}')
    if vol or pages or issue:
        vol_line = f'**Volume:** {vol}'
        if issue:
            vol_line += f'({issue})'
        if pages:
            vol_line += f', p. {pages}'
        meta_lines.append(vol_line)
    if doi:
        meta_lines.append(f'**DOI:** [{doi}](https://doi.org/{doi})')
    if pmid:
        meta_lines.append(f'**PMID:** {pmid}')
    if pmcid:
        meta_lines.append(f'**PMCID:** {pmcid}')

    # History
    history = ameta.find('history')
    if history is not None:
        for date in history.findall('date'):
            dtype = date.get('date-type', '')
            y = common.flat(date.findtext('year'))
            m = common.flat(date.findtext('month'))
            d = common.flat(date.findtext('day'))
            dparts = [x for x in [y, m.zfill(2) if m else '', d.zfill(2) if d else ''] if x]
            if dparts:
                meta_lines.append(f'**{dtype.capitalize()}:** {"-".join(dparts)}')

    # Copyright + license
    copyright_stmt = common.flat(ameta.findtext('.//copyright-statement'))
    if copyright_stmt:
        meta_lines.append(f'**License:** {copyright_stmt}')
    # The full license text usually lives in <license>/<license-p>; render
    # those separately so CC clauses don't get truncated to the bare
    # copyright statement.
    for license_el in ameta.findall('.//license'):
        href = license_el.get(f'{{{common.XLINK_NS}}}href') or license_el.get('href') or ''
        for lp in license_el.findall('license-p'):
            txt = inline_to_md(lp).strip()
            if txt:
                meta_lines.append(txt)
        if href and not any(href in line for line in meta_lines):
            meta_lines.append(f'License: [{href}]({href})')

    parts.append('\n'.join(meta_lines))

    # --- Abstracts ---
    # Articles often have multiple <abstract> elements: the default plus
    # publisher-specific variants like abstract-type="synopsis" (PLOS
    # Genetics) or "summary" (PMC author-summary). Render each.
    for abstract in ameta.findall('abstract'):
        parts.append(render_abstract(abstract))

    # --- Keywords ---
    # <kwd-group> (typically Frontiers, BMC) lists author-supplied keywords.
    # Render as a "**Keywords:** a, b, c" line per group. Multiple groups
    # may exist for different languages — keep them all.
    kw_lines = []
    for kg in ameta.findall('kwd-group'):
        kwds = [kwd for k in kg.findall('kwd') if (kwd := common.flat(k.text))]
        if not kwds:
            continue
        gtitle = common.flat(kg.findtext('title')) or 'Keywords'
        kw_lines.append(f'**{gtitle}:** {", ".join(kwds)}')
    if kw_lines:
        parts.append('\n'.join(kw_lines))

    # --- Funding (structured) ---
    funding_group = ameta.find('funding-group')
    funding = render_funding_group(funding_group) if funding_group is not None else ''
    if funding:
        parts.append(funding)

    return '\n\n'.join(parts)


def render_funding_group(fg: ET.Element) -> str:
    """Render <funding-group> as a Funding section with one bullet per <award-group>.

    Each bullet pulls the funder name from <funding-source> (preferring the
    inner <institution> text over an institution-id URI), the award IDs, and
    any named recipients.
    """
    if fg is None:
        return ''
    awards = fg.findall('award-group')
    if not awards:
        return ''

    lines = [_heading(2, 'Funding')]
    for ag in awards:
        # Funding source: prefer plain text, then <institution>, then any
        # nested text (skipping institution-id URIs).
        src = ag.find('funding-source')
        funder = ''
        if src is not None:
            inst = src.find('.//institution')
            if inst is not None:
                funder = common.flat_text(inst)
            if not funder:
                funder = common.flat(src.text) or common.flat(
                    ''.join(t for sub in src for t in sub.itertext() if common.get_tag(sub) != 'institution-id')
                )

        # Award IDs (typically grant numbers).
        ids = [award_id for a in ag.findall('award-id') if (award_id := common.flat(a.text))]

        # Recipients.
        recipients = []
        for prr in ag.findall('principal-award-recipient'):
            for n in prr.findall('name'):
                sn = common.flat(n.findtext('surname'))
                gn = common.flat(n.findtext('given-names'))
                full = f'{gn} {sn}'.strip()
                if full:
                    recipients.append(full)
            if not prr.findall('name'):
                txt = common.flat_text(prr)
                if txt:
                    recipients.append(txt)

        bits = []
        if funder:
            bits.append(funder)
        if ids:
            bits.append('Award IDs: ' + ', '.join(ids))
        if recipients:
            bits.append('Recipients: ' + ', '.join(recipients))
        if bits:
            lines.append('- ' + ' — '.join(bits))

    if len(lines) == 1:
        return ''
    return '\n'.join(lines)


def render_abstract(abstract: ET.Element, level: int = 2) -> str:
    # Heading: prefer the abstract's own <title>; fall back to a label
    # derived from abstract-type (e.g. "Author Summary"); else "Abstract".
    title_el = abstract.find('title')
    if title_el is not None:
        heading = inline_to_md(title_el).strip()
    else:
        atype = abstract.get('abstract-type', '')
        heading = {
            'summary': 'Author Summary',
            'synopsis': 'Synopsis',
            'graphical': 'Graphical Abstract',
            'toc': 'Table of Contents',
            'web-summary': 'Web summary',
            'executive-summary': 'Executive Summary',
            'precis': 'Précis',
        }.get(atype, 'Abstract')
    lines = [_heading(level, heading)]
    lines.extend(md for _, md in _render_content(abstract, level, skip=frozenset({'label', 'title', 'sec'})))
    for sec in abstract.findall('sec'):
        lines.extend(_render_abstract_sec(sec, level))
    return '\n\n'.join(lines)


# Some publishers (Springer/BMC) append an auto-generated <sec
# title="Electronic supplementary material"> footer with template boilerplate
# that doesn't exist in the published PDF. The DOI link it carries is already
# covered by the article-meta DOI line, so the whole sub-sec is skipped.
_SKIPPED_ABSTRACT_SEC_TITLES = frozenset({'electronic supplementary material', 'supplementary material'})


def _render_abstract_sec(sec: ET.Element, level: int) -> list[str]:
    """Render a structured abstract's <sec>: its title in bold, not as a heading; then its blocks and sub-sections."""
    title = sec.find('title')
    title_text = inline_to_md(title).strip()
    if title_text.lower() in _SKIPPED_ABSTRACT_SEC_TITLES:
        return []
    sec_id = sec.get('id', '')
    anchor = f'<a id="{sec_id}"></a>\n' if sec_id else ''
    lines = []
    if title is not None:
        lines.append(f'{anchor}**{title_text}**')
    elif sec_id:
        lines.append(anchor.rstrip())
    lines.extend(md for _, md in _render_content(sec, level, skip=frozenset({'label', 'title', 'sec'})))
    for sub in sec.findall('sec'):
        lines.extend(_render_abstract_sec(sub, level))
    return lines


# ---------------------------------------------------------------------------
# Body
# ---------------------------------------------------------------------------


def render_body(body: ET.Element, level: int = 2) -> str:
    """Render <body>: its sections and (BITS) nested <book-part>s head at ``level``; other blocks render in place."""
    return '\n\n'.join(md for _, md in _render_content(body, level - 1))


def render_sec(sec: ET.Element, level: int = 2) -> str:
    """Render <sec> headed at ``level``: its label and title as the heading, then its blocks in document order.

    A <ref-list> nested in the section follows the other blocks (JATS places
    it last) and is headed against the section's title, see
    :func:`_render_ref_list_in_sec`.
    """
    parts = []

    title = sec.find('title')
    title_md = inline_to_md(title).strip()
    label = common.flat_text(sec.find('label'))
    sec_id = sec.get('id', '')
    if title is not None or label:
        anchor = f'<a id="{sec_id}"></a>\n' if sec_id else ''
        parts.append(f'{anchor}{_heading(level, " ".join(t for t in (label, title_md) if t))}')
    elif sec_id:
        # Untitled section with an id (e.g. a wrapper around supplementary
        # materials). Emit just the anchor so cross-references resolve.
        parts.append(f'<a id="{sec_id}"></a>')

    parts.extend(md for _, md in _render_content(sec, level, skip=frozenset({'label', 'title', 'ref-list'})))
    parts.extend(_render_ref_list_in_sec(ref_list, level, title_md) for ref_list in sec.findall('ref-list'))
    return '\n\n'.join(part for part in parts if part)


def render_boxed_text(box: ET.Element, level: int = 2) -> str:
    """Render <boxed-text> as a fenced quote block (sidebar / callout).

    The label and the caption's title head the quote in bold; the caption's
    paragraphs lead its body. ``level`` is the enclosing section's heading
    level; the box's own sections head one level below it.
    """
    box_id = box.get('id', '')
    caption = box.find('caption')
    caption_title = caption.find('title') if caption is not None else None
    title = ' '.join(t for t in (inline_to_md(box.find('label')).strip(), inline_to_md(caption_title).strip()) if t)
    body_parts = (
        [md for _, md in _render_content(caption, level, skip=frozenset({'title'}))] if caption is not None else []
    )
    body_parts.extend(md for _, md in _render_content(box, level, skip=frozenset({'label', 'caption'})))
    body = '\n\n'.join(body_parts)
    # Indent each line with "> " so the box renders as a markdown
    # blockquote — the closest native equivalent to a sidebar callout.
    quoted = '\n'.join('> ' + line for line in body.splitlines())
    head = f'<a id="{box_id}"></a>\n' if box_id else ''
    if title:
        return head + f'> **{title}**\n>\n{quoted}'.rstrip()
    return head + quoted


def render_disp_quote(q: ET.Element, level: int = 2) -> str:
    """Render <disp-quote> as a blockquote: label and title in bold, then the blocks, then each <attrib>."""
    head = ' '.join(t for t in (common.flat_text(q.find('label')), inline_to_md(q.find('title')).strip()) if t)
    body_parts = [f'**{head}**'] if head else []
    body_parts.extend(md for _, md in _render_content(q, level, skip=frozenset({'label', 'title', 'attrib'})))
    body_parts.extend(f'— {inline_to_md(attrib).strip()}' for attrib in q.findall('attrib'))
    body = '\n\n'.join(body_parts)
    return '\n'.join('> ' + line for line in body.splitlines())


def render_code_block(el: ET.Element) -> str:
    """Render <code> or <preformat> as a fenced code block."""
    text = ''.join(el.itertext())
    # JATS allows a language attribute on <code>; emit it as the fence info.
    lang = el.get('language', '') or el.get('code-type', '')
    return f'```{lang}\n{text.rstrip()}\n```'


def render_statement(s: ET.Element, level: int = 2) -> str:
    """Render <statement> (theorem, axiom, definition...) as a labelled block: ``**label title** body``."""
    label = common.flat_text(s.find('label'))
    title = inline_to_md(s.find('title')).strip()
    body = '\n\n'.join(md for _, md in _render_content(s, level, skip=frozenset({'label', 'title'})))
    head = ' '.join(b for b in (label, title) if b)
    return f'**{head}** {body}'.strip() if head else body


def render_def_list(dl: ET.Element, level: int = 2) -> str:
    """Render <def-list> as a bullet list, one ``- **term** — definition`` entry per <def-item>.

    The list's <title>, and its <term-head>/<def-head> column headings, lead
    in bold. A definition's blocks render in document order
    (:func:`_render_content`): the first joins the term line, the rest stack
    under it. A nested <def-list> follows the entries, indented under them.
    """
    heads = ' — '.join(
        t for t in (inline_to_md(dl.find('term-head')).strip(), inline_to_md(dl.find('def-head')).strip()) if t
    )
    lead = [f'**{t}**' for t in (inline_to_md(dl.find('title')).strip(), heads) if t]
    entries = []
    for child in dl:
        tag = common.get_tag(child)
        if tag == 'def-item':
            term = ' '.join(
                t for t in (common.flat_text(child.find('label')), inline_to_md(child.find('term')).strip()) if t
            )
            blocks = [block for defn in child.findall('def') for block in _render_content(defn, level)]
            if term or blocks:
                entries.append(common.definition_item(term, blocks))
        elif tag == 'def-list':
            # Under preceding entries it is their sub-list; opening the list it is a plain grouping.
            nested = render_def_list(child, level)
            if nested:
                entries.append(textwrap.indent(nested, '  ') if entries else nested)
    body = '\n'.join(entries)
    return '\n\n'.join([*lead, body]) if body else ''


def render_supplementary(sm: ET.Element) -> str:
    """Render a <supplementary-material> entry: anchor + label + caption + link."""
    sm_id = sm.get('id', '')
    label = common.flat(sm.findtext('label'))

    caption_md = _caption_text(sm.find('caption'))

    # The asset can sit in either <media> (xlink:href to a file) or a
    # nested <graphic>/<inline-graphic>. <media> is the JATS norm for
    # supplementary data.
    media = sm.find('media')
    if media is None:
        media = sm.find('.//graphic')
    href = common.xlink_href(media) if media is not None else ''

    lines = []
    if sm_id:
        lines.append(f'<a id="{sm_id}"></a>')
    head = f'**{label}**' if label else '**Supplementary material**'
    if href:
        head = f'{head} — [download]({href})'
    lines.append(head)
    if caption_md:
        lines.append(caption_md)
    return '\n\n'.join(lines)


# Children that describe their container rather than being its content;
# excluded from every block walk by name, never by falling through.
_METADATA_TAGS = frozenset({'sec-meta', 'permissions', 'object-id'})


def _render_content(el: ET.Element, level: int, skip: frozenset[str] = frozenset()) -> list[common.BlockFragment]:
    """Render ``el``'s content in document order as ``(tag, markdown)`` block fragments.

    A child with a block renderer (``_BLOCK_RENDERERS``) contributes its
    rendering under its own tag, a <p> child its own fragments, and each run
    of text and inline children between blocks one paragraph fragment tagged
    ``p``. Children in ``skip`` are the caller's (a label or title it renders
    itself) and metadata children are not content; any other child is inline
    content, so nothing is dropped. Empty fragments are omitted.

    ``level`` is the heading level of the section enclosing ``el``: a nested
    <sec> heads one below it.
    """
    fragments: list[common.BlockFragment] = []
    run: list[ET.Element] = [common.text_carrier(el.text)]

    def flush() -> None:
        synth = ET.Element('p')
        synth.extend(run)
        text = common.flat(inline_to_md(synth))
        if text:
            fragments.append(('p', text))

    for child in el:
        tag = common.get_tag(child)
        if tag in skip or tag in _METADATA_TAGS:
            run.append(common.text_carrier(child.tail))
        elif tag == 'p':
            flush()
            fragments.extend(_render_content(child, level))
            run = [common.text_carrier(child.tail)]
        elif (render_block := _BLOCK_RENDERERS.get(tag)) is not None:
            flush()
            md = render_block(child, level)
            if md:
                fragments.append((tag, md))
            run = [common.text_carrier(child.tail)]
        else:
            run.append(child)
    flush()
    return fragments


def render_p(p: ET.Element, level: int = 2) -> list[str]:
    """Render a <p>, lifting block-level children to standalone fragments.

    JATS Archiving's <p> content model permits <fig>, <table-wrap>,
    <list>, <disp-formula>, <boxed-text>, <code>, etc. as direct
    children — common when a publisher wants the float to anchor at
    its first textual reference. Returning a list of fragments lets
    the caller join them at paragraph granularity instead of inlining
    the float's caption text into the surrounding paragraph. ``level``
    is the enclosing section's heading level, which a lifted
    <boxed-text> nests its sections below.
    """
    return [md for _, md in _render_content(p, level)]


def render_fig(fig: ET.Element) -> str:
    fig_id = fig.get('id', '')
    label = common.flat(fig.findtext('label'))

    # JATS <fig> permits multiple <graphic> children, one per panel
    # (a, b, c, ...). Emit one image link per panel. Some publishers
    # additionally wrap them in <alternatives> alongside thumbnails or
    # alternative formats; fall through to that if no direct child
    # <graphic> is present.
    graphics = list(fig.findall('graphic'))
    if not graphics:
        alts = fig.find('alternatives')
        if alts is not None:
            graphics = list(alts.findall('graphic'))

    caption_md = _caption_text(fig.find('caption'))
    # <object-id pub-id-type="doi"> often carries a figure-specific DOI
    # in PMC content. Surface it as a trailing markdown link.
    doi_link = _object_id_doi_link(fig)
    if doi_link:
        caption_md = (caption_md + ' ' + doi_link).strip()

    lines = []
    if fig_id:
        lines.append(f'<a id="{fig_id}"></a>')
    if graphics:
        # The image alt-text is just the figure label (e.g. "Figure 1").
        # Inlining the caption here means it shows up twice: once as
        # truncated alt-text and once as the visible caption line below.
        for i, g in enumerate(graphics, 1):
            href = common.xlink_href(g)
            if not href:
                continue
            alt = label or 'fig'
            if len(graphics) > 1:
                alt = f'{alt} ({chr(96 + i)})'
            lines.append(f'![{alt}]({href})')
    lines.append(f'**{label}** {caption_md}'.strip())

    # eLife and others nest figure supplements as <fig> descendants of
    # the parent <fig> (typically inside its trailing <p> children).
    # Recurse so each supplement gets its own anchor and caption.
    for nested in fig.iter('fig'):
        if nested is fig:
            continue
        lines.append('')
        lines.append(render_fig(nested))
    return '\n'.join(lines)


def _render_graphic(g: ET.Element, _level: int) -> str:
    """Render a <graphic>, <inline-graphic> or <media>: anchor, image (a link for <media>), then its label and caption.

    The <alt-text> is the image's alt. Publishers attach supplementary files as
    a labelled, captioned <media> inside a paragraph, so the label and caption
    are content, not decoration.
    """
    href = common.xlink_href(g)
    alt = common.flat_text(g.find('alt-text')) or common.get_tag(g)
    gid = g.get('id', '')
    label = common.flat_text(g.find('label'))
    caption_md = _caption_text(g.find('caption'))
    lines = []
    if gid:
        lines.append(f'<a id="{gid}"></a>')
    if href:
        lines.append(f'[{alt}]({href})' if common.get_tag(g) == 'media' else f'![{alt}]({href})')
    if label or caption_md:
        lines.append(f'**{label}** {caption_md}'.strip() if label else caption_md)
    return '\n'.join(lines)


def _render_group(group: ET.Element, level: int) -> str:
    """Render a <fig-group>, <table-wrap-group> or <disp-formula-group>: its label and caption head the members."""
    label = common.flat_text(group.find('label'))
    head = ' '.join(t for t in (f'**{label}**' if label else '', _caption_text(group.find('caption'))) if t)
    members = [md for _, md in _render_content(group, level, skip=frozenset({'label', 'caption'}))]
    return '\n\n'.join(part for part in (head, *members) if part)


def render_table_wrap(tw: ET.Element, level: int = 2) -> str:
    tw_id = tw.get('id', '')
    label = common.flat(tw.findtext('label'))

    caption_md = _caption_text(tw.find('caption'))
    doi_link = _object_id_doi_link(tw)
    if doi_link:
        caption_md = (caption_md + ' ' + doi_link).strip()

    table = tw.find('.//table')
    table_md = render_table(table, level) if table is not None else ''

    # JATS <table-wrap> content model lists <table> and <graphic> as
    # alternatives. Older articles (especially PLOS Genetics circa
    # 2006) typeset tables as images and ship only <graphic> with no
    # <table> markup. Fall back to an image link in that case so the
    # content isn't silently dropped — markdown can't reconstruct the
    # tabular structure from the image without an OCR step.
    image_md = ''
    if table is None:
        graphic = tw.find('graphic')
        if graphic is None:
            graphic = tw.find('.//graphic')
        if graphic is not None:
            href = common.xlink_href(graphic)
            if href:
                alt = f'{label}: {caption_md}'[:120] if caption_md else label
                image_md = f'![{alt}]({href})'

    foot = tw.find('table-wrap-foot')
    foot_md = ' '.join(_render_table_foot(foot, level)) if foot is not None else ''

    parts = []
    if tw_id:
        parts.append(f'<a id="{tw_id}"></a>')
    parts.append(f'**{label}** {caption_md}')
    if table_md:
        parts.append(table_md)
    elif image_md:
        parts.append(image_md)
    if foot_md:
        parts.append(f'*{foot_md}*')
    return '\n\n'.join(parts)


def _render_table_foot(foot: ET.Element, level: int) -> list[str]:
    """Render a <table-wrap-foot>'s children — <fn>s, grouped or not, paragraphs, attributions — each on one line."""
    parts = []
    for child in foot:
        tag = common.get_tag(child)
        if tag == 'fn':
            parts.append(_render_table_fn(child, level))
        elif tag == 'fn-group':
            parts.append(f'**{inline_to_md(child.find("title")).strip()}**' if child.find('title') is not None else '')
            parts.extend(_render_table_fn(fn, level) for fn in child.findall('fn'))
        elif tag not in _METADATA_TAGS:
            parts.append(common.flat(' '.join(md for _, md in _render_content(child, level))))
    return [part for part in parts if part]


def _render_table_fn(fn: ET.Element, level: int) -> str:
    """A table footnote on one line, its label as a superscript marker."""
    label = common.flat_text(fn.find('label'))
    body = common.flat(' '.join(md for _, md in _render_content(fn, level, skip=frozenset({'label'}))))
    return f'<sup>{label}</sup> {body}' if label and body else body


def render_table(table: ET.Element, level: int = 2) -> str:
    """Render an XHTML-model JATS <table> (thead/tbody/tr/td/th), or an <array>, as a GFM table.

    A cell's block content — several <p>s, a <list> — is laid out on one
    line with ``<br>`` separators (:func:`common.md_cell`).
    """

    def get_cells_raw(tr: ET.Element) -> list[tuple[str, int, int]]:
        """Return list of (content, colspan, rowspan) for each cell in a row."""
        cells = []
        for cell in tr:
            if common.get_tag(cell) not in ('td', 'th'):
                continue
            content = common.md_cell(md for _, md in _render_content(cell, level))
            colspan = max(1, int(cell.get('colspan', 1)))
            rowspan = max(1, int(cell.get('rowspan', 1)))
            cells.append((content, colspan, rowspan))
        return cells

    header_rows_raw = []
    thead = table.find('thead')
    if thead is not None:
        for tr in thead.findall('tr'):
            header_rows_raw.append(get_cells_raw(tr))

    body_rows_raw = []
    tbody = table.find('tbody')
    if tbody is not None:
        for tr in tbody.findall('tr'):
            body_rows_raw.append(get_cells_raw(tr))

    return common.render_grid(header_rows_raw, body_rows_raw)


def render_list(lst: ET.Element, level: int = 2) -> str:
    """Render <list>: its <title> as a bold lead line, then each <list-item>'s blocks stacked under its marker.

    The marker is the item's <label> when it has one, else its ordinal for
    ``list-type="order"`` and a bullet otherwise. The item's blocks render in
    document order (:func:`_render_content`) and are laid out by
    :func:`common.list_item`: a nested <list> or <def-list> becomes an
    indented sub-list, a later paragraph a continuation paragraph. ``level``
    is the heading level of the section enclosing the list.
    """
    ordered = lst.get('list-type') == 'order'
    items = []
    for i, item in enumerate(lst.findall('list-item'), 1):
        marker = common.flat_text(item.find('label')) or (f'{i}.' if ordered else '-')
        items.append(common.list_item(marker, _render_content(item, level, skip=frozenset({'label'}))))
    body = '\n'.join(items)
    title = inline_to_md(lst.find('title')).strip()
    return f'**{title}**\n\n{body}' if title and body else body


def _formula_body(formula: ET.Element, display: bool) -> str:
    """Render the body of a formula element (display or inline).

    Walks the element looking for a representation in this preference
    order:
      1. <tex-math>           — author-authored LaTeX (preferred for
                                 fidelity to source intent).
      2. <math> (MathML)      — converted via litdown.mathml.
      3. <graphic>/<inline-graphic> — image fallback for pre-MathML
                                       publisher tooling.

    All three may co-exist inside <alternatives>; descendant search
    picks up whichever is present.
    """
    tm = formula.find('.//tex-math')
    if tm is not None:
        tex = _extract_tex(tm)
        if tex:
            return f'$${tex}$$' if display else f'${tex}$'
    math = formula.find(f'.//{{{common.MML_NS}}}math')
    if math is not None:
        return mathml.render_mathml(math, display=display)
    g = formula.find('.//graphic')
    if g is None:
        g = formula.find('.//inline-graphic')
    href = common.xlink_href(g) if g is not None else ''
    if href:
        fid = formula.get('id', '')
        alt = f'eq {fid}' if fid else 'eq'
        return f'![{alt}]({href})'
    return ''


def _render_inline_formula(elem: ET.Element) -> str:
    return _formula_body(elem, display=False)


def render_formula(formula: ET.Element) -> str:
    fid = formula.get('id', '')
    anchor = f'<a id="{fid}"></a>\n' if fid else ''
    label = common.flat(formula.findtext('label'))
    label_suffix = f'  {label}' if label else ''
    body = _formula_body(formula, display=True)
    if body:
        return f'{anchor}{body}{label_suffix}'
    # Last resort: any text directly inside the disp-formula. Avoid
    # itertext() so we don't pick up tex-math preamble or MathML
    # element names from earlier-attempted siblings.
    text = common.flat(formula.text)
    return f'{anchor}$${text}$${label_suffix}' if text else ''


# ---------------------------------------------------------------------------
# Back matter
# ---------------------------------------------------------------------------


def render_floats_group(floats: ET.Element, level: int = 1) -> str:
    """Render a <floats-group> (figs/tables placed at article end); ``level`` is the document's heading level."""
    return '\n\n'.join(md for _, md in _render_content(floats, level))


def render_back(back: ET.Element, level: int = 2) -> str:
    """Render <back>, each of its sections headed at ``level``."""
    parts = []

    # JATS allows <back> to mix several block-level child types in any order:
    # <ack>, <app-group>/<app> (appendices — Nature places extended-data figs
    # here), bare <sec>s (extended methods), <ref-list>, <notes>, <fn-group>,
    # <bio>, <glossary>. Walk children once so order is preserved.
    for child in back:
        tag = common.get_tag(child)
        if tag == 'ack':
            # JATS convention: <ack> implies "Acknowledgments" even
            # without a <title>. Render the heading explicitly so it
            # doesn't disappear into the surrounding paragraph stream.
            sub_secs = child.findall('sec')
            if sub_secs:
                # If the inner <sec>s carry their own titles, let them
                # provide the heading. If not, prepend a default first.
                if not any(s.find('title') is not None for s in sub_secs):
                    parts.append(_heading(level, 'Acknowledgments'))
                for sec in sub_secs:
                    parts.append(render_sec(sec, level))
            else:
                if child.find('title') is None:
                    parts.append(_heading(level, 'Acknowledgments'))
                parts.append(render_sec(child, level))
        elif tag == 'app-group':
            # A titled group heads its appendices one level down; an untitled
            # one is a bare container.
            if child.find('title') is not None or child.find('label') is not None:
                parts.append(render_sec(child, level))
            else:
                parts.extend(render_sec(app, level) for app in child.findall('app'))
        elif tag in {'app', 'sec', 'bio'}:
            parts.append(render_sec(child, level))
        elif tag == 'ref-list':
            parts.append(render_ref_list(child, level))
        elif tag == 'notes':
            # <notes> typically holds Author contributions, Competing
            # interests, Data/Code availability, etc. Has a <title>
            # and one or more <p>/<sec> children — render as a section.
            parts.append(render_sec(child, level))
        elif tag == 'fn-group':
            parts.append(render_fn_group(child, level))
        elif tag == 'glossary':
            parts.append(render_glossary(child, level))

    return '\n\n'.join(part for part in parts if part)


def render_glossary(gloss: ET.Element, level: int = 2) -> str:
    """Render <glossary> headed at ``level``.

    Its <def-list>s, paragraphs and nested glossaries (headed one level down)
    follow in document order.
    """
    title_el = gloss.find('title')
    heading = inline_to_md(title_el).strip() if title_el is not None else 'Glossary'
    body = [md for _, md in _render_content(gloss, level, skip=frozenset({'label', 'title'}))]
    if not body:
        return ''
    return '\n\n'.join([_heading(level, heading), *body])


_FN_TYPE_LABELS = {
    'con': 'Author contributions',
    'COI-statement': 'Competing interests',
    'conflict': 'Conflict of interest',
    'financial-disclosure': 'Funding',
    'supported-by': 'Funding',
    'current-aff': 'Current address',
    'deceased': 'Deceased',
    'equal': 'Equal contribution',
    'presented-at': 'Presented at',
    'supplementary-material': 'Supplementary material',
    'other': 'Note',
}


def render_fn_group(fn_group: ET.Element, level: int = 2) -> str:
    """Render <fn-group> with each <fn>'s typed entry as its own heading at ``level``.

    Footnotes carrying an fn-type whose label is well-known (Author
    contributions, Competing interests, Funding, ...) become standalone
    headed sections — one per fn — instead of being lumped under a
    single generic "Notes" heading.

    A fn-group's own <title> (if present) overrides any per-fn heading.
    Footnotes with no fn-type fall back to a shared "Notes" section.
    """
    explicit_title_el = fn_group.find('title')
    explicit_title = inline_to_md(explicit_title_el).strip() if explicit_title_el is not None else ''

    fns = fn_group.findall('fn')
    if not fns:
        return ''

    blocks: list[str] = []
    untyped_lines: list[str] = []

    for fn in fns:
        fn_type = fn.get('fn-type', '')
        label = common.flat_text(fn.find('label'))
        body = '\n\n'.join(md for _, md in _render_content(fn, level, skip=frozenset({'label'})))
        if not body:
            continue

        # JATS <fn> content model is just (p)+ — there's nowhere to
        # put a typed heading. PLOS Genetics works around this by
        # opening the first <p> with a bold heading
        # ("**Competing interests.** ..."). Detect that and promote
        # the inline heading to an H2 of its own.
        if body.startswith('**') and '**' in body[2:]:
            close = body.index('**', 2)
            inline_heading = body[2:close].rstrip(':.').strip()
            after = body[close + 2 :].lstrip(' .').strip()
            if inline_heading and after:
                blocks.append(f'{_heading(level, inline_heading)}\n\n{after}')
                continue

        if explicit_title:
            untyped_lines.append(body)
        elif fn_type and fn_type in _FN_TYPE_LABELS:
            blocks.append(f'{_heading(level, _FN_TYPE_LABELS[fn_type])}\n\n{body}')
        elif label:
            untyped_lines.append(f'<sup>{label}</sup> {body}')
        else:
            untyped_lines.append(body)

    if untyped_lines:
        blocks.append(f'{_heading(level, explicit_title or "Notes")}\n\n' + '\n\n'.join(untyped_lines))

    return '\n\n'.join(blocks)


def _render_mixed_citation(ec: ET.Element) -> str:  # noqa: PLR0912
    """Render a <mixed-citation> as a single inline string.

    JATS <mixed-citation> is a free-form text container with optional
    structured children (person-group, pub-id, etc.). Two
    pre-processing steps before falling back to inline_to_md:

    * Strip <pub-id> descendants. Their raw text values (DOIs, PMCIDs,
      PMIDs) would otherwise concatenate into a single unbroken digit
      run. They're re-emitted at the end as proper hyperlinks unless
      already present in the inline text.
    * Flatten <person-group> in place. inline_to_md walks
      <name>/<surname> + <given-names> as bare text with no spacing
      ("AdamZ.AdamskaI."), so we replace the element with a
      pre-formatted "Surname Initials, ..." string.
    """
    pruned = copy.deepcopy(ec)

    # Strip <pub-id> descendants (rendered separately at the end).
    for parent in list(pruned.iter()):
        for child in list(parent):
            if common.get_tag(child) == 'pub-id':
                if child.tail:
                    idx = list(parent).index(child)
                    if idx == 0:
                        parent.text = (parent.text or '') + child.tail
                    else:
                        prev = parent[idx - 1]
                        prev.tail = (prev.tail or '') + child.tail
                parent.remove(child)

    # Replace <person-group> with a flattened "Author1, Author2, ..." text.
    for parent in list(pruned.iter()):
        for child in list(parent):
            if common.get_tag(child) != 'person-group':
                continue
            authors = []
            for sub in child:
                tag = common.get_tag(sub)
                if tag == 'name':
                    sn = common.flat(sub.findtext('surname'))
                    gn = common.flat(sub.findtext('given-names'))
                    authors.append(f'{sn} {gn}'.strip())
                elif tag in {'string-name', 'collab'}:
                    authors.append(common.flat_text(sub))
                elif tag == 'etal':
                    authors.append('et al.')
            joined = ', '.join(a for a in authors if a)
            # Replace the child with a TEXT-only representation by rewriting
            # parent.text/preceding-sibling.tail and removing the element.
            idx = list(parent).index(child)
            tail = child.tail or ''
            replacement = (joined + tail) if joined else tail
            if idx == 0:
                parent.text = (parent.text or '') + replacement
            else:
                prev = parent[idx - 1]
                prev.tail = (prev.tail or '') + replacement
            parent.remove(child)

    body = inline_to_md(pruned).strip()

    id_parts = []
    for pid in ec.findall('.//pub-id'):
        pid_type = pid.get('pub-id-type', '')
        val = common.flat(pid.text)
        if not val:
            continue
        if pid_type == 'doi' and val not in body:
            id_parts.append(f'[doi:{val}](https://doi.org/{val})')
        elif pid_type == 'pmid' and val not in body:
            id_parts.append(f'PMID:{val}')
        elif pid_type in ('pmcid', 'pmc') and val not in body:
            id_parts.append(f'PMC:{val}')
    if id_parts:
        body = (body + ' ' + ' '.join(id_parts)).strip()
    return body


_NUMBERED_REF_ID_RE = re.compile(r'B?(\d+)')


def render_ref_list(ref_list: ET.Element, level: int = 2) -> str:
    """Render <ref-list> headed at ``level``; the heading is "References" when it has no <title>."""
    title = inline_to_md(ref_list.find('title')).strip() or 'References'
    return '\n'.join([_heading(level, title), '', *_render_refs(ref_list)])


def _render_ref_list_in_sec(ref_list: ET.Element, level: int, sec_title: str) -> str:
    """Render a <ref-list> nested in a <sec> headed ``sec_title`` at ``level``.

    The section's heading already heads the list, so the list's own <title>
    adds a heading one level down only when it says something else — Bookshelf
    nests a "References" list under a "References" section. An untitled
    section emits no heading, so the list's heading takes ``level`` itself.
    """
    title = inline_to_md(ref_list.find('title')).strip()
    lines = _render_refs(ref_list)
    if not title or _heading_key(title) == _heading_key(sec_title):
        return '\n'.join(lines)
    return '\n'.join([_heading(level + 1 if sec_title else level, title), '', *lines])


def _heading_key(text: str) -> str:
    """Heading text reduced for equality: case-folded, trailing ``.`` and ``:`` dropped."""
    return text.rstrip('.:').casefold()


def _ref_label(ref: ET.Element) -> str:
    """The <ref>'s <label>, else the number a PMC-style ``B12`` or ``12`` id carries; ``''`` otherwise."""
    label = common.flat_text(ref.find('label'))
    if label:
        return label
    match = _NUMBERED_REF_ID_RE.fullmatch(ref.get('id', ''))
    return match.group(1) if match else ''


def _render_refs(ref_list: ET.Element) -> list[str]:  # noqa: C901, PLR0912, PLR0915
    """Render each <ref> as its anchor line, its citation line and a blank line.

    The citation line opens with the reference's label when it has one; a
    citation whose <ref> carries only an opaque id (``CR45``, ``bib7``,
    ``brca1.REF.doe.2020``) stands unlabelled behind its anchor.
    """
    lines: list[str] = []

    for ref in ref_list.findall('ref'):
        ref_id = ref.get('id', '')
        # Nature et al. wrap the citation in <citation-alternatives>; older
        # NLM dialects use a bare <citation>. Search descendants and accept
        # any of the three flavours. Use `is not None` checks rather than
        # `or` chains: an Element with no subelements is falsy in boolean
        # context (deprecated since Python 3.12), so a leaf <mixed-citation>
        # carrying only text would be skipped.
        ec = ref.find('.//element-citation')
        is_mixed = False
        if ec is None:
            ec = ref.find('.//mixed-citation')
            is_mixed = ec is not None
        if ec is None:
            ec = ref.find('.//citation')
        if ec is None:
            continue

        label = _ref_label(ref)
        # A label such as "11." already carries its period.
        prefix = f'{label}{"" if label.endswith(".") else "."} ' if label else ''

        # Mixed-citation is a free-form text container — render verbatim
        # via inline_to_md. Trying to extract structured fields from it
        # tends to lose the citation prose, since most of the content is
        # bare text rather than child elements.
        if is_mixed:
            body = _render_mixed_citation(ec)
            lines.append(f'<a id="{ref_id}"></a>')
            lines.append(f'{prefix}{body}'.rstrip())
            lines.append('')
            continue

        # Authors
        pg = ec.find('person-group')
        authors = []
        if pg is not None:
            for name in pg.findall('name'):
                sn = common.flat(name.findtext('surname'))
                gn = common.flat(name.findtext('given-names'))
                authors.append(f'{sn} {gn}'.strip())
            for sname in pg.findall('string-name'):
                # Nature uses <string-name> instead of <name> for free-form
                # author strings.
                authors.append(common.flat_text(sname))
            for collab in pg.findall('collab'):
                authors.append(common.flat_text(collab))
            if pg.find('etal') is not None:
                authors.append('et al.')
        authors_str = ', '.join(a for a in authors if a)

        # Title
        title_elem = ec.find('article-title')
        art_title = inline_to_md(title_elem).strip() if title_elem is not None else ''

        source = common.flat(ec.findtext('source'))  # journal / book

        # Numeric fields
        year = common.flat(ec.findtext('year'))
        volume = common.flat(ec.findtext('volume'))
        issue = common.flat(ec.findtext('issue'))
        fpage = common.flat(ec.findtext('fpage'))
        lpage = common.flat(ec.findtext('lpage'))
        pages = f'{fpage}–{lpage}' if fpage and lpage else fpage
        publisher_loc = common.flat(ec.findtext('publisher-loc'))
        publisher_name = common.flat(ec.findtext('publisher-name'))

        # Build citation string
        seg = []
        if authors_str:
            authors_suffix = '' if authors_str.rstrip().endswith('.') else '.'
            seg.append(authors_str + authors_suffix)
        if art_title:
            # Avoid double period when title already ends with punctuation.
            suffix = '' if art_title.rstrip().endswith(('.', '?', '!')) else '.'
            seg.append(art_title + suffix)
        if source:
            journal_part = f'*{source}*'
            if year:
                journal_part += f' {year}'
            if volume:
                journal_part += f';**{volume}**'
                if issue:
                    journal_part += f'({issue})'
            if pages:
                journal_part += f':{pages}'
            seg.append(journal_part + '.')
        # Book / proceedings refs: render publisher location and name as
        # "Loc: Publisher." (e.g. "Princeton: Princeton University Press.")
        if publisher_loc or publisher_name:
            pub = ': '.join(p for p in (publisher_loc, publisher_name) if p)
            seg.append(pub + '.')

        # Pub IDs
        id_parts = []
        for pid in ec.findall('pub-id'):
            pid_type = pid.get('pub-id-type', '')
            val = common.flat(pid.text)
            if not val:
                continue
            if pid_type == 'doi':
                id_parts.append(f'[doi:{val}](https://doi.org/{val})')
            elif pid_type == 'pmid':
                id_parts.append(f'PMID:{val}')
            elif pid_type in ('pmcid', 'pmc'):
                id_parts.append(f'PMC:{val}')
        if id_parts:
            seg.append(' '.join(id_parts))

        body = ' '.join(seg).strip()
        # Element-citation with no structured content at all — render
        # the element's text as a last resort rather than emitting just
        # an empty label line.
        if not body:
            body = inline_to_md(ec).strip()

        lines.append(f'<a id="{ref_id}"></a>')
        lines.append(f'{prefix}{body}'.rstrip())
        lines.append('')

    return lines


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

_ADJACENT_SUP_RE = re.compile(r'</sup><sup>')
_WRAPPER_META_TAGS = frozenset({'processing-meta', 'collection-meta', 'book-meta'})


def render(root: ET.Element) -> str:
    """Render a parsed JATS ``<article>`` root to Markdown.

    The dispatcher in :func:`litdown.convert` parses and sniffs the root,
    then calls this; it does not re-parse.
    """
    front = root.find('front')
    body = root.find('body')
    back = root.find('back')
    # <floats-group> (Archiving only) holds the figs/tables a publisher
    # places at article end rather than inline.
    floats = root.find('floats-group')
    return _render_document(
        [
            render_front(front) if front is not None else '',
            render_body(body) if body is not None else '',
            render_back(back) if back is not None else '',
            render_floats_group(floats) if floats is not None else '',
        ]
    )


def render_book_part_wrapper(root: ET.Element) -> str:
    """Render a parsed BITS ``<book-part-wrapper>`` root to Markdown.

    The wrapper holds one unit of a book; Europe PMC serves NCBI Bookshelf
    content as a wrapper around one ``<book-part>`` — a chapter. The part's
    ``<book-part-meta>`` takes the place of an article's ``<front>``; its
    ``<body>`` and ``<back>`` share the article content model and render
    the same way.

    Raises:
        ValueError: If the wrapper does not hold exactly one unit, or that
            unit is not a ``<book-part>`` — a ``<book-app>``, ``<preface>``,
            ``<glossary>``, ``<ref-list>``, ...
    """
    units = [child for child in root if common.get_tag(child) not in _WRAPPER_META_TAGS]
    if len(units) != 1 or common.get_tag(units[0]) != 'book-part':
        held = ' '.join(f'<{common.get_tag(unit)}>' for unit in units) or 'no unit'
        raise ValueError(f'book-part-wrapper holds {held}; only one <book-part> is rendered')
    return _render_document(_book_part_sections(units[0], level=1))


def render_book_part(part: ET.Element, level: int) -> str:
    """Render a ``<book-part>`` nested in a ``<body>``, its title headed at ``level``.

    BITS lets a part's body end in further parts — a Bookshelf part whose
    chapters nest. Meta, body and back render as they do at the top level,
    one heading level down per nesting, without rules between them.
    """
    return '\n\n'.join(_book_part_sections(part, level))


def _book_part_sections(part: ET.Element, level: int) -> list[str]:
    """Render a <book-part>'s meta, body and back, the title headed at ``level``; absent or empty ones are omitted."""
    meta = part.find('book-part-meta')
    body = part.find('body')
    back = part.find('back')
    sections = [
        render_book_part_meta(meta, level) if meta is not None else '',
        render_body(body, level + 1) if body is not None else '',
        render_back(back, level + 1) if back is not None else '',
    ]
    return [section for section in sections if section]


def render_book_part_meta(meta: ET.Element, level: int = 1) -> str:
    """Render ``<book-part-meta>`` as front matter: the labelled title headed at ``level``, then each abstract."""
    parts: list[str] = []
    title_group = meta.find('title-group')
    heading = _title_group_heading(title_group, 'title') if title_group is not None else ''
    if heading:
        parts.append(_heading(level, heading))
    parts.extend(render_abstract(abstract, level + 1) for abstract in meta.findall('abstract'))
    return '\n\n'.join(parts)


def _render_document(sections: list[str]) -> str:
    """Join a document's top-level sections with horizontal rules and fuse split superscripts.

    Empty sections are dropped, so a front matter or back that rendered
    nothing leaves no stray rule.

    SPEC DEVIATION (post-process): some publisher source splits a numeric
    exponent across two adjacent ``<sup>`` tags (``10<sup>-</sup><sup>4</sup>``).
    Any consumer would show that as "-4" anyway, so the pair is collapsed.
    Strict adjacency only: inline output is whitespace-flattened, so any
    separator means two distinct superscripts (a unit exponent followed by a
    citation marker), and fusing those corrupts both.
    """
    md = '\n\n---\n\n'.join(section for section in sections if section)
    return _ADJACENT_SUP_RE.sub('', md)


# ---------------------------------------------------------------------------
# Block dispatch
# ---------------------------------------------------------------------------

# Block-level tag → renderer taking the element and the enclosing section's
# heading level; the table :func:`_render_content` walks children against.
# <p> is handled by the walker itself (it yields several fragments). A tag
# absent here is inline content to the walker.
_BLOCK_RENDERERS: dict[str, Callable[[ET.Element, int], str]] = {
    'sec': lambda el, level: render_sec(el, level + 1),
    'app': lambda el, level: render_sec(el, level + 1),
    'ack': lambda el, level: render_sec(el, level + 1),
    'bio': lambda el, level: render_sec(el, level + 1),
    'notes': lambda el, level: render_sec(el, level + 1),
    'list': render_list,
    'def-list': render_def_list,
    'fig': lambda el, _level: render_fig(el),
    'fig-group': _render_group,
    'table-wrap': render_table_wrap,
    'table-wrap-group': _render_group,
    'array': render_table,
    'disp-formula': lambda el, _level: render_formula(el),
    'disp-formula-group': _render_group,
    'boxed-text': render_boxed_text,
    'disp-quote': render_disp_quote,
    'code': lambda el, _level: render_code_block(el),
    'preformat': lambda el, _level: render_code_block(el),
    'statement': render_statement,
    'supplementary-material': lambda el, _level: render_supplementary(el),
    'graphic': _render_graphic,
    'media': _render_graphic,
    'ref-list': lambda el, level: render_ref_list(el, level + 1),
    'fn-group': lambda el, level: render_fn_group(el, level + 1),
    'glossary': lambda el, level: render_glossary(el, level + 1),
    'book-part': lambda el, level: render_book_part(el, level + 1),
}
