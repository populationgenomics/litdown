#!/usr/bin/env python3
"""
grade_mml.py — Blind evaluation of MathML→LaTeX converters.

For each disagreement in the W3C Presentation test suite:
  1. Render our Python converter's LaTeX output as PNG
  2. Render mathml-to-latex (npm)'s LaTeX output as PNG
  3. Randomly assign them to positions A and B
  4. Send [reference PNG, candidate A, candidate B] to Gemini 2.5 Pro
  5. Ask Gemini whether A, B, both, or neither matches the reference equation
  6. Record results and summarise

Results are saved to a JSONL file so the run can be resumed if interrupted.

Usage:
    python3 grade_mml.py [--limit N] [--results grading.jsonl]
                         [--categories Presentation,General,TortureTests]
"""

import argparse
import json
import random
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import google.genai.types as gtypes
from google import genai

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from test_mml import (
    BASE_URL,
    DEFAULT_CATEGORIES,
    convert_npm,
    convert_ours,
    fetch_cached,
    get_toc_paths,
    load_math_elem,
    normalise,
    render_latex,
)

# ---------------------------------------------------------------------------
# Gemini client
# ---------------------------------------------------------------------------

CLIENT = genai.Client(vertexai=True, project='aasgard-dev', location='us-central1')
MODEL = 'gemini-2.5-pro'

PROMPT = """\
You are evaluating MathML-to-LaTeX converters.

**Reference** (Image 1): a browser rendering of a mathematical expression \
taken directly from the W3C MathML test suite.

**Candidate A** (Image 2) and **Candidate B** (Image 3): two different \
attempts to convert that same MathML to LaTeX and render it with pdflatex.

Your task: decide whether each candidate represents **the same mathematical \
expression** as the reference — meaning the same structure, operators, and \
values, even if the typography or visual style differs.

Respond with a JSON object and nothing else:
{
  "a_matches": true | false,
  "b_matches": true | false,
  "reasoning": "one short sentence explaining any key difference"
}"""

PROMPT_SINGLE = """\
You are evaluating a MathML-to-LaTeX converter.

**Reference** (Image 1): a browser rendering of a mathematical expression \
taken directly from the W3C MathML test suite.

**Candidate** (Image 2): an attempt to convert that same MathML to LaTeX \
and render it with pdflatex.

Your task: decide whether the candidate represents **the same mathematical \
expression** as the reference — meaning the same structure, operators, and \
values, even if the typography or visual style differs.

Respond with a JSON object and nothing else:
{
  "matches": true | false,
  "reasoning": "one short sentence explaining any key difference"
}"""


def _parse_gemini_response(text: str) -> dict:
    text = text.strip()
    if text.startswith('```'):
        text = '\n'.join(line for line in text.splitlines() if not line.strip().startswith('```'))
    return json.loads(text)


def ask_gemini(ref_png: bytes, cand_a: bytes, cand_b: bytes) -> dict:
    """Call Gemini with three images; return parsed JSON result."""
    parts = [
        gtypes.Part.from_text(text=PROMPT),
        gtypes.Part.from_text(text='Image 1 — Reference:'),
        gtypes.Part.from_bytes(data=ref_png, mime_type='image/png'),
        gtypes.Part.from_text(text='Image 2 — Candidate A:'),
        gtypes.Part.from_bytes(data=cand_a, mime_type='image/png'),
        gtypes.Part.from_text(text='Image 3 — Candidate B:'),
        gtypes.Part.from_bytes(data=cand_b, mime_type='image/png'),
    ]
    response = CLIENT.models.generate_content(model=MODEL, contents=parts)
    return _parse_gemini_response(response.text)


def ask_gemini_single(ref_png: bytes, cand: bytes) -> dict:
    """Call Gemini with two images (no competing candidate); return parsed JSON."""
    parts = [
        gtypes.Part.from_text(text=PROMPT_SINGLE),
        gtypes.Part.from_text(text='Image 1 — Reference:'),
        gtypes.Part.from_bytes(data=ref_png, mime_type='image/png'),
        gtypes.Part.from_text(text='Image 2 — Candidate:'),
        gtypes.Part.from_bytes(data=cand, mime_type='image/png'),
    ]
    response = CLIENT.models.generate_content(model=MODEL, contents=parts)
    result = _parse_gemini_response(response.text)
    # Normalise to the same shape as ask_gemini for uniform downstream handling.
    return {'a_matches': result.get('matches', False), 'reasoning': result.get('reasoning', '')}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--categories', default=','.join(DEFAULT_CATEGORIES))
    ap.add_argument('--limit', type=int, default=0, help='Grade at most N disagreements (0 = all)')
    ap.add_argument('--results', default='grading.jsonl', help='JSONL file to append results to (allows resumption)')
    ap.add_argument(
        '--delay',
        type=float,
        default=0.0,
        help='Seconds to wait between Gemini call submissions (usually 0 with --workers)',
    )
    ap.add_argument('--workers', type=int, default=8, help='Number of parallel Gemini calls')
    args = ap.parse_args()

    cats = set(args.categories.split(','))
    results_path = Path(args.results)

    # Load already-graded test names so we can skip them on resume.
    graded = set()
    if results_path.exists():
        for line in results_path.read_text().splitlines():
            if line.strip():
                graded.add(json.loads(line)['test'])
    print(f'Already graded: {len(graded)}')

    # Collect disagreements.
    print('Fetching TOC…')
    all_paths = get_toc_paths()
    paths = [p for p in all_paths if p.split('/')[0] in cats]

    disagreements = []
    print(f'Scanning {len(paths)} tests for disagreements…')
    for rel in paths:
        if rel in graded:
            continue
        mml_bytes = fetch_cached(f'{BASE_URL}/{rel}.mml')
        if not mml_bytes:
            continue
        mml_text = mml_bytes.decode('utf-8', errors='replace')
        math_elem = load_math_elem(mml_bytes)
        if math_elem is None:
            continue
        our_latex = (convert_ours(math_elem) or '').strip()
        npm_latex = (convert_npm(mml_text) or '').strip()
        if not our_latex or not npm_latex:
            continue
        if normalise(our_latex) == normalise(npm_latex):
            continue
        ref_png = fetch_cached(f'{BASE_URL}/{rel}.png')
        if not ref_png:
            continue
        disagreements.append((rel, our_latex, npm_latex, ref_png))

    print(f'Found {len(disagreements)} ungraded disagreements with reference PNG')
    if args.limit:
        disagreements = disagreements[: args.limit]

    # Grade each one.
    counts = {'our_only': 0, 'npm_only': 0, 'both': 0, 'neither': 0, 'api_error': 0, 'render_error': 0}
    lock = threading.Lock()
    done = [0]

    def grade_one(item):
        rel, our_latex, npm_latex, ref_png = item

        our_png = render_latex(our_latex)
        npm_png = render_latex(npm_latex)
        if not our_png:
            return rel, 'render_error', None, None, None, False, None

        npm_render_failed = npm_png is None

        if npm_render_failed:
            try:
                judgment = ask_gemini_single(ref_png, our_png)
            except Exception as e:
                return rel, 'api_error', None, None, None, True, str(e)
            our_matches = bool(judgment.get('a_matches'))
            npm_matches = False
            our_is_a = True
        else:
            our_is_a = random.random() < 0.5
            cand_a, cand_b = (our_png, npm_png) if our_is_a else (npm_png, our_png)
            try:
                judgment = ask_gemini(ref_png, cand_a, cand_b)
            except Exception as e:
                return rel, 'api_error', None, None, None, False, str(e)
            a_matches = bool(judgment.get('a_matches'))
            b_matches = bool(judgment.get('b_matches'))
            our_matches = a_matches if our_is_a else b_matches
            npm_matches = b_matches if our_is_a else a_matches

        if our_matches and not npm_matches:
            outcome = 'our_only'
        elif npm_matches and not our_matches:
            outcome = 'npm_only'
        elif our_matches and npm_matches:
            outcome = 'both'
        else:
            outcome = 'neither'

        record = {
            'test': rel,
            'our_latex': our_latex,
            'npm_latex': npm_latex,
            'our_is_a': our_is_a,
            'npm_render_failed': npm_render_failed,
            'our_matches': our_matches,
            'npm_matches': npm_matches,
            'outcome': outcome,
            'reasoning': judgment.get('reasoning', ''),
        }
        return rel, outcome, record, our_matches, npm_matches, npm_render_failed, judgment.get('reasoning', '')

    total_n = len(disagreements)
    with results_path.open('a') as out, ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {}
        for item in disagreements:
            if args.delay:
                time.sleep(args.delay)
            futures[pool.submit(grade_one, item)] = item[0]

        for fut in as_completed(futures):
            rel, outcome, record, _our_matches, _npm_matches, npm_render_failed, reasoning = fut.result()
            with lock:
                done[0] += 1
                n = done[0]
                name = rel.split('/')[-1]
                if outcome in ('render_error', 'api_error'):
                    counts[outcome] += 1
                    extra = f'  {reasoning}' if reasoning else ''
                    print(f'  [{n:4d}/{total_n}] {name:<30s}  {outcome}{extra}')
                else:
                    counts[outcome] += 1
                    flag = ' [npm_render_fail]' if npm_render_failed else ''
                    print(f'  [{n:4d}/{total_n}] {name:<30s}  {outcome:<10s}  {(reasoning or "")[:60]}{flag}')
                    out.write(json.dumps(record) + '\n')
                    out.flush()

    # Summary.
    total = sum(v for k, v in counts.items() if k not in ('api_error', 'render_error'))
    # Also tally npm_render_failed from the JSONL for reporting.
    npm_render_failed_count = 0
    if results_path.exists():
        for line in results_path.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                if r.get('npm_render_failed'):
                    npm_render_failed_count += 1
    print(f"""
Grading complete  ({sum(counts.values())} processed)
  Our converter correct, npm wrong : {counts['our_only']:4d}
  npm correct, our converter wrong : {counts['npm_only']:4d}
  Both correct                     : {counts['both']:4d}
  Neither correct                  : {counts['neither']:4d}
    (of which npm render failed)   : {npm_render_failed_count:4d}
  Our render errors (skipped)      : {counts['render_error']:4d}
  API errors    (skipped)          : {counts['api_error']:4d}
""")
    if total:
        our_score = (counts['our_only'] + counts['both']) / total * 100
        npm_score = (counts['npm_only'] + counts['both']) / total * 100
        print(f'  Our accuracy : {our_score:.1f}%')
        print(f'  npm accuracy : {npm_score:.1f}%')


if __name__ == '__main__':
    main()
