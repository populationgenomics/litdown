# jatsdown

[![Lint](https://github.com/populationgenomics/jatsdown/actions/workflows/lint.yaml/badge.svg)](https://github.com/populationgenomics/jatsdown/actions/workflows/lint.yaml)

Convert JATS XML articles (the format PubMed Central distributes) to Markdown
with embedded LaTeX for inline and display math.

The intended consumer is downstream LLM tooling — the markdown is plain text
suitable for retrieval, summarisation, or analysis without round-tripping
through a typesetter.

## Spec target

jatsdown is implemented against the **JATS Journal Archiving and Interchange
Tag Set (Archiving), NISO Z39.96-2024 v1.4** — the format PMC distributes. PMC
upconverts older content (NLM Archiving 1.x–3.x, JATS 1.0–1.3) into 1.4 when
serving the OA bucket, so a converter that handles 1.4 covers the entire PMC
corpus regardless of when the article was authored.

This is **not** the Article Authoring tag set (more restrictive; intended as
an authoring target, not a corpus). Article-Authoring-only content is a
subset of Archiving content and works without code changes.

## Install

```bash
pip install -e .              # runtime
pip install -e '.[dev]'       # runtime + pytest
pip install -r requirements-dev.txt && pre-commit install   # contributing
```

Editable install. Provides a `jatsdown` console script.

## Use

CLI:

```bash
jatsdown article.xml > article.md
jatsdown article.xml article.md
```

Library:

```python
from jatsdown import convert, mml_to_tex, render_mathml

md = convert("article.xml")           # JATS XML path → markdown string
latex = mml_to_tex(math_element)      # MathML Element → LaTeX
fragment = render_mathml(math_element, display=True)  # → "$$...$$"
```

## What's in the package

```text
jatsdown/
  jats.py    JATS XML → Markdown
  mathml.py  MathML → LaTeX
```

The MathML converter is the more battle-tested piece — it has been graded
against the W3C MathML 3 Presentation test suite using both Pandoc and a
Gemini blind-grading harness. The cases that survived grading are checked
in under `tests/w3c_mml/` with their expected LaTeX in `tests/golden.json`;
the regression suite re-runs the converter over them on every test run.

## Tests and fixtures

```bash
pytest                                # full suite
```

Two test files:

- `tests/test_mml_unit.py` — exhaustive per-element MathML cases.
- `tests/test_jats_articles.py` — structural assertions over real PMC
  articles in `tests/fixtures/<PMCID>/`, parametrised so adding a fixture
  extends the suite automatically. Known per-fixture defects are
  xfail-marked in a `KNOWN_BUGS` dict so the suite stays green; when a fix
  lands the xfail flips to "unexpectedly passed" and forces the entry's
  removal.

### Fetching test fixtures

PMC articles are not redistributed in this repository — each article has
its own licence (a mix of CC-BY, CC-BY-NC variants, and others), and the
publisher PDFs in particular carry more restrictive terms. The fixture
directories are gitignored. To populate them:

```bash
python tools/fetch_pmc.py --manifest tests/fixtures/MANIFEST.txt
```

This reads `tests/fixtures/MANIFEST.txt` (one PMCID per line), pulls each
article's JATS XML, publisher PDF, plain text, and referenced figure
assets from the public `pmc-oa-opendata` S3 bucket, and caches them under
`tests/fixtures/<PMCID>/`. Fetches are idempotent; re-running is cheap.

The article-fixture tests skip cleanly when no fixtures are present, so
`pytest` works against the MathML unit suite alone.

## tools/

Discovery and evaluation utilities — none are imported by the package or
needed for normal use.

| Script | Purpose |
|---|---|
| `fetch_pmc.py` | Cache a PMCID's JATS XML, publisher PDF, plain text and figure assets into `tests/fixtures/<PMCID>/`. Default `core` mode skips supplementary materials; pass `--all` to include them. |
| `eval_articles.py` | Send fixture PDF + our markdown to Vertex AI Gemini and ask it to enumerate content-fidelity gaps. Findings appended to `eval_findings.jsonl`. Run ad-hoc, not in CI. Requires `JATSDOWN_GCP_PROJECT` env var or `--project`. |
| `test_mml.py` | Run our MathML converter against the W3C test suite and against the npm `mathml-to-latex` package; produce a per-test report. |
| `grade_mml.py` | Blind A/B grade MathML disagreements against the W3C reference using Gemini. |
| `build_grading_page.py`, `build_preview_page.py` | Build self-contained HTML pages for human review of the grading runs. |
| `mml2tex_shim.js` | Node entry point used by `test_mml.py` to call the npm `mathml-to-latex` library. |

## The discovery loop

```text
        fetch_pmc.py            (acquire fixture)
              ↓
        jatsdown.convert
              ↓
        eval_articles.py        (Gemini reads PDF + our markdown)
              ↓
        triage findings         → encode each as a structural test
              ↓                    → fix the converter
        re-run, repeat
```

The structural test suite is the regression net (deterministic, runs in
CI). LLM eval is the discovery tool (non-deterministic, runs ad-hoc). Each
real defect the eval surfaces should be added to
`tests/test_jats_articles.py` once fixed, so it can never silently regress.

## Known limitations

- Tables typeset as images (older PLOS Genetics, BMJ, etc.) cannot be
  reconstructed as markdown tables — the converter falls back to an image
  link so content isn't lost, but downstream tools won't get structured
  data without an OCR step.
- The consortium author rendering for papers like gnomAD (PMC7334197)
  emits the consortium *name* only; individual members listed in nested
  `<contrib-group>` are dropped.
- Some end-of-article metadata sections (Author contributions, Competing
  interests, Funding, Data availability) live inside `<fn-group>` or
  `<notes>` in `<back>`; these aren't currently rendered.
- Soft hyphens / line-break artefacts in source XML are not normalised,
  so words split across lines in the JATS source can render with stray
  spaces ("si milarity").
