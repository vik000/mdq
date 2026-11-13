# MDQ Library — Implementation and Test Plan

**Status:** draft for review. Target: MDQ spec `0.1.0-draft`.
**Rule:** the normative files in `spec/` win over this plan. Where the spec is ambiguous, tests are written as `xfail(strict=True)` tagged with a gap ID (section 6) and flip to real tests when the spec is amended.

## 1. Decisions to confirm

| ID | Decision | Recommendation | Why |
| --- | --- | --- | --- |
| D1 | Repo layout | Cargo **workspace**: `mdq-core` (pure Rust, no PyO3) + `mdq-python` (thin PyO3 wrappers). `mdq-http`, `mdq-package` crates added later | The spec requires core to be usable offline and separable. Keeping PyO3 out of core forces all logic to be plain functions that the wrappers merely expose. |
| D2 | Test hooks | Always compile an `mdq._internal` module (underscore, documented unstable, excluded from `__all__`) | Lets pytest hit every building block directly. No feature flag to forget in CI. |
| D3 | Build order | **calc engine first**, then metadata, then Markdown structure, then composition (spec roadmap starts with parsing) | calc is pure functions with full spec test vectors and an exact Python oracle (`decimal`). Best place to learn Rust and the test workflow. |
| D4 | Decimal backend | **Own small `Decimal` over `num-bigint`** (revised from `bigdecimal`) | Exact control of rounding. `bigdecimal` divides at a 100-digit precision and then rounds again to 28, risking double rounding on ties, and could silently disagree with Python `decimal`. Ours rounds once, with exact tie detection (a "sticky" remainder). Validated against the oracle on 600,000 random cases. |
| D5 | CLI | Python `console_script` (`mdq.cli`) calling the library | Same engine, no second binary to test, and you stay in Python. |
| D6 | Numeric inputs | API accepts `int`, `str`, `decimal.Decimal`. **`float` raises `TypeError`** | Spec F25: never round through binary float. |
| D7 | Python | Develop on 3.12, `abi3-py39` wheels, CI matrix 3.9–3.13 | Skill default + spec F01 asks for documented minimums. |
| D8 | Rust-side tests | None written by hand (as you asked). We rely on Python tests plus Rust line coverage (section 3.7) to show nothing in core is unexercised | |

## 2. Architecture and the `_internal` pattern

```
mdq/
├── Cargo.toml                 # workspace
├── pyproject.toml             # maturin; module-name = "mdq._native"
├── crates/
│   ├── mdq-core/              # ALL logic. No pyo3 dependency.
│   │   └── src/ diag.rs  decimal.rs  units.rs  calc/{lexer,parser,eval,render}.rs
│   │            meta.rs  vfs.rs  md/{regions,directives,containers}.rs
│   │            refs.rs  slug.rs  imports.rs  export.rs  source.rs  policy.rs  pkg.rs
│   └── mdq-python/            # wrappers only: convert types, call core, convert errors
│       └── src/ lib.rs  public.rs  internal.rs
├── python/mdq/                # __init__.py, _internal.py (re-export), cli.py, py.typed, *.pyi
├── conformance/cases/*.json   # language-neutral fixtures (shared by tests)
├── conformance/rules.yaml     # rule ledger (see 3.5)
└── tests/                     # ALL tests, Python
```

**Wrapper rule:** a `#[pyfunction]` contains no logic, only argument conversion and one call into `mdq-core`. Then a passing Python test of the wrapper is a passing test of the logic, and you never need to read Rust to trust it.

**Exposure rule:** every non-trivial `pub fn` in `mdq-core` that another core function calls (tokeniser, slugger, path normaliser, IP classifier, decimal ops, ...) gets an `_internal` wrapper and its own Python tests. Data crosses the boundary as plain Python types: `str`, `int`, `bool`, `list`, `dict`, `decimal.Decimal`. ASTs and results are dicts with a stable shape (`{"type": "assign", "name": ..., "line": ..., ...}`), which also makes them easy to assert on and to store in fixtures.

**Error model (proposed):**
- `_internal` functions raise `mdq.MdqError` with `.code` (one of the 16 stable codes), `.message`, `.path`, `.line`, `.column`, `.span`.
- Public `compile`/`validate` return a result object with `.ok`, `.output`, `.diagnostics`, plus `.unwrap()` that raises.
- A Rust panic becomes `pyo3_runtime.PanicException`, which derives from `BaseException`, so `except Exception` will not catch it. A panic is therefore always a test failure and never a handled error.

## 3. Test architecture

### 3.1 Layers

| Layer | Directory | What it proves | Tools |
| --- | --- | --- | --- |
| L1 Unit | `tests/unit/` | Each internal function, one file per feature | pytest, `mdq._internal` |
| L2 API | `tests/api/` | Public API, types, exceptions, decimal fidelity, typing | pytest, mypy stubtest |
| L3 Conformance | `tests/conformance/` | Spec rules end to end from JSON fixtures | pytest parametrised |
| L4 Property | `tests/property/` | Invariants over generated inputs | Hypothesis |
| L5 Differential | `tests/differential/` | Agreement with independent Python oracles | `decimal`, `markdown-it-py`, `unicodedata`, `ipaddress`, `jsonpointer`, `pyrage` |
| L6 Security | `tests/security/` | Hostile paths, zips, URLs, limits | pytest, `zipfile`, fake transport |
| L7 Determinism | `tests/determinism/` | Same inputs give identical bytes | repeated runs, subprocesses |
| L8 Release | CI only | Wheel installs and imports on every supported Python | cibuildwheel, fresh venv |

Oracles are test-only reimplementations. They do not violate "no Python semantic duplication", which applies to the shipped library.

### 3.2 Conventions
- One file per feature: `test_f11_calc_parser.py`. Classes `TestGeneral`, `TestEdge`, `TestNegative`, `TestErrors` (same four categories as your workflow).
- Markers: `unit conformance property differential security slow gap`. Every test carries `@pytest.mark.spec("extended-markdown §8.5")`.
- `with assert_mdq_error("MDQ_CALC_ERROR", line=3, path="a.md"):` helper. It checks code, location and that the message is non-empty.
- Gap tests: `@pytest.mark.xfail(strict=True, reason="GAP-n")`. A strict xfail that starts passing fails the suite, so you are forced to promote it.
- Hypothesis: `dev` profile 100 examples; `ci` profile 1000 examples with `derandomize=True`; nightly job with random seed (printed on failure).
- Panic watchdog in `conftest.py`: no test may swallow `PanicException`.
- Fixture `fs`: `mdq.MemoryFS({...})`. Fixture `recording_fs`: a Python-callback FS that logs every read, used to prove "excluded branch never touched the filesystem".
- Fixture `fake_transport`: scripted HTTP responses and DNS answers. No real network, ever.

### 3.3 Conformance fixture shape
```json
{"id": "calc-vector-03", "rules": ["CALC-VEC-03"], "spec": "extended-markdown §8.9",
 "files": {"main.md": "```calc\nx = (2 + 3) * 4\n```\n{{calc.x}}\n"}, "entry": "main.md",
 "options": {}, "expect": {"output": "20\n"}}
{"id": "calc-vector-07", "rules": ["CALC-VEC-07"],
 "files": {"main.md": "```calc\nx = 10 / 0\n```\n"}, "entry": "main.md",
 "expect": {"diagnostics": [{"code": "MDQ_CALC_ERROR", "path": "main.md", "line": 2}]}}
```
One runner drives every case through the Python API, and the same JSON can later drive a Rust or CLI runner. The existing README mentions `conformance/cases/core.json` and `tests/test_conformance.py`, but neither is committed in the repo. We will create them.

### 3.4 Targets and release gates
- Every one of the 16 diagnostic codes is triggered by at least one test, and each test asserts code and location.
- Every normative MUST/MUST NOT has a rule ID in the ledger with at least one test (3.5).
- Rust line coverage of `mdq-core` at least 95 percent, with uncovered lines reviewed (3.7). Rough size: 1,500 to 2,500 parametrised cases plus Hypothesis.
- No skipped tests in release CI; `xfail` only for open gaps and each must cite a gap ID.

### 3.5 Rule ledger
`conformance/rules.yaml` lists IDs such as `CALC-GRAM-07: {spec: "§8.5", text: "inline comments rejected"}`. A meta-test greps for `@pytest.mark.rule("CALC-GRAM-07")` and fails if any ledger rule has no test, or any test names an unknown rule. This is how "every normative rule has a fixture" (F27) stays true as the suite grows.

### 3.6 Oracles
- Decimal arithmetic: `decimal.Context(prec=28, rounding=ROUND_HALF_EVEN)`. Trap: `Decimal("٣")` and `Decimal("1e3")` are valid in Python but invalid MDQ, so inputs are validated before reaching the oracle.
- Units: exact `fractions.Fraction` factor table kept in the tests (independent of the Rust table).
- Markdown regions and escaping: `markdown-it-py` (CommonMark compliant).
- Slugs and normalisation: `unicodedata.normalize("NFC")`, `str.casefold()`, plus hand-written vectors.
- JSON Pointer: RFC 6901 section 5 examples and the `jsonpointer` package.
- IP classification: `ipaddress` stdlib, plus a hand-written bypass table.
- age: `pyrage`.

### 3.7 Rust coverage from Python tests
`cargo llvm-cov show-env` exports the instrumentation env vars; run `maturin develop`, then `pytest`, then `cargo llvm-cov report --html`. Uncovered lines in `mdq-core` become new test cases or get deleted. Wrappers are excluded.

## 4. Epics, features and tests

Order of delivery follows D3. Internal names are proposals; spec feature IDs in brackets.

### Epic 0 — Foundations [F01, F24, F27 harness]
**Internal:** `version_info()`, `supported_profiles()`, `check_required(profiles, version)`, `diagnostic_codes()`, `default_limits()`.
**Tests**
- General: import works; versions are exposed (syntax, manifest, unit catalogue); the 16 codes exist exactly; diagnostic dict shape is stable.
- Negative: unsupported required profile or version gives `MDQ_UNSUPPORTED_FEATURE`; unknown limit key rejected.
- Meta: panic watchdog works (a deliberate `_internal._panic_probe()` raises `PanicException` and the watchdog catches it); rule-ledger meta-test; fixture JSON schema validation.
- CI: core builds with network disabled (cargo `--offline` after fetch).

### Epic 1 — calc engine [F11–F15]

**E1.1 Decimal [F13]**
`dec_parse`, `dec_canonical`, `dec_add/sub/mul/div`, `dec_round`.
- General: spec vectors (`2+3*4=14`, `120*21%=25.2`); `0.1+0.2=0.3` exactly.
- Edge: `.5`; `12.`(invalid); negative zero prints `0`; trailing zeros stripped (`1.500`→`1.5`); 28/29/30-digit operands; ties at digit 28 for half-even (e.g. `...5` with even/odd preceding digit); very large and very small magnitudes; `-0 * 5`.
- Negative: `1e3`, `NaN`, `Infinity`, `1,000`, `""`, `+`, `١٢٣` (non-ASCII digits), whitespace-padded input.
- Errors: division by zero `MDQ_CALC_ERROR`; overflow policy (gap G4).
- Property/differential: for 5,000 generated pairs, every op equals the Python-context oracle as a string; add is commutative, mul is commutative; canonical form is idempotent.

**E1.2 Unit catalogue and conversion [F14]**
`catalogue_version`, `unit_table`, `unit_lookup(sym)`, `unit_match_at(text, pos)` (longest match), `convert(value, from, to)`.
- General: every required symbol from `calculation-units.md` exists with a dimension; the 8 exact references (`1 in=0.0254 m`, ..., `K = degC + 273.15`).
- Edge: case sensitivity (`KM`, `Km`, `kb`, `KB` rejected; `kB` vs `KiB` correct); `m2` vs `m` then `2`; longest match `km/h` vs `km`; `m/2` stays `m` then `/`; `degF`→`degC` at -40 (equal), 32, 212, absolute zero.
- Negative: `C`, `F`, `miles_` , `Miles`, `metre`, empty string rejected as unknown alias (no guessing); cross-dimension conversion (`kg to m`).
- Property: for every ordered pair in the same dimension, `convert(convert(x,a,b),b,a) == x` within 28-digit tolerance; scale from the table equals the independent Fraction table for all entries.
- Note: factors beyond the 8 references are blocked by G1 unless we author the table first (section 7).

**E1.3 Lexer [F11]**
`calc_tokenize(line)`.
- General: each token kind; `21%`; `12 km/h`; `to` keyword.
- Edge: tabs, CRLF, leading/trailing whitespace, `#` only as first non-space char (`x = 1 # c` rejected), unit at end of line, `12miles` without space (grammar requires `ws1`, confirm via G3).
- Negative: `;`, `"str"`, `stock("AAPL")`, `2^2`, `1e3`, `2(3)`, `1 // 2`, `1_000`, non-ASCII identifiers, `==`, `!`.

**E1.4 Parser [F11]**
`calc_parse(block) -> list[dict]`, `calc_unparse(ast)`.
- General: precedence (`%` > unary > `*/` > `+-` > `to`), left associativity (`10-4-3=3`, `100/10/2=5`), parentheses, `(distance to km) / 2`.
- Edge: empty block, only comments/blank lines, last line without newline, CRLF, line numbers reported 1-based within the block, `x = to`, identifier named like a unit (`h = 2` then `12 / h`).
- Negative: chained `to`; `x = 1 + 2; y = 3`; `2 kg%`; `12 / h` with `h` undefined vs defined; duplicate statements on one line.
- Property: generate grammar-valid expression trees, `unparse` then `parse` gives the same AST; any inserted forbidden token makes parsing fail with a line number.

**E1.5 Evaluator, scopes and dimensional rules [F12, F13]**
`calc_eval(block, inputs, options)`.
- Scopes: earlier assignment visible; forward reference fails; duplicates fail; shadowing metadata inside a block only; metadata not mutated; blocks do not share locals; `calc.name` published; cross-block name collision fails.
- **Operator matrix (table-driven, ~150 cases):** operand kinds {decimal, quantity, money, absolute temperature, temperature difference, percent} crossed with `+ - * /` and `to`. Expected outcome (value kind or error) lives in one Python data table derived from §8.7.
- Specific rules: left operand's unit wins on `+`/`-`; bare number + quantity invalid; absolute temps cannot `* / +`; `a - b` gives `deltaK`; `deltaK * 2` ok; `deltaK + degC` fails; money: same-currency add/sub, scalar `*` `/`, money/money ratio, money*money invalid, money*quantity invalid, `price * shares` ok.
- Percent: `100 + 10%` equals `100.1` (per spec 10% is 0.1; confirm in G8); `(5+5)%`.
- Differential: all pure-decimal programs checked against the Python oracle.
- Spec vector table §8.9 as 10 parametrised cases (verified in section 5).

**E1.6 Currency observations [F15]**
`fx_select(observations, from, to, valuation|obs_id, market)`, `money_convert`.
- General: latest observation at or before valuation; exact ID; applied as `amount * rate`.
- Edge: observation exactly at valuation; one second after is excluded; market filter; equal timestamps without ID give `MDQ_SOURCE_UNRESOLVED`; same-currency conversion.
- Negative: rate 0, negative, `NaN`, string; missing pair; reverse-only pair (no inversion); triangulation not attempted; no valuation given; naive timestamp without offset (G6).
- Property: shuffling observation order never changes the selection; adding an observation after the valuation never changes the result.

**E1.7 Result rendering [F12]**
`calc_render(result)`: dimensionless canonical decimal; quantity `<decimal> <unit>`; money `<decimal> <CUR>`; no thousands separators; `-0` → `0`; typed value preserved separately from display. Implemented as a `display` field on every `calc_eval` result plus `calc_render(result_dict)` (G21).

### Epic 2 — Metadata and variables [F03, F08]
**Internal:** `frontmatter_split`, `yaml_load_strict`, `meta_merge`, `meta_cascade(files, doc)`, `var_format_scalar`, `md_escape_inline`, `interpolate(text, vars)`.
- Frontmatter: absent; empty; unterminated; CRLF; BOM; not a mapping (list, scalar); duplicate keys (also nested); `---` as thematic break in body is not frontmatter (G5).
- Merge: nested mappings merge recursively; arrays and scalars replace; `null` clears; every pairing of precedence layers (invocation > sidecar > frontmatter > nearest dir > project > defaults) generated and checked; provenance reports the winning layer for each key; missing sidecar is fine.
- Variables: `{{a.b.c}}`; ASCII segments only; undefined `MDQ_UNDEFINED_VARIABLE` with location; object/array interpolation rejected; null → empty; bool lowercase; numbers canonical; `\{{x}}` stays literal; `{{x}}` inside code/HTML/inline code stays literal; reserved-namespace conflict fails.
- **Escaping differential:** for generated strings `s`, the plain text of `markdown-it-py` rendering of `escape(s)` equals `s`.

### Epic 3 — Markdown structure and containers [F02, F09]
**Internal:** `utf8_decode`, `md_regions(text)`, `directive_scan(text)`, `attrs_parse`, `container_parse`, `container_eval`.
- UTF-8: invalid bytes, overlong encodings, surrogates, truncated sequence all rejected with offset; BOM policy (G5).
- Regions: fenced (backtick and tilde, different lengths, unclosed), indented code, inline code with multiple backticks, raw HTML blocks, escaped openers. **Differential** against `markdown-it-py` token spans over a Hypothesis-generated Markdown grammar and the CommonMark spec examples.
- Directives found only outside code/HTML/escapes; never by global regex (test with a directive split across a code span boundary).
- Containers: matching fence lengths; nested needs longer fence; unclosed; closer with wrong length; indented fence not a container; `if` true/false, string equality (`"true"` vs boolean `true`; number `1.0` vs `"1"` canonical form, G9); `for` order, empty array, shadowing, iteration cap `MDQ_RESOURCE_LIMIT`, loop var out of scope after loop.
- **Laziness:** `recording_fs` proves an excluded `if` branch never calls `read` on imports inside it.

### Epic 4 — Composition, references, anchors [F04–F07]
**Internal:** `path_normalise(base, rel)`, `path_confine(root, path)`, `section_extract`, `block_extract`, `import_expand`, `source_map_lookup`, `heading_plain_text`, `heading_match`, `block_ids_collect`, `resolve_ref`, `slugify`, `slug_dedupe`, `percent_encode_anchor`, `rebase_link`.
- Paths: `..` at start/middle/end; absolute; `C:/`; backslashes; NUL; `//`; `.`; empty; `https://`; case sensitivity; NFC vs NFD filenames (G5); symlink escape via `tmp_path` with real symlinks; root policy not relaxable by metadata.
- Imports: full, section (stops at equal or higher level heading), block; frontmatter stripped; heading levels unchanged; repeated and nested imports; missing target `MDQ_MISSING_IMPORT`; cycles A→A, A→B→A; **section cycle only when the section really contains the import** (A#H1 importing itself inside H1 fails; importing H2 that has no import does not); depth limit; expansion bomb (`a` imports `b` twice, `b` imports `c` twice, 25 levels) fails with `MDQ_RESOURCE_LIMIT` quickly, bounded memory; deterministic ordering; source map points to original file/line after expansion.
- References: first duplicate wins; later duplicate via block ID; NFC; case-sensitive; markup stripped (`## *Foo* &amp; Bar`); whitespace collapsed; paragraph `^id` pattern `[A-Za-z][A-Za-z0-9_-]*` (`^1a` rejected); duplicate IDs `MDQ_DUPLICATE_ID`; identity is `(canonical path, ordinal|id)` and survives repeated imports; labels (`|Label`) kept; missing `MDQ_UNRESOLVED_REFERENCE`.
- Slugs: Unicode (`Ünïcödé`, `İstanbul`, `ß` casefold to `ss`, CJK, emoji-only → `section`, combining marks); runs of punctuation; leading/trailing hyphens; `-2`, `-3` suffixes; user ID priority and non-collision; percent-encoding only in URLs. **Differential** against a Python implementation of §3.1 (`unicodedata` + `casefold`) on generated strings.
- Link rebasing: the `chapters/intro.md` → `index.md` image example; `../` chains; fragment-only links; query strings; `mailto:`; protocol-relative `//host/x`; absolute URLs untouched; reference-style links; asset outside root fails.

### Epic 5 — Rich features and export [F10, F23]
**Internal:** one parse+fallback function per construct (`highlight_fallback`, `callout_fallback`, `details_fallback`, `columns_fallback`, `math_fallback`, `footnote_rename`, `citation_resolve`, `toc_build`), `export_markdown`.
- Per construct: normal, nested, inside code (literal), unterminated, unknown callout type, `==` adjacent to `=` operators.
- Footnotes: collisions between imported files get deterministic renames; reference without definition; definition without reference.
- Citations: `[@key]`, `[@key, p. 12]`, missing key fails, CSL JSON and BibTeX fixtures (malformed ones too).
- TOC includes imported headings; slug links resolve.
- Generic containers: unknown required handler fails; optional keeps body with warning.
- Export properties: output contains no MDQ directives (run `directive_scan` on it, expect empty); **idempotent** (`export(export(x)) == export(x)`); **plain-Markdown passthrough** (a document with no MDQ constructs is byte-identical, and renders to the same HTML under `markdown-it-py`); deterministic across 200 repeated runs.

### Epic 6 — Sources and policy [F16–F19]
**Internal:** `source_validate`, `url_template_expand`, `json_pointer_select`, `value_type_check`, `url_classify`, `ip_is_blocked`, `policy_intersect`, `policy_decide`, plus the optional resolver driven by `fake_transport`.
- Declarations: inline, named, inherited, override; `arguments` encoding (spaces, `/`, `?`, `#`, `%`, unicode, `..`); undeclared placeholder and unused argument both fail; credentials in URL; non-HTTPS; `method: POST`; unknown kind; `reader:x` without adapter; `csv` select reserved.
- JSON Pointer: all RFC 6901 §5 vectors; `~0`, `~1`; array index leading zeros; `-`; missing key; pointer into scalar.
- Types: number/string/boolean/date mismatches, `unit` on non-number.
- **IP table (`ip_is_blocked`):** 127/8, `::1`, 10/8, 172.16/12, 192.168/16, 169.254/16 (incl. 169.254.169.254), `fe80::/10`, `fc00::/7`, 224/4, `ff00::/8`, `0.0.0.0`, `::`, `::ffff:127.0.0.1`, `::ffff:10.0.0.1`, plus a public control set. CGNAT 100.64/10 is not listed by the spec (G10). Host forms: decimal `2130706433`, octal `0177.0.0.1`, hex `0x7f.1`, trailing dot, brackets, IDN, `https://good@evil/`, `https://evil#@good`.
- Policy: default deny; intersection with host ≥ library ≥ document; manifest `requirements` never grant; `network: required` plus denial gives a clear failure; allowlist does not bypass IP rules; trusted private-endpoint override works only from trusted policy.
- Transport scenarios: success; timeout; slow-loris; oversize body (declared and undeclared); redirect to private IP; redirect loop; redirect chain over limit; DNS answer changes between check and connect (rebinding); invalid JSON; HTTP 4xx/5xx; credentials not forwarded cross-origin.
- **Canary test:** a secret string placed in URL query, header and error text must not appear in any diagnostic, repr or policy-decision log.
- Reproducibility: snapshot replay gives identical output with the transport removed; no auto-fetch on `parse`/`compile` (transport call count stays 0); failed fetch never substitutes stale data.

### Epic 7 — Packages and encryption [F20, F21] (MDZip F22 deferred)
Fixtures are generated in Python with `zipfile`.
- Valid: round-trip write/read; manifest required keys; `features` honoured; UTF-8 member names; entrypoint resolution.
- Hostile: `../x`, `/abs`, `C:\x`, backslash names, duplicate members, symlink entries (via `external_attr`), non-UTF-8 names (flag bit 11 unset, cp437), encrypted-zip flag, zip64, data descriptors, trailing garbage, truncated file, multiple EOCD records, declared size ≠ real size, nested zip, ~50 MB of zeros compressing to under 100 KB (ratio and expanded-size limits), too many entries.
- Manifest: invalid YAML, duplicate keys, wrong `format`, unsupported `version`, missing or traversing `entrypoint`; manifest `security`/`requirements` produce requests only.
- Opening a package makes zero transport calls.
- age: `pyrage`-encrypted `.mdq.age` opens with the right key; wrong key, missing key, malformed envelope, truncated envelope fail cleanly; Rust-encrypted output decrypts in `pyrage`; no implicit decryption; plaintext export needs explicit permission. Per-member encryption layout is blocked by spec gap 4 (xfail).

### Epic 8 — Python API, CLI, packaging [F25, F26]
- API: `compile(entry, fs=..., inputs=..., observations=..., valuation=..., profiles=..., limits=...)` accepts `str`/`bytes`/`Path`; results are typed; exceptions form a hierarchy under `MdqError`.
- Decimal fidelity: results come back as `decimal.Decimal`, never `float`; a `float` argument raises `TypeError`; 28-digit values survive a round trip unchanged.
- Thread safety: 8 threads compiling the same document concurrently give identical output.
- Memory: 10,000 compile calls leave RSS within a small bound.
- Typing: `py.typed`, `.pyi` stubs checked with `mypy --strict` and `stubtest`.
- Parity: the L3 fixture suite is the one truth; the same JSON drives the CLI via `subprocess`.
- CLI: exit codes for success, invalid input, unsupported feature, denied capability; `--format json` diagnostics validate against a schema; `--version` shows syntax, manifest and unit catalogue versions; offline by default.
- Wheel smoke test: build, install in a clean venv per Python version, import, run one document.

## 5. Spec vectors checked
The arithmetic vectors in §8.9 were recomputed with Python `decimal` (28 digits, half-even) and all agree with the spec: `14`, `20`, `25.2`, `19.312128` (12 × 1609.344 ÷ 1000) and `32 degF to degC` = `0`. Two details the tests must pin down:
- Python yields `25.20` for `120 * 0.21`; the spec's canonical form is `25.2`, so canonicalisation (strip trailing zeros) is a separate, tested step.
- `1234567.1 / 3` at 28 significant digits is `411522.3666666666666666666667` while `1 / 3` is 28 threes after the point. Rounding by significant digits (not by scale) is why D4 recommends `bigdecimal`.

## 6. Spec gaps found while planning
These need a decision in the normative spec before the matching fixtures are frozen. They are in addition to the nine gaps listed in the feature specification.

| ID | Gap | Where it bites |
| --- | --- | --- |
| G1 | ~~Full unit table missing~~ **Drafted** in `spec/calculation-units.md` + `spec/units.tsv` (55 rows, derived from exact definitions). Needs your review of the decisions in its section 10 (`cal`, `hp`, `miles`, ...) | E1.2, E1.5 |
| G2 | Result unit of derived dimensions. **Implemented (E1.5)** as the proposed rule in `calculation-units.md` §6 (u*u gives u2/u3, a/b gives `a/b`, else the coherent unit, else error); `number / quantity` is always an error. Not yet adopted in `extended-markdown.md` | E1.5, E1.7 |
| G3 | Identifier vs unit vs keyword. **Lexer policy implemented (E1.3):** `to` is reserved; units are recognised only in unit position (after a number or after `to`), so `h`, `min`, `m`, `EUR` stay valid identifiers elsewhere; a number glued to letters, digits or a dot (`12miles`, `1e3`, `2x`, `1_000`, `1.`, `1.2.3`) is `MDQ_CALC_ERROR`. Needs spec confirmation | E1.3, E1.4 |
| G4 | Numeric range: spec says "finite decimals" with no limits. **Provisional policy implemented:** leading-digit exponent within ±9999 (error `MDQ_CALC_ERROR`), literals at most 10,000 bytes (`MDQ_RESOURCE_LIMIT`). Needs spec confirmation, including the overflow code | E1.1 |
| G5 | Input hygiene: BOM, CRLF normalisation, NUL, NFC vs NFD file names, frontmatter vs leading thematic break (spec §12 lists the last as open) | E2, E3, E4 |
| G6 | Resolved in E1.6: timestamps need an explicit `Z`/`±HH:MM` offset (none is MDQ_SOURCE_INVALID, never assumed UTC); observation id is an optional unique non-empty string; `EUR to EUR` is the identity and needs no observation | E1.6 |
| G7 | `%` is grammatically allowed after any atom including an identifier, but prose says only numeric or parenthesised dimensionless atoms. **Parser policy (E1.4):** `x%`, `(expr)%` and `5%` parse, `5 kg%` / `5 EUR %` are syntax errors, `5%%` is a syntax error; the evaluator (E1.5) must reject a non-dimensionless operand | E1.4, E1.5 |
| G8 | `100 + 10%`: **implemented** as `100.1` (a percentage is the factor n/100, as in the spec's reading), not the spreadsheet `110`. Confirm intended | E1.5 |
| G9 | `if equals="1"` against YAML `1.0` or `true`: which canonical string is compared? | E3 |
| G10 | IP blocklist omits CGNAT 100.64/10 and IPv4-mapped IPv6 handling is implied, not stated | E6 |
| G11 | How do `calc` simple identifiers map to metadata (`apple_price` vs `variables.apple_price`)? §4 uses namespaces; §8.6 says simple names | E2, E1.5 |
| G12 | Unary minus: **implemented** on every type (so `-5 degC` and `-(5 degC)` give `-5 degC`; money may be negative). Money display precision (full vs 2 dp) is still open | E1.5, E1.7 |
| G13 | Conversion rounding: **proposed** exact rational value, rounded once to 28 digits (`calculation-units.md` §2). Rounding per step differs in the last digit for `degF`, `km/h`, `psi`, `deg` | E1.2 |
| G14 | `deltaK` is result-only (not a source unit token) and has no `deltaC`/`deltaF`; `PI` fixed at 40 decimals. **Evaluator policy:** `deltaK` may be added/subtracted with `deltaK`, multiplied/divided by plain numbers, and divided by `deltaK` (a plain ratio); any other product with it is an error | E1.2, E1.5 |
| G15 | Unit token boundary rule (longest match not followed by an identifier character) is proposed in `calculation-units.md` §3 | E1.2, E1.3 |
| G16 | Which ISO 4217 codes are valid in unit position (full active list vs host-declared). **Lexer policy:** any three ASCII capitals in unit position lex as a `currency` token; validity is checked later | E1.3, E1.6 |
| G17 | Whitespace before `%`: the lexer accepts `10 %` and `10%` alike. Should the parser accept both? | E1.3, E1.4 |
| G18 | Nesting limits so hostile input cannot overflow the stack. **Provisional:** at most 199 open parentheses/unary signs and an expression tree at most 500 deep (a chain of more than 500 operands), both `MDQ_RESOURCE_LIMIT`. Needs spec confirmation | E1.4 |
| G19 | Quantity `+`/`-` converts the right operand to the left unit (rounded once) and then adds (rounded once), following the spec sentence literally. An exact single-rounding sum would differ in the last digit for `degF`, `km/h`, `psi`, `deg` | E1.5 |
| G20 | FX policies (E1.6): malformed host data (bad rate/timestamp/currency, duplicate id, unknown field, bad pin key, `market` without `valuation`) is MDQ_SOURCE_INVALID and checked up front for every observation; a missing or tied observation is MDQ_SOURCE_UNRESOLVED; id wins over valuation; `market` is an exact-match requirement on the request; no inversion or triangulation; per-pair pins override the default valuation; `observations` provenance lists conversions on that line only (not chained through variables) | E1.6 |
| G21 | Rendering (E1.7): display is `<canonical decimal>[ <unit symbol or CUR>]`, one space, never an exponent, separator or `-0`; the 28-digit rounding is already done by the evaluator (render never rounds); `calc_render` accepts a `calc_eval` result dict (extra keys ignored, `unit`/`currency` optional), re-canonicalises `value`, and rejects inconsistent type/unit/currency or unknown units/codes with MDQ_CALC_ERROR; wrong Python types raise TypeError, missing `type`/`value` KeyError | E1.7 |
| G22 | Frontmatter (E2.1): resolves the G5 frontmatter-vs-thematic-break ambiguity: a document whose FIRST line is `---` (trailing blanks allowed) is always frontmatter, so a leading rule must be `***`/`___`; the first later `---` line closes it (even inside a YAML block scalar, since YAML is not parsed here); opened but unclosed is MDQ_METADATA_ERROR at 1:1; one leading BOM is ignored and dropped; `\r\n`/`\n` end lines, lone `\r` does not; frontmatter is returned LF-normalised, body verbatim | E2.1 |
| G23 | YAML (E2.2): own strict subset, no dependency. Supported: block/flow maps and lists (list may sit at its key's indent; flow may span lines), plain/single/double-quoted scalars (one line each; `\x \u \U` escapes, no surrogates), `|`/`>` with `+`/`-` chomping, comments, one leading `---`. YAML 1.2 core types (`yes`/`no`/`on`/`off`, timestamps are strings); keys always strings; numbers are exact `Decimal` (exponents expanded, |exp|<=400, <=400 digits). Rejected as MDQ_METADATA_ERROR: duplicate keys (incl. flow, quoted vs plain), anchors/aliases/tags/merge, `? ` keys, directives, extra `---`/`...`, tabs in indentation, control chars (C0, DEL, C1, BOM, U+FFFE/F), multi-line plain/quoted scalars, indentation indicators, ambiguous numbers (`007`, `0x1F`, `0o7`, `+1`, `.5`, `5.`, `.inf`, `.nan`: quote them). Limits (MDQ_RESOURCE_LIMIT): depth 64, 100 000 nodes, 2 MiB. Empty document is `{}` when a mapping is required. Differential oracles: PyYAML (block, flow, block scalars) and JSON round-trip | E2.2 |
| G24 | Merge (E2.3): `null` is a value, so it replaces what is below it and STAYS present as null (`document.x` then interpolates as empty); there is no "required key" list in 0.1, so every key can be cleared. A higher-layer `{}` merges as a no-op (it only replaces a non-mapping). Arrays replace whole, never concatenate or merge element-wise. Result key order = first appearance from the lowest layer; replaced keys keep their position. Provenance is keyed by RFC 6901 JSON Pointer, one entry per leaf (scalar, null, array, empty mapping) | E2.3 |
| G25 | Cascade (E2.3): spec 5 says "nearest directory metadata (`.mdq.yaml`, moving outward)" and separately "project `.mdq.yaml`"; read as: EVERY `.mdq.yaml` between the project root and the document's directory cascades, nearer overriding farther (not just the nearest one). Order low to high: defaults, project, directories (outermost first), frontmatter, sidecar, invocation overrides. Which keys are security values that must NOT cascade (spec 5: capabilities intersect, restrictions compose most-restrictive) is undecided until the permissions epic; the generic merge treats all keys alike. File discovery and sidecar lookup belong to the virtual-filesystem epic (E4); E2.3 takes already-read texts | E2.3 |
| G26 | Variables (E2.4): `{{ name }}` where name is dotted ASCII identifier segments (`[A-Za-z_][A-Za-z0-9_]*`); blanks/tabs inside the braces are allowed, a line break is not. A `{{` that is not a valid reference (and not escaped, not in code/HTML) is MDQ_INVALID_SYNTAX at its position, never literal text. Undefined name, or a path through a non-mapping, is MDQ_UNDEFINED_VARIABLE with line/column (chars); a defined `null` is not undefined. Number text is exact canonical decimal (no exponent, no trailing zeros, `-0` is `0`; magnitude limit 401 integer digits / 399 leading fraction zeros, else MDQ_RESOURCE_LIMIT); NaN/Infinity MDQ_METADATA_ERROR; floats are TypeError at the Python boundary. List/mapping values are MDQ_UNSUPPORTED_FEATURE (spec: "explicit renderer"). Values are escaped by `md_escape_inline`; the result is never re-scanned, so a value containing `{{y}}` is not expanded. Escape rule: an ODD number of backslashes before `{{` makes it literal and the text is left untouched for the Markdown renderer to unescape; an even number leaves it live (CommonMark backslash semantics). Escaping goes beyond ASCII punctuation where parsers would otherwise change the text: entities for blanks/Unicode white space at line edges, line-start markers (`#`, `-`, `+`, `>`, `=`, `:`, `1.`), `://` and `www.` (GFM autolinks), `@`, `$`, `{`, `|`, `~`. NUL is MDQ_INVALID_SYNTAX | E2.4 |
| G27 | Interpolation scope (E2.4, revised E3.2): code and HTML are never expanded at any nesting depth. `interpolate` runs on the block structure (see G29): only paragraph and heading content is scanned; fenced/indented code, HTML blocks, inline code spans, inline raw HTML and `<url>` autolinks are literal. Link destinations `[t](url)` get prose escaping (spec 12 open item); the right per-context escaping is decided with the link rules in E3. The 64 MiB output cap (MDQ_RESOURCE_LIMIT) is not exercised from Python (a test would need a 64 MiB loop) | E2.4, E3.2 |
| G28 | Input decoding (E3.1): strict UTF-8 (no overlong forms, surrogates, > U+10FFFF, truncated tail) else MDQ_INVALID_SYNTAX naming the byte offset, with line/column of the first bad byte counted in chars of the decoded text; ONE leading BOM is dropped (a second U+FEFF is content); NUL is rejected rather than replaced (CommonMark would substitute U+FFFD); line endings are left as they are (LF, CRLF and lone CR are all line ends everywhere); input over 16 MiB is MDQ_RESOURCE_LIMIT | E3.1 |
| G29 | Block structure (E3.2): follows the CommonMark block algorithm (open-container matching, lazy continuation, block-start order quote, ATX, fence, HTML, setext, thematic, list item, indented code). Only blockquotes and list items nest; nesting deeper than 128 is MDQ_RESOURCE_LIMIT at the line/column of the block start that exceeds it. Link reference definitions are plain paragraphs; a trailing line end adds no empty final line; tabs count to the next multiple of 4 columns. Verified against markdown-it-py on random documents; one deliberate difference: markdown-it treats a list-marker line after a lazy paragraph continuation inside nested quotes as a new list, CommonMark (and MDQ) as paragraph text | E3.2 |
| G30 | Containers (E3.3): a fence line is any line starting with 3+ colons at column zero that is not inside a code block or HTML block (a fence right after paragraph text still counts; quotes and list items cannot hold containers, since their lines do not start at column zero). Opener `:::name {attrs}` (name = dotted ASCII identifiers, blanks allowed between colons and name); closer = colons only, EXACTLY the opener's count (wrong count, stray closer, unclosed container are MDQ_INVALID_SYNTAX); a nested opener needs MORE colons than its parent; nesting limit 64 (MDQ_RESOURCE_LIMIT). Attributes: `key="value"` only (key `[A-Za-z_][A-Za-z0-9_-]*`, no blanks around `=`, double quotes only, escapes `\"` and `\\`, tab allowed, other control chars rejected, blanks required between pairs, duplicate key rejected, max 64). `if`/`for` attribute validation is E3.4 | E3.3 |
| G31 | Container evaluation (E3.4): `container_eval` resolves `if`/`for` AND interpolates in one pass; the container fences split the document into independent block contexts (each text run between fences is parsed on its own). `if {var equals}` (both attributes required, no others) compares the CANONICAL string of a scalar variable with the literal exactly (answers G9: `true` is `"true"`, YAML `1.0` is `"1"`, `1.0` as a literal never matches a number, null is `""`); undefined variable is MDQ_UNDEFINED_VARIABLE, list/map is MDQ_UNSUPPORTED_FEATURE. `for {each as}` needs a list (else MDQ_METADATA_ERROR); `as` is a plain identifier; the loop variable shadows every variable with the same first name segment, inner loops shadow outer ones, and it is gone after the loop. `max_iterations` (default 10000) caps iterations summed over all loops (MDQ_RESOURCE_LIMIT). Excluded branches are never evaluated (no errors from them). Containers other than `if`/`for` keep their fence lines verbatim (attribute values are NOT interpolated) and have their bodies evaluated. Laziness for imports inside excluded branches is tested with E4 | E3.4 |
| G32 | Paths (E4.1): references are `/`-separated, relative to the importing file; `.`/`..` are resolved without file access. MDQ_SECURITY_DENIED: leaves the root, leading `/` (incl. `//host`), any scheme or drive letter (`https:`, `C:`, `mailto:`; a name with a colon needs `./`); MDQ_INVALID_SYNTAX: empty, backslash, control characters, empty segment, trailing `/`, a result that names no file (`.`, `a/..`); MDQ_RESOURCE_LIMIT: path over 4096 bytes or segment over 255. No percent-decoding, `#` and `?` are ordinary characters (callers split fragments first). Case is preserved and significant. Identity for comparisons is the NFC form (`path_identity`); `path_confine` looks a file up as written, then NFC, then NFD (NFC/NFD name trouble, G5). Confinement is on the canonical path after symlinks (a symlink inside the root pointing inside is fine); missing file or directory is MDQ_MISSING_IMPORT; the check and the later read are separate steps (TOCTOU), so hosts that need strictness should read through the returned canonical path. NEW DEPENDENCY: `unicode-normalization` (pure Rust), also needed for headings and slugs in E4.2 | E4.1 |
| G33 | Heading text and anchors (E4.2): `heading_plain_text` renders inline Markdown as a CommonMark renderer shows it (escapes, code spans, entities, inline links/images, emphasis by the CommonMark delimiter algorithm, raw HTML dropped, autolink text kept; reference links stay literal), plus `~~x~~` and `==x==` only when the run is exactly 2 characters; then NFC, white space collapsed to one space, trimmed; `atx=True` first drops a closing `#` sequence. `heading_match` returns the index of the FIRST heading whose plain text equals the NFC/collapsed target (case-sensitive), else None. `slugify`: NFC, Unicode full case folding, keep letters/marks/numbers/`-`/`_`, other runs become one `-`, trim `-`, empty gives `section` (user hyphens are kept: `A -- B` gives `a----b`). `slug_dedupe`: first keeps the name, later get `-2`, `-3`..., skipping used and reserved names. `percent_encode_anchor`: everything but ASCII `A-Za-z0-9-._~` as uppercase %XX of UTF-8. Unicode/entity tables generated by `scripts/gen_unicode_tables.py` (currently Unicode 13; tests compare to frozen `tests/oracles/unitab.py`; regenerate all three files together) | E4.2 |
| G34 | Block IDs and references (E4.3): a block ID is a trailing `^id` (`[A-Za-z][A-Za-z0-9_-]*`, ASCII, case-sensitive) preceded by space/tab at the end of a paragraph or heading, or alone on the last line of a multi-line paragraph; a paragraph that is only `^id`, `a^id`, `\^id`, `^1a`, markers in code, HTML or indented code are not IDs. Other block kinds (list items, tables, fences) cannot carry IDs in 0.1. The marker is removed from heading text. Duplicates (headings and paragraphs share one namespace) raise MDQ_DUPLICATE_ID at the second marker (line, column of `^`). `doc_index` lists headings (ordinal, level, plain text, id, line range, `section_end` = next heading of level <= its own, else the end of the last block) and identified blocks; headings and paragraphs inside quotes and list items are included in document order. `resolve_ref(text, fragment)`: `` is the whole document, `^id` a block (invalid id: MDQ_INVALID_SYNTAX, absent: MDQ_UNRESOLVED_REFERENCE), anything else the FIRST heading with that plain text (NFC, collapsed white space, case-sensitive; absent: MDQ_UNRESOLVED_REFERENCE). Lines 0-based, ends exclusive; first_line only offsets diagnostics | E4.3 |
| G35 | Import expansion (E4.4): `![[path]]`, `![[path#Heading]]`, `![[path#^id]]` are block embeds: the paragraph must be exactly one embed at column 1 (not in a quote, list, heading or table row, no label `|x`, no empty path or fragment); anything else containing `![[...]]` is MDQ_INVALID_SYNTAX. Not embeds (literal, never read): code spans, fences, indented code, HTML blocks, inline HTML, `\![[`, unterminated `![[`, inner text with `[`/`]`, plain `[[links]]`. Path = relative to the importing file's directory (path_normalise: security-denied/invalid-syntax errors at the embed). Target body = text without frontmatter; heading = section of the FIRST matching heading up to the next heading of equal or higher level; `^id` = that block only (a heading id gives the heading line only). Expansion is verbatim (line endings, `^id` markers, heading levels, nested embeds expanded recursively); markers are removed by the exporter, not here. The host `read(path)` returns text or None (None = MDQ_MISSING_IMPORT at the embed; a callback exception propagates unchanged; each path is read at most once per call). Cycles: a frame is (path, line range); re-entering a frame already on the stack is MDQ_IMPORT_CYCLE with the chain, so a section cycles only if it really contains the import. Limits (MDQ_RESOURCE_LIMIT): depth 64 (root counts), 10 000 expansions, 16 MiB output, all configurable. Fragment lookups index the target (duplicate IDs there raise MDQ_DUPLICATE_ID; a plain full import does not index). The caller evaluates containers first (excluded `:::if` branches then never reach `read`); the host's `read` returns already-evaluated text for imports (wired in E8.1). Source map: {out_start, out_end (0-based, exclusive), path, src_start (1-based line of the file incl. frontmatter)} | E4.4 |
| G36 | Link rebasing (E4.5): `rebase_link(dest, from_dir, to_dir)` takes the DECODED destination written in a file of directory `from_dir` and returns the destination for output placed in `to_dir` (root-relative directories, "" = root, same rules as paths). Untouched: empty, `#frag`, `?query`, `/abs`, `//host`, any `scheme:` (incl. `mailto:`, `data:`, drive letters). Query and fragment are kept; `.` segments and empty segments are dropped; `%2e%2e` is an ordinary segment; a trailing `/` is kept; a first segment containing `:` gets `./`; a link leaving the root is MDQ_SECURITY_DENIED ("asset outside root fails"). `rebase_links(text, from_dir, to_dir)` rewrites inline links/images and one-line reference definitions at the start of paragraphs (not footnote definitions `[^x]:`, not multi-line definitions, not code, HTML blocks, inline/raw HTML such as `<img src>`, autolinks, wiki links); markup is decoded (`\\_`, `&amp;`) and re-encoded (angle form when the new destination has spaces, parentheses, `<`, `>`, `\\`); same directory returns the text unchanged. `import_expand(..., rebase=True)` rebases each embedded file's links straight from its own directory to the root file's directory (not hop by hop), only for the lines actually embedded; the root's own links are untouched; `rebase=False` keeps verbatim text. OPEN: reference-definition labels are file-scoped, so two imported files defining the same label collide in the combined output (to be decided with E5.2 footnote renaming); exporting to a directory other than the root file's is a later `rebase_links(text, root_dir, out_dir)` call | E4.5 |
| G37 | Rich-feature fallbacks (E5.1): `highlight_fallback` removes the `==` delimiters of `==text==` using the `~~` rules (paragraphs and headings only, not code/HTML); if removal would change how neighbouring `*`/`_` emphasis is read (`x ==*a==* y`) it fails with MDQ_UNSUPPORTED_FEATURE. `callout_fallback`: a quote whose first line is `[!TYPE]` alone (NOTE, TIP, IMPORTANT, WARNING, CAUTION, any case) becomes a bold label line `**Note**` inside the quote, with a blank quoted line after it; an UNKNOWN type fails with MDQ_UNSUPPORTED_FEATURE (OPEN: flagged for veto). `details_fallback`: `:::details {title="T"}` becomes a heading one level below the nearest preceding heading (max 6; level 2 when none; nested details deepen), title whitespace collapsed and inline-escaped, default `Details`, followed by the body. `columns_fallback`: `columns`/`column` containers are dropped, bodies kept in source order, blank-line separation preserved. All four keep line endings and take `first_line` for diagnostics | E5.1 |
| G38 | Maths and footnotes (E5.2a): `math_fallback(text, first_line=1)`: inline `$x$` / `$$x$$` becomes a code span (backtick run longer than any run in the body, padded with spaces when the body starts or ends with a backtick; body kept verbatim, so quote/list prefixes inside multi-line spans survive); Pandoc rules: the opening `$` is followed by a non-blank, the FIRST unescaped `$` after it closes (`$a $b$` is not one span), it must follow a non-blank and not precede an ASCII digit, and its run length must equal the opener's (`$a$$b$`, `$$x $ y$$` stay text); no maths in code, HTML blocks, inline code, escapes, autolinks, inline HTML or link destinations. A line holding only `$$` (after blanks and `>`) opens display maths and the next such line closes it (blank lines and list/heading-looking lines inside are fine); it becomes a fenced `math` block (fence longer than any backtick run in the body, min 3); an unclosed `$$` is MDQ_INVALID_SYNTAX at the opener; one-line `$$ x $$` blocks are not display maths (inline rule applies); `$$` pairs are not recognised inside code/HTML leaves. OPEN: maths in table cells containing `|`. `footnote_check(text, first_line=1)` returns the referenced labels in order of first reference; reference without definition is MDQ_UNRESOLVED_REFERENCE, a label defined twice MDQ_DUPLICATE_ID (first error in document order), unused definitions are allowed; labels are non-empty without whitespace or brackets; a definition is `[^id]:` at the start of any paragraph line (also interrupting a paragraph); not in code, escapes, inline HTML. KNOWN LIMIT: references inside indented continuation paragraphs of a footnote (parsed as indented code) are not seen. `footnote_rename(text, scope)` inserts `scope--` into every reference and definition label. `import_expand(..., footnotes=True)` renames the labels of each embedded piece with the scope of its file: the root-relative path with every non-alphanumeric byte as `_xx` (injective), `~N` for the Nth embedding of the same path; the root file keeps its labels; a partial embed whose definition lies outside it fails the final `footnote_check` (OPEN: flagged for veto). Citations are E5.2b | E5.2a |
| G39 | Citations (E5.2b): `bib_parse(text, format)` (`bibtex` | `csl-json`) returns entries `{key, type, authors[{family, given}], et_al, year, title, container}` (container = journal, booktitle, publisher in that order; CSL: container-title, then publisher). BibTeX: entries outside `@...{}` ignored; `@comment`/`@preamble` skipped; `@string` macros, `#` concatenation, predefined month macros (full names); `{}`/`""`/digits/macro values; types and fields case-insensitive; `(...)` delimiters; trailing comma; braces dropped and `\& \% \$ \# \_ \{ \}` unescaped, other LaTeX kept as text (never executed); names `Family, Given` / `Given Family` / `{Corporate Name}`, split on ` and ` outside braces, `others` sets `et_al`; year = first 4-digit run of `year`, else `date`. Duplicate key or field, undefined macro, unbalanced braces: MDQ_SOURCE_INVALID (key: MDQ_DUPLICATE_ID) with line/column in the bibliography. CSL JSON is parsed by a strict RFC 8259 parser (`json.rs`: duplicate keys, trailing commas, unpaired surrogates, control characters, depth over 64 are errors; MDQ_SOURCE_INVALID / MDQ_RESOURCE_LIMIT); wrong shapes (not an array, item without non-empty id, bad author/issued/title types) are MDQ_SOURCE_TYPE_ERROR, duplicate ids MDQ_DUPLICATE_ID; `null` counts as absent. `citation_resolve(text, bibs, first_line=1)`, `bibs` = list of `(format, text)`: `[@key]` / `[@key, locator]` (key: letters, digits, `_`, inner `:.+/-`; one locator on the same line) become `(Smith 2020)` / `(Smith 2020, p. 12)`: 1 author `Family`, 2 `A and B`, 3+ or et_al `A et al.`, none: first 3 words of the title, no year `n.d.`; equal labels get `a`, `b`... in order of first citation; label and locator are Markdown-escaped. Not citations (left as text): in code, escapes, inline HTML, autolinks, followed by `(`, `[` or `:`, `[@k extra]`, empty locator, locator over a line break, several keys (`[@a; @b]` OPEN). Missing key: MDQ_UNRESOLVED_REFERENCE; key in two resources: MDQ_DUPLICATE_ID. Bibliography = list of the cited entries sorted by authors, year, key (`- Smith, John and Doe, Jane (2020a). Title. Container.`): it replaces a paragraph `[[bibliography]]` at column 1 (also when nothing is cited: removed), otherwise it is appended under `## References` when something is cited. OPEN (flagged for veto): MDQ_SOURCE_INVALID for malformed bibliographies, the `(Label year)` format and `## References` heading | E5.2b |
| G40 | Table of contents (E5.3): `toc_build(text, first_line=1)` replaces every paragraph that is exactly `[[toc]]` (column 1, trailing blanks allowed; not in quotes, lists, code, HTML, with other text or indented) by a nested list `- [plain heading text](#slug)` of ALL headings of the text (also in quotes and lists, never in code; setext included), in document order, slugs from G33 (`slugify`, `slug_dedupe`, `percent_encode_anchor`); nesting by the stack rule (nested under the nearest earlier lower-level heading; skipped levels add nothing; a lower level than all before restarts at depth 0), two spaces per depth; link text is Markdown-escaped; an empty heading is linked as `section`; no headings: the placeholder becomes empty; line endings follow the first line break; no placeholder: text unchanged; errors of `doc_index` (duplicate block IDs) propagate. Imported headings are included by running it on the output of `import_expand`. OPEN: a level range option (`[[toc 2-3]]`) | E5.3 |

## 7. Order of work and first step
1. **Epic 0** scaffold (workspace, maturin, pytest harness, `PanicException` watchdog). Includes your environment check (`rustc`, `cargo`, `maturin`, `python`, `gh`, `claude` versions; Apple Silicon needs native arm64 Python).
2. **Author the full unit catalogue** in `spec/calculation-units.md` from exact SI/NIST definitions (it is release-blocking anyway, and it becomes the Fraction oracle).
3. **E1.1 Decimal** as the first Rust feature, with a line-by-line explanation of the Rust, then E1.2 → E1.7.
4. Epics 2–8 in order, one feature at a time; you confirm tests pass locally before the next feature starts.
