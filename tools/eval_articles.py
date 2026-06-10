"""Ask Gemini to spot content-fidelity gaps in our markdown vs the publisher PDF.

Discovery tool, not a regression test. For each fixture, sends the
publisher PDF + our markdown to a Vertex AI Gemini endpoint and asks
for distinct findings where content is missing, mis-ordered, or
misrepresented. Findings are appended to ``eval_findings.jsonl`` for
triage; encode each finding as a structural test in
``tests/test_jats_articles.py`` once the underlying bug is fixed.

Sends fixture PDFs and converted markdown to the chosen GCP project's
Vertex AI endpoint — pick a project where this is acceptable.

Usage:
    export LITDOWN_GCP_PROJECT=your-project
    python tools/eval_articles.py PMC60000 PMC1713260
    python tools/eval_articles.py --all --project your-project
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import google.genai as genai
import google.genai.types as gtypes

sys.path.insert(0, str(Path(__file__).parent.parent))
from litdown import convert

ROOT = Path(__file__).parent.parent
FIXTURES = ROOT / 'tests' / 'fixtures'
DEFAULT_OUT = ROOT / 'eval_findings.jsonl'

DEFAULT_MODEL = 'gemini-2.5-pro'
DEFAULT_LOCATION = 'us-central1'


def make_client(project: str, location: str) -> genai.Client:
    return genai.Client(vertexai=True, project=project, location=location)


PROMPT = """\
You are evaluating a JATS-XML to Markdown converter against the publisher's \
reference PDF for the same article.

You receive:
  1. The publisher PDF (input 1) — the reference rendering.
  2. The converter's markdown output (input 2, attached as text below).

Your task: enumerate distinct cases where SCIENTIFIC CONTENT in the PDF \
is MISSING, MIS-ORDERED, or MISREPRESENTED in the markdown.

Focus on content fidelity: paragraph text, equations (compare LaTeX \
semantics, not visual style), figures, tables (numeric values matter), \
captions, references, footnotes, section structure, author/affiliation \
lists.

IGNORE typographic differences: fonts, column count, page layout, exact \
figure placement on the page, line breaks, soft hyphens, exact whitespace.

Output a JSON object matching the supplied schema. An empty findings \
array means the markdown is content-faithful to the PDF.
"""

RESPONSE_SCHEMA = {
    'type': 'object',
    'properties': {
        'findings': {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {
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
                    'severity': {
                        'type': 'string',
                        'enum': ['high', 'medium', 'low'],
                    },
                    'location': {'type': 'string'},
                    'description': {'type': 'string'},
                    'evidence_pdf': {'type': 'string'},
                    'evidence_markdown': {'type': 'string'},
                },
                'required': [
                    'category',
                    'severity',
                    'location',
                    'description',
                ],
            },
        },
    },
    'required': ['findings'],
}


def evaluate(pmcid: str, *, client: genai.Client, model: str) -> dict:
    pmcid_dir = FIXTURES / pmcid
    if not pmcid_dir.is_dir():
        raise FileNotFoundError(f'no fixture at {pmcid_dir}')

    xml_path = next(iter(sorted(pmcid_dir.glob(f'{pmcid}.*.xml'))), None)
    pdf_path = next(iter(sorted(pmcid_dir.glob(f'{pmcid}.*.pdf'))), None)
    if xml_path is None:
        raise FileNotFoundError(f'no JATS XML in {pmcid_dir}')
    if pdf_path is None:
        raise FileNotFoundError(f'no publisher PDF in {pmcid_dir}')

    md = convert(str(xml_path))
    pdf_bytes = pdf_path.read_bytes()

    parts = [
        gtypes.Part.from_text(text=PROMPT),
        gtypes.Part.from_text(text='Input 1 — Publisher reference PDF:'),
        gtypes.Part.from_bytes(data=pdf_bytes, mime_type='application/pdf'),
        gtypes.Part.from_text(text='Input 2 — Converter markdown output:'),
        gtypes.Part.from_text(text=md),
    ]
    config = gtypes.GenerateContentConfig(
        response_mime_type='application/json',
        response_schema=RESPONSE_SCHEMA,
    )
    response = client.models.generate_content(
        model=model,
        contents=parts,
        config=config,
    )
    return json.loads(response.text)


def main() -> int:
    ap = argparse.ArgumentParser(
        description='Send fixture PDF + our markdown to Vertex AI Gemini and record content-fidelity findings.',
    )
    ap.add_argument('pmcids', nargs='*', help='Fixture PMCIDs to evaluate (e.g. PMC60000)')
    ap.add_argument('--all', action='store_true', help='Evaluate every fixture under tests/fixtures/')
    ap.add_argument(
        '--out', default=str(DEFAULT_OUT), help=f'JSONL file to append findings to (default: {DEFAULT_OUT.name})'
    )
    ap.add_argument(
        '--project',
        default=os.environ.get('LITDOWN_GCP_PROJECT'),
        help='GCP project for Vertex AI (env: LITDOWN_GCP_PROJECT)',
    )
    ap.add_argument(
        '--location',
        default=os.environ.get('LITDOWN_GCP_LOCATION', DEFAULT_LOCATION),
        help=f'Vertex AI region (default: {DEFAULT_LOCATION}, env: LITDOWN_GCP_LOCATION)',
    )
    ap.add_argument('--model', default=DEFAULT_MODEL, help=f'Gemini model (default: {DEFAULT_MODEL})')
    args = ap.parse_args()

    if not args.project:
        print(
            'no GCP project set; pass --project or set LITDOWN_GCP_PROJECT',
            file=sys.stderr,
        )
        return 2
    client = make_client(args.project, args.location)

    pmcids = sorted(d.name for d in FIXTURES.iterdir() if d.is_dir()) if args.all else args.pmcids
    if not pmcids:
        print('no PMCIDs given; pass --all or specific PMCIDs', file=sys.stderr)
        return 1

    out_path = Path(args.out)

    for pmcid in pmcids:
        print(f'Evaluating {pmcid}...', file=sys.stderr)
        t0 = time.time()
        try:
            result = evaluate(pmcid, client=client, model=args.model)
        except Exception as e:
            print(f'  ERROR: {type(e).__name__}: {e}', file=sys.stderr)
            continue
        elapsed = time.time() - t0
        findings = result.get('findings', [])
        record = {
            'pmcid': pmcid,
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'model': args.model,
            'project': args.project,
            'elapsed_s': round(elapsed, 1),
            'findings': findings,
        }
        with out_path.open('a') as f:
            f.write(json.dumps(record) + '\n')

        print(f'  {len(findings)} findings in {elapsed:.1f}s', file=sys.stderr)
        for finding in findings:
            sev = finding.get('severity', '?')
            cat = finding.get('category', '?')
            loc = finding.get('location', '')
            desc = finding.get('description', '')
            print(f'    [{sev}/{cat}] {loc}: {desc}', file=sys.stderr)

    return 0


if __name__ == '__main__':
    sys.exit(main())
