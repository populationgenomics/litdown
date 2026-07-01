"""Dialect-neutral leaves shared by litdown's XML dialects.

These helpers carry no JATS- or Elsevier-specific knowledge; they're the
bits both :mod:`litdown.jats` and :mod:`litdown.elsevier` would otherwise
duplicate verbatim: namespace-stripping tag helpers, the xlink href
accessor, table-cell escaping, the inline typographic leaf formatters, and
the markdown-table grid builder (colspan/rowspan expansion + multi-row
header collapse).

The inline *dispatchers* are deliberately NOT shared — JATS and Elsevier
diverge on cross-ref/link attribute handling enough that one config-driven
function would be more tangled than two small ones. Each dialect keeps its
own dispatcher and calls :func:`inline_wrap` for the shared leaf wrappings.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

XLINK_NS = 'http://www.w3.org/1999/xlink'
MML_NS = 'http://www.w3.org/1998/Math/MathML'


def get_tag(elem: ET.Element) -> str:
    """Return an element's local tag name, stripping any ``{ns}`` prefix."""
    tag = elem.tag
    return tag.split('}', 1)[1] if '}' in tag else tag


# Alias: tests and the Elsevier dialect spell it ``_local``; the JATS code
# spells it ``get_tag``. Same function, two historical names.
_local = get_tag


def xlink_href(elem: ET.Element) -> str:
    return elem.get(f'{{{XLINK_NS}}}href') or elem.get('href') or ''


def md_escape_cell(text: str) -> str:
    """Escape pipe characters inside a markdown table cell."""
    return text.replace('|', '\\|')


# ---------------------------------------------------------------------------
# Inline leaf formatters
# ---------------------------------------------------------------------------

# Canonical inline-styling tag → markdown wrapping template. Each dialect
# maps its own element names (e.g. JATS <sub> vs Elsevier <inf>) onto these
# canonical keys before calling inline_wrap.
_INLINE_WRAP = {
    'italic': '*{}*',
    'bold': '**{}**',
    'sup': '<sup>{}</sup>',
    'sub': '<sub>{}</sub>',
    'underline': '<u>{}</u>',
    'monospace': '`{}`',
    'strike': '~~{}~~',
}


def inline_wrap(name: str, inner: str) -> str | None:
    """Wrap ``inner`` markdown for a canonical inline-styling tag.

    Returns ``None`` if ``name`` is not a recognised shared leaf, so the
    caller can fall through to its dialect-specific handling.
    """
    tpl = _INLINE_WRAP.get(name)
    return tpl.format(inner) if tpl is not None else None


# ---------------------------------------------------------------------------
# Markdown-table grid builder
# ---------------------------------------------------------------------------


def expand_rows(raw_rows: list[list[tuple[str, int, int]]]) -> list[list[str]]:
    """Expand colspan/rowspan into a rectangular grid of strings.

    Each input row is a list of ``(content, colspan, rowspan)`` tuples.

    colspan > 1: content in the first slot, empty string in the rest
                 (preserves the column label without duplicating it).
    rowspan > 1: content repeated in each spanned row
                 (keeps every row self-contained for an LLM reader).
    """
    if not raw_rows:
        return []
    occupied: dict[tuple[int, int], str] = {}
    for row_idx, cells in enumerate(raw_rows):
        col_idx = 0
        for content, colspan, rowspan in cells:
            # Advance past any cells already occupied by a rowspan above.
            while (row_idx, col_idx) in occupied:
                col_idx += 1
            for dr in range(rowspan):
                for dc in range(colspan):
                    # Repeat content across rowspan; use "" for extra colspan slots.
                    occupied[(row_idx + dr, col_idx + dc)] = content if dc == 0 else ''
            col_idx += colspan

    if not occupied:
        return []
    nrows = max(r for r, _ in occupied) + 1
    ncols = max(c for _, c in occupied) + 1
    return [[occupied.get((r, c), '') for c in range(ncols)] for r in range(nrows)]


def render_grid(
    header_rows_raw: list[list[tuple[str, int, int]]],
    body_rows_raw: list[list[tuple[str, int, int]]],
) -> str:
    """Build a markdown table from normalized ``(content, colspan, rowspan)`` rows.

    Shared by the JATS (XHTML) and Elsevier (CALS) table renderers: each
    dialect translates its own spanning model into the normalized tuple
    rows, then hands them here. Markdown tables only support a single
    header row, so multi-row headers are collapsed column-by-column with
    " / " joins (Nature / extended-data tables routinely use 2-4 levels).
    """
    header_rows = expand_rows(header_rows_raw)
    body_rows = expand_rows(body_rows_raw)

    all_rows = header_rows + body_rows
    if not all_rows:
        return ''

    ncols = max(len(r) for r in all_rows)

    def pad(row: list[str]) -> list[str]:
        return row + [''] * (ncols - len(row))

    def is_decorator(row: list[str]) -> bool:
        text = ''.join(row).strip()
        return text in {'', '<hr/>', '<hr />'}

    real_header_rows = [pad(r) for r in header_rows if not is_decorator(r)]
    if real_header_rows:
        combined = []
        for col in range(ncols):
            seen: list[str] = []
            for r in real_header_rows:
                v = r[col].strip()
                if v and v not in seen:
                    seen.append(v)
            combined.append(' / '.join(seen))
    else:
        combined = []

    lines = []
    if combined:
        lines.append('| ' + ' | '.join(combined) + ' |')
        lines.append('| ' + ' | '.join(['---'] * ncols) + ' |')
    else:
        lines.append('| ' + ' | '.join([''] * ncols) + ' |')
        lines.append('| ' + ' | '.join(['---'] * ncols) + ' |')

    for row in body_rows:
        lines.append('| ' + ' | '.join(pad(row)) + ' |')

    return '\n'.join(lines)
