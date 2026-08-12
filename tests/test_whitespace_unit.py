"""Single-line markdown constructs survive pretty-printed source XML.

Publisher XML is pretty-printed: a cell, heading or list item whose content
opens with an element carries the indentation newline in its text nodes.
Markdown table rows, headings and list items are line-terminated, so the
renderers flatten inline output (:func:`litdown.common.norm_ws`). These are
minimal hand-written documents — one per construct — rather than fixture
assertions, since the shape that triggers it (a cell containing only an
element) is easy to state directly and hard to spot in a real article.
"""

import textwrap

from litdown import common, convert

_JATS_TABLE = b"""<article><body><sec><table-wrap id="T1"><label>Table 1</label>
<table><thead><tr><th>Gene</th><th>Note</th></tr></thead>
<tbody>
<tr><td>ABC</td><td>plain cell</td></tr>
<tr><td colspan="1" rowspan="1">
<italic toggle="yes">UROD</italic>
</td><td>cell after a pretty-printed one</td></tr>
</tbody></table></table-wrap></sec></body></article>"""

_ELSEVIER_TABLE = b"""<full-text-retrieval-response>
<originalText><article><body><sections><section><section-title>S</section-title>
<table id="tbl1"><label>Table 1</label><tgroup cols="2">
<colspec colname="c1"/><colspec colname="c2"/>
<tbody><row><entry>ABC</entry><entry>plain cell</entry></row>
<row><entry>
<italic>UROD</italic>
</entry><entry>cell after a pretty-printed one</entry></row>
</tbody></tgroup></table>
</section></sections></body></article></originalText></full-text-retrieval-response>"""


def _rows(md: str) -> list[str]:
    return [line for line in md.splitlines() if line.startswith('|')]


def test_jats_element_only_cell_stays_in_its_row() -> None:
    assert _rows(convert(_JATS_TABLE)) == [
        '| Gene | Note |',
        '| --- | --- |',
        '| ABC | plain cell |',
        '| *UROD* | cell after a pretty-printed one |',
    ]


def test_elsevier_element_only_cell_stays_in_its_row() -> None:
    assert _rows(convert(_ELSEVIER_TABLE)) == [
        '|  |  |',
        '| --- | --- |',
        '| ABC | plain cell |',
        '| *UROD* | cell after a pretty-printed one |',
    ]


def test_section_heading_stays_on_one_line() -> None:
    xml = textwrap.dedent("""\
        <article><body><sec id="s1"><title>
        <italic>UROD</italic> variants</title>
        <p>text</p></sec></body></article>""").encode()
    assert '## *UROD* variants' in convert(xml)


def test_list_item_stays_on_one_line() -> None:
    xml = textwrap.dedent("""\
        <article><body><sec><list list-type="bullet"><list-item><p>
        <italic>UROD</italic> is affected</p></list-item></list></sec></body></article>""").encode()
    assert '- *UROD* is affected' in convert(xml)


def test_distinct_superscripts_are_not_fused() -> None:
    """A unit exponent then a citation marker: two superscripts, not one number.

    They are newline-separated in the source, so flattening leaves a space —
    the adjacent-<sup> collapse must not bridge it.
    """
    xml = (
        b'<article><body><sec><p>volume ~56,000 \xc2\xb5m<sup>3</sup>\n'
        b'<sup><xref ref-type="bibr" rid="CR45">45</xref></sup>; thus</p></sec></body></article>'
    )
    md = convert(xml)
    assert '<sup>3</sup> <sup>[45](#CR45)</sup>' in md


def test_split_exponent_superscripts_are_fused() -> None:
    """The artefact the collapse exists for: one number across two <sup>s."""
    xml = b'<article><body><sec><p>p &lt; 10<sup>-</sup><sup>4</sup> overall</p></sec></body></article>'
    assert '10<sup>-4</sup> overall' in convert(xml)


def test_unicode_spaces_are_content() -> None:
    """NBSP / thin / em spaces are typography, not source layout.

    Publishers indent pseudocode listings with em-space runs and bind units
    with thin spaces; only ASCII whitespace gets collapsed.
    """
    # thin space between value and unit; em-space run indenting pseudocode
    assert common.norm_ws('56,000\u2009\u00b5m') == '56,000\u2009\u00b5m'
    assert common.norm_ws('\n\u2003\u2002<bold>if</bold>') == ' \u2003\u2002<bold>if</bold>'
