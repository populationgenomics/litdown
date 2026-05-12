#!/usr/bin/env python3
"""
build_grading_page.py — Generate a self-contained HTML grading page.

Embeds all rendered PNGs as data URIs.  Grades are stored in localStorage
and can be downloaded as a JSON file with the Export button.

Usage:
    python3 build_grading_page.py [--input grading.jsonl]
                                  [--output grading_page.html]
                                  [--limit N]
"""

import argparse
import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))
from test_mml import BASE_URL, fetch_cached, load_math_elem, render_latex

from jatsdown.mathml import mml_to_tex


def b64(data: bytes | None) -> str:
    if not data:
        return ''
    return 'data:image/png;base64,' + base64.b64encode(data).decode()


def build_page(records: list[dict], path: Path) -> None:
    # Build the JS data array — one entry per case.
    cases = []
    total = len(records)
    for i, r in enumerate(records):
        name = r['test'].split('/')[-1]
        print(f'  [{i + 1:4d}/{total}] {name}', end='\r', flush=True)

        # Re-run our converter fresh so images reflect the current code,
        # not the potentially stale LaTeX stored in the JSONL.
        mml_bytes = fetch_cached(f'{BASE_URL}/{r["test"]}.mml')
        if mml_bytes:
            math_elem = load_math_elem(mml_bytes)
            fresh_our_latex = mml_to_tex(math_elem).strip() if math_elem else r['our_latex']
        else:
            fresh_our_latex = r['our_latex']

        our_png = render_latex(fresh_our_latex)
        npm_png = render_latex(r['npm_latex']) if r.get('npm_latex') else None
        ref_png = fetch_cached(f'{BASE_URL}/{r["test"]}.png')

        cases.append(
            {
                'id': r['test'],
                'name': name,
                'gemini': r.get('outcome', ''),
                'npm_render_failed': r.get('npm_render_failed', False),
                'reasoning': r.get('reasoning', ''),
                'our_latex': fresh_our_latex,
                'npm_latex': r.get('npm_latex', ''),
                'ref_img': b64(ref_png),
                'our_img': b64(our_png),
                'npm_img': b64(npm_png),
            }
        )

    print()  # end \r line

    cases_json = json.dumps(cases, ensure_ascii=False)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>MathML grading</title>
<style>
* {{ box-sizing: border-box; }}
body {{ font: 14px/1.5 sans-serif; margin: 0; background: #f5f5f5; }}

#toolbar {{
  position: sticky; top: 0; z-index: 10;
  background: #222; color: #eee;
  padding: 8px 16px;
  display: flex; align-items: center; gap: 16px;
}}
#toolbar h1 {{ font-size: 14px; margin: 0; flex: 1; }}
#progress {{ font-size: 12px; color: #aaa; }}
#export-btn {{
  background: #4a9; color: #fff; border: none;
  padding: 5px 12px; border-radius: 3px; cursor: pointer; font-size: 13px;
}}

#cases {{ padding: 12px 16px; }}

.case {{
  background: #fff; border-radius: 6px;
  margin-bottom: 16px; padding: 16px;
  box-shadow: 0 1px 3px rgba(0,0,0,.08);
}}
.case-header {{
  display: flex; align-items: baseline; gap: 10px; margin-bottom: 12px;
}}
.case-name {{ font-weight: bold; font-size: 13px; }}
.gemini-badge {{
  font-size: 11px; padding: 2px 7px; border-radius: 10px;
  background: #eee; color: #555;
}}
.gemini-badge.our_only  {{ background: #d4edda; color: #155724; }}
.gemini-badge.npm_only  {{ background: #f8d7da; color: #721c24; }}
.gemini-badge.both      {{ background: #cce5ff; color: #004085; }}
.gemini-badge.neither   {{ background: #fff3cd; color: #856404; }}
.reasoning {{ font-size: 11px; color: #666; margin-bottom: 12px; }}
.npm-fail-note {{ font-size: 11px; color: #999; font-style: italic; }}

.imgs {{
  display: flex; gap: 24px; align-items: flex-start;
  flex-wrap: wrap; margin-bottom: 12px;
}}
.img-col {{ display: flex; flex-direction: column; align-items: flex-start; }}
.img-label {{ font-size: 11px; color: #888; margin-bottom: 6px; text-transform: uppercase; letter-spacing: .04em; }}
.img-col img {{ max-height: 72px; display: block; }}
.img-col .no-render {{ font-size: 11px; color: #c00; font-style: italic; }}

/* Clickable image wrapper */
.img-toggle {{
  position: relative; cursor: pointer; display: inline-block;
  border-radius: 4px; padding: 4px;
  border: 2px solid transparent;
  transition: border-color .1s;
  user-select: none;
}}
.img-toggle:hover {{ border-color: #bbb; }}
.img-toggle.ticked {{ border-color: #28a745; background: #f0faf2; }}
.img-toggle .tick {{
  display: none; position: absolute; top: -8px; right: -8px;
  width: 20px; height: 20px; border-radius: 50%;
  background: #28a745; color: #fff;
  font-size: 12px; line-height: 20px; text-align: center;
  box-shadow: 0 1px 3px rgba(0,0,0,.3);
}}
.img-toggle.ticked .tick {{ display: block; }}

.outcome-label {{
  font-size: 11px; font-weight: bold; margin-top: 6px; min-height: 16px;
}}
.outcome-label.our_only {{ color: #155724; }}
.outcome-label.npm_only {{ color: #721c24; }}
.outcome-label.both     {{ color: #004085; }}
.outcome-label.neither  {{ color: #856404; }}

details {{ margin-top: 8px; }}
summary {{ font-size: 11px; color: #999; cursor: pointer; user-select: none; }}
pre.latex {{ font-size: 10px; font-family: monospace; white-space: pre-wrap;
             word-break: break-all; margin: 4px 0 0; color: #444;
             max-height: 80px; overflow-y: auto; }}

.comment-row {{ margin-top: 8px; }}
.comment-box {{
  width: 100%; max-width: 560px;
  font: 12px/1.4 sans-serif; color: #333;
  border: 1px solid #ddd; border-radius: 4px;
  padding: 5px 8px; resize: vertical;
  min-height: 32px; height: 32px;
  background: #fafafa;
}}
.comment-box:focus {{ outline: none; border-color: #aaa; background: #fff; }}
</style>
</head>
<body>

<div id="toolbar">
  <h1>MathML grading</h1>
  <span id="progress"></span>
  <button id="export-btn" onclick="exportGrades()">Download grades.json</button>
</div>

<div id="cases"></div>

<script>
const CASES = {cases_json};
const STORAGE_KEY = "mml_grades";

function loadGrades() {{
  try {{ return JSON.parse(localStorage.getItem(STORAGE_KEY) || "{{}}"); }}
  catch {{ return {{}}; }}
}}
function saveGrade(id, outcome) {{
  const g = loadGrades();
  if (!g[id]) g[id] = {{}};
  g[id].outcome = outcome;
  localStorage.setItem(STORAGE_KEY, JSON.stringify(g));
  updateProgress();
}}
function saveComment(id, comment) {{
  const g = loadGrades();
  if (!g[id]) g[id] = {{}};
  g[id].comment = comment;
  localStorage.setItem(STORAGE_KEY, JSON.stringify(g));
}}

function updateProgress() {{
  const g = loadGrades();
  const graded = Object.values(g).filter(v =>
    typeof v === "string" ? v : v.outcome
  ).length;
  document.getElementById("progress").textContent =
    graded + " / " + CASES.length + " graded";
}}

function exportGrades() {{
  const g = loadGrades();
  const out = CASES.map(c => {{
    const entry = g[c.id] || {{}};
    const result = {{
      test:    c.id,
      outcome: entry.outcome ?? c.gemini,
      human:   !!entry.outcome,
      gemini:  c.gemini,
    }};
    if (entry.comment) result.comment = entry.comment;
    return result;
  }});
  const blob = new Blob([JSON.stringify(out, null, 2)], {{type: "application/json"}});
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "grades.json";
  a.click();
}}

const OUTCOMES = ["our_only", "npm_only", "both", "neither"];
const LABELS   = {{
  our_only: "Ours ✓",
  npm_only: "npm ✓",
  both:     "Both ✓",
  neither:  "Neither ✓",
}};

const OUTCOME_LABELS = {{
  our_only: "Ours correct",
  npm_only: "npm correct",
  both:     "Both correct",
  neither:  "Neither correct",
}};

function inferOutcome(ourTicked, npmTicked) {{
  if (ourTicked && npmTicked)  return "both";
  if (ourTicked)               return "our_only";
  if (npmTicked)               return "npm_only";
  return "neither";
}}

function toggleTick(id, which, el) {{
  el.classList.toggle("ticked");
  const caseEl = el.closest(".case");
  const ourTicked = !!caseEl.querySelector(".img-toggle[data-which='our']").classList.contains("ticked");
  const npmTicked = !!caseEl.querySelector(".img-toggle[data-which='npm']").classList.contains("ticked");
  const outcome = inferOutcome(ourTicked, npmTicked);
  saveGrade(id, outcome);
  const lbl = caseEl.querySelector(".outcome-label");
  lbl.textContent = OUTCOME_LABELS[outcome];
  lbl.className = "outcome-label " + outcome;
}}

function render() {{
  const grades = loadGrades();
  // grades[id] is now {{outcome, comment}} — handle legacy string format too.
  function getOutcome(id) {{
    const v = grades[id];
    if (!v) return null;
    return typeof v === "string" ? v : v.outcome ?? null;
  }}
  function getComment(id) {{
    const v = grades[id];
    if (!v || typeof v === "string") return "";
    return v.comment ?? "";
  }}
  const container = document.getElementById("cases");
  container.innerHTML = "";

  CASES.forEach(c => {{
    const current = getOutcome(c.id);
    const comment = getComment(c.id);

    const ourTicked = current === "our_only" || current === "both";
    const npmTicked = current === "npm_only" || current === "both";

    function imgToggle(which, imgSrc, ticked) {{
      const tickedCls = ticked ? " ticked" : "";
      const inner = imgSrc
        ? `<img src="${{imgSrc}}" alt="${{which}}">`
        : `<span class="no-render">render fail</span>`;
      return `<div class="img-toggle${{tickedCls}}" data-which="${{which}}"
                   onclick="toggleTick('${{c.id}}','${{which}}',this)">
                ${{inner}}<div class="tick">✓</div>
              </div>`;
    }}

    const refInner = c.ref_img
      ? `<img src="${{c.ref_img}}" alt="reference">`
      : `<span class="no-render">no ref</span>`;

    const npmNote = c.npm_render_failed
      ? `<span class="npm-fail-note">(npm failed to compile)</span>` : "";

    const outcomeLbl = current
      ? `<span class="outcome-label ${{current}}">${{OUTCOME_LABELS[current]}}</span>`
      : `<span class="outcome-label"></span>`;

    const div = document.createElement("div");
    div.className = "case";
    div.id = "case-" + CSS.escape(c.id);
    div.innerHTML = `
      <div class="case-header">
        <span class="case-name">${{c.name}}</span>
        <span class="gemini-badge ${{c.gemini}}">Gemini: ${{c.gemini || "—"}}</span>
        ${{npmNote}}
      </div>
      <div class="reasoning">${{c.reasoning || ""}}</div>
      <div class="imgs">
        <div class="img-col">
          <div class="img-label">Reference</div>
          ${{refInner}}
        </div>
        <div class="img-col">
          <div class="img-label">Ours</div>
          ${{imgToggle("our", c.our_img, ourTicked)}}
        </div>
        <div class="img-col">
          <div class="img-label">npm</div>
          ${{imgToggle("npm", c.npm_img, npmTicked)}}
        </div>
      </div>
      ${{outcomeLbl}}
      <div class="comment-row">
        <textarea class="comment-box" placeholder="Comment (optional)"
          oninput="saveComment('${{c.id}}', this.value)"
        >${{comment}}</textarea>
      </div>
      <details>
        <summary>LaTeX source</summary>
        <pre class="latex">Ours: ${{c.our_latex}}\n\nnpm:  ${{c.npm_latex}}</pre>
      </details>
    `;
    container.appendChild(div);
  }});

  updateProgress();
}}

render();
</script>
</body>
</html>
"""
    path.write_text(html)
    print(f'Written {path}  ({len(records)} cases)')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--input', default='grading.jsonl')
    ap.add_argument('--output', default='grading_page.html')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument(
        '--filter',
        choices=['all', 'npm_only', 'neither', 'failures'],
        default='all',
        help='all=everything, failures=npm_only+neither',
    )
    args = ap.parse_args()

    records = [json.loads(line) for line in Path(args.input).read_text().splitlines() if line.strip()]

    if args.filter == 'npm_only':
        records = [r for r in records if r.get('outcome') == 'npm_only']
    elif args.filter == 'neither':
        records = [r for r in records if r.get('outcome') == 'neither']
    elif args.filter == 'failures':
        records = [r for r in records if r.get('outcome') in ('npm_only', 'neither')]

    if args.limit:
        records = records[: args.limit]

    print(f'Building page for {len(records)} cases…')
    build_page(records, Path(args.output))


if __name__ == '__main__':
    main()
