#!/usr/bin/env python3
"""
build_preview_page.py — Build a 4-column HTML preview page.

Columns: MML source | W3C reference | Our render | Generated LaTeX

Usage:
    python3 build_preview_page.py [--output preview.html]
                                  [--limit N]
                                  [--filter all|our_only|both|failures|neither|npm_only]
                                  [--source cache|grading]
"""

import argparse
import base64
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from defusedxml.ElementTree import fromstring as defused_fromstring

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))
from test_mml import BASE_URL, CACHE_DIR, fetch_cached, load_math_elem, render_latex

from jatsdown.mathml import mml_to_tex

MML_NS = 'http://www.w3.org/1998/Math/MathML'


def b64(data: bytes | None) -> str:
    if not data:
        return ''
    return 'data:image/png;base64,' + base64.b64encode(data).decode()


def pretty_mml(mml_bytes: bytes) -> str:
    """Return indented MML source (strip XML declaration, trim whitespace)."""
    text = mml_bytes.decode('utf-8', errors='replace')
    text = re.sub(r'<\?xml[^?]*\?>\s*', '', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def load_all_math_elems(test_id: str) -> list[tuple[ET.Element, bool]]:
    """Return all (math_elem, is_display) pairs for a test.

    Fetches the full XHTML (which may embed multiple <math> blocks for
    tests like mathAdisplay1) and falls back to the .mml file when the
    XHTML has only one block or is unavailable.  Returns a list of
    (element, is_display_mode) tuples.
    """
    xhtml = fetch_cached(f'{BASE_URL}/{test_id}-full.xhtml')
    if xhtml:
        text = xhtml.decode('utf-8', errors='replace')
        # Extract all <math>…</math> source blocks via regex.
        blocks = re.findall(r'<math[\s>].*?</math>', text, re.DOTALL)
        if len(blocks) > 1:
            elems = []
            for block in blocks:
                try:
                    elem = defused_fromstring(block)
                except ET.ParseError:
                    continue
                display = elem.get('display', elem.get('mode', 'inline'))
                elems.append((elem, display == 'block' or display == 'display'))
            if elems:
                return elems

    mml_bytes = fetch_cached(f'{BASE_URL}/{test_id}.mml')
    if not mml_bytes:
        return []
    elem = load_math_elem(mml_bytes)
    if elem is None:
        return []
    display = elem.get('display', elem.get('mode', 'inline'))
    return [(elem, display == 'block' or display == 'display')]


def load_records_from_cache() -> list[dict]:
    """Yield one record per cached MML file."""
    records = []
    for mml_path in sorted(CACHE_DIR.rglob('*.mml')):
        rel = mml_path.relative_to(CACHE_DIR).with_suffix('').as_posix()
        records.append({'test': rel, 'source': 'cache'})
    return records


def load_records_from_grading(grading_map: dict) -> list[dict]:
    """Use grading.jsonl order (has outcome metadata)."""
    return list(grading_map.values())


def _esc(s: str) -> str:
    return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')


def _npm_latex_block(row: dict) -> str:
    if not row['npm_latex']:
        return ''
    return (
        "<details onclick='event.stopPropagation()'>"
        '<summary>npm LaTeX</summary>'
        f"<pre class='latex npm'>{_esc(row['npm_latex'])}</pre>"
        '</details>'
    )


def build_page(records: list[dict], grading_map: dict, path: Path) -> None:
    rows = []
    total = len(records)

    for i, r in enumerate(records):
        test_id = r['test']
        name = test_id.split('/')[-1]
        print(f'  [{i + 1:4d}/{total}] {name}', end='\r', flush=True)

        math_elems = load_all_math_elems(test_id)
        if not math_elems:
            continue

        # Build LaTeX: join multiple expressions with \qquad, prefix
        # block/display ones with \displaystyle.
        parts = []
        for elem, is_display in math_elems:
            t = mml_to_tex(elem).strip()
            if t:
                parts.append(r'\displaystyle ' + t if is_display else t)
        our_latex = r' \qquad '.join(parts)

        our_png = render_latex(our_latex) if our_latex else None
        ref_png = fetch_cached(f'{BASE_URL}/{test_id}.png')

        # MML source: join all blocks, separated by a blank line.
        mml_src_parts = []
        for elem, _ in math_elems:
            mml_src_parts.append(ET.tostring(elem, encoding='unicode'))
        mml_src = '\n\n'.join(mml_src_parts)

        grade_rec = grading_map.get(test_id, {})
        outcome = grade_rec.get('outcome', '')
        npm_latex = grade_rec.get('npm_latex', '')

        rows.append(
            {
                'id': test_id,
                'name': name,
                'outcome': outcome,
                'mml_src': mml_src,
                'our_latex': our_latex,
                'npm_latex': npm_latex,
                'ref_img': b64(ref_png),
                'our_img': b64(our_png),
            }
        )

    print()

    # --- HTML ------------------------------------------------------------------
    def esc(s: str) -> str:
        return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')

    outcome_colours = {
        'our_only': ('#d4edda', '#155724'),
        'both': ('#cce5ff', '#004085'),
        'npm_only': ('#f8d7da', '#721c24'),
        'neither': ('#fff3cd', '#856404'),
        '': ('#f5f5f5', '#555'),
    }

    row_html_parts = []
    for row in rows:
        bg, fg = outcome_colours.get(row['outcome'], outcome_colours[''])
        ref_cell = (
            f'<img src="{row["ref_img"]}" alt="ref">' if row['ref_img'] else '<span class="missing">no ref</span>'
        )
        our_cell = (
            f'<img src="{row["our_img"]}" alt="ours">' if row['our_img'] else '<span class="missing">render fail</span>'
        )
        badge = (
            f'<span class="badge" style="background:{bg};color:{fg}">{esc(row["outcome"] or "—")}</span>'
            if row['outcome']
            else ''
        )

        row_html_parts.append(f"""
<tr id="{esc(row['id'])}" onclick="toggleFlag('{esc(row['id'])}')">
  <td class="name-cell">
    <div class="flag-dot"></div>
    <div class="test-name">{esc(row['name'])}</div>
    <div class="test-path">{esc(row['id'])}</div>
    {badge}
  </td>
  <td class="mml-cell"><pre class="mml">{esc(row['mml_src'])}</pre></td>
  <td class="img-cell">{ref_cell}</td>
  <td class="img-cell">{our_cell}</td>
  <td class="latex-cell">
    <pre class="latex">{esc(row['our_latex'])}</pre>
    {_npm_latex_block(row)}
  </td>
</tr>""")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>MathML preview — {len(rows)} cases</title>
<style>
* {{ box-sizing: border-box; }}
body {{ font: 13px/1.4 sans-serif; margin: 0; background: #f4f4f4; }}

#toolbar {{
  position: sticky; top: 0; z-index: 10;
  background: #222; color: #eee;
  padding: 7px 16px; display: flex; align-items: center; gap: 16px;
}}
#toolbar h1 {{ font-size: 13px; margin: 0; flex: 1; }}
#filter-input {{
  background: #333; color: #eee; border: 1px solid #555;
  border-radius: 3px; padding: 4px 8px; font-size: 12px; width: 200px;
}}
#show-flagged {{
  background: transparent; color: #f90; border: 1px solid #f90;
  border-radius: 3px; padding: 4px 10px; font-size: 12px; cursor: pointer;
}}
#show-flagged.active {{ background: #f90; color: #222; }}
#export-btn {{
  background: #4a9; color: #fff; border: none;
  border-radius: 3px; padding: 4px 10px; font-size: 12px; cursor: pointer;
}}
#count {{ font-size: 12px; color: #aaa; white-space: nowrap; }}

table {{
  border-collapse: collapse;
  width: 100%;
  background: #fff;
}}
thead th {{
  background: #333; color: #fff;
  padding: 8px 10px; text-align: left;
  position: sticky; top: 37px; z-index: 5;
  font-size: 12px; font-weight: normal; letter-spacing: .04em;
  text-transform: uppercase;
}}
tbody tr {{ border-bottom: 1px solid #e0e0e0; cursor: pointer; user-select: none; }}
tbody tr:hover {{ background: #f0f0f0; }}
tbody tr.flagged {{ border-left: 4px solid #e55; background: #fff8f8; }}
tbody tr.flagged:hover {{ background: #fff0f0; }}
td {{ padding: 10px; vertical-align: top; }}

.name-cell {{
  width: 160px; min-width: 130px;
  position: relative; padding-left: 22px;
}}
tr:hover .flag-dot {{ border-color: #e55; }}
.flag-dot {{
  position: absolute; left: 8px; top: 12px;
  width: 10px; height: 10px; border-radius: 50%;
  border: 2px solid #ccc;
  transition: background .1s, border-color .1s;
}}
tr.flagged .flag-dot {{ background: #e55; border-color: #e55; }}
.test-name {{ font-weight: bold; font-size: 12px; word-break: break-all; }}
.test-path {{ font-size: 10px; color: #999; margin-top: 2px; word-break: break-all; }}
.badge {{
  display: inline-block; margin-top: 5px;
  font-size: 10px; padding: 2px 6px; border-radius: 8px; font-weight: bold;
}}

.mml-cell {{ width: 260px; }}
pre.mml {{
  font-size: 10px; font-family: monospace; white-space: pre-wrap;
  word-break: break-all; margin: 0; color: #336;
  max-height: 180px; overflow-y: auto;
  background: #f0f4ff; border-radius: 3px; padding: 6px;
}}

.img-cell {{ width: 140px; text-align: center; }}
.img-cell img {{ max-height: 80px; max-width: 130px; display: block; margin: 0 auto; }}
.missing {{ font-size: 11px; color: #c00; font-style: italic; }}

pre.latex {{
  font-size: 10px; font-family: monospace; white-space: pre-wrap;
  word-break: break-all; margin: 0; color: #333;
  max-height: 120px; overflow-y: auto;
  background: #fafafa; border-radius: 3px; padding: 6px;
}}
pre.latex.npm {{ color: #888; background: #f5f5f5; margin-top: 6px; }}
details {{ cursor: default; }}
details summary {{ font-size: 10px; color: #999; cursor: pointer; margin-top: 6px; }}
</style>
</head>
<body>

<div id="toolbar">
  <h1>MathML preview</h1>
  <input id="filter-input" type="text" placeholder="Filter by name…" oninput="applyFilter()">
  <button id="show-flagged" onclick="toggleFlagFilter()">Flagged: 0</button>
  <button id="export-btn" onclick="exportFlagged()">Export flagged</button>
  <span id="count">{len(rows)} cases</span>
</div>

<table>
<thead>
  <tr>
    <th>Test</th>
    <th>MML source</th>
    <th>W3C reference</th>
    <th>Our render</th>
    <th>Generated LaTeX</th>
  </tr>
</thead>
<tbody id="tbody">
{''.join(row_html_parts)}
</tbody>
</table>

<script>
const STORAGE_KEY = "mml_preview_flags";
let onlyFlagged = false;

function loadFlags() {{
  try {{ return new Set(JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]")); }}
  catch {{ return new Set(); }}
}}
function saveFlags(flags) {{
  localStorage.setItem(STORAGE_KEY, JSON.stringify([...flags]));
}}

function toggleFlag(id) {{
  const flags = loadFlags();
  if (flags.has(id)) flags.delete(id); else flags.add(id);
  saveFlags(flags);
  const tr = document.getElementById(id);
  tr.classList.toggle("flagged", flags.has(id));
  updateFlagCount();
}}

function updateFlagCount() {{
  const n = loadFlags().size;
  document.getElementById("show-flagged").textContent = "Flagged: " + n;
}}

function applyFilter() {{
  const q = document.getElementById("filter-input").value.toLowerCase();
  const flags = onlyFlagged ? loadFlags() : null;
  const rows = document.querySelectorAll('#tbody tr');
  let visible = 0;
  rows.forEach(tr => {{
    const matchesText = !q || tr.id.toLowerCase().includes(q)
      || tr.querySelector('.test-name').textContent.toLowerCase().includes(q)
      || tr.querySelector('pre.mml').textContent.toLowerCase().includes(q)
      || tr.querySelector('pre.latex').textContent.toLowerCase().includes(q);
    const matchesFlag = !flags || flags.has(tr.id);
    const show = matchesText && matchesFlag;
    tr.style.display = show ? '' : 'none';
    if (show) visible++;
  }});
  document.getElementById('count').textContent = visible + ' / {len(rows)} cases';
}}

function toggleFlagFilter() {{
  onlyFlagged = !onlyFlagged;
  document.getElementById("show-flagged").classList.toggle("active", onlyFlagged);
  applyFilter();
}}

function exportFlagged() {{
  const flags = [...loadFlags()].sort();
  const blob = new Blob([JSON.stringify(flags, null, 2)], {{type: "application/json"}});
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "flagged.json";
  a.click();
}}

// Restore flags from localStorage on load.
(function() {{
  const flags = loadFlags();
  flags.forEach(id => {{
    const tr = document.getElementById(id);
    if (tr) tr.classList.add("flagged");
  }});
  updateFlagCount();
}})();
</script>
</body>
</html>
"""

    path.write_text(html)
    print(f'Written {path}  ({len(rows)} rows)')


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--output', default='preview.html')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument(
        '--filter', choices=['all', 'our_only', 'both', 'failures', 'neither', 'npm_only', 'ungraded'], default='all'
    )
    ap.add_argument(
        '--source',
        choices=['cache', 'grading'],
        default='cache',
        help='cache = all cached MML files; grading = grading.jsonl only',
    )
    args = ap.parse_args()

    # Load grading outcomes for badges.
    grading_path = Path('grading.jsonl')
    grading_map: dict = {}
    if grading_path.exists():
        for line in grading_path.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                grading_map[r['test']] = r

    records = load_records_from_grading(grading_map) if args.source == 'grading' else load_records_from_cache()

    # Apply outcome filter.
    if args.filter == 'our_only':
        records = [r for r in records if grading_map.get(r['test'], {}).get('outcome') == 'our_only']
    elif args.filter == 'both':
        records = [r for r in records if grading_map.get(r['test'], {}).get('outcome') == 'both']
    elif args.filter == 'npm_only':
        records = [r for r in records if grading_map.get(r['test'], {}).get('outcome') == 'npm_only']
    elif args.filter == 'neither':
        records = [r for r in records if grading_map.get(r['test'], {}).get('outcome') == 'neither']
    elif args.filter == 'failures':
        records = [r for r in records if grading_map.get(r['test'], {}).get('outcome') in ('npm_only', 'neither')]
    elif args.filter == 'ungraded':
        records = [r for r in records if r['test'] not in grading_map]

    if args.limit:
        records = records[: args.limit]

    print(f'Building preview for {len(records)} cases…')
    build_page(records, grading_map, Path(args.output))


if __name__ == '__main__':
    main()
