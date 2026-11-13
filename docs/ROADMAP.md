# MDQ Library — Roadmap

Updated with every commit. Estimates are in sessions (one session = one feature cycle: Rust, shim check, tests,
commit message), rough, about ±50%. Feature definitions and tests: `docs/TEST_PLAN.md` section 4.

**Last update:** E6.4 built natively (4310 passed, 1 hook fix pending rerun); E4.3–E6.3 built green natively.

## Status

| Epic | Status | Features | Commit | Sessions left |
|---|---|---|---|---|
| 0 Foundations | done | scaffold, versions, diagnostics, panic watchdog | 88d6173 | 0 |
| 1 Calc (F11–F15) | done | E1.1–E1.7 decimal, units, lexer, parser, evaluator, FX, rendering | 88d6173 (E1.1–E1.5), e2f5aa1, 95c7f90 | 0 |
| 2 Metadata and variables | done | E2.1 frontmatter, E2.2 YAML, E2.3 merge/cascade, E2.4 interpolation and escaping | 8962058, 1d797a0, a4cfc4b, 6f26fe2 | 0 |
| 3 Structure and containers | done | E3.1 UTF-8 ✔ (59509ac), E3.2 block parser and directive scan ✔ (2a00403), E3.3 `attrs_parse`/`container_parse` ✔ (ae0f097), E3.4 `container_eval` ✔ (this commit) | | 0 |
| Build | done | Rust of E1.6–E3.2 compiles; all tests pass | | 0 |
| 4 Composition and references (F04–F07) | done | E4.1 paths and confinement ✔, E4.2 heading text/slugs/dedupe ✔, E4.3 block IDs and `resolve_ref` ✔, E4.4 import expansion ✔, E4.5 link rebasing ✔ | | 0 |
| 5 Rich features and export (F10, F23) | done (shim; E5.3–E5.4 Rust not compiled) | E5.1 highlight/callout/details/columns ✔, E5.2a math/footnotes ✔, E5.2b citations ✔, E5.3 TOC ✔, E5.4 `export_markdown` ✔ (shim; Rust not compiled) | | 0 |
| 6 Sources and policy (F16–F19) | in progress | E6.1 declarations and URL templates ✔, E6.2 JSON Pointer and types ✔ (shim; Rust not compiled), E6.3 URL/IP classification ✔ (shim; Rust not compiled), E6.4 policy ✔ (shim; Rust not compiled), E6.5 resolver and fake transport | | 2.5 |
| 7 Packages and encryption (F20, F21) | not started | E7.1 hostile-zip reader, E7.2 manifest, E7.3 age | | 2.5 |
| 8 API, CLI, packaging (F25, F26) | not started | E8.1 `compile`, E8.2 typing/threads/memory, E8.3 CLI, E8.4 wheel smoke test | | 6 |
| Cross-cutting | open | rule ledger and conformance fixtures, CI (incl. `HYPOTHESIS_PROFILE=ci`), Rust coverage, sdist fix (`include_str!("../../../spec/units.tsv")`) | | 3 |
| **Total** | | | | **about 18** |

At the pace so far (12 commits), 36 sessions is roughly 6–9 working days, not weeks; build-error rounds add to that.

## Open decisions

- G10 resolved in G44 (CGNAT blocked)
- G16: ISO 4217 list or any 3-capital currency code (blocks E6)
- G24 / G25: offered for veto
- Per-member encryption layout (spec gap 4): xfail in Epic 7
- MDZip (F22): deferred by the spec

## Next

1. E6.5 resolver and fake transport (`test_source`, snapshots; first feature that needs a callable transport). G16 still open.
