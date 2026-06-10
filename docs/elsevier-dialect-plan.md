# Plan: Elsevier `ce:`/`ja:` dialect for litdown

Handoff plan for adding an **Elsevier full-text XML → Markdown** dialect to
litdown. Written 2026-06-10 after an investigation in the `pubmedifier`
repo. Scope here is **litdown only** (bytes → markdown); the consumer that
fetches Elsevier XML lives elsewhere (see "Out of scope").

## Why

litdown was renamed from `jatsdown` and widened to "scholarly full-text XML
→ markdown" precisely so it can host more than the JATS dialect. The
Elsevier (ScienceDirect) Article Retrieval API returns full text in
Elsevier's own `xocs`/`ja`/`ce` schema, **not JATS** — `litdown.convert`
(JATS path) currently yields an empty string on it. This dialect closes
that gap.

Public API to preserve: `litdown.convert`, `litdown.mathml.mml_to_tex`,
`litdown.mathml.render_mathml`.

## The Elsevier XML format (what `convert` will receive)

Root element: `full-text-retrieval-response` in namespace
`http://www.elsevier.com/xml/svapi/article/dtd` (the SVAPI response
envelope). Relevant children:

- `coredata` — Dublin Core / PRISM metadata: `dc:title`, `dc:description`
  (abstract), `openaccess`, `openaccessUserLicense`, DOI, etc.
- `originalText` → `xocs:doc` → `xocs:serial-item` → `ja:article` →
  `{item-info, head, body, tail}`.

Body structure (this is what to render):

```text
ja:body
└─ ce:sections
   └─ ce:section            (nestable)
      ├─ ce:section-title
      ├─ ce:para
      ├─ ce:list → ce:list-item
      ├─ ce:formula / ce:inline-formula   → MathML
      └─ ce:float-anchor    (figures/tables; floats live in ja:floats)
```

Inline elements inside `ce:para`: `ce:italic`, `ce:bold`, `ce:sup`,
`ce:inf`, `ce:cross-ref`, `ce:inter-ref`, etc.

Namespaces / schemas (authoritative element lists):

| Concern | Namespace | Schema file |
|---|---|---|
| `ce:` content model (sections/para/list/formula/...) | `http://www.elsevier.com/xml/common/dtd` | `common170/common170.ent.xsd` |
| `ja:` journal-article structure | `http://www.elsevier.com/xml/ja/dtd` | `serial570/JA.xsd` (DTD 5.7; `serial520` = 5.2) |
| Math | `http://www.w3.org/1998/Math/MathML` (standard **MathML 3**) | `common170/mathml3*.xsd` |
| Tables | `http://www.elsevier.com/xml/common/cals/dtd` (+ `tb`) | `common170/tb.xsd` |

Schemas are live at `https://schema.elsevier.com/dtds/document/fulltext/xcr/`
— start from `xocs-article.xsd` and follow every `xs:import`/`xs:include`
(35 files total). Author docs:
<https://www.elsevier.com/authors/policies-and-guidelines/elsevier-xml-dtds-and-transport-schemas>.
The SVAPI envelope itself is **not** in that repo (it's the API gateway
wrapper) and isn't needed — just locate `originalText` and work below it.

### Two facts that de-risk the work

1. **Math is standard W3C MathML** — `<math>` in
   `http://www.w3.org/1998/Math/MathML` with ordinary `mn`/`mo`/`mfrac`
   children, wrapped in `ce:formula` (display) / `ce:inline-formula`
   (inline), plus an `altimg` SVG fallback to ignore. litdown's existing
   `mathml.render_mathml` consumes a `<math>` element directly and was
   verified on real Elsevier math: it produces `$3\times3$` etc. **Reuse it
   verbatim** — do not drop math.
2. **Tables are CALS** — the same model JATS uses. Check `litdown/jats.py`
   for a CALS renderer to share rather than reimplement.

## Design

1. **Dispatch in `convert`.** Sniff the root element + namespace:
   - JATS `<article>` → existing `litdown.jats` path.
   - Elsevier `full-text-retrieval-response`, or presence of the `xocs`/`ja`
     namespaces → new `litdown.elsevier` path.
   Keep the sniff cheap (root tag + namespace map from a single parse).
2. **New module `litdown/elsevier.py`.** Locate `ja:article` under
   `originalText`; render: title (`coredata/dc:title`, fall back to
   `ce:title`), abstract (`ce:abstract`), body (`ce:sections` recursion →
   headings by nesting depth), and optionally references from `tail`.
   **Match on local tag names** (strip the namespace) — Elsevier mixes
   `ja:`/`ce:`/`xocs:` prefixes freely; structure matters, not prefix.
3. **Share renderers.** `mathml.py` is already dialect-neutral. If
   `jats.py`'s inline-emphasis / list / CALS-table helpers are cleanly
   extractable, factor them into a shared module both dialects import;
   otherwise duplicate the small bits rather than over-abstract.
4. **Math wiring.** For each `ce:formula` / `ce:inline-formula`, find the
   `{http://www.w3.org/1998/Math/MathML}math` descendant and call
   `render_mathml(math_el, display=<formula is block?>)`.

## Reference prototype

A throwaway prototype proved the structure walk on real fixtures (Heliyon
44 KB, Cell 33 KB — correct nested headings). It lives at
`pubmedifier/scripts/proto_elsevier_to_markdown.py`. **Caveats to fix when
productionising:** it (a) *drops* math/formulas — must delegate to
`render_mathml`; (b) has minimal abstract handling; (c) renders no tables.
Its core idea to keep: `_local(tag)` namespace-stripping + recursive
`ce:section` → headings by depth, `ce:para` → paragraph, `ce:list` →
markdown list, inline `ce:italic`/`ce:bold`.

## Fixtures & testing

One small CC-BY 4.0 smoke fixture is vendored under
`tests/fixtures/elsevier/`:

| File | Shape |
|---|---|
| `heliyon_2026_e45068.xml` | general research article (~340 KB) |

**You will need to generate the math/table/list-heavy fixtures yourself** —
the obvious candidates (a Results-in-Physics article with ~780 `<math>`, 5
CALS tables, 22 lists; a larger Heliyon) are 0.7–1.3 MB and trip
`check-added-large-files` (500 KB limit), so they aren't vendored. A
math-heavy fixture is essential for verifying the `render_mathml` wiring.

To generate fixtures use `pubmedifier/scripts/probe_elsevier_tdm.py` (needs
`ELSEVIER_API_KEY`; a personal dev key from dev.elsevier.com works): it
pulls DOIs from gold-OA Elsevier journals via Crossref (by ISSN), fetches
each through the Article Retrieval API
(`GET https://api.elsevier.com/content/article/doi/{doi}`, header
`X-ELS-APIKey`, `Accept: text/xml`), and keeps `coredata/openaccess=1`.
**Use only CC-BY (`by/4.0`) articles** — `by-nc`/`by-nc-nd` are not freely
redistributable. For ones too large to vendor, keep them in a local
scratch dir and point the golden harness at it, or trim to a representative
subtree. Across the original harvest the corpus spanned: sections 5–107,
MathML 0–1130, CALS tables 0–15, lists 0–22, 58 KB–1.3 MB.

Test pattern: mirror the JATS harness — `tests/test_jats_articles.py` +
`tests/golden.json` + `tests/regenerate_golden.py`. Either extend the
golden harness to the Elsevier fixtures or add a parallel
`golden_elsevier.json`. Assertions to cover: title, nested heading depth,
paragraphs, lists, inline emphasis, **MathML → LaTeX (present, not
dropped)**, cross-ref handling, CALS tables.

## Definition of Done

- `litdown/elsevier.py` implements the dialect; `convert` dispatches by
  schema sniff; JATS path unchanged.
- Math rendered via `render_mathml` (not dropped); CALS tables rendered
  (reusing the JATS path where possible).
- CC-BY fixtures + golden tests committed and green.
- `litdown/__init__.py` docstring updated ("Elsevier dialect to follow" →
  "supported") and README mentions both dialects.
- `pre-commit run --all-files` green (ruff check + ruff-format + mypy +
  hygiene). The repo enforces this in CI now.

## Out of scope (lives in `pubmedifier`, not litdown)

- Fetching Elsevier XML / the `ElsevierSource` full-text backend, per-user
  publisher credentials, rate-limit handling — tracked under RD-1015 **T6**
  (`pubmedifier/.jira-drafts/t6-elsevier-tdm-source.md`), gated on T5
  (per-user credential storage). That code will call `litdown.convert` on
  Article Retrieval responses; litdown only needs to convert the bytes.
- Investigation notes: `pubmedifier` memory `project_elsevier_tdm_investigation.md`.
