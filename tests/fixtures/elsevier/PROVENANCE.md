# Elsevier fixture provenance

All fixtures here are **CC-BY 4.0** (`openaccessUserLicense` ends in
`/by/4.0/`) and therefore freely redistributable — unlike the PMC JATS
fixtures (gitignored, fetched on demand). Each was retrieved from the
ScienceDirect Article Retrieval API
(`GET https://api.elsevier.com/content/article/doi/{doi}`,
`Accept: text/xml`) and is committed verbatim.

`tests/test_elsevier_articles.py` discovers every `*.xml` here and asserts
structural invariants; `test_fixture_is_ccby` re-checks the licence on each.

The set was curated from a coverage probe over ~185 CC-BY articles across 13
gold-OA Elsevier journals (physics, engineering, CS, medicine, neuroscience,
social science, data) to span the structural variants the dialect handles.
Each fixture below guards a distinct path.

| File | DOI | Journal | Exercises |
|---|---|---|---|
| `heliyon_2026_e45068.xml` | 10.1016/j.heliyon.2026.e45068 | Heliyon | smoke: general article, CALS tables w/ footnotes, authors/affiliations |
| `datainbrief_2026_112937.xml` | 10.1016/j.dib.2026.112937 | Data in Brief | math-heavy (~51 `<math>`), display formulas, multi-`tgroup` tables, lists, e-component |
| `resultsinphysics_2026_108706.xml` | 10.1016/j.rinp.2026.108706 | Results in Physics | math (~40), `<further-reading>` reference list |
| `energyreports_2026_109401.xml` | 10.1016/j.egyr.2026.109401 | Energy Reports | `<nomenclature>`/`def-list`, 7-`tgroup` multi-part tables |
| `chbreports_2026_101064.xml` | 10.1016/j.chbr.2026.101064 | Computers in Human Behavior Reports | `<enunciation>` (theorems), e-component |
| `methodsx_2026_103952.xml` | 10.1016/j.mex.2026.103952 | MethodsX | `<textbox>` boxed callout (Algorithm), e-component |
| `patterns_2026_101540.xml` | 10.1016/j.patter.2026.101540 | Patterns | `<simple-article>` (Cell Press), graphical abstract, author `<biography>` figures |
| `heliyon_2026_e45072.xml` | 10.1016/j.heliyon.2026.e45072 | Heliyon | body `<footnote>`s → trailing Notes section |
| `openceramics_2026_100996.xml` | 10.1016/j.oceram.2026.100996 | Open Ceramics | `<chem>` reaction equations (non-MathML formulas) |
| `onehealth_2015_08_001.xml` | 10.1016/j.onehlt.2015.08.001 | One Health | `<displayed-quote>` block quotes (older article) |
| `fundamentalresearch_2026_04_017.xml` | 10.1016/j.fmre.2026.04.017 | Fundamental Research | `<simple-article>`, `<article-footnote>`, author `<biography>` |

`other-ref`/`textref` free-text references are exercised by
`patterns_2026_101540.xml`. One path has **no** CC-BY ≤500 KB exemplar and
so is covered only by the harvest probe, not a committed fixture:
`<glossary>` abbreviation lists.

## Coverage validation

The dialect was validated by a harvest probe over **~1000 CC-BY articles
across 33 journal families** (physics, engineering, chemistry, materials,
CS, medicine, neuroscience, social science, geoscience, data, protocols,
Cell Press / Lancet families). Successive *fresh-journal* batches converged
to zero structural-invariant failures, and a tag census over the whole
corpus confirmed no content-bearing element type is silently dropped. The
fixtures above are the curated regression guards distilled from that probe;
each was chosen because it is the smallest CC-BY exemplar of a distinct
structural path.
