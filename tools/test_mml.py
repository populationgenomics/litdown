#!/usr/bin/env python3
"""
test_mml.py — Compare our MathML→LaTeX converter against mathml-to-latex (npm)
using the W3C MathML 3 Presentation test suite.

For each test:
  - Run our Python converter  (jatsdown.mathml.mml_to_tex)
  - Run mathml-to-latex       (via mml2tex_shim.js)
  - If they agree: record pass
  - If they disagree: render both outputs + fetch W3C reference PNG,
    collect into an HTML report for visual comparison

Usage:
    python3 test_mml.py [--categories Presentation,General]
                        [--limit N]
                        [--report mml_report.html]
"""

import argparse
import base64
import hashlib
import html
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

from defusedxml.ElementTree import fromstring as defused_fromstring

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))
from jatsdown.mathml import mml_to_tex  # noqa: E402

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BASE_URL = 'https://www.w3.org/Math/testsuite/build/main'
TOC_URL = f'{BASE_URL}/toc-full.xhtml'
CACHE_DIR = _ROOT / 'w3c_cache'
RENDER_DIR = _ROOT / 'render_cache'
SHIM = Path(__file__).parent / 'mml2tex_shim.js'

DEFAULT_CATEGORIES = ['Presentation', 'General', 'TortureTests']

# ---------------------------------------------------------------------------
# Network / cache helpers
# ---------------------------------------------------------------------------


def fetch_cached(url: str) -> bytes | None:
    """Download url, caching to CACHE_DIR. Returns None on HTTP error."""
    rel = url.removeprefix(BASE_URL + '/')
    local = CACHE_DIR / rel
    if local.exists():
        return local.read_bytes()
    try:
        data = urllib.request.urlopen(url, timeout=15).read()
    except Exception:
        return None
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_bytes(data)
    return data


def get_toc_paths() -> list[str]:
    """Return all relative test paths (e.g. 'Presentation/…/mfrac1') from TOC."""
    data = fetch_cached(TOC_URL)
    if not data:
        sys.exit('Could not fetch TOC')
    text = data.decode('utf-8', errors='replace')
    links = re.findall(r'href="([^"]+?-full\.xhtml)"', text)
    # Strip the -full.xhtml suffix to get the bare test path.
    return [link.replace('-full.xhtml', '') for link in links]


# ---------------------------------------------------------------------------
# Converter wrappers
# ---------------------------------------------------------------------------

MML_NS = 'http://www.w3.org/1998/Math/MathML'


def load_math_elem(mml_bytes: bytes) -> ET.Element | None:
    """Parse .mml bytes and return the root <math> Element."""
    try:
        root = defused_fromstring(mml_bytes.decode('utf-8', errors='replace'))
    except ET.ParseError:
        return None
    tag = root.tag
    local = tag.split('}', 1)[1] if '}' in tag else tag
    if local == 'math':
        return root
    # Might be wrapped in an XHTML body — find the math child.
    for elem in root.iter(f'{{{MML_NS}}}math'):
        return elem
    for elem in root.iter('math'):
        return elem
    return None


def convert_ours(math_elem: ET.Element) -> str | None:
    try:
        return mml_to_tex(math_elem).strip()
    except Exception:
        return None


def convert_npm(mml_str: str) -> str | None:
    try:
        r = subprocess.run(
            ['node', str(SHIM)],
            input=mml_str,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return r.stdout.strip() if r.returncode == 0 else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# LaTeX → PNG rendering  (pdflatex + pdftoppm)
# ---------------------------------------------------------------------------

LATEX_TEMPLATE = r"""\documentclass{article}
\usepackage{amsmath,amssymb}
\usepackage{cancel}
\usepackage[margin=8pt,paperwidth=500pt,paperheight=300pt]{geometry}
\pagestyle{empty}
\begin{document}
$BODY$
\end{document}
"""


def render_latex(latex: str) -> bytes | None:
    """Render a LaTeX math expression to a cropped PNG. Returns None on failure."""
    key = hashlib.sha256(latex.encode()).hexdigest()[:16]
    cached = RENDER_DIR / f'{key}.png'
    if cached.exists():
        return cached.read_bytes()

    doc = LATEX_TEMPLATE.replace('BODY', latex)
    with tempfile.TemporaryDirectory() as td:
        tex = Path(td) / 'expr.tex'
        tex.write_text(doc)
        subprocess.run(
            ['pdflatex', '-interaction=nonstopmode', '-halt-on-error', 'expr.tex'],
            cwd=td,
            capture_output=True,
            timeout=20,
            check=False,
        )
        pdf = Path(td) / 'expr.pdf'
        if not pdf.exists():
            return None
        # Crop to bounding box, then rasterise.
        subprocess.run(
            ['pdfcrop', 'expr.pdf', 'expr-crop.pdf'],
            cwd=td,
            capture_output=True,
            timeout=10,
        )
        src = 'expr-crop.pdf' if (Path(td) / 'expr-crop.pdf').exists() else 'expr.pdf'
        subprocess.run(
            ['pdftoppm', '-r', '144', '-png', src, 'page'],
            cwd=td,
            capture_output=True,
            timeout=10,
        )
        pages = sorted(Path(td).glob('page*.png'))
        if not pages:
            return None
        png_bytes = pages[0].read_bytes()

    RENDER_DIR.mkdir(parents=True, exist_ok=True)
    cached.write_bytes(png_bytes)
    return png_bytes


def png_to_data_uri(data: bytes) -> str:
    return 'data:image/png;base64,' + base64.b64encode(data).decode()


# ---------------------------------------------------------------------------
# HTML report
# ---------------------------------------------------------------------------

HTML_HEAD = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>MathML converter comparison</title>
<style>
  body { font-family: sans-serif; font-size: 13px; margin: 2em; }
  h1 { font-size: 1.4em; }
  .summary { background: #f0f0f0; padding: 1em; border-radius: 4px;
             margin-bottom: 1.5em; }
  table.results { border-collapse: collapse; width: 100%; }
  table.results th { background: #333; color: #fff; padding: 6px 10px;
                     text-align: left; }
  table.results td { border: 1px solid #ddd; padding: 6px 8px;
                     vertical-align: top; }
  table.results tr:nth-child(even) td { background: #f9f9f9; }
  .test-name { font-size: 11px; color: #555; }
  .mml-src { font-size: 11px; font-family: monospace; white-space: pre-wrap;
             max-width: 280px; overflow: auto; color: #333; }
  .latex   { font-family: monospace; font-size: 11px; white-space: pre-wrap;
             word-break: break-all; }
  .err     { color: #c00; font-style: italic; }
  img.ref  { max-height: 80px; background: white; border: 1px solid #ccc;
             padding: 2px; }
  .agree   { color: green; font-weight: bold; }
  .disagree { color: #c60; font-weight: bold; }
  .status-bar { margin-bottom: 1em; font-size: 12px; color: #555; }
</style>
</head>
<body>
<h1>MathML converter comparison — W3C Presentation test suite</h1>
"""

HTML_TAIL = '</body></html>\n'


def build_report(results: list[dict], path: Path) -> None:
    agree = sum(1 for r in results if r['status'] == 'agree')
    disagree = sum(1 for r in results if r['status'] == 'disagree')
    our_err = sum(1 for r in results if r['status'] == 'our_error')
    npm_err = sum(1 for r in results if r['status'] == 'npm_error')
    both_err = sum(1 for r in results if r['status'] == 'both_error')

    rows = []
    for r in results:
        if r['status'] == 'agree':
            continue  # only show disagreements / errors

        name_esc = html.escape(r['name'])
        mml_esc = html.escape(r['mml_src'])

        def latex_cell(latex, img_data):
            if latex is None:
                return '<span class="err">error</span>'
            cell = f'<div class="latex">{html.escape(latex)}</div>'
            if img_data:
                cell += f'<br><img class="ref" src="{png_to_data_uri(img_data)}">'
            return cell

        our_cell = latex_cell(r.get('our_latex'), r.get('our_img'))
        npm_cell = latex_cell(r.get('npm_latex'), r.get('npm_img'))

        ref_cell = ''
        if r.get('ref_png'):
            ref_cell = f'<img class="ref" src="{png_to_data_uri(r["ref_png"])}">'
        else:
            ref_cell = '<span class="err">no ref</span>'

        status_cls = 'disagree' if r['status'] == 'disagree' else 'err'
        rows.append(f"""
  <tr>
    <td><span class="test-name">{name_esc}</span><br>
        <span class="{status_cls}">{r['status']}</span></td>
    <td><pre class="mml-src">{mml_esc}</pre></td>
    <td>{our_cell}</td>
    <td>{npm_cell}</td>
    <td>{ref_cell}</td>
  </tr>""")

    summary = f"""
<div class="summary">
  <strong>Tests run:</strong> {len(results)} &nbsp;|&nbsp;
  <strong class="agree">Agree:</strong> {agree} ({100 * agree // max(len(results), 1)}%) &nbsp;|&nbsp;
  <strong class="disagree">Disagree:</strong> {disagree} &nbsp;|&nbsp;
  <strong>Our error:</strong> {our_err} &nbsp;|&nbsp;
  <strong>npm error:</strong> {npm_err} &nbsp;|&nbsp;
  <strong>Both error:</strong> {both_err}
</div>
<p class="status-bar">Showing {len(rows)} disagreements / errors. Agreements hidden.</p>
"""

    table = (
        """
<table class="results">
<thead><tr>
  <th>Test</th>
  <th>MathML source</th>
  <th>Ours</th>
  <th>mathml-to-latex (npm)</th>
  <th>W3C reference</th>
</tr></thead>
<tbody>
"""
        + ''.join(rows)
        + '\n</tbody></table>\n'
    )

    with path.open('w') as f:
        f.write(HTML_HEAD + summary + table + HTML_TAIL)

    print(f'Report written to {path}')


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def normalise(s: str) -> str:
    """Strip all whitespace for structural comparison.
    This treats e.g. '\\frac{x}{x+1}' and '\\frac{x}{x + 1}' as equal."""
    return re.sub(r'\s+', '', s)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        '--categories', default=','.join(DEFAULT_CATEGORIES), help='Comma-separated top-level categories to test'
    )
    ap.add_argument('--limit', type=int, default=0, help='Stop after N tests (0 = all)')
    ap.add_argument('--report', default='mml_report.html', help='Output HTML report path')
    ap.add_argument('--no-render', action='store_true', help='Skip LaTeX rendering (faster, images absent)')
    args = ap.parse_args()

    cats = set(args.categories.split(','))
    render = not args.no_render

    print('Fetching TOC…')
    all_paths = get_toc_paths()
    paths = [p for p in all_paths if p.split('/')[0] in cats]
    if args.limit:
        paths = paths[: args.limit]
    print(f'  {len(paths)} tests in categories: {", ".join(sorted(cats))}')

    results = []
    t0 = time.time()

    for i, rel_path in enumerate(paths):
        mml_url = f'{BASE_URL}/{rel_path}.mml'
        png_url = f'{BASE_URL}/{rel_path}.png'

        # Progress line (overwrite in terminal)
        elapsed = time.time() - t0
        rate = (i + 1) / max(elapsed, 0.01)
        eta = (len(paths) - i - 1) / rate
        print(f'  [{i + 1:4d}/{len(paths)}] {rel_path:<55s}  {eta:5.0f}s remaining', end='\r', flush=True)

        mml_bytes = fetch_cached(mml_url)
        if mml_bytes is None:
            results.append({'name': rel_path, 'status': 'fetch_error', 'mml_src': ''})
            continue

        mml_text = mml_bytes.decode('utf-8', errors='replace')
        math_elem = load_math_elem(mml_bytes)
        if math_elem is None:
            results.append({'name': rel_path, 'status': 'parse_error', 'mml_src': mml_text[:300]})
            continue

        # Keep the pretty-printed source for the report.
        mml_src = ET.tostring(math_elem, encoding='unicode')

        our_latex = convert_ours(math_elem)
        # Pass the raw file bytes to npm — ET.tostring adds ns0: prefixes that
        # mathml-to-latex does not accept.
        npm_latex = convert_npm(mml_text)

        # Determine status
        if our_latex is None and npm_latex is None:
            status = 'both_error'
        elif our_latex is None:
            status = 'our_error'
        elif npm_latex is None:
            status = 'npm_error'
        elif normalise(our_latex) == normalise(npm_latex):
            status = 'agree'
        else:
            status = 'disagree'

        rec = {
            'name': rel_path,
            'status': status,
            'mml_src': mml_src,
            'our_latex': our_latex,
            'npm_latex': npm_latex,
        }

        # Only fetch ref PNG and render for non-agree cases
        if status != 'agree':
            ref_png = fetch_cached(png_url)
            rec['ref_png'] = ref_png

            if render:
                if our_latex:
                    rec['our_img'] = render_latex(our_latex)
                if npm_latex:
                    rec['npm_img'] = render_latex(npm_latex)

        results.append(rec)

    print()  # end the \r line

    agree = sum(1 for r in results if r['status'] == 'agree')
    disagree = sum(1 for r in results if r['status'] == 'disagree')
    errors = sum(1 for r in results if r['status'] not in ('agree', 'disagree'))
    print(
        f'\nResults: {agree} agree, {disagree} disagree, {errors} errors '
        f'({len(results)} total, {time.time() - t0:.0f}s)'
    )

    build_report(results, Path(args.report))


if __name__ == '__main__':
    main()
