"""Structural assertions over Elsevier fixtures.

Each ``*.xml`` committed directly under ``tests/fixtures/elsevier/`` is
converted with :func:`litdown.convert` and a handful of targeted
invariants are checked against the source XML. As with
``test_jats_articles.py`` this is a regression suite asserting structural
invariants over the Elsevier local-names — deliberately *not* a
golden-file diff.

Elsevier fixtures are committed as flat files (one ``.xml`` per article),
unlike the per-PMCID subdirectories the JATS suite uses, so they get their
own discovery + module here.

Adding new fixtures: drop a CC-BY (``by/4.0``) ``*.xml`` into
``tests/fixtures/elsevier/``; it's discovered automatically. See the
handoff plan (``docs/elsevier-dialect-plan.md``) for how to harvest them.
"""

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import pytest
from defusedxml.ElementTree import parse as defused_parse

from litdown import convert
from litdown.mathml import mml_to_tex

FIXTURES_DIR = Path(__file__).parent / 'fixtures' / 'elsevier'


@dataclass
class Article:
    name: str
    xml_path: Path
    md: str
    root: ET.Element


def _local(tag: str) -> str:
    return tag.split('}', 1)[-1]


def _ids_for(root: ET.Element, tag: str) -> list[str]:
    return [eid for e in root.iter() if _local(e.tag) == tag for eid in [e.get('id')] if eid]


def _has_anchor(md: str, anchor_id: str) -> bool:
    return f'<a id="{anchor_id}"></a>' in md


# ---------------------------------------------------------------------------
# Fixture discovery
# ---------------------------------------------------------------------------


def _fixture_files() -> list[Path]:
    if not FIXTURES_DIR.exists():
        return []
    return sorted(FIXTURES_DIR.glob('*.xml'))


@pytest.fixture(
    scope='module',
    params=_fixture_files(),
    ids=lambda p: p.stem,
)
def article(request) -> Article:
    xml_path: Path = request.param
    md = convert(str(xml_path))
    root = defused_parse(str(xml_path)).getroot()
    assert root is not None
    return Article(name=xml_path.stem, xml_path=xml_path, md=md, root=root)


# ---------------------------------------------------------------------------
# Invariants
# ---------------------------------------------------------------------------


def test_starts_with_h1(article: Article) -> None:
    assert article.md.lstrip().startswith('# '), 'converted markdown does not begin with an H1 title'


def test_no_namespace_prefix_leak(article: Article) -> None:
    # Elsevier mixes ja:/ce:/xocs:/sb: namespaces; a fall-through to str()
    # would leak a '{http://...}tag' into the output.
    assert '{http://' not in article.md
    assert '{https://' not in article.md


def test_every_bib_reference_anchored(article: Article) -> None:
    missing = [rid for rid in _ids_for(article.root, 'bib-reference') if not _has_anchor(article.md, rid)]
    assert not missing, f'{len(missing)} bib-reference ids missing anchor: {missing[:5]}'


def test_every_float_anchored(article: Article) -> None:
    float_ids = [
        eid for e in article.root.iter() if _local(e.tag) in ('figure', 'table') for eid in [e.get('id')] if eid
    ]
    missing = [fid for fid in float_ids if not _has_anchor(article.md, fid)]
    assert not missing, f'{len(missing)} float ids missing anchor: {missing[:5]}'


def test_math_not_dropped(article: Article) -> None:
    """Every source <math> with a non-empty rendering appears in the output.

    This is the litmus for the render_mathml wiring: a dialect that
    silently drops formulas (as the throwaway prototype did) fails here.
    The math-heavy fixtures (datainbrief / resultsinphysics) exercise it
    against dozens of equations — display, multi-line, and table-cell math.

    Comparison normalises whitespace (prose flattens interior whitespace)
    and tolerates the ``|`` -> ``\\|`` escaping applied to math inside table
    cells, so only a genuine drop fails.
    """
    md_norm = re.sub(r'\s+', ' ', article.md)
    nonempty = [re.sub(r'\s+', ' ', mml_to_tex(m).strip()) for m in article.root.iter() if _local(m.tag) == 'math']
    nonempty = [m for m in nonempty if m]
    dropped = [m for m in nonempty if m not in md_norm and m.replace('|', '\\|') not in md_norm]
    assert not dropped, f'{len(dropped)}/{len(nonempty)} <math> renderings missing from output: {dropped[:2]}'


def test_fixture_is_ccby(article: Article) -> None:
    """Vendored fixtures must be CC-BY 4.0 (freely redistributable).

    ``by-nc`` / ``by-nc-nd`` / ``by-nc-sa`` are not, so guard against one
    slipping into the committed corpus.
    """
    lic = ''
    for e in article.root.iter():
        if _local(e.tag) in ('openaccessUserLicense', 'oa-user-license') and (e.text or '').strip():
            lic = (e.text or '').strip()
            break
    assert lic.rstrip('/').endswith('/by/4.0'), f'fixture {article.name} is not CC-BY 4.0 (license: {lic!r})'


def test_cals_tables_emit_markdown(article: Article) -> None:
    """If the source has a CALS table body, the output has a markdown table."""
    has_cals = any(_local(e.tag) == 'tgroup' for e in article.root.iter())
    if not has_cals:
        pytest.skip('no CALS tables in this fixture')
    assert '| ---' in article.md, 'CALS table present in source but no markdown table emitted'


def test_no_dangling_links(article: Article) -> None:
    """Every emitted in-document link target has a matching anchor."""
    anchors = set(re.findall(r'<a id="([^"]+)"></a>', article.md))
    targets = set(re.findall(r'\]\(#([^)]+)\)', article.md))
    dangling = sorted(t for t in targets if t not in anchors)
    assert not dangling, f'{len(dangling)} dangling link targets: {dangling[:5]}'


def test_no_duplicate_anchors(article: Article) -> None:
    """No `<a id>` is emitted twice — a precise signal of any double-render.

    A duplicate anchor means some element (a bibliography entry, a float,
    a section) was rendered more than once. This is a general guard, not
    bibliography-specific, and also makes the in-document links it backs
    unambiguous.
    """
    ids = re.findall(r'<a id="([^"]+)"></a>', article.md)
    dups = sorted({i for i in ids if ids.count(i) > 1})
    assert not dups, f'{len(dups)} anchor id(s) emitted more than once: {dups[:5]}'


def test_bibliography_entries_unique(article: Article) -> None:
    """No two reference entries render identical citation text.

    Elsevier encodes free-text citations as an `<other-ref>/<textref>`
    nested inside `<bib-reference>`; a naive walk renders both the wrapper
    and the nested ref, duplicating the citation. This asserts the
    rendered "## References" list has no repeated entry (anchors + leading
    label stripped), regardless of the source nesting.
    """
    refs_idx = article.md.find('\n## References')
    if refs_idx < 0:
        pytest.skip('no References section')
    block = article.md[refs_idx:]
    # An entry begins at each anchor line within the references block.
    entries = re.split(r'(?=<a id=")', block)
    bodies = []
    for e in entries:
        if '<a id="' not in e:
            continue
        body = re.sub(r'<a id="[^"]+"></a>', '', e)
        body = re.sub(r'^\s*\[?\d+\]?[.)]?\s*', '', body).strip()
        if body:
            bodies.append(' '.join(body.split()))
    dups = sorted({b for b in bodies if bodies.count(b) > 1})
    assert not dups, f'{len(dups)} duplicate reference entr(ies): {[d[:60] for d in dups[:3]]}'


def test_cross_ref_targets_resolve(article: Article) -> None:
    """Every ce:cross-ref refid resolves to an emitted anchor.

    Float/bib/section anchors come from the renderers; affiliation,
    correspondence and table-footnote anchors are emitted specifically so
    their cross-refs aren't left dangling.
    """
    anchors = set(re.findall(r'<a id="([^"]+)"></a>', article.md))
    refids: set[str] = set()
    for e in article.root.iter():
        if _local(e.tag) == 'cross-ref' and e.get('refid'):
            refids.update((e.get('refid') or '').split())
    unresolved = sorted(r for r in refids if r not in anchors)
    assert not unresolved, f'{len(unresolved)} cross-ref targets without anchor: {unresolved[:5]}'
