"""Fetch PMC OA full-text packages into tests/fixtures/<pmcid>/.

Sync, stdlib-only port of lit-manager/backend/app/services/pmc_s3.py. Reads
from the public ``s3://pmc-oa-opendata/`` bucket via anonymous HTTPS, picks
the highest available version, and caches the article into
tests/fixtures/<PMCID>/.

By default fetches the article *core*: the JATS XML, publisher PDF, plain
text, and only the figure assets the XML actually references via
<graphic>/<inline-graphic>. Supplementary materials (Nature MOESM zips,
extra method PDFs, etc.) are skipped — they aren't part of the paper
proper and can dwarf the article itself. Pass ``--all`` to fetch
everything.

Usage:
    python tools/fetch_pmc.py PMC60000 PMC2435556 ...
    python tools/fetch_pmc.py --all PMC7334197    # include supplementary
    python tools/fetch_pmc.py --manifest tests/fixtures/MANIFEST.txt
"""

import re
import sys
import urllib.request
from pathlib import Path

from defusedxml.ElementTree import fromstring as defused_fromstring

_BUCKET = 'pmc-oa-opendata'
_BASE_URL = f'https://{_BUCKET}.s3.amazonaws.com'
_S3_NS = {'s3': 'http://s3.amazonaws.com/doc/2006-03-01/'}
_VERSION_RE = re.compile(r'^PMC\d+\.(\d+)/$')
_SKIP_SUFFIX = ('.json',)
_FIXTURES = Path(__file__).parent.parent / 'tests' / 'fixtures'


def _get(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as resp:
        return resp.read()


def list_pmc_files(pmcid: str) -> list[str]:
    """Return S3 keys for the latest version of pmcid (empty if not in OA)."""
    pmcid = pmcid.upper().strip()
    if not pmcid.startswith('PMC'):
        pmcid = f'PMC{pmcid}'

    root = defused_fromstring(_get(f'{_BASE_URL}/?prefix={pmcid}.&delimiter=/'))
    versions: list[int] = []
    for prefix_el in root.findall('s3:CommonPrefixes/s3:Prefix', _S3_NS):
        m = _VERSION_RE.match(prefix_el.text or '')
        if m:
            versions.append(int(m.group(1)))
    if not versions:
        return []

    version_prefix = f'{pmcid}.{max(versions)}/'
    root = defused_fromstring(_get(f'{_BASE_URL}/?prefix={version_prefix}'))
    keys: list[str] = []
    for content_el in root.findall('s3:Contents/s3:Key', _S3_NS):
        key = content_el.text or ''
        if key and not key.endswith(_SKIP_SUFFIX):
            keys.append(key)
    return keys


_XLINK = '{http://www.w3.org/1999/xlink}href'


def _referenced_assets(xml_bytes: bytes) -> set[str]:
    """Return basenames referenced by <graphic>/<inline-graphic> in XML."""
    root = defused_fromstring(xml_bytes.decode('utf-8', errors='replace'))
    refs: set[str] = set()
    for tag in ('graphic', 'inline-graphic'):
        for el in root.iter():
            local = el.tag.split('}', 1)[-1]
            if local == tag:
                href = el.get(_XLINK) or el.get('href') or ''
                if not href:
                    continue
                # JATS hrefs are usually basenames without an extension;
                # match any file beginning with the basename.
                refs.add(href.rsplit('/', 1)[-1])
    return refs


def fetch_pmc(
    pmcid: str,
    dest_root: Path = _FIXTURES,
    core_only: bool = True,
) -> Path:
    """Fetch pmcid's files into dest_root/<PMCID>/.

    When ``core_only`` (default), keeps only the .xml/.pdf/.txt for the
    article and any figure assets referenced by the XML; supplementary
    materials are skipped. Idempotent: files already on disk are not
    re-downloaded. Returns the article directory.
    """
    keys = list_pmc_files(pmcid)
    if not keys:
        raise RuntimeError(f'no objects in {_BUCKET} for {pmcid}')

    pmcid = pmcid.upper().strip()
    if not pmcid.startswith('PMC'):
        pmcid = f'PMC{pmcid}'

    article_dir = dest_root / pmcid
    article_dir.mkdir(parents=True, exist_ok=True)

    # Always-fetch core: the article's own .xml, .pdf, .txt.
    article_prefix = pmcid + '.'
    keep: set[str] = {
        k.rsplit('/', 1)[-1]
        for k in keys
        if k.rsplit('/', 1)[-1].startswith(article_prefix) and k.rsplit('.', 1)[-1].lower() in {'xml', 'pdf', 'txt'}
    }

    if core_only:
        # Need the XML first to discover referenced assets.
        xml_key = next(
            (k for k in keys if k.rsplit('/', 1)[-1] in keep and k.endswith('.xml')),
            None,
        )
        if xml_key is None:
            raise RuntimeError(f'no JATS XML found in keys for {pmcid}')
        xml_dest = article_dir / xml_key.rsplit('/', 1)[-1]
        if not xml_dest.exists():
            print(f'  fetching: {xml_dest.name}')
            xml_dest.write_bytes(_get(f'{_BASE_URL}/{xml_key}'))
        else:
            print(f'  cached:   {xml_dest.name}')
        refs = _referenced_assets(xml_dest.read_bytes())
        for k in keys:
            name = k.rsplit('/', 1)[-1]
            stem = name.rsplit('.', 1)[0]
            if stem in refs or name in refs:
                keep.add(name)
        skipped = [k.rsplit('/', 1)[-1] for k in keys if k.rsplit('/', 1)[-1] not in keep]
    else:
        keep = {k.rsplit('/', 1)[-1] for k in keys}
        skipped = []

    for key in keys:
        name = key.rsplit('/', 1)[-1]
        if name not in keep:
            continue
        dest = article_dir / name
        if dest.exists():
            print(f'  cached:   {name}')
            continue
        print(f'  fetching: {name}')
        dest.write_bytes(_get(f'{_BASE_URL}/{key}'))

    if skipped:
        print(f'  skipped {len(skipped)} non-core file(s)')
    return article_dir


def _read_manifest(path: Path) -> list[str]:
    pmcids: list[str] = []
    for line in path.read_text().splitlines():
        entry = line.split('#', 1)[0].strip()
        if entry:
            pmcids.append(entry)
    return pmcids


def main() -> int:
    args = sys.argv[1:]
    core_only = True
    if '--all' in args:
        core_only = False
        args = [a for a in args if a != '--all']

    pmcids: list[str] = []
    i = 0
    while i < len(args):
        if args[i] == '--manifest':
            if i + 1 >= len(args):
                print('--manifest requires a path', file=sys.stderr)
                return 1
            pmcids.extend(_read_manifest(Path(args[i + 1])))
            i += 2
        else:
            pmcids.append(args[i])
            i += 1

    if not pmcids:
        print(
            f'Usage: {sys.argv[0]} [--all] [--manifest FILE] <PMCID> [PMCID ...]',
            file=sys.stderr,
        )
        return 1
    for pmcid in pmcids:
        print(f'PMC: {pmcid}')
        fetch_pmc(pmcid, core_only=core_only)
    return 0


if __name__ == '__main__':
    sys.exit(main())
