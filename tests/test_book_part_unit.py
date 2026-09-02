"""A BITS ``<book-part-wrapper>`` — one NCBI Bookshelf unit — converts like an article.

Europe PMC serves Bookshelf chapters (GeneReviews, say) as BITS: the chapter's
title and abstracts sit on ``<book-part-meta>`` rather than ``<front>``, the
body nests its ``<ref-list>`` inside a ``<sec>``, references carry opaque ids
rather than numbers, and table cells carry pretty-printing newlines.
Hand-written documents rather than fixtures: Bookshelf prose is not
redistributable, and the shapes under test are a few dozen lines.
"""

import pytest

from litdown import convert

_CHAPTER = b"""<book-part-wrapper xmlns:xlink="http://www.w3.org/1999/xlink" content-type="chapter" dtd-version="2.0">
<book-meta><book-title-group><book-title>Synthetic Reviews</book-title></book-title-group></book-meta>
<book-part book-part-type="chapter">
<book-part-meta>
<title-group><title><italic>ABC1</italic>-Related Disorder</title>
<alt-title>Synonym: ABC1 Syndrome</alt-title></title-group>
<abstract id="abc1.Summary"><title>Summary</title>
<sec><title>Clinical characteristics</title><p>Onset is in childhood.</p></sec>
<sec><title>Diagnosis/testing</title><p>Established by a pathogenic variant in <italic>ABC1</italic>.</p></sec>
</abstract>
</book-part-meta>
<body>
<sec id="abc1.Diagnosis"><title>Diagnosis</title>
<p>Suggestive findings [<xref ref-type="bibr" rid="abc1.REF.doe.2020">Doe et al 2020</xref>].</p>
<table-wrap id="abc1.T1"><label>Table 1. </label><caption><p>Testing Used</p></caption>
<table><thead><tr>
    <th>Gene</th>
    <th>Method</th>
</tr></thead><tbody><tr>
    <td>
        <italic>ABC1</italic>
    </td>
    <td>Sequence
        analysis</td>
</tr></tbody></table></table-wrap>
</sec>
</body>
<back>
<ref-list><title>Literature Cited</title>
<ref id="abc1.REF.doe.2020"><mixed-citation publication-type="journal"><string-name><surname>Doe</surname>
<given-names>J</given-names></string-name>. A synthetic study. <source>J Synth</source>. 2020;1:1-2.
<pub-id pub-id-type="pmid">10000000</pub-id></mixed-citation></ref>
</ref-list>
</back>
</book-part>
</book-part-wrapper>"""

_NESTED_PARTS = b"""<book-part-wrapper><book-meta/>
<book-part book-part-type="part">
<book-part-meta><title-group><label>Part I.</label><title>Overview</title></title-group></book-part-meta>
<body>
<p>The chapters of this part.</p>
<book-part book-part-type="chapter">
<book-part-meta><title-group><label>Chapter 1.</label><title>Alpha</title></title-group>
<abstract><p>Alpha in brief.</p></abstract></book-part-meta>
<body><sec id="c1.s1"><title>Findings</title>
<p>Reported by [<xref ref-type="bibr" rid="c1.REF.doe">Doe 2020</xref>].</p></sec>
<book-part book-part-type="section">
<book-part-meta><title-group><label>1.1</label><title>Beta</title></title-group></book-part-meta>
<body><sec><title>Detail</title><p>Deep text.</p></sec></body>
</book-part>
</body>
<back><ref-list><title>Literature Cited</title>
<ref id="c1.REF.doe"><mixed-citation>Doe J. A study. 2020.</mixed-citation></ref></ref-list></back>
</book-part>
<book-part book-part-type="chapter">
<body><sec><title>Orphan</title><p>A chapter without metadata.</p></sec></body>
</book-part>
</body>
</book-part>
</book-part-wrapper>"""

_REF_LIST_IN_SEC = b"""<article><body>
<sec id="s9"><title>References</title>
<sec id="s9a"><title>Literature Cited</title>
<ref-list><ref id="R1"><mixed-citation>Doe J. A study. 2020.</mixed-citation></ref></ref-list>
</sec>
<sec id="s9b"><title>Guidelines</title>
<ref-list><title>Consensus Statements</title>
<ref id="R2"><mixed-citation>Roe R. A statement. 2021.</mixed-citation></ref></ref-list>
</sec>
</sec></body></article>"""

_BACK_REF_LISTS = b"""<article><back>
<sec><title>Web Resources</title>
<ref-list><ref id="W1"><mixed-citation>Web one.</mixed-citation></ref></ref-list></sec>
<app-group><app><title>Appendix A</title>
<ref-list><title>Appendix References</title><ref id="A1"><mixed-citation>App one.</mixed-citation></ref></ref-list>
</app></app-group>
<ref-list><ref id="B1"><mixed-citation>Main one.</mixed-citation></ref></ref-list>
</back></article>"""

_TITLE = '<title-group><title>Alpha</title></title-group>'
_ABSTRACT = '<abstract><title>Summary</title><p>In brief.</p></abstract>'
_BODY = '<sec id="s1"><title>Diagnosis</title><p>Body text.</p></sec>'
_BACK = '<ref-list><ref id="B1"><mixed-citation>Doe J. A study. 2020.</mixed-citation></ref></ref-list>'


def _wrapper(*, meta: str = '', body: str = '', back: str = '') -> bytes:
    """A wrapper around one <book-part> holding only the pieces given."""
    pieces = ''.join(
        f'<{tag}>{xml}</{tag}>' for tag, xml in (('book-part-meta', meta), ('body', body), ('back', back)) if xml
    )
    return f'<book-part-wrapper><book-meta/><book-part>{pieces}</book-part></book-part-wrapper>'.encode()


def _sec_with_ref_list(sec_title: str, list_title: str) -> bytes:
    """An article whose body <sec> (untitled when ``sec_title`` is empty) holds one titled <ref-list>."""
    title = f'<title>{sec_title}</title>' if sec_title else ''
    ref_list = f'<ref-list><title>{list_title}</title>{_BACK[10:]}'
    return f'<article><body><sec id="s9">{title}{ref_list}</sec></body></article>'.encode()


def _headings(md: str) -> list[str]:
    return [line for line in md.splitlines() if line.startswith('#')]


def _rows(md: str) -> list[str]:
    return [line for line in md.splitlines() if line.startswith('|')]


def test_chapter_title_is_the_h1() -> None:
    assert convert(_CHAPTER).startswith('# *ABC1*-Related Disorder\n')


@pytest.mark.parametrize(
    ('title_group', 'heading'),
    [
        ('<label>Chapter 1.</label><title>Alpha</title>', '# Chapter 1. Alpha'),
        ('<title>Alpha</title><subtitle>A Primer</subtitle>', '# Alpha: A Primer'),
        ('<title>Alpha?</title><subtitle>A Primer</subtitle>', '# Alpha? A Primer'),
        ('<title><italic>Why?</italic></title><subtitle>A Primer</subtitle>', '# *Why?* A Primer'),
        ('<label>1</label><title>Alpha</title><subtitle>A Primer</subtitle>', '# 1 Alpha: A Primer'),
    ],
    ids=['label', 'subtitle', 'punctuated-title', 'punctuation-inside-markup', 'label-and-subtitle'],
)
def test_label_and_subtitle_join_the_title_in_the_h1(title_group: str, heading: str) -> None:
    md = convert(_wrapper(meta=f'<title-group>{title_group}</title-group>', body=_BODY))
    assert md.startswith(f'{heading}\n')


def test_article_subtitle_joins_the_title_the_same_way() -> None:
    xml = (
        b'<article><front><article-meta><title-group><article-title>Alpha</article-title>'
        b'<subtitle>A Primer</subtitle></title-group></article-meta></front></article>'
    )
    assert convert(xml).startswith('# Alpha: A Primer\n')


def test_abstract_renders_with_labelled_sections() -> None:
    md = convert(_CHAPTER)
    assert '## Summary' in md
    assert '**Clinical characteristics**\n\nOnset is in childhood.' in md
    assert '**Diagnosis/testing**\n\nEstablished by a pathogenic variant in *ABC1*.' in md


def test_front_matter_precedes_body_and_back() -> None:
    md = convert(_CHAPTER)
    front, body, back = md.split('\n\n---\n\n')
    assert front.startswith('# ') and '## Summary' in front
    assert body.startswith('<a id="abc1.Diagnosis"></a>\n## Diagnosis')
    assert back.startswith('## Literature Cited')


def test_meta_without_title_starts_with_the_abstract() -> None:
    md = convert(_wrapper(meta=_ABSTRACT, body=_BODY))
    assert md.startswith('## Summary\n\nIn brief.\n\n---\n\n')


def test_meta_without_abstract_is_the_title_alone() -> None:
    md = convert(_wrapper(meta=_TITLE, body=_BODY))
    assert md.startswith('# Alpha\n\n---\n\n<a id="s1"></a>\n## Diagnosis')


def test_each_abstract_renders() -> None:
    meta = _TITLE + _ABSTRACT + '<abstract abstract-type="toc"><p>Contents in brief.</p></abstract>'
    md = convert(_wrapper(meta=meta, body=_BODY))
    assert '## Summary\n\nIn brief.' in md
    assert '## Table of Contents\n\nContents in brief.' in md


def test_abstract_without_title_is_headed_abstract() -> None:
    md = convert(_wrapper(meta=_TITLE + '<abstract><p>In brief.</p></abstract>', body=_BODY))
    assert '## Abstract\n\nIn brief.' in md


def test_body_absent_leaves_front_matter_and_back() -> None:
    sections = convert(_wrapper(meta=_TITLE, back=_BACK)).split('\n\n---\n\n')
    assert len(sections) == 2
    assert sections[0] == '# Alpha'
    assert sections[1].startswith('## References\n\n<a id="B1"></a>\n1. Doe J.')


def test_book_part_without_meta_starts_with_the_body() -> None:
    md = convert(_wrapper(body=_BODY))
    assert md.startswith('<a id="s1"></a>\n## Diagnosis')
    assert '---' not in md


def test_meta_rendering_nothing_leaves_no_leading_rule() -> None:
    md = convert(_wrapper(meta='<book-part-id book-part-id-type="doi">10.1/x</book-part-id>', body=_BODY))
    assert md.startswith('<a id="s1"></a>\n## Diagnosis')
    assert '---' not in md


def test_body_rendering_nothing_leaves_no_rule() -> None:
    sections = convert(_wrapper(meta=_TITLE, body='<p/><sec><p> </p></sec>', back=_BACK)).split('\n\n---\n\n')
    assert len(sections) == 2
    assert sections[1].startswith('## References')


def test_empty_book_part_renders_nothing() -> None:
    assert convert(b'<book-part-wrapper><book-meta/><book-part/></book-part-wrapper>') == ''


def test_nested_book_parts_render_one_heading_level_down() -> None:
    """Parts nest as sections at any depth: meta, body and back one level below the part, no rules between.

    A nested part without metadata has no heading of its own; its body keeps its structural depth.
    """
    md = convert(_NESTED_PARTS)
    assert _headings(md) == [
        '# Part I. Overview',
        '## Chapter 1. Alpha',
        '### Abstract',
        '### Findings',
        '### 1.1 Beta',
        '#### Detail',
        '### Literature Cited',
        '### Orphan',
    ]
    assert md.count('\n---\n') == 1
    assert md.count('<a id="c1.REF.doe"></a>\nDoe J. A study. 2020.') == 1


def test_boxed_text_sections_nest_below_the_enclosing_heading() -> None:
    chapter = (
        '<book-part><book-part-meta><title-group><title>Beta</title></title-group></book-part-meta>'
        '<body><sec><title>Findings</title><p>Lead.<boxed-text><sec><title>In the box</title>'
        '<p>Boxed.</p></sec></boxed-text></p></sec></body></book-part>'
    )
    md = convert(_wrapper(meta=_TITLE, body=chapter))
    assert _headings(md) == ['# Alpha', '## Beta', '### Findings']
    assert '> #### In the box' in md


def test_pretty_printed_cells_render_as_single_rows() -> None:
    assert _rows(convert(_CHAPTER)) == [
        '| Gene | Method |',
        '| --- | --- |',
        '| *ABC1* | Sequence analysis |',
    ]


def test_back_reference_is_anchored_and_cited_unlabelled() -> None:
    md = convert(_CHAPTER)
    assert '[Doe et al 2020](#abc1.REF.doe.2020)' in md
    assert '<a id="abc1.REF.doe.2020"></a>\nDoe J.' in md


@pytest.mark.parametrize(
    ('ref', 'line'),
    [
        ('<ref id="B12">', '12. Doe J.'),
        ('<ref id="12">', '12. Doe J.'),
        ('<ref id="x"><label>3.</label>', '3. Doe J.'),
        ('<ref id="B12"><label>3</label>', '3. Doe J.'),
        ('<ref id="B12"><label><bold>9</bold></label>', '9. Doe J.'),
        ('<ref id="CR45">', 'Doe J.'),
        ('<ref id="bib7">', 'Doe J.'),
        ('<ref id="brca1.REF.doe.2020.307">', 'Doe J.'),
        ('<ref>', 'Doe J.'),
    ],
    ids=[
        'B-number',
        'number',
        'label',
        'label-over-id',
        'marked-up-label',
        'springer',
        'elsevier',
        'bookshelf',
        'no-id',
    ],
)
def test_ref_label_comes_from_the_label_or_a_numeric_id(ref: str, line: str) -> None:
    xml = (
        f'<article><back><ref-list>{ref}<mixed-citation>Doe J. A study. 2020.</mixed-citation></ref></ref-list></back>'
    )
    assert f'</a>\n{line} A study. 2020.\n' in convert(f'{xml}</article>'.encode())


@pytest.mark.parametrize(
    ('units', 'held'),
    [
        ('<book-app/>', '<book-app>'),
        ('<preface/>', '<preface>'),
        ('<book-part/><book-app/>', '<book-part> <book-app>'),
        ('', 'no unit'),
    ],
    ids=['book-app', 'preface', 'book-part-and-sibling', 'none'],
)
def test_wrapper_not_holding_exactly_one_book_part_is_refused(units: str, held: str) -> None:
    xml = f'<book-part-wrapper><book-meta/>{units}</book-part-wrapper>'.encode()
    with pytest.raises(ValueError, match=f'holds {held}; only one <book-part> is rendered'):
        convert(xml)


def test_unknown_root_is_refused() -> None:
    with pytest.raises(ValueError, match='unrecognized root element'):
        convert(b'<book><book-part/></book>')


def test_ref_list_nested_in_a_sec_renders_under_the_section_heading() -> None:
    """The section's title heads an untitled list; a titled list adds its own heading one level down."""
    md = convert(_REF_LIST_IN_SEC)
    assert md.count('References') == 1
    assert '### Literature Cited\n\n<a id="R1"></a>\nDoe J. A study. 2020.' in md
    assert '### Guidelines\n\n#### Consensus Statements\n\n<a id="R2"></a>\nRoe R. A statement. 2021.' in md


@pytest.mark.parametrize('list_title', ['References', 'REFERENCES.', 'references:'])
def test_ref_list_titled_like_its_section_is_headed_once(list_title: str) -> None:
    md = convert(_sec_with_ref_list('References', list_title))
    assert _headings(md) == ['## References']
    assert '<a id="B1"></a>\n1. Doe J. A study. 2020.' in md


def test_ref_list_in_an_untitled_sec_heads_at_the_sec_level() -> None:
    assert _headings(convert(_sec_with_ref_list('', 'Literature Cited'))) == ['## Literature Cited']


def test_back_ref_lists_render_once_each() -> None:
    """<back>'s own <ref-list>, one in a <sec> and one in an <app> each render once, headed in place."""
    md = convert(_BACK_REF_LISTS)
    assert _headings(md) == ['## Web Resources', '## Appendix A', '### Appendix References', '## References']
    for citation in ('Web one.', 'App one.', 'Main one.'):
        assert md.count(citation) == 1


def test_heading_depth_is_clamped_at_six() -> None:
    inner = (
        '<ref-list><title>Consensus</title><ref id="R1"><mixed-citation>Roe R. 2021.</mixed-citation></ref></ref-list>'
    )
    for depth in range(7, 1, -1):
        inner = f'<sec><title>Level {depth}</title>{inner}</sec>'
    md = convert(f'<article><body>{inner}</body></article>'.encode())
    assert '#######' not in md
    assert '\n###### Level 7\n' in md
    assert '\n###### Consensus\n' in md
