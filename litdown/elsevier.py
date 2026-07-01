"""Convert an Elsevier (ScienceDirect) full-text XML response to Markdown.

The Article Retrieval API returns full text wrapped in an SVAPI envelope
(``<full-text-retrieval-response>``) whose body is Elsevier's own
``ce:``/``ja:``/``xocs:`` schema — *not* JATS. This module renders that
dialect with content-parity to the JATS output shape
(:mod:`litdown.jats`); the dispatcher in :func:`litdown.convert` picks
between the two on the root element's local name.

Implementation notes:

* **Match on local tag names throughout.** Elsevier mixes ``ja:``/``ce:``/
  ``xocs:``/``sb:`` prefixes freely; structure matters, not prefix.
* **Math is standard W3C MathML** — rendered via
  :func:`litdown.mathml.render_mathml`, the same path the JATS dialect
  uses. In real articles ``<math>`` may sit inside ``ce:formula`` /
  ``ce:inline-formula`` or appear bare inline; all three are handled.
* **Tables are CALS** (``tgroup``/``colspec``/``row``/``entry``), not the
  XHTML model JATS uses, so :func:`_Renderer._render_cals_table` translates
  CALS spanning into normalized rows and hands them to the shared grid
  builder in :mod:`litdown.common`.
* **References** parse the structured ``sb:`` model (Siemens) as primary,
  falling back to the publisher-rendered ``ce:source-text`` only when the
  structured parse comes up empty.
* **Floats** (figures/tables) live in ``ja:floats``; body prose carries
  empty ``<ce:float-anchor refid="…"/>`` markers. Each float is rendered
  at the first anchor that references it, keeping it next to its
  discussion.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import ClassVar

from litdown import common, mathml

# Elsevier <inf> is JATS <sub>; map onto the shared canonical name.
_INLINE_CANONICAL = {
    'italic': 'italic',
    'bold': 'bold',
    'sup': 'sup',
    'inf': 'sub',
    'underline': 'underline',
    'monospace': 'monospace',
    'strike': 'strike',
    'cross-out': 'strike',
}

_YEAR_RE = re.compile(r'\b(\d{4})\b')

# Bibliography entry element types (a free-text <other-ref> or a structured
# <bib-reference>). Used to identify top-level reference entries.
_ENTRY_TAGS = ('bib-reference', 'other-ref')


def _find(elem: ET.Element, name: str) -> ET.Element | None:
    """First descendant (or self) with the given local tag name."""
    if common.get_tag(elem) == name:
        return elem
    for x in elem.iter():
        if common.get_tag(x) == name:
            return x
    return None


def _child(elem: ET.Element | None, name: str) -> ET.Element | None:
    """First *direct child* with the given local tag name."""
    if elem is None:
        return None
    for c in elem:
        if common.get_tag(c) == name:
            return c
    return None


def _children(elem: ET.Element, name: str) -> list[ET.Element]:
    """All direct children with the given local tag name."""
    return [c for c in elem if common.get_tag(c) == name]


def _text(elem: ET.Element | None) -> str:
    return ''.join(elem.itertext()).strip() if elem is not None else ''


def _coalesce(*els: ET.Element | None) -> ET.Element | None:
    """Return the first non-None element.

    ``a or b`` is unsafe here: an Element with no children is falsy, which
    ElementTree deprecates for truth tests.
    """
    for e in els:
        if e is not None:
            return e
    return None


def _norm(s: str) -> str:
    """Collapse whitespace runs to single spaces and strip.

    Elsevier XML is pretty-printed, so an element that opens with a child
    (e.g. a <para> leading with a <float-anchor>) carries the indentation
    newline as its ``.text``. Prose is single-line in Markdown, so
    flattening interior whitespace is both safe and necessary.
    """
    return re.sub(r'\s+', ' ', s).strip()


def _is_compact_view(el: ET.Element) -> bool:
    """True for the abbreviated alternate of a dual-view element.

    Elsevier emits some back-matter sections twice — an ``extended`` view
    and a ``compact-standard`` view of the same content. Rendering both
    duplicates the section, so the compact variant is dropped.
    """
    return (el.get('view') or '').startswith('compact')


def _anchor_only(el: ET.Element) -> str:
    """A bare ``<a id>`` for an element rendered nowhere else.

    Used for a dropped compact-view section so a cross-ref pointing at its
    id still resolves to an anchor.
    """
    eid = el.get('id', '')
    return f'<a id="{eid}"></a>' if eid else ''


class _Renderer:
    """Stateful Elsevier→Markdown renderer.

    Holds the float registry so floats can be emitted at their first
    in-text anchor (see :meth:`_para`), and a per-paragraph pending list
    that :meth:`inline` populates when it crosses a ``float-anchor``.
    """

    def __init__(self, article: ET.Element, coredata: ET.Element | None) -> None:
        self.article = article
        self.coredata = coredata
        # Float registry: id → element, in document order.
        floats = _child(article, 'floats')
        self.floats: dict[str, ET.Element] = {}
        if floats is not None:
            for f in floats:
                if common.get_tag(f) in ('figure', 'table', 'textbox') and f.get('id'):
                    self.floats[f.get('id') or ''] = f
        self.rendered_floats: set[str] = set()
        # Float ids referenced by the paragraph currently being rendered.
        self._pending: list[str] = []
        # Body footnotes encountered inline, collected into a trailing Notes
        # section: list of (id, label, body); ids deduped via the set.
        self.footnotes: list[tuple[str, str, str]] = []
        self._footnote_ids: set[str] = set()

    # -- inline -----------------------------------------------------------

    def inline(self, elem: ET.Element | None) -> str:
        """Render an element's mixed content as inline Markdown."""
        if elem is None:
            return ''
        buf: list[str] = []
        if elem.text:
            buf.append(elem.text)

        for child in elem:
            tag = common.get_tag(child)

            if tag == 'math':
                # Bare inline MathML (Elsevier embeds <math> directly in
                # running prose for short expressions).
                buf.append(self._math_inline(child))
                if child.tail:
                    buf.append(child.tail)
                continue

            canonical = _INLINE_CANONICAL.get(tag)
            wrapped = common.inline_wrap(canonical, self.inline(child)) if canonical else None
            if wrapped is not None:
                buf.append(wrapped)
            elif tag in ('cross-ref', 'cross-refs'):
                # Every target (bib/float/section/equation/footnote) gets an
                # anchor, so a uniform link works with no ref-type lookup.
                # <cross-refs> (plural) is a grouped citation whose refid lists
                # several ids; link the rendered text to the first.
                raw_refid = child.get('refid', '')
                refid = raw_refid.split()[0] if raw_refid else ''
                inner = _norm(self.inline(child))
                buf.append(f'[{inner}](#{refid})' if refid else inner)
            elif tag == 'inter-ref':
                href = common.xlink_href(child)
                inner = self.inline(child)
                if href.startswith(('http://', 'https://', 'ftp://')):
                    buf.append(f'[{inner or href}]({href})')
                else:
                    buf.append(inner or href)
            elif tag == 'float-anchor':
                buf.append(self._float_anchor_inline(child))
            elif tag == 'footnote':
                buf.append(self._footnote_inline(child))
            elif tag in ('inline-formula', 'formula'):
                buf.append(self._math_inline(child))
            else:
                buf.append(self.inline(child))

            if child.tail:
                buf.append(child.tail)

        return ''.join(buf)

    def _float_anchor_inline(self, anchor: ET.Element) -> str:
        """Queue a float for placement; emit nothing inline.

        ``float-anchor`` is a placement-only marker — it sits next to a
        ``cross-ref`` to the same float, which supplies the visible link.
        Emitting a second link here would duplicate it. The float block
        itself is rendered by :meth:`_para` after the paragraph.
        """
        refid = anchor.get('refid', '')
        if refid and refid not in self.rendered_floats and refid not in self._pending:
            self._pending.append(refid)
        return ''

    def _footnote_inline(self, fn: ET.Element) -> str:
        """Emit a footnote marker inline and collect the note.

        The note goes to a trailing Notes section, which carries the anchor the
        cross-ref resolves to.
        """
        fid = fn.get('id', '')
        label = _text(_child(fn, 'label'))
        if fid and fid not in self._footnote_ids:
            self._footnote_ids.add(fid)
            body_parts = [
                _norm(self.inline(p)) for p in fn if common.get_tag(p) in ('note-para', 'para', 'simple-para')
            ]
            body = ' '.join(b for b in body_parts if b)
            self.footnotes.append((fid, label, body))
        return f'<sup>{label}</sup>' if label else ''

    def _render_footnotes(self) -> str:
        """Render collected body footnotes as a trailing ## Notes section."""
        if not self.footnotes:
            return ''
        lines = ['## Notes', '']
        for fid, label, body in self.footnotes:
            anchor = f'<a id="{fid}"></a>' if fid else ''
            marker = f'<sup>{label}</sup> ' if label else ''
            lines.append(f'{anchor}{marker}{body}'.strip())
            lines.append('')
        return '\n'.join(lines)

    def _math_inline(self, elem: ET.Element) -> str:
        math = elem if common.get_tag(elem) == 'math' else _find(elem, 'math')
        if math is not None:
            out = mathml.render_mathml(math, display=False)
            if out:
                return out
        # No MathML present — fall back to the altimg graphic as an image.
        href = elem.get('altimg', '') or (math.get('altimg', '') if math is not None else '')
        if href:
            return f'![eq]({href})'
        # A non-MathML (inline-)formula, e.g. a <chem> expression: render its
        # inline content rather than dropping it.
        if common.get_tag(elem) in ('formula', 'inline-formula'):
            return ''.join(self.inline(c) for c in elem if common.get_tag(c) != 'label')
        return ''

    # -- sections ---------------------------------------------------------

    # Block-level elements that may appear as direct children of a <para>;
    # they're lifted to standalone fragments rather than inlined (mirrors the
    # JATS render_p block-lifting). <display> wraps inline equations / floats;
    # bare <figure>/<table> are "unnumbered" floats placed in running prose.
    _BLOCK_IN_PARA = frozenset(
        {
            'formula',
            'display',
            'list',
            'def-list',
            'figure',
            'table',
            'textbox',
            'enunciation',
            'e-component',
            'displayed-quote',
        }
    )

    # Default H2 headings for titled back-matter blocks that may ship without
    # their own <section-title>.
    _DEFAULT_HEADINGS: ClassVar[dict[str, str]] = {
        'nomenclature': 'Nomenclature',
        'glossary': 'Glossary',
        'acknowledgment': 'Acknowledgements',
        'conflict-of-interest': 'Declaration of competing interest',
        'data-availability': 'Data availability',
    }

    def _para(self, p: ET.Element) -> list[str]:
        """Render a <para>/<simple-para>, lifting block-level children out.

        Returns the inline runs as paragraph fragments, the lifted blocks
        (equations/tables/figures/lists) in document order, and the full
        block for any float first referenced here via a <float-anchor>
        (placed after the paragraph, next to its discussion).
        """
        self._pending = []
        block_kids = [c for c in p if common.get_tag(c) in self._BLOCK_IN_PARA]
        fragments: list[str] = []

        if not block_kids:
            text = _norm(self.inline(p))
            if text:
                fragments.append(text)
        else:
            inline_kids: list[ET.Element] = []
            lead = [p.text or '']

            def flush() -> None:
                if not inline_kids and not lead[0].strip():
                    return
                synth = ET.Element('para')
                synth.text = lead[0]
                synth.extend(inline_kids)
                text = _norm(self.inline(synth))
                if text:
                    fragments.append(text)

            for child in p:
                if common.get_tag(child) in self._BLOCK_IN_PARA:
                    flush()
                    inline_kids = []
                    block_md = self._render_block(child)
                    if block_md:
                        fragments.append(block_md)
                    lead[0] = child.tail or ''
                else:
                    inline_kids.append(child)
            flush()

        # Floats referenced by a <float-anchor> in this paragraph, placed
        # immediately after it (deduped against anything already rendered).
        for refid in self._pending:
            if refid in self.rendered_floats:
                continue
            flt = self.floats.get(refid)
            if flt is not None:
                fragments.append(self._render_float(flt))
                self.rendered_floats.add(refid)
        self._pending = []
        return [f for f in fragments if f]

    def _render_block(self, child: ET.Element) -> str:
        """Render a block-level element (equation/float/list/display/theorem)."""
        tag = common.get_tag(child)
        if tag == 'formula':
            return self._render_block_formula(child)
        if tag == 'list':
            return self._list(child)
        if tag == 'def-list':
            return self._render_deflist(child)
        if tag == 'display':
            return self._render_display(child)
        if tag in ('figure', 'table', 'textbox'):
            return self._render_inline_float(child)
        if tag == 'enunciation':
            return self._render_enunciation(child)
        if tag == 'e-component':
            return self._render_ecomponent(child)
        if tag == 'displayed-quote':
            return self._render_quote(child)
        return ''

    def _render_quote(self, quote: ET.Element) -> str:
        """Render <displayed-quote> (a block quote / callout) as a blockquote."""
        body_parts: list[str] = []
        for c in quote:
            ct = common.get_tag(c)
            if ct in ('para', 'simple-para'):
                body_parts.extend(self._para(c))
            elif ct == 'attribution':
                body_parts.append(f'— {_norm(self.inline(c))}')
            elif ct in self._BLOCK_IN_PARA:
                body_parts.append(self._render_block(c))
        body = '\n\n'.join(p for p in body_parts if p)
        return '\n'.join('> ' + line for line in body.splitlines())

    def _render_display(self, display: ET.Element) -> str:
        """Render a <display> wrapper's block children in place.

        <display> groups inline-placed equations, floats and lists.
        <e-component>/<link> point at publisher-internal multimedia
        locators (no resolvable URL), so they're dropped.
        """
        out = []
        for c in display:
            tag = common.get_tag(c)
            if tag in ('formula', 'list', 'figure', 'table', 'textbox', 'enunciation'):
                out.append(self._render_block(c))
            elif tag in ('para', 'simple-para'):
                out.extend(self._para(c))
            elif tag == 'e-component':
                out.append(self._render_ecomponent(c))
        return '\n\n'.join(o for o in out if o)

    def _render_ecomponent(self, ec: ET.Element) -> str:
        """Anchor + label a supplementary <e-component> (multimedia).

        The asset is referenced by an internal ``pii:`` locator, not a
        resolvable URL, so only the anchor + label are emitted — enough for
        a cross-ref to it to resolve.
        """
        eid = ec.get('id', '')
        label = _text(_child(ec, 'label')) or 'Supplementary material'
        anchor = f'<a id="{eid}"></a>' if eid else ''
        return f'{anchor}**{label}**'

    def _render_inline_float(self, flt: ET.Element) -> str:
        """Render a float placed inline in body prose (not in <floats>).

        Registers it so cross-refs/float-anchors resolve and the trailing
        orphan pass doesn't render it a second time.
        """
        fid = flt.get('id', '')
        if fid:
            self.floats.setdefault(fid, flt)
            if fid in self.rendered_floats:
                return ''
            self.rendered_floats.add(fid)
        return self._render_float(flt)

    def _render_enunciation(self, enun: ET.Element) -> str:
        """Render <enunciation> (theorem/lemma/definition) as a labelled block.

        Its <para> children go through :meth:`_para` so any block content
        they wrap (a display equation or table) is lifted, not inlined.
        """
        eid = enun.get('id', '')
        anchor = f'<a id="{eid}"></a>\n' if eid else ''
        label = _text(_child(enun, 'label'))
        body_parts: list[str] = []
        for p in enun:
            if common.get_tag(p) in ('para', 'simple-para'):
                body_parts.extend(self._para(p))
        body = '\n\n'.join(b for b in body_parts if b) or _norm(self.inline(enun))
        head = f'**{label}**' if label else ''
        if head and body:
            return f'{anchor}{head} {body}'
        return f'{anchor}{head}{body}'.strip()

    def _render_deflist(self, dl: ET.Element) -> str:
        """Render <def-list> (e.g. a nomenclature/symbol glossary) as bullets.

        ``<def-term>`` and ``<def-description>`` are siblings, so walk in
        document order to pair each term with the description that follows.
        """
        pairs: list[str] = []
        pending_term = ''
        for child in dl:
            ct = common.get_tag(child)
            if ct == 'def-term':
                if pending_term:
                    pairs.append(f'- **{pending_term}**')
                pending_term = _norm(self.inline(child))
            elif ct == 'def-description':
                desc = _norm(self.inline(child))
                if pending_term or desc:
                    pairs.append(f'- **{pending_term}** — {desc}' if pending_term else f'- {desc}')
                pending_term = ''
        if pending_term:
            pairs.append(f'- **{pending_term}**')
        return '\n'.join(pairs)

    def _list(self, lst: ET.Element) -> str:
        list_type = lst.get('type', '') or lst.get('list-type', '')
        ordered = list_type in ('simple', 'ordered', 'order') or bool(lst.get('mark-prefix'))
        items = []
        for i, item in enumerate(_children(lst, 'list-item'), 1):
            # Render the label via inline so any markup/math in it (some lists
            # use a math symbol as the bullet) is rendered, not flattened.
            label = _norm(self.inline(_child(item, 'label')))
            paras = _children(item, 'para') or _children(item, 'simple-para')
            if paras:
                # Via _para so a display equation/table inside a list item is
                # lifted (and anchored) rather than inlined away.
                frags: list[str] = []
                for p in paras:
                    frags.extend(self._para(p))
                body = ' '.join(f for f in frags if f).strip()
            else:
                # list-item may hold inline content directly.
                body = _norm(self.inline(item))
            prefix = label or (f'{i}.' if ordered else '-')
            items.append(f'{prefix} {body}'.rstrip())
        return '\n'.join(items)

    def _section(self, sec: ET.Element, level: int) -> str:
        parts: list[str] = []
        hashes = '#' * level
        sec_id = sec.get('id', '')
        label = _text(_child(sec, 'section-label')) or _text(_child(sec, 'label'))
        title_el = _child(sec, 'section-title')
        anchor = f'<a id="{sec_id}"></a>\n' if sec_id else ''
        if title_el is not None:
            heading = ' '.join(b for b in (label, _norm(self.inline(title_el))) if b)
            parts.append(f'{anchor}{hashes} {heading}')
        elif common.get_tag(sec) in self._DEFAULT_HEADINGS:
            # A titled back-matter block (Nomenclature, Acknowledgements, …)
            # shipped without its own <section-title>.
            parts.append(f'{anchor}{hashes} {self._DEFAULT_HEADINGS[common.get_tag(sec)]}')
        elif sec_id:
            parts.append(anchor.rstrip())

        for child in sec:
            tag = common.get_tag(child)
            if tag in ('label', 'section-label', 'section-title'):
                continue
            if tag == 'section':
                if _is_compact_view(child):
                    parts.append(_anchor_only(child))
                    continue
                parts.append(self._section(child, level + 1))
            elif tag in ('para', 'simple-para'):
                parts.extend(self._para(child))
            elif tag in self._BLOCK_IN_PARA:
                parts.append(self._render_block(child))
            elif tag == 'float-anchor':
                # A float-anchor outside a paragraph: resolve it directly.
                refid = child.get('refid', '')
                if refid and refid not in self.rendered_floats:
                    flt = self.floats.get(refid)
                    if flt is not None:
                        parts.append(self._render_float(flt))
                        self.rendered_floats.add(refid)
        return '\n\n'.join(p for p in parts if p)

    def _render_block_formula(self, formula: ET.Element) -> str:
        fid = formula.get('id', '')
        anchor = f'<a id="{fid}"></a>\n' if fid else ''
        label = _text(_child(formula, 'label'))
        label_suffix = f'  {label}' if label else ''

        # A multi-line equation wraps several child <formula>s, each with its
        # own <math> (e.g. a main equation plus an "s.t." constraint line).
        # Render every one so no line is dropped, keeping this formula's
        # anchor/label around the group.
        child_formulas = _children(formula, 'formula')
        if child_formulas:
            body = '\n'.join(b for b in (self._render_block_formula(cf) for cf in child_formulas) if b)
            if body:
                return f'{anchor}{body}{label_suffix}'
            return (f'{anchor}{label}'.rstrip() if label else anchor.rstrip()) if anchor else ''

        math = _find(formula, 'math')
        if math is not None:
            body = mathml.render_mathml(math, display=True)
            if body:
                return f'{anchor}{body}{label_suffix}'
        href = formula.get('altimg', '') or (math.get('altimg', '') if math is not None else '')
        if href:
            return f'{anchor}![eq {fid}]({href}){label_suffix}'.strip()

        # No MathML: a <chem> reaction equation or plain-text formula. Render
        # its inline content (minus the label) so the equation isn't lost.
        inner = _norm(''.join(self.inline(c) for c in formula if common.get_tag(c) != 'label'))
        if inner:
            return f'{anchor}{inner}{label_suffix}'
        # Nothing renderable, but still emit the anchor (+label) so a cross-ref
        # pointing at this equation resolves rather than dangling.
        if anchor:
            return f'{anchor}{label}'.rstrip() if label else anchor.rstrip()
        return ''

    # -- floats -----------------------------------------------------------

    def _caption(self, flt: ET.Element) -> str:
        cap = _child(flt, 'caption')
        if cap is None:
            return ''
        parts = []
        for c in cap:
            if common.get_tag(c) in ('simple-para', 'para'):
                t = _norm(self.inline(c))
                if t:
                    parts.append(t)
        if not parts:
            return _norm(self.inline(cap))
        return ' '.join(parts)

    def _render_float(self, flt: ET.Element) -> str:  # noqa: C901
        tag = common.get_tag(flt)
        fid = flt.get('id', '')
        label = _text(_child(flt, 'label'))
        caption = self._caption(flt)
        lines = []
        if fid:
            lines.append(f'<a id="{fid}"></a>')
        # Caption line — but skip an empty "****" when a float (e.g. a
        # graphical-abstract figure) has neither label nor caption.
        head = f'**{label}**' if label else ''
        head = f'{head} {caption}'.strip() if caption else head
        if head:
            lines.append(head)
        if tag == 'table':
            # A CALS table may hold several <tgroup>s (a multi-part table);
            # render each as its own markdown grid.
            for tgroup in _children(flt, 'tgroup'):
                table_md = self._render_cals_table(tgroup)
                if table_md:
                    lines.append('')
                    lines.append(table_md)
            foot = self._table_footnotes(flt)
            if foot:
                lines.append('')
                lines.append(foot)
        elif tag == 'figure':
            # Multi-panel figures nest sub-<figure>s (fig4a/fig4b/…), each
            # cross-referenced; render each so its anchor + caption exist.
            for sub in _children(flt, 'figure'):
                sub_id = sub.get('id', '')
                sub_label = _text(_child(sub, 'label'))
                sub_cap = self._caption(sub)
                sub_anchor = f'<a id="{sub_id}"></a>' if sub_id else ''
                sub_head = f'**{sub_label}**' if sub_label else ''
                sub_head = f'{sub_head} {sub_cap}'.strip() if sub_cap else sub_head
                if sub_id:
                    self.rendered_floats.add(sub_id)
                if sub_anchor or sub_head:
                    lines.append(f'{sub_anchor}{sub_head}'.strip())
        elif tag == 'textbox':
            # A boxed callout. Content sits under <textbox-body> (with an
            # optional <textbox-head> title); render it as a blockquote.
            body_parts: list[str] = []

            def collect(container: ET.Element) -> None:
                for c in container:
                    ct = common.get_tag(c)
                    if ct in ('sections',):
                        collect(c)
                    elif ct == 'section':
                        body_parts.append(self._section(c, level=3))
                    elif ct in ('para', 'simple-para'):
                        body_parts.extend(self._para(c))
                    elif ct in self._BLOCK_IN_PARA:
                        body_parts.append(self._render_block(c))

            head_el = _child(flt, 'textbox-head')
            if head_el is not None:
                ht = _norm(self.inline(head_el))
                if ht:
                    body_parts.append(f'**{ht}**')
            body_container = _coalesce(_child(flt, 'textbox-body'), flt)
            if body_container is not None:
                collect(body_container)
            quoted = '\n'.join('> ' + line for line in '\n\n'.join(p for p in body_parts if p).splitlines())
            if quoted:
                lines.append(quoted)
        # Figures carry only a publisher-internal pii: locator (not a
        # resolvable URL), so there's no image link worth emitting — the
        # label + caption are the renderable content.
        return '\n'.join(lines)

    def _table_footnotes(self, flt: ET.Element) -> str:
        """Render a table's <legend> + <table-footnote>s below the grid.

        Each footnote gets an ``<a id>`` anchor so the in-cell cross-refs
        that point at it resolve, plus a superscript label marker.
        """
        parts: list[str] = []
        for legend in _children(flt, 'legend'):
            for p in legend:
                if common.get_tag(p) in ('simple-para', 'para'):
                    t = _norm(self.inline(p))
                    if t:
                        parts.append(t)
        for fn in _children(flt, 'table-footnote'):
            fn_id = fn.get('id', '')
            label = _text(_child(fn, 'label'))
            body_parts = [
                _norm(self.inline(p)) for p in fn if common.get_tag(p) in ('note-para', 'simple-para', 'para')
            ]
            body = ' '.join(b for b in body_parts if b) or _norm(self.inline(fn))
            anchor = f'<a id="{fn_id}"></a>' if fn_id else ''
            marker = f'<sup>{label}</sup> ' if label else ''
            parts.append(f'{anchor}{marker}{body}'.strip())
        if not parts:
            return ''
        return '*' + ' '.join(parts) + '*'

    def _render_cals_table(self, tgroup: ET.Element) -> str:
        # Map colspec names → 1-based column number for namest/nameend spans.
        colnum: dict[str, int] = {}
        for i, cs in enumerate(_children(tgroup, 'colspec'), 1):
            num = cs.get('colnum')
            idx = int(num) if num and num.isdigit() else i
            name = cs.get('colname')
            if name:
                colnum[name] = idx

        def cells(row: ET.Element) -> list[tuple[str, int, int]]:
            out = []
            for entry in _children(row, 'entry'):
                content = common.md_escape_cell(_norm(self.inline(entry)))
                namest = entry.get('namest')
                nameend = entry.get('nameend')
                colspan = 1
                if namest and nameend and namest in colnum and nameend in colnum:
                    colspan = max(1, colnum[nameend] - colnum[namest] + 1)
                morerows = entry.get('morerows')
                rowspan = int(morerows) + 1 if morerows and morerows.lstrip('-').isdigit() else 1
                rowspan = max(1, rowspan)
                out.append((content, colspan, rowspan))
            return out

        header_rows_raw = []
        thead = _child(tgroup, 'thead')
        if thead is not None:
            header_rows_raw = [cells(r) for r in _children(thead, 'row')]
        body_rows_raw = []
        tbody = _child(tgroup, 'tbody')
        if tbody is not None:
            body_rows_raw = [cells(r) for r in _children(tbody, 'row')]
        return common.render_grid(header_rows_raw, body_rows_raw)

    # -- references -------------------------------------------------------

    def _reference(self, bib_ref: ET.Element) -> str:
        ref_id = bib_ref.get('id', '')
        label = _text(_child(bib_ref, 'label'))

        reference = _find(bib_ref, 'reference')
        body = self._sb_reference(reference) if reference is not None else ''
        if not body:
            # Structured parse empty → fall back to the publisher's rendered
            # citation string.
            body = _text(_find(bib_ref, 'source-text'))
        if not body:
            # Free-text citation: <bib-reference> wrapping <other-ref><textref>.
            textref = _find(bib_ref, 'textref')
            if textref is not None:
                body = _norm(self.inline(textref))

        # DOI / external link, appended if not already in the body text.
        doi_link = ''
        doi_el = _find(bib_ref, 'doi')
        if doi_el is not None and _text(doi_el):
            doi = _text(doi_el)
            doi_link = f'[doi:{doi}](https://doi.org/{doi})'
        else:
            inter = _find(bib_ref, 'inter-ref')
            if inter is not None:
                href = common.xlink_href(inter)
                if href.startswith(('http://', 'https://', 'ftp://')) and href not in body:
                    doi_link = f'[{href}]({href})'
        if doi_link and doi_link not in body:
            body = (body + ' ' + doi_link).strip()

        # A <note> in the reference may carry a trailing DOI/identifier not in
        # the structured fields; append it if it adds something.
        for note in (n for n in bib_ref.iter() if common.get_tag(n) == 'note'):
            nt = _norm(self.inline(note))
            if nt and nt not in body:
                body = (body + ' ' + nt).strip()

        label_sep = '' if label.rstrip().endswith(('.', ']')) else '.'
        lines = []
        if ref_id:
            lines.append(f'<a id="{ref_id}"></a>')
        lines.append(f'{label}{label_sep} {body}'.strip())
        lines.append('')
        return '\n'.join(lines)

    def _other_ref(self, ref: ET.Element) -> str:
        """Render an <other-ref> with its anchor.

        An <other-ref> is a free-text <textref> citation, sibling of
        <bib-reference> in the bibliography.
        """
        ref_id = ref.get('id', '')
        label = _text(_child(ref, 'label'))
        textref = _find(ref, 'textref')
        body = _norm(self.inline(textref)) if textref is not None else _norm(self.inline(ref))
        prefix = ''
        if label:
            sep = '' if label.rstrip().endswith(('.', ']')) else '.'
            prefix = f'{label}{sep} '
        lines = []
        if ref_id:
            lines.append(f'<a id="{ref_id}"></a>')
        lines.append(f'{prefix}{body}'.strip())
        lines.append('')
        return '\n'.join(lines)

    def _sb_reference(self, reference: ET.Element) -> str:  # noqa: C901, PLR0912
        contribution = _find(reference, 'contribution')
        authors: list[str] = []
        art_title = ''
        if contribution is not None:
            auth_block = _child(contribution, 'authors')
            if auth_block is not None:
                for a in auth_block:
                    atag = common.get_tag(a)
                    if atag == 'author':
                        sn = _text(_child(a, 'surname'))
                        gn = _text(_child(a, 'given-name'))
                        full = f'{sn} {gn}'.strip()
                        if full:
                            authors.append(full)
                    elif atag in ('et-al', 'etal'):
                        authors.append('et al.')
            title_el = _child(contribution, 'title')
            if title_el is not None:
                art_title = self.inline(_coalesce(_child(title_el, 'maintitle'), title_el)).strip()

        host = _find(reference, 'host')
        source = year = volume = issue = pages = ''
        publisher = ''
        if host is not None:
            issue_el = _find(host, 'issue')
            if issue_el is not None:
                series = _child(issue_el, 'series')
                if series is not None:
                    stitle = _child(series, 'title')
                    if stitle is not None:
                        source = self.inline(_coalesce(_child(stitle, 'maintitle'), stitle)).strip()
                    volume = _text(_child(series, 'volume-nr'))
                issue = _text(_child(issue_el, 'issue-nr'))
                date_text = _text(_find(issue_el, 'date'))
                m = _YEAR_RE.search(date_text)
                year = m.group(1) if m else date_text
            # Book hosts use <book>/<edited-book> with a title + publisher.
            book = _coalesce(_child(host, 'book'), _child(host, 'edited-book'))
            if book is not None and not source:
                btitle = _child(book, 'title')
                if btitle is not None:
                    source = self.inline(_coalesce(_child(btitle, 'maintitle'), btitle)).strip()
                date_text = _text(_find(book, 'date'))
                m = _YEAR_RE.search(date_text)
                year = m.group(1) if m else (year or date_text)
                pub_el = _find(book, 'publisher')
                if pub_el is not None:
                    name = _text(_child(pub_el, 'name'))
                    loc = _text(_child(pub_el, 'location'))
                    publisher = ': '.join(p for p in (loc, name) if p)
            pages_el = _find(host, 'pages')
            if pages_el is not None:
                fp = _text(_child(pages_el, 'first-page'))
                lp = _text(_child(pages_el, 'last-page'))
                pages = f'{fp}–{lp}' if fp and lp else fp

        seg: list[str] = []
        authors_str = ', '.join(authors)
        if authors_str:
            seg.append(authors_str + ('' if authors_str.rstrip().endswith('.') else '.'))
        if art_title:
            seg.append(art_title + ('' if art_title.rstrip().endswith(('.', '?', '!')) else '.'))
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
        if publisher:
            seg.append(publisher + '.')
        return ' '.join(seg).strip()


# ---------------------------------------------------------------------------
# Front matter
# ---------------------------------------------------------------------------


def _render_front(renderer: _Renderer, head: ET.Element | None, coredata: ET.Element | None) -> str:  # noqa: C901, PLR0912
    parts: list[str] = []

    def cd(name: str) -> str:
        return _text(_child(coredata, name)) if coredata is not None else ''

    # -- Title --
    title = ''
    if head is not None:
        title = renderer.inline(_child(head, 'title')).strip()
    if not title:
        title = cd('title')
    parts.append(f'# {title}')

    # -- Authors + affiliations --
    if head is not None:
        author_group = _find(head, 'author-group')
        if author_group is not None:
            parts.extend(_render_authors(author_group))

    # -- Metadata block (sourced from coredata) --
    if coredata is not None:
        meta_lines: list[str] = []
        if head is not None:
            dochead = _find(head, 'dochead')
            atype = _text(_child(dochead, 'textfn')) if dochead is not None else ''
            if atype:
                meta_lines.append(f'**Article type:** {atype}')
        jname = cd('publicationName')
        issn = cd('issn')
        if jname:
            meta_lines.append(f'**Journal:** {jname}' + (f' (ISSN {issn})' if issn else ''))
        cover = cd('coverDate')
        if cover:
            meta_lines.append(f'**Published:** {cover}')
        vol = cd('volume')
        issue = cd('issueIdentifier')
        pages = cd('pageRange') or cd('startingPage')
        if vol or pages or issue:
            vol_line = f'**Volume:** {vol}'
            if issue:
                vol_line += f'({issue})'
            if pages:
                vol_line += f', p. {pages}'
            meta_lines.append(vol_line)
        doi = cd('doi')
        if doi:
            meta_lines.append(f'**DOI:** [{doi}](https://doi.org/{doi})')
        # coredata/copyright is a copyright statement, not the licence; the
        # licence itself is the openaccessUserLicense URL below.
        copyright_stmt = cd('copyright')
        if copyright_stmt:
            meta_lines.append(f'**Copyright:** {copyright_stmt}')
        license_url = cd('openaccessUserLicense')
        if license_url:
            meta_lines.append(f'**License:** [{license_url}]({license_url})')
        if meta_lines:
            parts.append('\n'.join(meta_lines))

    # -- Article-level footnotes (e.g. "Peer review under responsibility of …") --
    if head is not None:
        for afn in _children(head, 'article-footnote'):
            afn_id = afn.get('id', '')
            note_parts = [
                _norm(renderer.inline(p)) for p in afn if common.get_tag(p) in ('note-para', 'para', 'simple-para')
            ]
            note = ' '.join(p for p in note_parts if p) or _norm(renderer.inline(afn))
            if note:
                anchor = f'<a id="{afn_id}"></a>' if afn_id else ''
                parts.append(f'{anchor}{note}')

    # -- Abstract(s) --
    # An article often carries several <abstract>s: the author abstract plus
    # a graphical abstract (whose figure must still be anchored). Render each.
    if head is not None:
        for abstract in _children(head, 'abstract'):
            parts.append(_render_abstract(renderer, abstract))

    # -- Keywords --
    if head is not None:
        keywords = _find(head, 'keywords')
        if keywords is not None:
            kws = [_text(k) for k in _children(keywords, 'keyword')]
            kws = [k for k in kws if k]
            if kws:
                gtitle = _text(_child(keywords, 'section-title')) or 'Keywords'
                parts.append(f'**{gtitle}:** {", ".join(kws)}')

    return '\n\n'.join(p for p in parts if p)


def _render_authors(author_group: ET.Element) -> list[str]:
    aff_ids = {a.get('id') for a in _children(author_group, 'affiliation') if a.get('id')}
    corr_ids = {c.get('id') for c in _children(author_group, 'correspondence') if c.get('id')}

    author_lines = []
    any_corresp = False
    for author in _children(author_group, 'author'):
        gn = _text(_child(author, 'given-name'))
        sn = _text(_child(author, 'surname'))
        full = f'{gn} {sn}'.strip()
        markers: list[str] = []
        is_corresp = False
        for xref in _children(author, 'cross-ref'):
            refid = xref.get('refid', '')
            if refid in aff_ids:
                # The marker glyph sits in a <sup> child of the cross-ref;
                # take its bare text — wrapping the already-<sup> rendering
                # would nest <sup> inside <sup>.
                marker = _text(xref)
                if marker:
                    markers.append(marker)
            elif refid in corr_ids:
                is_corresp = True
        if is_corresp:
            any_corresp = True
        sup_str = f'<sup>{",".join(markers)}</sup>' if markers else ''
        corresp = '\\*' if is_corresp else ''
        author_lines.append(f'{full}{corresp}{sup_str}')

    out = [', '.join(author_lines)]
    if any_corresp:
        out.append('\\* Corresponding author')

    # Affiliations (only those referenced above are present in the group).
    # Each carries an <a id> anchor so author affiliation cross-refs resolve.
    aff_parts = []
    for aff in _children(author_group, 'affiliation'):
        aff_id = aff.get('id', '')
        label = _text(_child(aff, 'label'))
        textfn = _text(_child(aff, 'textfn'))
        if not textfn:
            # No rendered textfn — assemble from the structured organization
            # names if present.
            org = [_text(o) for o in aff.iter() if common.get_tag(o) == 'organization']
            textfn = ', '.join(o for o in org if o)
        if textfn:
            anchor = f'<a id="{aff_id}"></a>' if aff_id else ''
            aff_parts.append(f'{anchor}<sup>{label}</sup> {textfn}' if label else f'{anchor}{textfn}')
    if aff_parts:
        out.append('\n'.join(aff_parts))

    # Correspondence note (anchored so the corresponding-author cross-ref
    # resolves).
    corr_parts = []
    for corr in _children(author_group, 'correspondence'):
        corr_id = corr.get('id', '')
        txt = _text(_child(corr, 'text'))
        if txt:
            anchor = f'<a id="{corr_id}"></a>' if corr_id else ''
            corr_parts.append(f'{anchor}{txt}')
    if corr_parts:
        out.append('\n'.join(corr_parts))

    # Author footnotes (e.g. "these authors contributed equally"), anchored
    # so the author-name cross-ref that points at them resolves.
    fn_parts = []
    for fn in _children(author_group, 'footnote'):
        fn_id = fn.get('id', '')
        label = _text(_child(fn, 'label'))
        body_parts = [_norm(_text(p)) for p in fn if common.get_tag(p) in ('note-para', 'para', 'simple-para')]
        body = ' '.join(b for b in body_parts if b)
        anchor = f'<a id="{fn_id}"></a>' if fn_id else ''
        marker = f'<sup>{label}</sup> ' if label else ''
        if body or anchor:
            fn_parts.append(f'{anchor}{marker}{body}'.strip())
    if fn_parts:
        out.append('\n'.join(fn_parts))

    return out


def _render_abstract(renderer: _Renderer, abstract: ET.Element) -> str:
    # Heading: the abstract's own <section-title> ("Graphical abstract",
    # "Highlights", ...) when present, else a label from its class, else
    # the default "Abstract".
    heading = _text(_child(abstract, 'section-title'))
    if not heading:
        heading = {'graphical': 'Graphical abstract', 'highlights': 'Highlights'}.get(
            abstract.get('class', ''), 'Abstract'
        )
    lines = [f'## {heading}']
    for child in abstract:
        tag = common.get_tag(child)
        if tag == 'section-title':
            continue
        if tag == 'abstract-sec':
            sub_title = _text(_child(child, 'section-title'))
            if sub_title:
                lines.append(f'**{sub_title}**')
            # Paragraphs via _para so a graphical-abstract figure (wrapped in
            # <display>) is lifted out and anchored rather than inlined away.
            for p in _children(child, 'simple-para') or _children(child, 'para'):
                lines.extend(renderer._para(p))
        elif tag in ('simple-para', 'para'):
            lines.extend(renderer._para(child))
    return '\n\n'.join(line for line in lines if line)


# ---------------------------------------------------------------------------
# Body & back matter
# ---------------------------------------------------------------------------


def _render_body(renderer: _Renderer, body: ET.Element, head: ET.Element | None) -> str:
    parts: list[str] = []
    for child in body:
        tag = common.get_tag(child)
        if tag == 'sections':
            # <sections> usually holds <section>s, but a <para>/<display> can
            # sit directly under it (e.g. an unnumbered table between
            # sections); render those too rather than dropping them.
            for sub in child:
                stag = common.get_tag(sub)
                if stag == 'section':
                    if _is_compact_view(sub):
                        parts.append(_anchor_only(sub))
                    else:
                        parts.append(renderer._section(sub, level=2))
                elif stag in ('para', 'simple-para'):
                    parts.extend(renderer._para(sub))
                elif stag in renderer._BLOCK_IN_PARA:
                    parts.append(renderer._render_block(sub))
        elif tag in ('para', 'simple-para'):
            parts.extend(renderer._para(child))
        elif tag == 'appendices':
            for sec in _children(child, 'section'):
                if _is_compact_view(sec):
                    parts.append(_anchor_only(sec))
                    continue
                parts.append(renderer._section(sec, level=2))

    # Data availability is carried in <head>; emit it as a trailing section.
    if head is not None:
        da = _find(head, 'data-availability')
        if da is not None:
            parts.append(renderer._section(da, level=2))

    # Trailing body blocks (acknowledgment, conflict-of-interest, etc.) in
    # document order, rendered through the normal section machinery.
    for child in body:
        tag = common.get_tag(child)
        if tag in ('sections', 'appendices', 'para', 'simple-para'):
            continue
        parts.append(renderer._section(child, level=2))

    return '\n\n'.join(p for p in parts if p)


def _render_biographies(renderer: _Renderer, tail: ET.Element) -> str:
    """Render author <biography> blocks.

    Their portrait figures get anchored via _para's block-lifting.
    """
    bios = [x for x in tail.iter() if common.get_tag(x) == 'biography']
    if not bios:
        return ''
    blocks: list[str] = ['## Author biographies']
    for bio in bios:
        for p in bio:
            if common.get_tag(p) in ('para', 'simple-para'):
                blocks.extend(renderer._para(p))
    return '\n\n'.join(b for b in blocks if b) if len(blocks) > 1 else ''


def _render_glossary(renderer: _Renderer, scope: ET.Element) -> str:
    """Render <glossary> (abbreviation lists) as a bulleted term list.

    Searches the whole article scope so a glossary in either <body> or
    <tail> is captured; glossaries are reference material, so emitting them
    in one trailing block is fine.
    """
    # Collect every <glossary-entry> in scope (handles both <glossary> and a
    # standalone <glossary-sec>); dedupe by id so a glossary-sec nested in a
    # glossary isn't rendered twice.
    entries: list[ET.Element] = []
    seen: set[int] = set()
    for ge in (e for e in scope.iter() if common.get_tag(e) == 'glossary-entry'):
        if id(ge) in seen:
            continue
        seen.add(id(ge))
        entries.append(ge)
    if not entries:
        return ''
    lines = ['## Abbreviations']
    for ge in entries:
        head = _norm(renderer.inline(_child(ge, 'glossary-heading')))
        defn = _norm(renderer.inline(_child(ge, 'glossary-def')))
        if not (head or defn):
            continue
        gid = ge.get('id', '')
        anchor = f'<a id="{gid}"></a>' if gid else ''
        lines.append(f'{anchor}- **{head}** — {defn}' if defn else f'{anchor}- **{head}**')
    return '\n'.join(lines) if len(lines) > 1 else ''


def _render_bibliography(renderer: _Renderer, tail: ET.Element) -> str:
    """Render <bibliography> and any <further-reading> list in <tail>.

    Both hold ``<bib-reference>``s; ``<further-reading>`` is a separate,
    uncited reading list some journals append after the citations.
    """
    blocks: list[str] = []
    for container_tag, default_title in (('bibliography', 'References'), ('further-reading', 'Further reading')):
        for container in (x for x in tail.iter() if common.get_tag(x) == container_tag):
            # A reference entry is a *top-level* <bib-reference> or <other-ref>
            # — one not nested inside another entry. A nested <other-ref> is
            # the free-text body of its parent <bib-reference> (rendered by
            # _reference), so collecting it separately would render the
            # citation twice. The "no entry inside another entry" rule is
            # structural (not content-based), so it dedupes by construction
            # for any nesting without ever dropping a distinct citation.
            parent: dict[int, ET.Element] = {}
            for p in container.iter():
                for c in p:
                    parent[id(c)] = p

            def _nested_in_entry(e: ET.Element, _parent: dict[int, ET.Element] = parent) -> bool:
                cur = _parent.get(id(e))
                while cur is not None:
                    if common.get_tag(cur) in _ENTRY_TAGS:
                        return True
                    cur = _parent.get(id(cur))
                return False

            refs = [x for x in container.iter() if common.get_tag(x) in _ENTRY_TAGS and not _nested_in_entry(x)]
            if not refs:
                continue
            title = _text(_child(container, 'section-title')) or default_title
            lines = [f'## {title}', '']
            for ref in refs:
                if common.get_tag(ref) == 'bib-reference':
                    lines.append(renderer._reference(ref))
                else:
                    lines.append(renderer._other_ref(ref))
            blocks.append('\n'.join(lines))
    return '\n\n'.join(blocks)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

_ADJACENT_SUP_RE = re.compile(r'</sup>[ \t]*<sup>')


def render(root: ET.Element) -> str:
    """Render a parsed Elsevier ``<full-text-retrieval-response>`` to Markdown.

    The dispatcher in :func:`litdown.convert` parses and sniffs the root,
    then calls this; it does not re-parse.
    """
    coredata = _find(root, 'coredata')
    original = _find(root, 'originalText')
    # The journal-article element is <article> for most titles, but Cell Press
    # journals (Patterns, iScience, …) use <simple-article>; older content
    # uses <converted-article>. All share the head/body/tail shape.
    article = None
    if original is not None:
        for atag in ('article', 'simple-article', 'converted-article', 'exam'):
            article = _find(original, atag)
            if article is not None:
                break
    if article is None:
        # No journal-article body (e.g. a metadata-only response). Fall back
        # to whatever the coredata gives us so the title isn't lost.
        title = _text(_child(coredata, 'title')) if coredata is not None else ''
        return f'# {title}' if title else ''

    renderer = _Renderer(article, coredata)
    head = _coalesce(_child(article, 'head'), _child(article, 'simple-head'))
    body = _child(article, 'body')
    tail = _coalesce(_child(article, 'tail'), _child(article, 'simple-tail'))

    sections: list[str] = []

    front = _render_front(renderer, head, coredata)
    if front:
        sections.append(front)

    if body is not None:
        body_md = _render_body(renderer, body, head)
        if body_md:
            sections.append(body_md)

    # Body footnotes collected during front/body rendering → trailing Notes.
    notes_md = renderer._render_footnotes()
    if notes_md:
        sections.append(notes_md)

    # Glossary / abbreviation lists (may sit in <body> or <tail>).
    glossary_md = _render_glossary(renderer, article)
    if glossary_md:
        sections.append(glossary_md)

    if tail is not None:
        refs_md = _render_bibliography(renderer, tail)
        if refs_md:
            sections.append(refs_md)
        bios_md = _render_biographies(renderer, tail)
        if bios_md:
            sections.append(bios_md)

    # Any float never anchored in prose: append in a trailing block so its
    # content isn't dropped.
    orphans = [
        renderer._render_float(flt) for fid, flt in renderer.floats.items() if fid not in renderer.rendered_floats
    ]
    orphans = [o for o in orphans if o]
    if orphans:
        sections.append('\n\n'.join(orphans))

    md = '\n\n---\n\n'.join(sections)
    return _ADJACENT_SUP_RE.sub('', md)
