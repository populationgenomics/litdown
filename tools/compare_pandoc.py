"""Blind A/B compare jatsdown vs pandoc on the same JATS XML.

For each fixture:
  1. Convert with jatsdown.convert
  2. Convert with `pandoc -f jats -t markdown`
  3. Randomly assign them to positions A and B
  4. Send (publisher PDF, candidate A, candidate B) to Vertex Gemini
  5. Ask which conversion better preserves the article's scientific
     content
  6. Record the judgment + which side was which

Output is appended to compare_pandoc.jsonl. Each record has:

  {
    "pmcid": "...",
    "timestamp": "...",
    "model": "...",
    "a_engine": "jatsdown" | "pandoc",
    "b_engine": "...",
    "winner": "a" | "b" | "tie" | "neither",
    "reasoning": "...",
    "differences": [...]   # optional structured findings
  }

Usage:
    export JATSDOWN_GCP_PROJECT=your-project
    python tools/compare_pandoc.py PMC60000 PMC1713260
    python tools/compare_pandoc.py --all
"""

import argparse
import json
import os
import random
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import google.genai as genai
import google.genai.types as gtypes

sys.path.insert(0, str(Path(__file__).parent.parent))
from jatsdown import convert

ROOT = Path(__file__).parent.parent
FIXTURES = ROOT / 'tests' / 'fixtures'
DEFAULT_OUT = ROOT / 'compare_pandoc.jsonl'

DEFAULT_MODEL = 'gemini-2.5-pro'
DEFAULT_LOCATION = 'us-central1'


def make_client(project, location):
    return genai.Client(vertexai=True, project=project, location=location)


PROMPT = """\
You are evaluating two competing JATS-XML to Markdown converters.

You receive:
  1. The publisher PDF for the article (input 1) — the reference
     rendering of what the article actually contains.
  2. Candidate A markdown (input 2, attached as text below).
  3. Candidate B markdown (input 3, attached as text below).

A and B are blinded — they were converted from the *same* JATS XML
source by two different engines. Your task is to judge which one
better preserves the article's SCIENTIFIC CONTENT relative to the
publisher PDF.

What "better" means here:
  - Faithful: the markdown contains the same text, equations,
    figure references, citations, table data, and section
    structure as the PDF.
  - Complete: nothing material from the PDF is missing.
  - Unambiguous: structural elements (headings, bullets, code,
    quotes, math) are encoded in markdown rather than left as
    raw XML or stripped of structure.

What to IGNORE:
  - Typographic differences (fonts, columns, page layout).
  - Cosmetic markdown style (* vs _, two-space indent vs four,
    HTML-tag fallbacks for things markdown can't natively
    represent).
  - The order of metadata blocks at the top (e.g. journal info,
    license) so long as the content is present.

Decide whether A or B is closer to the PDF, or whether they are
substantively equivalent. Output a JSON object matching the
supplied schema. Empty differences list = the two are essentially
equivalent.
"""

RESPONSE_SCHEMA = {
    'type': 'object',
    'properties': {
        'winner': {
            'type': 'string',
            'enum': ['a', 'b', 'tie', 'neither'],
        },
        'reasoning': {'type': 'string'},
        'differences': {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {
                    'favor': {'type': 'string', 'enum': ['a', 'b']},
                    'category': {
                        'type': 'string',
                        'enum': [
                            'missing_content',
                            'misrepresented',
                            'ordering',
                            'structure',
                            'formatting',
                            'other',
                        ],
                    },
                    'description': {'type': 'string'},
                },
                'required': ['favor', 'category', 'description'],
            },
        },
    },
    'required': ['winner', 'reasoning'],
}


def pandoc_convert(xml_path: Path) -> str:
    """Run pandoc -f jats -t markdown on the JATS XML."""
    r = subprocess.run(
        ['pandoc', '-f', 'jats', '-t', 'markdown', str(xml_path)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if r.returncode != 0:
        raise RuntimeError(f'pandoc failed: {r.stderr[:500]}')
    return r.stdout


def compare(pmcid: str, *, client, model: str) -> dict:
    pmcid_dir = FIXTURES / pmcid
    if not pmcid_dir.is_dir():
        raise FileNotFoundError(f'no fixture at {pmcid_dir}')
    xml_path = next(iter(sorted(pmcid_dir.glob(f'{pmcid}.*.xml'))), None)
    pdf_path = next(iter(sorted(pmcid_dir.glob(f'{pmcid}.*.pdf'))), None)
    if xml_path is None:
        raise FileNotFoundError(f'no JATS XML in {pmcid_dir}')
    if pdf_path is None:
        raise FileNotFoundError(f'no publisher PDF in {pmcid_dir}')

    ours = convert(str(xml_path))
    pandoc = pandoc_convert(xml_path)

    # Randomize the A/B assignment so position bias doesn't favour
    # either engine systematically across the run.
    if random.random() < 0.5:
        a_engine, a_md, b_engine, b_md = 'jatsdown', ours, 'pandoc', pandoc
    else:
        a_engine, a_md, b_engine, b_md = 'pandoc', pandoc, 'jatsdown', ours

    parts = [
        gtypes.Part.from_text(text=PROMPT),
        gtypes.Part.from_text(text='Input 1 — Publisher reference PDF:'),
        gtypes.Part.from_bytes(data=pdf_path.read_bytes(), mime_type='application/pdf'),
        gtypes.Part.from_text(text='Input 2 — Candidate A markdown:'),
        gtypes.Part.from_text(text=a_md),
        gtypes.Part.from_text(text='Input 3 — Candidate B markdown:'),
        gtypes.Part.from_text(text=b_md),
    ]
    config = gtypes.GenerateContentConfig(
        response_mime_type='application/json',
        response_schema=RESPONSE_SCHEMA,
    )
    resp = client.models.generate_content(
        model=model,
        contents=parts,
        config=config,
    )
    judgment = json.loads(resp.text)
    judgment['a_engine'] = a_engine
    judgment['b_engine'] = b_engine
    return judgment


def main():
    ap = argparse.ArgumentParser(
        description='Blind A/B compare jatsdown vs pandoc with Gemini as judge.',
    )
    ap.add_argument('pmcids', nargs='*')
    ap.add_argument('--all', action='store_true')
    ap.add_argument('--out', default=str(DEFAULT_OUT))
    ap.add_argument('--project', default=os.environ.get('JATSDOWN_GCP_PROJECT'))
    ap.add_argument('--location', default=os.environ.get('JATSDOWN_GCP_LOCATION', DEFAULT_LOCATION))
    ap.add_argument('--model', default=DEFAULT_MODEL)
    ap.add_argument('--seed', type=int, default=None, help='Random seed for reproducible A/B assignment')
    args = ap.parse_args()

    if not args.project:
        print('no GCP project set; pass --project or set JATSDOWN_GCP_PROJECT', file=sys.stderr)
        return 2
    if args.seed is not None:
        random.seed(args.seed)

    pmcids = sorted(d.name for d in FIXTURES.iterdir() if d.is_dir()) if args.all else args.pmcids
    if not pmcids:
        print('no PMCIDs given; pass --all or specific PMCIDs', file=sys.stderr)
        return 1

    client = make_client(args.project, args.location)
    out_path = Path(args.out)

    for pmcid in pmcids:
        print(f'Comparing {pmcid}...', file=sys.stderr)
        t0 = time.time()
        try:
            j = compare(pmcid, client=client, model=args.model)
        except Exception as e:
            print(f'  ERROR: {type(e).__name__}: {e}', file=sys.stderr)
            continue
        elapsed = time.time() - t0
        winner_engine = (
            j['a_engine']
            if j.get('winner') == 'a'
            else j['b_engine']
            if j.get('winner') == 'b'
            else j.get('winner', '?')
        )
        record = {
            'pmcid': pmcid,
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'model': args.model,
            'project': args.project,
            'elapsed_s': round(elapsed, 1),
            **j,
            'winner_engine': winner_engine,
        }
        with out_path.open('a') as f:
            f.write(json.dumps(record) + '\n')

        n_diff = len(j.get('differences', []))
        print(
            f'  winner={winner_engine} ({n_diff} differences) in {elapsed:.1f}s — A={j["a_engine"]}, B={j["b_engine"]}',
            file=sys.stderr,
        )
        for d in j.get('differences', []):
            engine = j['a_engine'] if d['favor'] == 'a' else j['b_engine']
            print(f'    [+{engine}/{d["category"]}] {d["description"][:140]}', file=sys.stderr)

    return 0


if __name__ == '__main__':
    sys.exit(main())
