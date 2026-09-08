"""Block content inside a container survives, in document order, in both dialects.

A list item, a definition, a table cell, a footnote, a boxed text or a quote can
hold several paragraphs, a nested list or another block. Each container renders
its children through one document-order walker (JATS
:func:`litdown.jats._render_content`, Elsevier
:meth:`litdown.elsevier._Renderer._content`), so no text is dropped and nesting
is laid out as markdown: a sub-list indented by its parent's marker width, a
later paragraph as a continuation paragraph, a cell's blocks ``<br>``-separated.
Hand-written documents: each shape under test is a few lines, and Bookshelf
prose is not redistributable.
"""

import defusedxml.ElementTree
import pytest

from litdown import convert


def _jats(body: str) -> bytes:
    return f'<article><body>{body}</body></article>'.encode()


def _elsevier(section: str) -> bytes:
    return (
        '<full-text-retrieval-response><originalText><article><body><sections>'
        f'<section><section-title>S</section-title>{section}</section></sections></body></article>'
        '</originalText></full-text-retrieval-response>'
    ).encode()


_TABLE = '<table><tbody><tr><td>cell</td></tr></tbody></table>'

# One document per container shape; the text of each is distinct so a missing node is attributable.
DOCUMENTS = {
    'jats-list-under-item': _jats(
        '<sec><list list-type="bullet"><list-item><p>Outer one.</p><list list-type="bullet">'
        '<list-item><p>Inner A.</p></list-item><list-item><p>Inner B.</p></list-item></list></list-item>'
        '<list-item><p>Outer two.</p></list-item></list></sec>'
    ),
    'jats-list-inside-paragraph': _jats(
        '<sec><list><list-item><p>Outer one.<list><list-item><p>Inner A.</p></list-item>'
        '<list-item><p>Inner B.</p></list-item></list></p></list-item>'
        '<list-item><p>Outer two.</p></list-item></list></sec>'
    ),
    'jats-ordered-under-ordered': _jats(
        '<sec><list list-type="order"><list-item><p>Step one.</p><list list-type="order">'
        '<list-item><p>Sub a.</p></list-item><list-item><p>Sub b.</p></list-item></list></list-item>'
        '<list-item><p>Step two.</p></list-item></list></sec>'
    ),
    'jats-three-levels': _jats(
        '<sec><list><list-item><p>L1.</p><list><list-item><p>L2.</p><list list-type="order">'
        '<list-item><p>L3 a.</p></list-item><list-item><p>L3 b.</p></list-item></list></list-item></list>'
        '</list-item></list></sec>'
    ),
    'jats-labels': _jats(
        '<sec><list list-type="simple"><list-item><label>1</label><p>Describe the findings;</p></list-item>'
        '<list-item><label>2</label><p>Review the causes.</p></list-item></list></sec>'
    ),
    'jats-multi-paragraph-item': _jats(
        '<sec><list><list-item><p>First paragraph.</p><p>Second paragraph.</p></list-item>'
        '<list-item><p>Other.</p></list-item></list></sec>'
    ),
    'jats-def-list-under-item': _jats(
        '<sec><list><list-item><p>Terms:</p><def-list><def-item><term>allele</term>'
        '<def><p>One version of a gene.</p></def></def-item></def-list></list-item></list></sec>'
    ),
    'jats-list-title': _jats(
        '<sec><list list-type="bullet"><title>Newly Posted</title><list-item><p>Alpha.</p></list-item></list></sec>'
    ),
    'jats-wide-marker': _jats(
        '<sec><list list-type="order">'
        + ''.join(f'<list-item><p>Item {i}.</p></list-item>' for i in range(1, 10))
        + '<list-item><p>Ten.</p><list><list-item><p>Sub of ten.</p></list-item></list></list-item></list></sec>'
    ),
    'jats-list-in-cell': _jats(
        '<sec><table-wrap id="T1"><label>Table 1</label><table><tbody><tr><td><list list-type="bullet">'
        '<list-item><p><italic>SOST</italic>-related sclerosteosis</p></list-item>'
        '<list-item><p>Facial deformity</p></list-item></list></td><td><p>Para one</p><p>Para two</p></td>'
        '</tr></tbody></table></table-wrap></sec>'
    ),
    'jats-table-foot-fn-group': _jats(
        f'<sec><table-wrap id="T1"><label>Table 1</label>{_TABLE}<table-wrap-foot><fn-group><fn id="tfn1">'
        '<label>a</label><p>Grouped footnote.</p></fn></fn-group><attrib>Adapted from Doe.</attrib>'
        '</table-wrap-foot></table-wrap></sec>'
    ),
    'jats-list-in-footnote': _jats(
        f'<sec><table-wrap id="T1"><label>Table 1</label>{_TABLE}<table-wrap-foot><fn><label>1</label>'
        '<p>Note with <list><list-item><p>foot item</p></list-item></list></p></fn></table-wrap-foot>'
        '</table-wrap></sec>'
    ),
    'jats-boxed-text': _jats(
        '<sec><boxed-text id="B1"><caption><title>Learn More</title><p>Caption para.</p></caption><p>Lead.</p>'
        '<list><list-item><p>Boxed item.</p></list-item></list>'
        f'<table-wrap id="BT1"><label>Table B</label>{_TABLE}</table-wrap>'
        '<boxed-text id="B2"><p>Inner box.</p></boxed-text>'
        '<def-list><def-item><term>boxed term</term><def><p>boxed definition</p></def></def-item></def-list>'
        '</boxed-text></sec>'
    ),
    'jats-disp-quote': _jats(
        '<sec><disp-quote><p>Quoted.</p><list><list-item><p>Quoted item.</p></list-item></list>'
        '<attrib>Someone</attrib></disp-quote></sec>'
    ),
    'jats-statement': _jats(
        '<sec><statement><label>Theorem 1</label><p>Claim.</p><list><list-item><p>Case A.</p></list-item></list>'
        '</statement></sec>'
    ),
    'jats-def-list': _jats(
        '<sec><def-list><title>Terms</title><def-item><term><italic>ABC1</italic> gene</term><def><p>Para one.</p>'
        '<p>Para two.</p><list><list-item><p>Def item.</p></list-item></list></def></def-item></def-list></sec>'
    ),
    'jats-glossary': (
        b'<article><back><glossary><title>Glossary</title><def-list><def-list><def-item>'
        b'<term><italic>ABC1</italic> gene</term><def><p>Gloss para.</p><list><list-item><p>Gloss item.</p>'
        b'</list-item></list></def></def-item></def-list></def-list></glossary></back></article>'
    ),
    'jats-structured-abstract': (
        b'<article><front><article-meta><abstract><sec><title>Methods</title><p>We did.</p>'
        b'<list><list-item><p>Abstract item.</p></list-item></list></sec></abstract></article-meta></front></article>'
    ),
    'jats-body-level-blocks': _jats(
        '<p>Body para.</p><list><list-item><p>Body item.</p></list-item></list>'
        f'<table-wrap id="T1"><label>Table 1</label>{_TABLE}</table-wrap><sec><title>S</title><p>t</p></sec>'
    ),
    'jats-sec-label-and-fn-group': _jats(
        '<sec id="s1"><label>1.</label><title>Intro</title><p>Text.</p>'
        '<fn-group><fn><p>Section footnote.</p></fn></fn-group></sec>'
    ),
    'jats-captioned-media-in-paragraph': _jats(
        '<sec><p><media xmlns:xlink="http://www.w3.org/1999/xlink" xlink:href="s1.pdf" id="M1">'
        '<label>Additional file 1.</label><caption><p>Supplementary methods.</p></caption></media></p></sec>'
    ),
    'elsevier-list-under-item': _elsevier(
        '<list><list-item><para>Outer one.</para><list><list-item><para>Inner A.</para></list-item></list>'
        '</list-item><list-item><para>Outer two.</para></list-item></list>'
    ),
    'elsevier-list-inside-para': _elsevier(
        '<list><list-item><para>Outer one.<list><list-item><para>Inner A.</para></list-item>'
        '<list-item><para>Inner B.</para></list-item></list></para></list-item>'
        '<list-item><para>Outer two.</para></list-item></list>'
    ),
    'elsevier-labelled-multi-para-item': _elsevier(
        '<list><list-item><label>(i)</label><para>First.</para><para>Second.</para></list-item></list>'
    ),
    'elsevier-def-list': _elsevier(
        '<def-list><def-term>term</def-term><def-description><para>Desc one.</para><para>Desc two.</para>'
        '<list><list-item><para>Desc item.</para></list-item></list></def-description></def-list>'
    ),
    'elsevier-display': _elsevier(
        '<para>Lead.<display><def-list><def-term>t</def-term><def-description>d</def-description></def-list>'
        '<displayed-quote><para>Quoted.</para></displayed-quote></display></para>'
    ),
    'elsevier-list-in-entry': _elsevier(
        '<table id="tbl1"><label>Table 1</label><tgroup cols="1"><colspec colname="c1"/><tbody><row><entry>'
        '<list><list-item><para>Cell item A</para></list-item><list-item><para>Cell item B</para></list-item>'
        '</list></entry></row></tbody></tgroup></table>'
    ),
    'elsevier-table-footnote': _elsevier(
        '<table id="tbl1"><label>Table 1</label><tgroup cols="1"><colspec colname="c1"/><tbody><row>'
        '<entry>x</entry></row></tbody></tgroup><table-footnote id="tf1"><label>a</label><note-para>Foot lead.'
        '<list><list-item><para>Foot item.</para></list-item></list></note-para></table-footnote></table>'
    ),
    'elsevier-quote-with-source': _elsevier(
        '<displayed-quote><para>Quoted.</para><list><list-item><para>Quoted item.</para></list-item></list>'
        '<source>Someone</source><attribution>Elsewhere</attribution></displayed-quote>'
    ),
    'elsevier-enunciation': _elsevier(
        '<enunciation id="e1"><label>Theorem 1</label><para>Claim.</para>'
        '<list><list-item><para>Case A.</para></list-item></list></enunciation>'
    ),
}


def _text_nodes(xml: bytes) -> list[str]:
    """Every non-blank text node of the document, whitespace-flattened."""
    nodes = []
    for el in defusedxml.ElementTree.fromstring(xml).iter():
        for text in (el.text, el.tail):
            flat = ' '.join((text or '').split())
            if flat:
                nodes.append(flat)
    return nodes


@pytest.mark.parametrize('name', DOCUMENTS)
def test_every_text_node_survives(name: str) -> None:
    xml = DOCUMENTS[name]
    md = convert(xml)
    nodes = _text_nodes(xml)
    assert nodes, 'a document without text cannot exercise the invariant'
    for node in nodes:
        assert node in md


@pytest.mark.parametrize(
    ('name', 'expected'),
    [
        ('jats-list-under-item', '- Outer one.\n  - Inner A.\n  - Inner B.\n- Outer two.'),
        ('jats-list-inside-paragraph', '- Outer one.\n  - Inner A.\n  - Inner B.\n- Outer two.'),
        ('jats-ordered-under-ordered', '1. Step one.\n   1. Sub a.\n   2. Sub b.\n2. Step two.'),
        ('jats-three-levels', '- L1.\n  - L2.\n    1. L3 a.\n    2. L3 b.'),
        ('jats-labels', '1 Describe the findings;\n2 Review the causes.'),
        ('jats-multi-paragraph-item', '- First paragraph.\n\n  Second paragraph.\n- Other.'),
        ('jats-def-list-under-item', '- Terms:\n  - **allele** — One version of a gene.'),
        ('jats-list-title', '**Newly Posted**\n\n- Alpha.'),
        ('jats-wide-marker', '10. Ten.\n    - Sub of ten.'),
        ('jats-list-in-cell', '| - *SOST*-related sclerosteosis<br>- Facial deformity | Para one<br>Para two |'),
        ('jats-table-foot-fn-group', '*<sup>a</sup> Grouped footnote. Adapted from Doe.*'),
        ('jats-boxed-text', '> **Learn More**\n>\n> Caption para.\n> \n> Lead.\n> \n> - Boxed item.'),
        ('jats-boxed-text', '> <a id="B2"></a>\n> > Inner box.'),
        ('jats-disp-quote', '> Quoted.\n> \n> - Quoted item.\n> \n> — Someone'),
        ('jats-statement', '**Theorem 1** Claim.\n\n- Case A.'),
        ('jats-def-list', '**Terms**\n\n- ***ABC1* gene** — Para one.\n\n  Para two.\n  - Def item.'),
        ('jats-glossary', '## Glossary\n\n- ***ABC1* gene** — Gloss para.\n  - Gloss item.'),
        ('jats-structured-abstract', '**Methods**\n\nWe did.\n\n- Abstract item.'),
        ('jats-sec-label-and-fn-group', '## 1. Intro\n\nText.\n\n### Notes\n\nSection footnote.'),
        (
            'jats-captioned-media-in-paragraph',
            '<a id="M1"></a>\n[media](s1.pdf)\n**Additional file 1.** Supplementary methods.',
        ),
        ('elsevier-list-under-item', '- Outer one.\n  - Inner A.\n- Outer two.'),
        ('elsevier-list-inside-para', '- Outer one.\n  - Inner A.\n  - Inner B.\n- Outer two.'),
        ('elsevier-labelled-multi-para-item', '(i) First.\n\n    Second.'),
        ('elsevier-def-list', '- **term** — Desc one.\n\n  Desc two.\n  - Desc item.'),
        ('elsevier-list-in-entry', '| - Cell item A<br>- Cell item B |'),
        ('elsevier-quote-with-source', '> Quoted.\n> \n> - Quoted item.\n> \n> Someone\n> \n> — Elsewhere'),
    ],
)
def test_nesting_is_laid_out_as_markdown(name: str, expected: str) -> None:
    """A sub-list indents by its parent's marker width; a later block is a continuation paragraph."""
    assert expected in convert(DOCUMENTS[name])


@pytest.mark.parametrize(
    ('xml', 'expected'),
    [
        (_jats('<sec><list><list-item><p>a</p></list-item><list-item><p>b</p></list-item></list></sec>'), '- a\n- b'),
        (
            _jats(
                '<sec><list list-type="order"><list-item><p>a</p></list-item>'
                '<list-item><p>b</p></list-item></list></sec>'
            ),
            '1. a\n2. b',
        ),
        (
            _elsevier('<list><list-item><para>a</para></list-item><list-item><para>b</para></list-item></list>'),
            '- a\n- b',
        ),
    ],
    ids=['jats-bullets', 'jats-ordered', 'elsevier-bullets'],
)
def test_flat_list_of_single_paragraphs_is_unchanged(xml: bytes, expected: str) -> None:
    assert f'\n\n{expected}' in convert(xml) or convert(xml).startswith(expected)
