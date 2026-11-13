# MDQ Library — Roadmap

Updated with every commit. Estimates are in sessions (one session = one feature cycle: Rust, shim check, tests,
commit message), rough, about ±50%. Feature definitions and tests: `docs/TEST_PLAN.md` section 4.

**Last update:** E5.2a backtick fix built natively (5152 passed, 3 strict xfails: G48, GAP-4, G38 maths/code overlap).

## Status

| Epic | Status | Features | Commit | Sessions left |
|---|---|---|---|---|
| 0 Foundations | done | scaffold, versions, diagnostics, panic watchdog | 88d6173 | 0 |
| 1 Calc (F11–F15) | done | E1.1–E1.7 decimal, units, lexer, parser, evaluator, FX, rendering | 88d6173 (E1.1–E1.5), e2f5aa1, 95c7f90 | 0 |
| 2 Metadata and variables | done | E2.1 frontmatter, E2.2 YAML, E2.3 merge/cascade, E2.4 interpolation and escaping | 8962058, 1d797a0, a4cfc4b, 6f26fe2 | 0 |
| 3 Structure and containers | done | E3.1 UTF-8 ✔ (59509ac), E3.2 block parser and directive scan ✔ (2a00403), E3.3 `attrs_parse`/`container_parse` ✔ (ae0f097), E3.4 `container_eval` ✔ (d06c11c) | | 0 |
| Build | done | Rust of E1.6–E3.2 compiles; all tests pass | | 0 |
| 4 Composition and references (F04–F07) | done | E4.1 paths and confinement ✔, E4.2 heading text/slugs/dedupe ✔, E4.3 block IDs and `resolve_ref` ✔, E4.4 import expansion ✔, E4.5 link rebasing ✔ | | 0 |
| 5 Rich features and export (F10, F23) | done | E5.1 highlight/callout/details/columns ✔, E5.2a math/footnotes ✔ (fixes: fixed point 1f8c87b, backticks pending commit), E5.2b citations ✔, E5.3 TOC ✔, E5.4 `export_markdown` ✔ | | 0 |
| 6 Sources and policy (F16–F19) | done | E6.1 declarations and URL templates ✔, E6.2 JSON Pointer and types ✔, E6.3 URL/IP classification ✔, E6.4 policy ✔, E6.5 resolver, fake transport and snapshots ✔ | | 0 |
| 7 Packages and encryption (F20, F21) | done | E7.1 hostile-zip reader ✔, E7.2 manifest and `package_open` ✔, E7.3 age (X25519 + passphrase, pyrage-verified) ✔ | c04ca23 (E7.1), 1f8c87b (E7.2 + E5.2a fix), fd299f6 (E7.3) | 0 |
| 8 API, CLI, packaging (F25, F26) | not started | E8.1 `compile`, E8.2 typing/threads/memory, E8.3 CLI, E8.4 wheel smoke test | | 6 |
| Cross-cutting | open | rule ledger and conformance fixtures, CI (incl. `HYPOTHESIS_PROFILE=ci`), Rust coverage, sdist fix (`include_str!("../../../spec/units.tsv")`) | | 3 |
| **Total** | | | | **about 9** |

At the pace so far (29 commits for Epics 0–6), the remaining 9 sessions are roughly 2–4 working days; build-error rounds add to that.

## Open decisions

- G10 resolved in G44 (CGNAT blocked)
- G16: ISO 4217 list or any 3-capital currency code (no longer blocks E6; blocks unit/currency validation in E8.1)
- G24 / G25: offered for veto
- Per-member encryption layout (spec gap 4): strict xfail in `test_e7_f73_age.py`
- MDZip (F22): deferred by the spec
- E7.1 (G47): case-insensitive name collisions rejected although the MDQ VFS is case-sensitive: offered for veto
- E7.2 (G48): spec conflict package.md `security: {network: deny, remote_imports: false}` vs G45 `network: request|none`; decided: accept both spellings (`deny` = `none`). Spec fix wanted
- E7.2 (G48): the package.md example manifest has `features: [core, rich]` without `package`, which our rule rejects (strict xfail). Fix the example or drop the rule
- E5.2a (G38): maths touching a backtick is now refused (MDQ_UNSUPPORTED_FEATURE): offered for veto
- E5.2a (G38): maths overlapping a code span (`` $`$` ``): Pandoc vs CommonMark, which wins? Current: maths (strict xfail)
- E7.3 (G49): RustCrypto primitives + own age format, host-supplied randomness, X25519 + passphrase (scrypt cap log2 N <= 20), explicit decrypt API: offered for veto
- E8.1: plaintext export of a decrypted package needs explicit permission (moved from E7.3)

## Next

1. Epic 8, E8.1 `compile` (cascade with the manifest as lowest layer, sources validation, plaintext export permission, `test_source`). G16 still open.
