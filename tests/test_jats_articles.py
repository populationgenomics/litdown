"""Structural assertions over JATS fixtures.

Each fixture under ``tests/fixtures/<PMCID>/`` is converted with
:func:`jatsdown.convert` and a handful of targeted invariants are
checked against the source XML. The aim is a regression suite that
catches real coverage gaps in ``jatsdown.jats`` — not a golden-file
diff.

Known bugs (cases where a fixture currently fails an invariant) are
recorded in ``KNOWN_BUGS`` and xfail-marked. When a bug is fixed the
test will "unexpectedly pass" and force the entry's removal.

Adding new fixtures: drop them into ``tests/fixtures/<PMCID>/``
(typically via ``python tools/fetch_pmc.py PMCxxxxx``); they're
discovered automatically.
"""

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import pytest
from defusedxml.ElementTree import parse as defused_parse

from jatsdown import convert

FIXTURES_DIR = Path(__file__).parent / 'fixtures'


@dataclass
class Article:
    pmcid: str
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
# Known-bug table
# ---------------------------------------------------------------------------
# Maps (pmcid, test_name) → human reason. Tests look this up and call
# pytest.xfail() so each entry is a live TODO, not a silent skip.
KNOWN_BUGS: dict[tuple[str, str], str] = {}


def _maybe_xfail(article: Article, test_name: str) -> None:
    reason = KNOWN_BUGS.get((article.pmcid, test_name))
    if reason:
        pytest.xfail(reason)


# ---------------------------------------------------------------------------
# Fixture discovery
# ---------------------------------------------------------------------------


def _fixture_dirs() -> list[Path]:
    if not FIXTURES_DIR.exists():
        return []
    return sorted(d for d in FIXTURES_DIR.iterdir() if d.is_dir())


@pytest.fixture(
    scope='module',
    params=_fixture_dirs(),
    ids=lambda d: d.name,
)
def article(request) -> Article:
    pmcid_dir: Path = request.param
    xmls = sorted(pmcid_dir.glob(f'{pmcid_dir.name}.*.xml'))
    if not xmls:
        pytest.skip(f'no JATS XML in {pmcid_dir}')
    xml_path = xmls[-1]  # highest version
    md = convert(str(xml_path))
    root = defused_parse(str(xml_path)).getroot()
    assert root is not None
    return Article(pmcid=pmcid_dir.name, xml_path=xml_path, md=md, root=root)


# ---------------------------------------------------------------------------
# Universal invariants — must hold for every fixture
# ---------------------------------------------------------------------------


def test_starts_with_h1(article: Article) -> None:
    assert article.md.lstrip().startswith('# '), 'converted markdown does not begin with an H1 title'


def test_no_namespace_prefix_leak(article: Article) -> None:
    # ElementTree leaves '{http://...}tag' in the output if a namespaced
    # element falls through to the str() fallback.
    assert '{http://' not in article.md
    assert '{https://' not in article.md


def test_every_table_wrap_has_anchor(article: Article) -> None:
    for tw_id in _ids_for(article.root, 'table-wrap'):
        assert _has_anchor(article.md, tw_id), f'table-wrap id={tw_id} missing anchor in markdown'


def test_every_table_wrap_renders_content(article: Article) -> None:
    """A table-wrap must produce either a markdown table or an image link.

    Some publishers (e.g. PLOS Genetics circa 2006) ship tables as images.
    Either way the table-wrap shouldn't lose its content.
    """
    for tw in (e for e in article.root.iter() if _local(e.tag) == 'table-wrap'):
        tw_id = tw.get('id') or ''
        # Locate the rendered block: from the table-wrap anchor to the
        # next blank line gap.
        if not tw_id:
            continue
        anchor = f'<a id="{tw_id}"></a>'
        idx = article.md.find(anchor)
        assert idx >= 0, f'table-wrap id={tw_id}: no anchor'
        # Read until two consecutive blank lines (next block) or EOF.
        block_end = article.md.find('\n\n\n', idx)
        block = article.md[idx : block_end if block_end >= 0 else len(article.md)]
        has_table_syntax = '| ---' in block
        has_image = '![' in block and '](' in block
        assert has_table_syntax or has_image, f'table-wrap id={tw_id} produced no table or image: {block[:200]!r}'


# ---------------------------------------------------------------------------
# Invariants with known per-fixture failures
# ---------------------------------------------------------------------------


def test_no_empty_sup_tags(article: Article) -> None:
    _maybe_xfail(article, 'test_no_empty_sup_tags')
    matches = re.findall(r'<sup>\s*</sup>', article.md)
    assert not matches, f'{len(matches)} empty <sup> tags in markdown'


def test_no_adjacent_sup_tags(article: Article) -> None:
    """<sup>1</sup><sup>2</sup> reads as "12" — inline-adjacent markers
    must collapse into one <sup>. Paragraph-break separation is fine.
    """
    matches = re.findall(r'</sup>[ \t]*<sup>', article.md)
    assert not matches, (
        f'{len(matches)} adjacent <sup> pairs (would read as concatenated numbers); use a single <sup>1,2</sup>'
    )


def test_every_fig_has_anchor(article: Article) -> None:
    _maybe_xfail(article, 'test_every_fig_has_anchor')
    missing = [fid for fid in _ids_for(article.root, 'fig') if not _has_anchor(article.md, fid)]
    assert not missing, f'{len(missing)} <fig> ids missing anchor: {missing[:3]}'


def test_every_ref_has_anchor(article: Article) -> None:
    _maybe_xfail(article, 'test_every_ref_has_anchor')
    missing = [rid for rid in _ids_for(article.root, 'ref') if not _has_anchor(article.md, rid)]
    assert not missing, f'{len(missing)} <ref> ids missing anchor: {missing[:3]}'


def test_every_sec_with_id_has_anchor(article: Article) -> None:
    _maybe_xfail(article, 'test_every_sec_with_id_has_anchor')
    missing = [sid for sid in _ids_for(article.root, 'sec') if not _has_anchor(article.md, sid)]
    assert not missing, f'{len(missing)} <sec> ids missing anchor: {missing[:3]}'
