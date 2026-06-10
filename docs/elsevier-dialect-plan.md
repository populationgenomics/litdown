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

### What de-risks the work (and one thing that doesn't)

1. **Math is standard W3C MathML** — `<math>` in
   `http://www.w3.org/1998/Math/MathML` with ordinary `mn`/`mo`/`mfrac`
   children, wrapped in `ce:formula` (display) / `ce:inline-formula`
   (inline), plus an `altimg` SVG fallback to ignore. litdown's existing
   `mathml.render_mathml` consumes a `<math>` element directly. **Reuse it
   verbatim** — do not drop math. NB the vendored `heliyon` fixture has only
   **one** `<math>`, so the math path is only genuinely validated against the
   math-saturated `rinp` fixture (see Fixtures).
2. **References ship a pre-rendered string.** Each `ce:bib-reference` has a
   structured `sb:reference` (Siemens model: `sb:contribution`/`sb:host`/
   `sb:authors`/`sb:title`/`sb:date`...) *and* a `ce:source-text` (the
   publisher's rendered citation). Parse the **`sb:` structured model as
   primary** (consistent with the JATS field-by-field assembler; we don't
   control `source-text` and some journals may be pathological about it),
   falling back to `ce:source-text` only when the structured parse is empty.
3. **CORRECTION — tables are CALS, and JATS does *not* share a renderer.**
   `litdown/jats.py`'s `render_table` handles the **XHTML** table model
   (`thead/tbody/tr/td/th`); there is **no CALS renderer** in the codebase.
   The Elsevier fixture is genuinely CALS (`tgroup`/`colspec`/`row`/`entry`,
   `namest`/`nameend` colspans, `morerows` rowspans). A CALS renderer must be
   **written from scratch**: translate CALS spanning into
   `(content, colspan, rowspan)` integers, then hand off to the
   `expand_rows`/`pad`/header-collapse logic extracted from `jats.py` into the
   shared module. Reuse the *grid logic*, not `render_table` itself.

## Design

`convert(xml_path)` stays **path-based** — no bytes entry point; the
consumer is responsible for getting the response onto a path. **Match on
local tag names** throughout (strip the namespace) — Elsevier mixes
`ja:`/`ce:`/`xocs:`/`sb:` prefixes freely; structure matters, not prefix.

1. **Dispatch in `convert`.** Sniff the **root local-name** (cheap, single
   parse): `article` → existing `litdown.jats` path;
   `full-text-retrieval-response` → new `litdown.elsevier` path; **anything
   else raises `ValueError(f'unrecognized root element: {root.tag}')`**
   (silent `''` masks "wrong bytes" bugs in the consumer — the exact failure
   that motivated this work). Namespace-presence sniffing is unnecessary: the
   Article Retrieval API always returns the envelope as document root.

2. **Shared module `litdown/common.py`.** Extract dialect-neutral leaves both
   dialects import — `get_tag`/`_local`, `xlink_href`, `md_escape_cell`, the
   table **grid builder** (`expand_rows`/`pad`/header-collapse, operating on
   normalized `(content, colspan, rowspan)` rows), and the inline **leaf
   formatters** (`*…*`, `**…**`, `<sup>`/`<sub>` wrapping). `mathml.py` is
   already shared. Do **not** unify `inline_to_md` into one config-driven
   function — the cross-ref/link attribute divergence makes that more tangled
   than it's worth; each dialect keeps its own inline dispatcher calling the
   shared leaves.

3. **New module `litdown/elsevier.py`.** Locate `ja:article` under
   `originalText/xocs:doc/xocs:serial-item`; render with **content-parity to
   the JATS output shape** (small divergences OK where Elsevier lacks the
   data):
   - **Title** `head/title` → H1 (fall back to `coredata/dc:title`).
   - **Authors + affiliations** from `head/author-group` (`ce:author` →
     `ce:given-name`/`ce:surname`; affiliation superscripts from
     `ce:cross-ref` → `ce:affiliation`), mirroring the JATS superscript
     convention; `ce:correspondence` for the corresponding author.
   - **Metadata block** sourced from `coredata` (cleaner than `head`):
     `prism:publicationName`, DOI, volume/issue/pages, cover date,
     license/openaccess.
   - **Keywords** from `head/keywords`.
   - **Abstract** `## Abstract`; structured `abstract-sec` → bold sub-label +
     `simple-para` (mirrors `jats.render_abstract`).
   - **Body** `ce:sections` recursion: top-level `ce:section` → H2, each
     nesting level +1; heading = `ce:label` + `ce:section-title`
     (`## 1 Introduction`, `### 2.1 …`); `<a id>` anchor per section with an
     id; `ce:para`/`ce:simple-para` → paragraphs; `ce:list` → markdown list.
   - **Body trailing blocks** as H2 sections after `ce:sections`, in order:
     `data-availability` (pulled down from `head`), `acknowledgment`,
     `conflict-of-interest`, `appendices` (each `ce:section` via the normal
     recursion). Unnumbered trailing sections inside `ce:sections`
     (CRediT/Ethics/Funding) render with bare titles.
   - **References** from `tail/bibliography`: `sb:` structured model primary
     (authors → title → host/journal/volume/issue/pages → year, matching the
     JATS citation string shape), `ce:source-text` fallback; `ce:label`
     prefix; `<a id="{refid}">` anchor; DOI appended as a link.

4. **Floats — at-anchor placement.** Floats live in `article/floats`
   (`figure`/`table`, each with an `id`); body prose carries empty
   `<ce:float-anchor refid="…"/>` markers. Render each float's full content
   (with its `<a id>`) **at the first `ce:float-anchor` that references it**,
   tracking rendered ids in a `set`; append any never-anchored float in a
   trailing block. Keeps tables next to their discussion for the LLM reader.

5. **CALS tables.** `_render_cals_table` in `elsevier.py`: `tgroup` →
   `colspec` (column count + `colname`); `thead`/`tbody` → `row`/`entry`;
   translate `namest`/`nameend` (via colspec names) → colspan and `morerows`
   (0-based) → rowspan; hand the normalized rows to the shared grid builder.

6. **Math wiring.** `ce:formula` (block) → `<a id>` anchor +
   `render_mathml(math, display=True)` + trailing `ce:label`;
   `ce:inline-formula` handled in Elsevier `inline_to_md` →
   `render_mathml(math, display=False)`. If no `{…MathML}math` descendant is
   present, fall back to the `altimg` href as an image link (same graphic
   fallback as `jats.render_formula`) rather than dropping it.

7. **Cross-refs / links.** `ce:cross-ref` → `[{inner}](#{refid})` uniformly
   (every target — bib/float/section/equation/footnote — gets an anchor, so
   no `ref-type` classification needed). `ce:inter-ref` with an
   `http(s)/ftp` `xlink:href` → `[{inner or href}]({href})`; no
   accession-resolver table (Elsevier `inter-ref` is plain URLs).

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

The math-saturated `rinp` fixture is **essential** to prove the math path.
At 0.7–1.3 MB it trips `check-added-large-files` (500 KB), but that hook can
be pushed past (`--no-verify` / `SKIP=check-added-large-files`), so vendor
the full file if it's the cleanest option; trimming to a representative
<500 KB subtree (a dense block of `<math>` + a CALS table + a list) is a
nicety, not a requirement. Vendor only `by/4.0` articles — assert
`coredata/openaccessUserLicense` (or `oa-user-license`) ends in `/by/4.0/`
before committing any fixture; reject `by-nc`/`by-nc-nd`/`by-nc-sa`.

Test pattern: **mirror the structural-invariant style of
`tests/test_jats_articles.py`, not a golden file.** (Note `tests/golden.json`
and `regenerate_golden.py` are the *MathML* unit golden consumed by
`test_mml_unit.py` — unrelated to the article harness, which deliberately
asserts invariants "not a golden-file diff.") Add a new
`tests/test_elsevier_articles.py` that discovers the committed flat `*.xml`
under `tests/fixtures/elsevier/` (the JATS discovery globs per-subdirectory
`{dir}.*.xml` and won't find them) and asserts, over Elsevier local-names:
starts with an H1; no `{http(s)://` namespace leak; every `ce:bib-reference`
refid anchored; every float `id` anchored; **math not dropped** (count
`<math>` in source ≤ count of `$`/`$$` spans out); CALS tables emit `| ---`
syntax; `ce:cross-ref` targets resolve to an emitted anchor. No
`golden_elsevier.json`.

## Definition of Done

- `litdown/elsevier.py` implements the dialect; `convert` dispatches on
  root local-name and raises on an unknown root; JATS path unchanged.
- `litdown/common.py` holds the extracted shared leaves (tag helpers,
  grid builder, inline leaf formatters); both dialects import it.
- Math rendered via `render_mathml` (not dropped); CALS tables rendered via
  a net-new `_render_cals_table` sharing only the grid logic.
- Front-matter parity with JATS (authors/affiliations/metadata/keywords/
  abstract); references via `sb:` model; floats placed at-anchor.
- CC-BY (`by/4.0`) fixtures committed (math fixture trimmed <500 KB) +
  `tests/test_elsevier_articles.py` structural invariants green.
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
