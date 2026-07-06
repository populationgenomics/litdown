"""`convert` accepts a path, raw bytes, or a binary stream — same result.

A caller with the XML already in hand (fetched into memory) should not have to
spill it to a temp file. These assert the three input forms are interchangeable
and produce byte-identical Markdown, for both dialects.
"""

import io
from pathlib import Path

import pytest

from litdown import convert

FIXTURES_DIR = Path(__file__).parent / 'fixtures'

_JATS = next(iter(sorted(FIXTURES_DIR.glob('PMC*/PMC*.*.xml'))), None)
_ELSEVIER = next(iter(sorted((FIXTURES_DIR / 'elsevier').glob('*.xml'))), None)


@pytest.mark.parametrize('xml_path', [p for p in (_JATS, _ELSEVIER) if p is not None])
def test_path_bytes_and_stream_agree(xml_path: Path) -> None:
    data = xml_path.read_bytes()

    from_path = convert(str(xml_path))
    from_bytes = convert(data)
    from_stream = convert(io.BytesIO(data))

    assert from_path.strip()
    assert from_bytes == from_path
    assert from_stream == from_path
