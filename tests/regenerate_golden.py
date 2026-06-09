#!/usr/bin/env python3
"""Regenerate tests/golden.json from tests/w3c_mml/*.mml.

Run this script after intentionally improving the converter to update the
regression baseline:

    python3 tests/regenerate_golden.py

The golden file records the current converter output for every cached W3C
MathML test in tests/w3c_mml/. The set of tests included there was
curated from a Gemini-graded run (see tools/grade_mml.py); to add or
remove members of that set, drop or remove .mml files in tests/w3c_mml/
and re-run this script.

After regenerating, review the diff (git diff tests/golden.json) to
confirm only expected changes are present, then commit.
"""

import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from defusedxml.ElementTree import fromstring as defused_fromstring

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))
from litdown.mathml import mml_to_tex  # noqa: E402

MML_NS = 'http://www.w3.org/1998/Math/MathML'
MML_DIR = Path(__file__).parent / 'w3c_mml'


def find_math_elem(root: ET.Element) -> ET.Element | None:
    tag = root.tag
    local = tag.split('}', 1)[1] if '}' in tag else tag
    if local == 'math':
        return root
    for elem in root.iter(f'{{{MML_NS}}}math'):
        return elem
    for elem in root.iter('math'):
        return elem
    return None


def main() -> None:
    if not MML_DIR.exists():
        sys.exit(f'{MML_DIR} not found')

    golden: dict[str, str] = {}
    skipped: list[str] = []

    for mml_path in sorted(MML_DIR.rglob('*.mml')):
        test_id = str(mml_path.relative_to(MML_DIR).with_suffix(''))
        try:
            root = defused_fromstring(mml_path.read_bytes().decode('utf-8', errors='replace'))
            math_elem = find_math_elem(root)
            if math_elem is None:
                skipped.append(f'{test_id}: no <math> element')
                continue
            golden[test_id] = mml_to_tex(math_elem).strip()
        except Exception as e:
            skipped.append(f'{test_id}: {e}')

    out = Path(__file__).parent / 'golden.json'
    out.write_text(json.dumps(golden, indent=2, ensure_ascii=False, sort_keys=True) + '\n')
    print(f'Written {len(golden)} entries to {out}')
    if skipped:
        print(f'Skipped {len(skipped)}:')
        for s in skipped:
            print(f'  {s}')


if __name__ == '__main__':
    main()
