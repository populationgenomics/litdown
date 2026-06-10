"""Convert a JATS XML article to semantically-richer Markdown.

Public entry point: :func:`convert`.
"""

import re
import xml.etree.ElementTree as ET

from litdown.common import (
    MML_NS,
    XLINK_NS,
    get_tag,
    inline_wrap,
    md_escape_cell,
    render_grid,
    xlink_href,
)
from litdown.mathml import mml_to_tex, render_mathml  # noqa: F401

__all__ = [
    'MML_NS',
    'XLINK_NS',
    'get_tag',
    'md_escape_cell',
    'render',
    'xlink_href',
]


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
    """Return a markdown DOI link for an <object-id pub-id-type='doi'>
    child of elem, or empty string if absent. PLOS uses these to
    attach a per-figure / per-table DOI distinct from the article DOI.
    """
    for oid in elem.findall('object-id'):
        if oid.get('pub-id-type') == 'doi':
            val = (oid.text or '').strip()
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
        tag = get_tag(child)
        if tag in ('title', 'p'):
            text = inline_to_md(child).strip()
            if text:
                parts.append(text)
    return ' '.join(parts)


def _extract_tex(tex_math_el: ET.Element) -> str:
    """Pull the actual math expression out of a <tex-math> element.

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


# ---------------------------------------------------------------------------
# Inline renderer  (elem → markdown string, no trailing newline)
# ---------------------------------------------------------------------------


def inline_to_md(elem: ET.Element | None) -> str:
    """Render an element's mixed content as inline Markdown."""
    if elem is None:
        return ''

    buf = []
    if elem.text:
        buf.append(elem.text)

    for child in elem:
        tag = get_tag(child)
        inner = inline_to_md(child)

        # Shared inline typographic leaves (italic/bold/sup/sub/underline/
        # monospace/strike). JATS tag names line up 1:1 with the canonical
        # keys, so no remapping is needed. monospace → backtick code span
        # doubles as the markdown convention for variable names, paths, etc.
        wrapped = inline_wrap(tag, inner)
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
            href = xlink_href(child)
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
        elif tag == 'named-content':
            buf.append(inner)
        else:
            buf.append(inner)

        if child.tail:
            buf.append(child.tail)

    return ''.join(buf)


# ---------------------------------------------------------------------------
# Front matter
# ---------------------------------------------------------------------------


def render_front(front: ET.Element) -> str:
    jmeta = front.find('journal-meta')
    ameta = front.find('article-meta')
    if ameta is None:
        # Every JATS article has <article-meta>; without it there's nothing
        # to render in the front matter beyond the bare journal title.
        return ''
    parts: list[str] = []

    # --- Title ---
    title_elem = ameta.find('.//article-title')
    title = inline_to_md(title_elem)
    parts.append(f'# {title}')

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
        aff.get('id') or '': aff for aff in ameta.iter() if get_tag(aff) == 'aff' and aff.get('id')
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
            sn = name.findtext('surname') or ''
            gn = name.findtext('given-names') or ''
            full = f'{gn} {sn}'.strip()
        elif collab is not None:
            # The collab's lead text is the consortium name; nested
            # <contrib-group> holds individual members which we drop here.
            full = (collab.text or '').strip()
            if not full:
                full = ''.join(collab.itertext()).strip()
        else:
            full = ''.join(c.itertext()).strip()

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
            marker = ''.join(x.itertext()).strip()
            if not marker and rt == 'aff':
                rid = x.get('rid', '')
                aff = aff_map.get(rid)
                if aff is not None:
                    marker = (aff.findtext('label') or '').strip()
                    if not marker:
                        # JATS Archiving permits <sup> as an aff-level
                        # marker alongside <label>. Frontiers uses <sup>;
                        # PLOS uses <label>. Both are spec-valid.
                        sup = aff.find('sup')
                        if sup is not None:
                            marker = (sup.text or '').strip()
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
        email = (email_el.text or '').strip() if email_el is not None else ''
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
        label = (aff.findtext('label') or '').strip()
        if not label:
            sup = aff.find('sup')
            if sup is not None:
                label = (sup.text or '').strip()
        if not label:
            label = aff_ordinal.get(aff_id, '')
        text_parts = []
        if aff.text:
            text_parts.append(aff.text.strip())
        for child in aff:
            ctag = get_tag(child)
            if ctag == 'label' or ctag == 'sup':
                # Skip label/sup-as-label markers — already extracted above.
                pass
            elif ctag == 'institution-wrap':
                # <institution-wrap> contains <institution> name(s)
                # alongside zero or more <institution-id> children
                # carrying ROR / GRID / ISNI / FundRef identifiers.
                # Render only the <institution> text — the URI-shaped
                # identifiers don't help a markdown reader.
                if child.text:
                    text_parts.append(child.text.strip())
                for sub in child:
                    if get_tag(sub) == 'institution':
                        text_parts.append(inline_to_md(sub).strip())
                    if sub.tail:
                        text_parts.append(sub.tail.strip())
            elif ctag == 'institution-id':
                pass
            else:
                text_parts.append(inline_to_md(child))
            if child.tail:
                text_parts.append(child.tail.strip())
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
                sn = name.findtext('surname') or ''
                gn = name.findtext('given-names') or ''
                full = f'{gn} {sn}'.strip()
            else:
                full = ''.join(c.itertext()).strip()
            role = c.findtext('role') or 'Editor'
            # Editor's affiliation lookup
            aff_text = ''
            for x in c.findall("xref[@ref-type='aff']"):
                rid = x.get('rid', '')
                aff = aff_map.get(rid)
                if aff is not None:
                    aff_text = ' '.join(t for t in aff.itertext()).strip()
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
            tag = get_tag(child)
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
    jname = jmeta.findtext('.//journal-title') if jmeta is not None else ''
    jname = jname or ''
    issn = (jmeta.findtext('issn') if jmeta is not None else '') or ''
    pmcid = ameta.findtext("article-id[@pub-id-type='pmcid']") or ''
    pmid = ameta.findtext("article-id[@pub-id-type='pmid']") or ''
    doi = ameta.findtext("article-id[@pub-id-type='doi']") or ''

    # Publication dates
    epub = ameta.find("pub-date[@pub-type='epub']")
    date_parts = []
    if epub is not None:
        y = epub.findtext('year') or ''
        m = epub.findtext('month') or ''
        d = epub.findtext('day') or ''
        date_parts = [x for x in [y, m.zfill(2) if m else '', d.zfill(2) if d else ''] if x]

    vol = ameta.findtext('volume') or ''
    issue = ameta.findtext('issue') or ''
    fpage = ameta.findtext('fpage') or ''
    lpage = ameta.findtext('lpage') or ''
    pages = f'{fpage}–{lpage}' if fpage and lpage else fpage

    # Article subject / category (e.g. "Research Article", "Plant Science /
    # Review Article"). Some publishers (Frontiers) print this at the top
    # of page 1 alongside the journal title.
    cats = ameta.find('article-categories')
    cat_subjects: list[str] = []
    if cats is not None:
        for sg in cats.findall('subj-group'):
            cat_subjects.extend(
                (s.text or '').strip() for s in sg.iter() if get_tag(s) == 'subject' and (s.text or '').strip()
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
            y = date.findtext('year') or ''
            m = date.findtext('month') or ''
            d = date.findtext('day') or ''
            dparts = [x for x in [y, m.zfill(2) if m else '', d.zfill(2) if d else ''] if x]
            if dparts:
                meta_lines.append(f'**{dtype.capitalize()}:** {"-".join(dparts)}')

    # Copyright + license
    copyright_stmt = (ameta.findtext('.//copyright-statement') or '').strip()
    if copyright_stmt:
        meta_lines.append(f'**License:** {copyright_stmt}')
    # The full license text usually lives in <license>/<license-p>; render
    # those separately so CC clauses don't get truncated to the bare
    # copyright statement.
    for license_el in ameta.findall('.//license'):
        href = license_el.get(f'{{{XLINK_NS}}}href') or license_el.get('href') or ''
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
        kwds = [(k.text or '').strip() for k in kg.findall('kwd') if (k.text or '').strip()]
        if not kwds:
            continue
        gtitle = (kg.findtext('title') or 'Keywords').strip()
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

    lines = ['## Funding']
    for ag in awards:
        # Funding source: prefer plain text, then <institution>, then any
        # nested text (skipping institution-id URIs).
        src = ag.find('funding-source')
        funder = ''
        if src is not None:
            inst = src.find('.//institution')
            if inst is not None:
                funder = ''.join(inst.itertext()).strip()
            if not funder:
                funder = (src.text or '').strip() or ''.join(
                    t for sub in src for t in sub.itertext() if get_tag(sub) != 'institution-id'
                ).strip()

        # Award IDs (typically grant numbers).
        ids = [(a.text or '').strip() for a in ag.findall('award-id') if (a.text or '').strip()]

        # Recipients.
        recipients = []
        for prr in ag.findall('principal-award-recipient'):
            for n in prr.findall('name'):
                sn = (n.findtext('surname') or '').strip()
                gn = (n.findtext('given-names') or '').strip()
                full = f'{gn} {sn}'.strip()
                if full:
                    recipients.append(full)
            if not prr.findall('name'):
                txt = ''.join(prr.itertext()).strip()
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


def render_abstract(abstract: ET.Element) -> str:
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
    lines = [f'## {heading}']
    # Walk the abstract's children in document order. Some publishers
    # (Springer/BMC) append an auto-generated <sec
    # title="Electronic supplementary material"> footer with template
    # boilerplate that doesn't exist in the published PDF. The DOI link
    # it carries is already covered by the article-meta DOI line, so
    # skip the whole sub-sec.
    skip_sec_titles = {
        'electronic supplementary material',
        'supplementary material',
    }
    for child in abstract:
        tag = get_tag(child)
        if tag == 'title':
            continue
        if tag == 'p':
            lines.append(inline_to_md(child))
        elif tag == 'sec':
            title = child.find('title')
            title_text = inline_to_md(title).strip().lower() if title is not None else ''
            if title_text in skip_sec_titles:
                continue
            sec_id = child.get('id', '')
            anchor = f'<a id="{sec_id}"></a>\n' if sec_id else ''
            if title is not None:
                lines.append(f'{anchor}**{inline_to_md(title)}**')
            elif sec_id:
                lines.append(anchor.rstrip())
            for p in child.findall('p'):
                lines.append(inline_to_md(p))
    return '\n\n'.join(lines)


# ---------------------------------------------------------------------------
# Body
# ---------------------------------------------------------------------------


def render_body(body: ET.Element) -> str:
    parts = []
    for child in body:
        tag = get_tag(child)
        if tag == 'sec':
            parts.append(render_sec(child, level=2))
        elif tag == 'p':
            parts.append(inline_to_md(child))
    return '\n\n'.join(parts)


def render_sec(sec: ET.Element, level: int = 2) -> str:
    parts = []
    hashes = '#' * level

    title = sec.find('title')
    sec_id = sec.get('id', '')
    if title is not None:
        anchor = f'<a id="{sec_id}"></a>\n' if sec_id else ''
        parts.append(f'{anchor}{hashes} {inline_to_md(title)}')
    elif sec_id:
        # Untitled section with an id (e.g. a wrapper around supplementary
        # materials). Emit just the anchor so cross-references resolve.
        parts.append(f'<a id="{sec_id}"></a>')

    for child in sec:
        tag = get_tag(child)
        if tag == 'title':
            continue
        if tag == 'sec':
            parts.append(render_sec(child, level + 1))
        elif tag == 'p':
            parts.extend(render_p(child))
        elif tag == 'fig':
            parts.append(render_fig(child))
        elif tag == 'table-wrap':
            parts.append(render_table_wrap(child))
        elif tag == 'list':
            parts.append(render_list(child))
        elif tag == 'def-list':
            parts.append(render_def_list(child))
        elif tag == 'disp-formula':
            parts.append(render_formula(child))
        elif tag == 'supplementary-material':
            parts.append(render_supplementary(child))
        elif tag == 'boxed-text':
            parts.append(render_boxed_text(child))
        elif tag == 'disp-quote':
            parts.append(render_disp_quote(child))
        elif tag in ('code', 'preformat'):
            parts.append(render_code_block(child))
        elif tag == 'statement':
            parts.append(render_statement(child))

    return '\n\n'.join(parts)


def render_boxed_text(box: ET.Element) -> str:
    """Render <boxed-text> as a fenced quote block (sidebar / callout)."""
    box_id = box.get('id', '')
    title_el = box.find('label')
    if title_el is None:
        title_el = box.find('caption/title')
    title = inline_to_md(title_el).strip() if title_el is not None else ''
    body_parts: list[str] = []
    for child in box:
        tag = get_tag(child)
        if tag in ('label', 'caption'):
            continue
        if tag == 'p':
            body_parts.extend(render_p(child))
        elif tag == 'sec':
            body_parts.append(render_sec(child, level=3))
        elif tag == 'list':
            body_parts.append(render_list(child))
    body = '\n\n'.join(body_parts)
    # Indent each line with "> " so the box renders as a markdown
    # blockquote — the closest native equivalent to a sidebar callout.
    quoted = '\n'.join('> ' + line for line in body.splitlines())
    head = f'<a id="{box_id}"></a>\n' if box_id else ''
    if title:
        return head + f'> **{title}**\n>\n{quoted}'.rstrip()
    return head + quoted


def render_disp_quote(q: ET.Element) -> str:
    """Render <disp-quote> as a markdown blockquote."""
    body_parts: list[str] = []
    for child in q:
        tag = get_tag(child)
        if tag == 'p':
            body_parts.extend(render_p(child))
        elif tag == 'attrib':
            body_parts.append(f'— {inline_to_md(child).strip()}')
    body = '\n\n'.join(body_parts)
    return '\n'.join('> ' + line for line in body.splitlines())


def render_code_block(el: ET.Element) -> str:
    """Render <code> or <preformat> as a fenced code block."""
    text = ''.join(el.itertext())
    # JATS allows a language attribute on <code>; emit it as the fence info.
    lang = el.get('language', '') or el.get('code-type', '')
    return f'```{lang}\n{text.rstrip()}\n```'


def render_statement(s: ET.Element) -> str:
    """Render <statement> (theorem, axiom, definition...) as a labelled block."""
    label = (s.findtext('label') or '').strip()
    title_el = s.find('title')
    title = inline_to_md(title_el).strip() if title_el is not None else ''
    body_parts: list[str] = []
    for child in s:
        tag = get_tag(child)
        if tag in ('label', 'title'):
            continue
        if tag == 'p':
            body_parts.extend(render_p(child))
    body = '\n\n'.join(body_parts)
    head = ' '.join(b for b in (label, title) if b)
    return f'**{head}** {body}'.strip() if head else body


def render_def_list(dl: ET.Element) -> str:
    """Render <def-list> outside <glossary> as a markdown bullet list."""
    lines = []
    for di in dl.findall('def-item'):
        term = (di.findtext('term') or '').strip()
        defn_el = di.find('def')
        defn = ''
        if defn_el is not None:
            defn = (
                ' '.join(inline_to_md(p).strip() for p in defn_el.findall('p')).strip() or inline_to_md(defn_el).strip()
            )
        if term or defn:
            lines.append(f'- **{term}** — {defn}' if defn else f'- **{term}**')
    return '\n'.join(lines)


def render_supplementary(sm: ET.Element) -> str:
    """Render a <supplementary-material> entry: anchor + label + caption + link."""
    sm_id = sm.get('id', '')
    label = (sm.findtext('label') or '').strip()

    caption_md = _caption_text(sm.find('caption'))

    # The asset can sit in either <media> (xlink:href to a file) or a
    # nested <graphic>/<inline-graphic>. <media> is the JATS norm for
    # supplementary data.
    media = sm.find('media')
    if media is None:
        media = sm.find('.//graphic')
    href = xlink_href(media) if media is not None else ''

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


_BLOCK_IN_P = {
    'fig',
    'table-wrap',
    'disp-formula',
    'list',
    'boxed-text',
    'disp-quote',
    'code',
    'preformat',
    'statement',
    'def-list',
    'supplementary-material',
}


def render_p(p: ET.Element) -> list[str]:
    """Render a <p>, lifting block-level children to standalone fragments.

    JATS Archiving's <p> content model permits <fig>, <table-wrap>,
    <list>, <disp-formula>, <boxed-text>, <code>, etc. as direct
    children — common when a publisher wants the float to anchor at
    its first textual reference. Returning a list of fragments lets
    render_sec join them at paragraph granularity instead of inlining
    the float's caption text into the surrounding paragraph.
    """
    block_children = [c for c in p if get_tag(c) in _BLOCK_IN_P]
    if not block_children:
        text = inline_to_md(p)
        return [text] if text.strip() else []

    fragments: list[str] = []
    inline_kids: list = []
    inline_lead = p.text or ''

    def flush_inline():
        if not inline_kids and not inline_lead.strip():
            return
        synth = ET.Element('p')
        synth.text = inline_lead
        for c in inline_kids:
            synth.append(c)
        text = inline_to_md(synth)
        if text.strip():
            fragments.append(text)

    for child in p:
        if get_tag(child) in _BLOCK_IN_P:
            flush_inline()
            inline_kids = []
            tag = get_tag(child)
            if tag == 'fig':
                fragments.append(render_fig(child))
            elif tag == 'table-wrap':
                fragments.append(render_table_wrap(child))
            elif tag == 'disp-formula':
                fragments.append(render_formula(child))
            elif tag == 'list':
                fragments.append(render_list(child))
            elif tag == 'def-list':
                fragments.append(render_def_list(child))
            elif tag == 'boxed-text':
                fragments.append(render_boxed_text(child))
            elif tag == 'disp-quote':
                fragments.append(render_disp_quote(child))
            elif tag in ('code', 'preformat'):
                fragments.append(render_code_block(child))
            elif tag == 'statement':
                fragments.append(render_statement(child))
            elif tag == 'supplementary-material':
                fragments.append(render_supplementary(child))
            inline_lead = child.tail or ''
        else:
            inline_kids.append(child)
    flush_inline()
    return fragments


def render_fig(fig: ET.Element) -> str:
    fig_id = fig.get('id', '')
    label = fig.findtext('label') or ''

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
            href = xlink_href(g)
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


def render_table_wrap(tw: ET.Element) -> str:
    tw_id = tw.get('id', '')
    label = tw.findtext('label') or ''

    caption_md = _caption_text(tw.find('caption'))
    doi_link = _object_id_doi_link(tw)
    if doi_link:
        caption_md = (caption_md + ' ' + doi_link).strip()

    table = tw.find('.//table')
    table_md = render_table(table) if table is not None else ''

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
            href = xlink_href(graphic)
            if href:
                alt = f'{label}: {caption_md}'[:120] if caption_md else label
                image_md = f'![{alt}]({href})'

    foot = tw.find('table-wrap-foot')
    foot_md = ''
    if foot is not None:
        foot_parts: list[str] = []
        # Direct <p> children — older JATS / simple footnotes.
        foot_parts.extend(inline_to_md(p).strip() for p in foot.findall('p'))
        # <fn> children wrapping paragraphs — PLOS Comp Biol style.
        for fn in foot.findall('fn'):
            fn_label = (fn.findtext('label') or '').strip()
            for p in fn.findall('p'):
                t = inline_to_md(p).strip()
                if t:
                    foot_parts.append(f'<sup>{fn_label}</sup> {t}' if fn_label else t)
        foot_md = ' '.join(p for p in foot_parts if p)

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


def render_table(table: ET.Element) -> str:
    """Render an XHTML-model JATS <table> (thead/tbody/tr/td/th)."""

    def get_cells_raw(tr: ET.Element) -> list[tuple[str, int, int]]:
        """Return list of (content, colspan, rowspan) for each cell in a row."""
        cells = []
        for cell in tr.findall('td') + tr.findall('th'):
            content = md_escape_cell(inline_to_md(cell))
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

    return render_grid(header_rows_raw, body_rows_raw)


def render_list(lst: ET.Element) -> str:
    list_type = lst.get('list-type', 'bullet')
    items = []
    for i, item in enumerate(lst.findall('list-item'), 1):
        text = ' '.join(inline_to_md(p) for p in item.findall('p'))
        prefix = f'{i}.' if list_type == 'order' else '-'
        items.append(f'{prefix} {text}')
    return '\n'.join(items)


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
    math = formula.find(f'.//{{{MML_NS}}}math')
    if math is not None:
        return render_mathml(math, display=display)
    g = formula.find('.//graphic')
    if g is None:
        g = formula.find('.//inline-graphic')
    href = xlink_href(g) if g is not None else ''
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
    label = (formula.findtext('label') or '').strip()
    label_suffix = f'  {label}' if label else ''
    body = _formula_body(formula, display=True)
    if body:
        return f'{anchor}{body}{label_suffix}'
    # Last resort: any text directly inside the disp-formula. Avoid
    # itertext() so we don't pick up tex-math preamble or MathML
    # element names from earlier-attempted siblings.
    text = (formula.text or '').strip()
    return f'{anchor}$${text}$${label_suffix}' if text else ''


# ---------------------------------------------------------------------------
# Back matter
# ---------------------------------------------------------------------------


def render_floats_group(floats: ET.Element) -> str:
    """Render a <floats-group> (figs/tables placed by publisher at article end)."""
    parts = []
    for child in floats:
        tag = get_tag(child)
        if tag == 'fig':
            parts.append(render_fig(child))
        elif tag == 'table-wrap':
            parts.append(render_table_wrap(child))
        elif tag == 'fig-group':
            for sub in child.findall('fig'):
                parts.append(render_fig(sub))
        elif tag == 'disp-formula':
            parts.append(render_formula(child))
    return '\n\n'.join(parts)


def render_back(back: ET.Element) -> str:
    parts = []

    # JATS allows <back> to mix several block-level child types in any order:
    # <ack>, <app-group>/<app> (appendices — Nature places extended-data figs
    # here), bare <sec>s (extended methods), <ref-list>, <notes>, <fn-group>,
    # <bio>, <glossary>. Walk children once so order is preserved.
    for child in back:
        tag = get_tag(child)
        if tag == 'ack':
            # JATS convention: <ack> implies "Acknowledgments" even
            # without a <title>. Render the heading explicitly so it
            # doesn't disappear into the surrounding paragraph stream.
            sub_secs = child.findall('sec')
            if sub_secs:
                # If the inner <sec>s carry their own titles, let them
                # provide the heading. If not, prepend a default first.
                if not any(s.find('title') is not None for s in sub_secs):
                    parts.append('## Acknowledgments')
                for sec in sub_secs:
                    parts.append(render_sec(sec, level=2))
            else:
                if child.find('title') is None:
                    parts.append('## Acknowledgments')
                parts.append(render_sec(child, level=2))
        elif tag == 'app-group':
            for app in child.findall('app'):
                parts.append(render_sec(app, level=2))
        elif tag == 'app' or tag == 'sec':
            parts.append(render_sec(child, level=2))
        elif tag == 'ref-list':
            parts.append(render_ref_list(child))
        elif tag == 'notes':
            # <notes> typically holds Author contributions, Competing
            # interests, Data/Code availability, etc. Has a <title>
            # and one or more <p>/<sec> children — render as a section.
            parts.append(render_sec(child, level=2))
        elif tag == 'fn-group':
            parts.append(render_fn_group(child))
        elif tag == 'glossary':
            parts.append(render_glossary(child))

    return '\n\n'.join(parts)


def render_glossary(gloss: ET.Element) -> str:
    """Render <glossary> as a heading + def-list."""
    title_el = gloss.find('title')
    heading = inline_to_md(title_el).strip() if title_el is not None else 'Glossary'
    lines = [f'## {heading}']
    for dl in gloss.findall('def-list'):
        for di in dl.findall('def-item'):
            term = di.findtext('term') or ''
            defn_el = di.find('def')
            defn = ''
            if defn_el is not None:
                defn = (
                    ' '.join(inline_to_md(p).strip() for p in defn_el.findall('p')).strip()
                    or inline_to_md(defn_el).strip()
                )
            if term or defn:
                lines.append(f'- **{term}** — {defn}' if defn else f'- **{term}**')
    if len(lines) == 1:
        return ''
    return '\n'.join(lines)


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


def render_fn_group(fn_group: ET.Element) -> str:
    """Render <fn-group> with each <fn>'s typed entry as its own H2.

    Footnotes carrying an fn-type whose label is well-known (Author
    contributions, Competing interests, Funding, ...) become standalone
    "## Heading" sections — one per fn — instead of being lumped under a
    single generic "Notes" heading.

    A fn-group's own <title> (if present) overrides any per-fn heading.
    Footnotes with no fn-type fall back to a shared "## Notes" section.
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
        label = fn.findtext('label') or ''
        body = ' '.join(inline_to_md(p).strip() for p in fn.findall('p')).strip()
        if not body:
            body = inline_to_md(fn).strip()
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
            after = body[close + 2 :].lstrip(' .')
            if inline_heading and after:
                heading = inline_heading
                blocks.append(f'## {heading}\n\n{after}')
                continue

        if explicit_title:
            untyped_lines.append(body)
        elif fn_type and fn_type in _FN_TYPE_LABELS:
            heading = _FN_TYPE_LABELS[fn_type]
            blocks.append(f'## {heading}\n\n{body}')
        elif label:
            untyped_lines.append(f'<sup>{label}</sup> {body}')
        else:
            untyped_lines.append(body)

    if untyped_lines:
        heading = explicit_title or 'Notes'
        blocks.append(f'## {heading}\n\n' + '\n\n'.join(untyped_lines))

    return '\n\n'.join(blocks)


def _render_mixed_citation(ec: ET.Element) -> str:
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
    import copy

    pruned = copy.deepcopy(ec)

    # Strip <pub-id> descendants (rendered separately at the end).
    for parent in list(pruned.iter()):
        for child in list(parent):
            if get_tag(child) == 'pub-id':
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
            if get_tag(child) != 'person-group':
                continue
            authors = []
            for sub in child:
                tag = get_tag(sub)
                if tag == 'name':
                    sn = (sub.findtext('surname') or '').strip()
                    gn = (sub.findtext('given-names') or '').strip()
                    authors.append(f'{sn} {gn}'.strip())
                elif tag == 'string-name' or tag == 'collab':
                    authors.append(''.join(sub.itertext()).strip())
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
        val = (pid.text or '').strip()
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


def render_ref_list(ref_list: ET.Element) -> str:
    title = ref_list.findtext('title') or 'References'
    lines = [f'## {title}', '']

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

        # Reference number label
        label_text = ref.findtext('label') or ref_id.lstrip('B')
        # The label often already ends with "." (e.g. "11."); avoid the
        # double-period when we add our own separator.
        label_sep = '' if label_text.rstrip().endswith('.') else '.'

        # Mixed-citation is a free-form text container — render verbatim
        # via inline_to_md. Trying to extract structured fields from it
        # tends to lose the citation prose, since most of the content is
        # bare text rather than child elements.
        if is_mixed:
            body = _render_mixed_citation(ec)
            lines.append(f'<a id="{ref_id}"></a>')
            lines.append(f'{label_text}{label_sep} {body}'.rstrip())
            lines.append('')
            continue

        # Authors
        pg = ec.find('person-group')
        authors = []
        if pg is not None:
            for name in pg.findall('name'):
                sn = name.findtext('surname') or ''
                gn = name.findtext('given-names') or ''
                authors.append(f'{sn} {gn}'.strip())
            for sname in pg.findall('string-name'):
                # Nature uses <string-name> instead of <name> for free-form
                # author strings.
                authors.append(''.join(sname.itertext()).strip())
            for collab in pg.findall('collab'):
                authors.append(''.join(collab.itertext()))
            if pg.find('etal') is not None:
                authors.append('et al.')
        authors_str = ', '.join(a for a in authors if a)

        # Title
        title_elem = ec.find('article-title')
        art_title = inline_to_md(title_elem) if title_elem is not None else ''

        source = ec.findtext('source') or ''  # journal / book

        # Numeric fields
        year = ec.findtext('year') or ''
        volume = ec.findtext('volume') or ''
        issue = ec.findtext('issue') or ''
        fpage = ec.findtext('fpage') or ''
        lpage = ec.findtext('lpage') or ''
        pages = f'{fpage}–{lpage}' if fpage and lpage else fpage
        publisher_loc = ec.findtext('publisher-loc') or ''
        publisher_name = ec.findtext('publisher-name') or ''

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
            val = pid.text or ''
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
        lines.append(f'{label_text}{label_sep} {body}'.rstrip())
        lines.append('')

    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

_ADJACENT_SUP_RE = re.compile(r'</sup>[ \t]*<sup>')


def render(root: ET.Element) -> str:
    """Render a parsed JATS ``<article>`` root to Markdown.

    The dispatcher in :func:`litdown.convert` parses and sniffs the root,
    then calls this; it does not re-parse.
    """
    sections = []

    front = root.find('front')
    if front is not None:
        sections.append(render_front(front))

    body = root.find('body')
    if body is not None:
        sections.append(render_body(body))

    back = root.find('back')
    if back is not None:
        sections.append(render_back(back))

    # <floats-group> is a JATS Archiving element (not in Article
    # Authoring) for figs/tables placed by the publisher at article end
    # rather than inline. Render its contents so they aren't lost.
    floats = root.find('floats-group')
    if floats is not None:
        sections.append(render_floats_group(floats))

    md = '\n\n---\n\n'.join(sections)
    # SPEC DEVIATION (post-process): some publisher source has split
    # numeric exponents across two adjacent <sup> tags
    # (e.g. 10<sup>-</sup><sup>4</sup>). The spec doesn't endorse this
    # — it would render as "-4" in any consumer anyway, so collapse the
    # pair. Use [ \t]* (not \s*) so paragraph breaks aren't bridged.
    return _ADJACENT_SUP_RE.sub('', md)
