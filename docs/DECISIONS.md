# MDQ — Decisions, sacrifices, open questions

Per feature. Veto by editing or telling me. Test-plan gap rows (G-numbers) in `docs/TEST_PLAN.md` hold the full wording.

## E7.3 age encryption (G49)
**Decisions**
- Audited RustCrypto primitives (x25519-dalek, chacha20poly1305, hkdf, hmac, sha2, scrypt; default features off, so no OS random source in the core); the age v1 format (header, stanzas, MAC, STREAM payload, Bech32 keys, base64) is our own code; pyrage is the oracle in both directions.
- Randomness is supplied by the host (`random` bytes of exactly `age_random_len(...)`); the core never reads the OS random source. Same input + same random = same output.
- Recipient types: X25519 and passphrase (scrypt). Decryption caps the scrypt work factor at log2 N <= 20 (host may lower it), checked before any scrypt work; above: MDQ_RESOURCE_LIMIT.
- API: explicit `age_decrypt`, `age_encrypt`, `package_open_age`; plain `package_open` on age data is MDQ_SECURITY_DENIED (no implicit decryption).
- Codes: malformed envelope MDQ_INVALID_SYNTAX; missing/wrong key and any authentication failure (header MAC, modified or truncated payload) MDQ_SECURITY_DENIED; armored files and other age versions MDQ_UNSUPPORTED_FEATURE.
- Header bounds: 1 MiB and 256 stanzas (the header is attacker-controlled; each X25519 stanza costs one key exchange per identity).
- Unknown stanza types are ignored (age "grease"), known ones are checked strictly; an scrypt stanza must be alone.
- `[profile.dev.package."*"] opt-level = 3`: dependencies are optimised in dev builds (scrypt is unusably slow otherwise); our own crates keep debug settings.
- Plaintext export permission is part of export (E8.1).

**Sacrifices**
- Armored (PEM) age files are not supported.
- No plugin recipients (age-plugin-*), no SSH keys.
- scrypt at log2 N = 20 needs 1 GiB of memory; hosts with less should lower `max_work_factor`.

**Open**
- Per-member encryption layout (spec gap 4): strict xfail `test_per_member_encryption_layout`.

## Examples and cheatsheet (G59)
**Decisions**
- Examples are small runnable projects with their exact output recorded as terminal transcripts; a test reruns them all, so they cannot drift from the library.
- The cheatsheet is generated, never hand-edited: each snippet's output is produced by the library at build time (the old hand-written page had a `{{calc.total}}` without a calc block and a manifest without `package`). A test fails when it is stale.
- Generated packages (`guide.mdq`, `guide.mdq.age`, `key.txt`) are not committed: encryption is randomised, and the demo key must not look like a real one.
- Cheatsheet snippets avoid values at the start of a line, so the open G26 escaping question (`7.5` -> `7\.5`) does not show up as if it were intended behaviour.

## Cross-cutting: CI (G58) — decisions taken before implementation
**Decisions**
- Tests on every push/PR to master: Ubuntu x Python 3.9-3.13, plus macOS and Windows on 3.12; `HYPOTHESIS_PROFILE=ci`; also mypy --strict and clippy.
- Wheels with PyO3/maturin-action (abi3: one wheel per platform for every Python >= 3.9): Linux x86_64 + aarch64 (manylinux), macOS x86_64 + arm64, Windows x64, plus the sdist; each runs `scripts/wheel_smoke.py` where the runner can execute it.
- Wheel builds on version tags (`v*`) and manual dispatch, uploaded as artifacts; nothing is published to PyPI.
- Before the first push, the suite is run locally on Python 3.9, 3.10, 3.11 and 3.13 (uv-managed interpreters, in uv's cache).

## Fix: references, block anchors, duplicate IDs (G57; BUG-1, BUG-2, BUG-3 of G56)
**Decisions**
- BUG-1: `[[...]]` reference links are implemented as spec 3/3.1 defines them: `[[path.md]]`, `[[path.md#Heading]]`, `[[path.md#^id]]`, `|Label`; an empty path means the containing file. A target rendered in the output becomes `[Label](#anchor)` (its first rendered occurrence); default label = heading text, block ID or the path as written. Unknown heading/block: MDQ_UNRESOLVED_REFERENCE; missing file: MDQ_MISSING_IMPORT; never in code or HTML; `\[[` stays literal; `[[toc]]` keeps its meaning.
- Block IDs appear as raw HTML anchors `<a id="p1"></a>` at the start of their block (after `# ` for headings), for every block ID; generated heading slugs never take a block ID's name (`a` becomes `a-2`), also in the TOC (BUG-3).
- A target that is not in the compiled output links the referenced file: `[Setup](other.md#setup)` (path relative to the entry's directory, anchor = the 3.1 slug inside that file); the heading or block must still exist there, so the anchor is never broken.
- BUG-2: every source document is checked for duplicate block IDs (MDQ_DUPLICATE_ID at the second one).
- Two phases: references are resolved per source file (so errors point at the reference and targets use source identity), then rendered against the combined output (first rendered occurrence, output slugs). Placeholders made of private-use characters carry them through imports and the later passes.
- References in headings are refused (they would change the heading's slug and the TOC).
- The duplicate-ID check runs on the source with container fence lines blanked, so IDs inside `:::if`/`:::for` bodies are seen, including excluded branches (uniqueness is per source document).
- Also fixed (G56 findings): unsupported `mdq`/`features` and reserved-namespace errors now name the declaring file.
- Existing tests adjusted (assertions only): two E8.1a tests and two G56 fixtures expected markers simply removed; they now expect the `<a id>` anchors. The three BUG fixtures pass, their xfails are removed.

**Sacrifices**
- Heading anchors are implicit (3.1 slugs); a renderer that slugs differently (e.g. GitHub's `user-content-` prefix) will not match them. Block anchors are explicit HTML, so they always work.
- Lines of reference errors after removed `:::if`/`:::for` branches can be off (same as other post-container errors).

**Open**
- The import-error line inside an INCLUDED `:::if` branch (G56 finding) is still off by the removed fence line; it is tied to G50 (keep fence lines as blank lines or not).

## Cross-cutting: rule ledger and conformance fixtures (G56)
**Decisions**
- Linking: each rule in `conformance/rules.yaml` lists the tests that cover it by pytest node ID and/or conformance fixture ID; a meta-test fails if a listed test or fixture does not exist, or a rule has neither. Existing test files stay untouched (new tests may also use `@pytest.mark.rule`).
- Scope: every normative MUST/MUST NOT now (about 100 rules), each with at least one test or fixture; a rule without a test gets a new fixture; an ambiguous one a strict xfail with its gap ID.
- CLI parity: every fixture also runs through `mdq.cli.main([..., "--format", "json"])` in-process and must agree with the library (markdown, diagnostic code/path/line) and the CLI schema; a handful also run as a real `mdq` subprocess.
- Built with three parallel mapping passes (spec 1-7, 8-11, the other spec files); every cited test was read before being cited, every fixture was run before its expectation was recorded, and an expectation that would bless spec-violating behaviour was recorded as a BUG instead. 172 rules, 155 fixtures.
- `xfail` tags: `G<n>`/`GAP-<n>` for spec gaps, `BUG-<n>` for known defects listed below (the schema allows exactly these).

**Bugs found (strict xfail fixtures) — all three fixed in G57**
- BUG-1: `[[...]]` reference/wiki links stay literal in compile output (recorded as a choice in E8.1a), but spec §1, §3 and §3.1 require them to be rewritten to the compiled target or fail with MDQ_UNRESOLVED_REFERENCE (`See [[b.md#Nope]]` compiles silently). Needs your decision: implement reference links, or amend the spec.
- BUG-2: duplicate block IDs in one source document are not detected by compile (`a ^p1` / `b ^p1` compiles); only `#^id` embeds index the target. Spec §3: IDs MUST be unique per source document.
- BUG-3: generated heading anchors ignore user block IDs (`Para ^a` + `# A` gives a TOC link `#a`); spec §3.1 gives the block ID priority (generated one should become `a-2`). `slug_dedupe` already takes reserved names; `toc_build`/compile do not pass them.

**Other findings (no xfail yet)**
- An MDQ_MISSING_IMPORT raised inside an INCLUDED `:::if` branch reports line 1 instead of 2 (no branch was removed, so this is beyond the known approximation).
- The reserved-namespace MDQ_METADATA_ERROR and the unsupported `mdq`/`features` errors carry no path although the declaring file is known.
- A missing host-supplied declared variable has a path but no line (the YAML loader keeps no value positions).
- `CALC-PRECISION-IN-FIXTURES` (fixtures must state their precision) is met implicitly: precision is fixed at 28 (tested); the fixture format has no precision field. `CALC-PRECISION-EXPOSED` is met only through `_internal.dec_precision()`, not `mdq.version_info()`.
- `CALC-EXPORT-COLLISION`: only the default MDQ_CALC_ERROR exists; compile has no "disambiguating namespace" option.
- `SRC-CACHE-KEY`: there is no cache; the snapshot key covers the normalised declaration and arguments, not the date. Does "keyed by ... date" apply to snapshots?
- `PKG-MARKDOWN-SEMANTICS`: path/URL security inside packages (`../`, `https://`, `/abs` imports and variable files) was checked by hand but no unit test asserts it for packages.

**Sacrifices**
- The fixture format covers single documents with inputs/profile/calc_blocks only; FX, limits, packages, transports and policy rules link unit tests instead of fixtures.

## Cross-cutting: sdist fix (G55)
**Decisions**
- The sdist carries `spec/units.tsv` through `[tool.maturin] include` (format sdist), at the same relative path, so `include_str!("../../../spec/units.tsv")` resolves when building from the sdist. The normative file in `spec/` stays the single copy (no duplicate in the crate to keep in sync).
- Guard: a fast test lists every `include_str!`/`include_bytes!` in `crates/` and checks the built sdist contains each; verified to fail when the include line is removed. The opt-in run also builds a wheel from the unpacked sdist and smoke-tests it.

**Sacrifices**
- `mdq-core` alone still cannot be packaged for crates.io (`cargo package`), since the file is outside the crate; not a goal for 0.1.

## E8.4 wheel smoke test (G55)
**Decisions**
- A standalone script (`scripts/wheel_smoke.py`) does the work, so CI can run it directly; the pytest test wraps it and is opt-in (`MDQ_WHEEL_SMOKE=1`), because a cold release build takes minutes. The cheap checks (version equals the Cargo workspace version, packaged files exist, console script declared) always run.
- Installs use `--no-index`: the wheel has no runtime dependencies, and the check must not depend on the network.
- Every check runs from a temporary directory outside the repo with PYTHONPATH cleared, and asserts mdq was imported from the venv, so the source tree can never mask a packaging bug.
- An interpreter that cannot load the wheel's architecture (x86_64 Python under Rosetta on Apple Silicon) is reported as SKIP with the reason, not as a failure; testing no interpreter at all fails.

**Sacrifices**
- Locally verified only on macOS arm64 with Python 3.12 and 3.14; the 3.9-3.13 matrix and Linux/Windows wheels are left to CI (cross-cutting).
- The sdist is not tested yet (known issue: `include_str!("../../../spec/units.tsv")` reaches outside the crate).

**Open**
- None new.

## E8.3 CLI (G54)
**Decisions**
- Commands: `mdq build ENTRY` (Markdown to stdout or `-o FILE`), `mdq check ENTRY` (evaluate, diagnostics only), `mdq sources ENTRY` (declarations, offline), `mdq --version`. Common options: `--root DIR` or `--package FILE` (`--identity FILE`, `--passphrase-file FILE` for .mdq.age), `--input name=value`, `--snapshot FILE.json`, `--profile`, `--calc-blocks`, `--allow-plaintext`, `--format text|json`. No network command: `test_source` stays a library call. Never writes over a source file.
- Exit codes: 0 success; 1 invalid input (syntax, metadata, references, variables, calc, any MDQ_SOURCE_*); 2 usage error; 3 unsupported feature; 4 security denied; 5 resource limit.
- `--format json`: always one JSON object on stdout `{ok, diagnostics: [{code, message, path, line, column}], ...}`; success adds `markdown`/`calc`/`values` (build), `calc`/`values` (check), `declarations` (sources); decimals as strings; JSON Schema shipped as `mdq/cli.schema.json`, every output validated in tests (jsonschema in dev extras).
- `-o` protection covers every file the command read through the file system (document, metadata files, sidecars, imports, variable files, bibliographies) and the package file; replacing a previous output is fine.
- `--input` values: a plain decimal is a number, `true`/`false` a boolean, anything else text (so `007` stays text).
- Option files (snapshot, observations) are JSON whose numbers are read as exact Decimal; an unreadable or invalid file is a usage error (exit 2), not a document error.
- Value errors about a declared variable (missing input, missing snapshot entry, type mismatch) now carry the declaring file's path (core change, also visible in the library).
- `__main__.py` is guarded so tools (stubtest) can import it.
- Parity of library and CLI on shared JSON fixtures waits for the conformance fixtures (cross-cutting).

**Sacrifices**
- No network command; `test_source` is library-only.
- No `--limits` or `--inputs FILE` options yet.

**Open**
- The G26 escaper also escapes a value starting with `-` at the start of a line (`-1.5` becomes `\-1.5`, renders the same); same question as `7.5`.

## E8.2 exceptions, typing, threads, memory (G53)
**Decisions**
- Exception hierarchy: one subclass of `MdqError` per stable code (16), plus two families: `ReferenceFailure` (missing import, import cycle, ambiguous/unresolved reference, duplicate id) and `SourceError` (the four MDQ_SOURCE_* codes). `.code`, `.path`, `.line`, `.column` stay on every exception; `except mdq.MdqError` still catches everything. `ReferenceFailure` instead of `ReferenceError` so Python's built-in is not shadowed.
- The code-to-class mapping is a Rust `match` without a fallback arm: a new code without a class does not compile.
- Typing: the compiled module gets a hand-written stub (`_native.pyi`), checked by `stubtest`; the unstable `_internal` hooks stay untyped and are excluded from `mypy --strict`. `mypy` joins the dev extras.
- Thread safety comes from the GIL: compile holds it (the host's read/transport callbacks are Python), so calls are serialised and results identical; no shared mutable state in the core.
- Memory is measured as peak RSS growth (`ru_maxrss`), because Rust allocations are invisible to `tracemalloc`.

**Sacrifices**
- Compile does not release the GIL, so threads do not run compiles in parallel (correct, not faster).
- `_internal` has no type stubs.

**Open**
- None new.

## E8.1c packages in compile and the public source API (G52)
**Decisions**
- Packages: `pkg = mdq.PackageFS(data, identities=None, passphrase=None, limits=None)` opens and checks the package (E7.1-E7.3); `mdq.compile(pkg)` compiles its entrypoint (`entry` optional). The manifest's metadata is the lowest layer (under the root `.mdq.yaml`); its `security`/`requirements` are requests only (E6.4). Works with `evaluate` and every option.
- Plaintext: compiling a package opened from `.mdq.age` is MDQ_SECURITY_DENIED unless `compile(..., allow_plaintext=True)`; `evaluate` is allowed (the host holds the keys and chose to decrypt); a plain `.mdq` needs no permission.
- Sources: `mdq.validate_sources(entry, fs)` lists the declarations of the document's effective metadata offline; `mdq.test_source(entry, fs, name, *, host_policy, library_policy, transport, retrieved_at, allow_network=True)` performs one retrieval (E6.5) and returns a snapshot entry for `compile(snapshot=[...])`.
- Policy: two trusted dicts (`host_policy`, `library_policy`); the document's requests (manifest and every metadata layer) are applied automatically; network denied unless both trusted layers allow (E6.4).
- Several document request layers compose most-restrictively (new `policy_intersect_docs`): a later layer (sidecar, frontmatter) can never widen what an earlier one (manifest, `.mdq.yaml`) narrowed, unlike ordinary metadata, which cascades by precedence.
- Requests in metadata files use the strict G45 spelling (`security.network: request|none`); only the manifest also accepts `deny` (G48).
- The manifest's `features` are checked when the package opens (G48), not again as metadata.
- `PackageFS` detects age data itself (`age_is`); credentials are used only for age data.
- The public `test_source` is marked `__test__ = False` so pytest never collects it.

**Sacrifices**
- `allow_plaintext` is a per-call flag; there is no policy object for it yet.

**Open**
- None new.

## E8.1b data and sources in compile (G51)
**Decisions**
- Variable files: a string directly under `variables:` is a file reference only if it starts with `./` or `../` and ends in .yaml, .yml or .json; anything else stays a plain string. The path is relative to the file that declared it (`inputs`: the root), with the usual confinement. The file must hold a mapping, which becomes the namespace (`{{project.name}}`); missing file MDQ_MISSING_IMPORT; references inside variable files are not followed.
- Bibliographies: metadata key `bibliography:` (a path or a list of paths, Pandoc's convention), relative to the declaring file; format from the extension (.bib BibTeX, .json CSL JSON, else MDQ_UNSUPPORTED_FEATURE); missing MDQ_MISSING_IMPORT.
- Declared variables: `compile` never fetches. A source variable takes its value from `snapshot=[...]` (E6.5 format, checked against its declaration); a host-supplied one (type, no source) from `inputs`, type-checked. Any declared variable without a value is MDQ_SOURCE_UNRESOLVED, even if the text never uses it.
- FX: `compile` takes the E1.6 arguments (`observations`, `valuation`, `market`, `pins`).
- Order: declarations are read from the file layers before `inputs` go on top and before variable files are loaded (so an input cannot overwrite the declaration it fills, and a data file's `type:` key is data).
- An input for a variable that has a `source` is refused (MDQ_SOURCE_INVALID): its value must come from the snapshot.
- A declared number shows as `"1.5 USD"` in text and is a quantity/money in calc; a unit that is neither a catalogue unit nor a currency code is refused up front (never silently invisible to calc).
- The returned `metadata` is what the document sees: variable files loaded, declarations replaced by their values (the raw declarations are still in the source files; `values` lists them with type, unit and source).
- E8.1a test adjusted: `test_variable_files_not_supported_yet` (the E8.1a placeholder) now checks the E8.1b rules; same name kept.

**Sacrifices**
- Only strings directly under `variables:` can be files (not deeper in a namespace).
- Provenance for values inside a variable file points to the layer that named the file, not to the file's own lines.

**Open**
- Provenance inside variable files; scoped metadata for imports (both carried from E8.1a).

## E8.1a compile and evaluate (G50)
**Decisions**
- Sources are never modified: `compile`/`evaluate` read through the file-system callback and return new text and data; they write nothing (saving is the host's job).
- Three slices: E8.1a one document from a file system (frontmatter, sidecar, `.mdq.yaml` cascade, imports, if/for, calc, interpolation, rich fallbacks, export); E8.1b variable files, FX observations, source snapshots; E8.1c packages (manifest as lowest layer, `.mdq.age`, plaintext-export permission) and the public `test_source`.
- File input: `compile(entry, fs=...)` with `mdq.MemoryFS({path: str|bytes})` or `mdq.DirectoryFS(root)` (E4.1 confinement, symlinks included); a dict is shorthand for MemoryFS; the core reads through one callback and never touches the disk itself.
- G11 resolved for compile: a bare calc name resolves to (1) an earlier assignment in the same block, then (2) an entry directly under `variables:` of the effective metadata. Both places count: any metadata layer (sidecar, frontmatter, directory/project `.mdq.yaml`, later the manifest) and in-memory host `inputs=`, which is the top layer ("explicit invocation overrides", spec 5). Unknown name: MDQ_CALC_ERROR. Other metadata (`title`, ...) is never visible to calc.
- Two functions: `mdq.compile(entry, fs, inputs=..., calc_blocks="remove"|"keep")` returns the new Markdown plus the data; `mdq.evaluate(entry, fs, inputs=...)` returns only the data (each calc block's source, position and results as exact Decimal, type, unit, display text; the effective metadata). `calc_blocks="remove"` (default) leaves calc blocks out of the output, `"keep"` keeps their source as a plain code block; the data is always returned either way.
- Order for calc (spec 9 leaves it open): containers and `{{...}}` per file with `{{calc.*}}` deferred, then imports, then the calc blocks that survived, then a second pass for `{{calc.*}}`. So calc blocks in excluded branches are never evaluated, and `:::if`/`:::for` cannot use calc results (MDQ_UNSUPPORTED_FEATURE).
- Interpolation scope (spec 5 "frontmatter fields under `document.*`, imported variable mappings use their declared namespace"): `document.*` = the whole effective metadata; each `variables:` entry is a top-level name; `document` and `calc` are reserved (MDQ_METADATA_ERROR).
- Calc inputs from metadata: numbers, and strings written as calc literals (`"120 EUR"`); other values are not visible to calc.
- Each calc block keeps its own scope (spec 8.1); blocks share values only through `{{calc.*}}` in text.
- Strings that look like variable files are refused until E8.1b reads them, so their meaning cannot change silently.
- Calc fences inside quotes or list items are refused, not skipped.
- Imported files' own frontmatter is ignored; the root document's metadata applies everywhere.
- Block-ID markers are stripped from the output (spec 2); wiki links `[[x]]` stay literal text.
- `mdq`/`features` are checked; core, rich and calc are now supported profiles.
- Existing tests adjusted (assertions only): `test_e5_f52_math_footnotes.py` (the idempotency test now also accepts MDQ_UNSUPPORTED_FEATURE since 3544e4b; the regex oracle ignores the `\$` fixed-point escape since 1f8c87b); `test_e7_f73_age.py` (`test_bad_identities[ids2]` was flaky once in 32 runs).

**Sacrifices**
- Line numbers of errors raised after imports (second calc pass, export) are mapped through the import source map; after removed `:::if`/`:::for` branches they can be off (calc block errors are exact when the block content is unique in its file).
- No scoped metadata for imported files.
- Bibliographies are not loaded yet, so citations fail until E8.1b.

**Open**
- After `:::if`/`:::for`, the paragraphs on either side of a removed fence merge (`A` / `:::` / `B` renders as one paragraph), although G31 treats fences as block boundaries. Inserting a blank line would keep them apart but turn loop-built lists into loose lists. Strict xfail `test_container_boundaries_survive_evaluation`. Which behaviour?
- The E2.4 escaper (G26) escapes `N.` at the start of a line even when no space follows (`7.5` becomes `7\.5`, renders as `7.5`). Narrow it to real list markers?
- Exception hierarchy under `MdqError`: E8.2.

## Fix: E5.2a maths fallback, backticks (G38)
**Decisions**
- The inline code-span delimiter avoids the length of every unmatched (literal) backtick run in the paragraph, not just runs in the maths body: `` `a $b$ c `` becomes `` `a ``b`` c `` (before: `` `a `b` c ``, which rendered as code `a `). Matched code spans are ignored, so existing outputs such as `` `code` and `x` `` are unchanged.
- An inline span directly touching a backtick (literal run or code-span delimiter) is MDQ_UNSUPPORTED_FEATURE at the opener's position: the new delimiter would merge with that run. Escaping was tried and rejected: an escaped backtick can still close an earlier code span (`` `a``$a$ ``). An escaped backtick next to maths is fine.
- New property test: the CommonMark-rendered text of the output equals the input's minus `$` (40,000 examples clean).

**Sacrifices**
- `` `$a$ `` or `` `x`$a$ `` (maths glued to a backtick) cannot be exported; the author must add a space.

**Open**
- Maths overlapping a code span (`` $`$` ``): Pandoc rules (G38) read maths, CommonMark reads a code span. Current behaviour: maths wins. Strict xfail `test_math_overlapping_a_code_span`. Which should win?

## Fix: E5.2a maths fallback output is a fixed point (G38)
**Decisions**
- Found by Hypothesis (`$$a\n$$a$$a$$`): a `$` run that failed to open a span (its first following `$` follows a line break) could pair on a second pass once a later span had become a code span. Such runs are now escaped as `\$`, only when the re-scan of the output would pair them (loop until nothing pairs). `$a $b$` stays `$a `b``.

**Sacrifices**
- Output may contain `\$` where the input had `$`; renders identically in CommonMark.

## E7.2 package manifest (G48)
**Decisions**
- `security`: accept both spellings. `network: deny` is read as `none` (package.md example) next to G45's `request|none`; `remote_imports: false` is accepted, `true` is MDQ_UNSUPPORTED_FEATURE. The result is the G45 document layer (requests only, never grants).
- `features`: non-empty list of unique known profiles (core, rich, calc, package), must include `package`; unknown profile MDQ_UNSUPPORTED_FEATURE naming it. `package` is now a supported profile; core/rich/calc are added when `compile` lands (E8.1).
- Other top-level keys are shared metadata: returned untouched as `metadata` (lowest cascade layer in E8.1). Sources/variables are validated at compile, not at open (a named source may live in another layer).
- API: `package_open(data, limits=None)` = `zip_read` + `manifest_parse(text)`; no transport parameter (opening never fetches).
- `version` must be a quoted string: `version: 0.1` (a YAML number) is MDQ_METADATA_ERROR with a hint, never coerced.
- `entrypoint` uses the import path rules (G32) and must end in `.md` (case-sensitive); it is found among the archive files by NFC identity, and must itself be valid UTF-8 (G28).
- The manifest must be exactly `manifest.yaml` at the root (case-sensitive, no `.yml`).
- `requirements` errors keep the E6.4 codes (MDQ_SOURCE_INVALID "invalid policy: ...") so frontmatter and manifest requirements fail the same way.
- Wrong `format`/`version` are MDQ_UNSUPPORTED_FEATURE (a future format), wrong types and missing keys MDQ_METADATA_ERROR.

**Sacrifices**
- Manifest key errors carry no line number (the YAML loader keeps no value positions; same as E6.1).
- A package whose manifest says `features: [core, rich]` (the spec's own example) does not open until `package` is added.

**Open**
- Spec example lacks `package` in `features` (strict xfail `test_spec_example_verbatim`): fix the example, or drop the "must include package" rule.
- Spec conflict on `security` spelling (package.md vs sources-and-permissions.md): ask for a spec fix.

## E7.1 hostile-zip reader (G47)
**Decisions**
- Own DEFLATE decoder (RFC 1951, puff-style) and own CRC-32: no new dependency; zlib raw inflate is the differential oracle. Output is bounded while decoding.
- Strict subset: methods stored and deflate only; data descriptors allowed but must equal the central directory; zip64, multi-disk, zip encryption and unknown flags are MDQ_UNSUPPORTED_FEATURE.
- Names: UTF-8 (bit 11) or plain ASCII; no cp437 guessing. Absolute, `..`, backslash and `:` are MDQ_SECURITY_DENIED; empty/`.` segments and control characters MDQ_INVALID_SYNTAX.
- Collisions: duplicates compared in NFC, plus case-insensitive collisions and file-vs-parent-directory conflicts are MDQ_SECURITY_DENIED (they would overwrite each other on macOS/Windows), although the MDQ VFS itself is case-sensitive.
- Symlinks and special files are refused whenever the high 16 bits of external_attr carry a Unix type (the "made by" host byte is not trusted).
- The local records must tile the archive exactly: this rejects prefix data (self-extractors), gaps and overlapping members (non-recursive bombs).
- Limits: defaults 1000 entries, 16 MiB per member, 64 MiB total, 64 MiB archive, ratio 100:1 above 1 MiB; caps 10000 / 64 / 256 / 256 MiB / 100; host values above a cap are clamped (as in E6.4). All size checks use declared sizes before decompressing; decoding then enforces them.
- Messages name members by position only, never by (hostile) name. Nested zips are returned as opaque bytes, never opened.
- Directory entries are listed separately and carry no data.

**Sacrifices**
- Archives over 4 GiB or 65,535 entries (zip64) cannot be read.
- Archives from writers that add non-TLV padding to extra fields (e.g. some APK aligners) are rejected.
- Bit-by-bit Huffman decoding: simple to check, slower than zlib (fine at these limits).

**Open**
- Manifest, `features`, entrypoint (E7.2); age and per-member encryption (E7.3, spec gap 4).

## E6.5 resolver, fake transport and snapshots (G46)
**Decisions**
- Transport protocol: a callable, dict in / dict out (`resolve`, `get`); in Rust a `Transport` trait. Unknown response keys are ignored.
- The connection is pinned to the first validated address; every resolved address must pass the policy; the reported `peer` must be that address (strict, compared as IPs).
- No automatic fallback to snapshots or stale data: a failed fetch is an error.
- Caller headers go only to the first origin (scheme + host + port); names equal up to case are rejected.
- Source identity = origin + sha256 key of the declaration (template URL, not the expanded URL); `named` is not identity.
- `retrieved_at` is supplied by the caller; the core never reads the clock.
- csv and `reader:` sources are MDQ_UNSUPPORTED_FEATURE here (need a reader adapter).
- One leading BOM in a response body is dropped (same rule as G28).
- JSON parse errors get a fixed message (the parser could quote a duplicate key from the body).
- A Location that is not a valid URL is MDQ_SOURCE_FETCH_FAILED (server fault), not MDQ_SOURCE_INVALID.
- Exception classes: only the class name is reported (plain identifier, else `Exception`); KeyboardInterrupt and other BaseExceptions propagate unchanged.
- Public name `test_source` reserved for E8.
- Fixed a bug in the reference `url_join`: `/..` and `.` on an empty base path now give `/` (RFC 3986).

**Sacrifices**
- No HTTP/2, streaming, cookies or automatic retry; redirect targets limited to https (policy).
- Snapshot numbers must be int or Decimal (a float is a type error).

**Open**
- Port policy beyond the defaults.
- Carried forward: G16 (ISO 4217 vs any 3 capitals), G24/G25, per-member encryption layout (Epic 7), block-ID `^id` / wiki links in export (E8.1), ISO date-times.

## E6.4 network policy (G45)
**Decisions**
- Network is allowed only if reader/user AND library layers both say allow; a missing layer or key means deny.
- Documents can only request `network` (none/optional/required) and `hosts`; any other key is an error (never silently ignored).
- `required` is only a flag; a denial stays a denial (MDQ_SECURITY_DENIED).
- Host patterns: exact, canonical IP literal, or `*.suffix` (>= 2 labels; never matches the apex or an IP).
- `private_endpoints` must be listed by both trusted layers; an allowlist never bypasses IP rules.
- Defaults: port 443 only, 1 MiB, 10 s, 0 redirects, 4 concurrent; hard caps 16 MiB, 60 s, 5, 16 (clamped).
- Malformed policy = MDQ_SOURCE_INVALID ("invalid policy: ...").

**Sacrifices**
- Policy dicts, not YAML; no per-host limits.

**Open**
- Per-host ports/limits; whether the library should ship a default allowlist.

## E6.3 URL and IP classification (G44, resolves G10)
**Decisions**
- CGNAT 100.64/10 blocked; also Azure 168.63.129.16, documentation, benchmark, reserved and 6to4-relay ranges.
- IPv6: only 2000::/3 allowed; mapped, NAT64 and 6to4 follow the embedded IPv4.
- Legacy IPv4 notations (decimal, octal, hex, short) are parsed WHATWG-style and classified as the address they mean.
- Blocked names: single-label hosts, localhost, local, internal, localdomain, home.arpa, lan, corp, home (and subdomains).
- Non-https scheme, credentials, blocked IP/name = MDQ_SECURITY_DENIED; malformed URL = MDQ_SOURCE_INVALID.
- Diagnostics never echo any part of the URL.

**Sacrifices**
- IDN hosts rejected (give punycode); `%` in hosts rejected; empty port rejected.
- `.example`, `.test`, `.invalid` are NOT blocked (spec examples use example.invalid).

**Open**
- Port policy (only 443?) belongs to E6.4.

## E6.2 JSON Pointer and type checks (G43)
**Decisions**
- Failed selection is MDQ_SOURCE_UNRESOLVED; bad pointer syntax or bad JSON is MDQ_SOURCE_INVALID; type mismatch is MDQ_SOURCE_TYPE_ERROR.
- No coercion of JSON values ("1" is not a number); dates are `YYYY-MM-DD` only, returned as str.
- Numbers keep their exact digits (no rounding to 28); out of range = type error.
- Text bodies: JSON number grammar after trimming; one trailing line break dropped for strings.
- Error messages never include document values.

**Open**
- ISO date-times; unit token validity (G16).

## E6.1 sources and URL templates (G42)
**Decisions**
- Named source `arguments` are defaults; variable `arguments` override.
- Empty argument values rejected.
- An all-dots value (`.`, `..`) has its dots percent-encoded.
- Templates: ASCII only, no fragment, no `${...}`, placeholders only after the authority.
- Credential-like keys (headers, token, password, ...) rejected.
- `reader:<id>` accepted without an adapter; only `arguments` allowed with it.
- A variable is a declaration iff it is a mapping with `source` or `type`.
- All declaration errors are MDQ_SOURCE_INVALID, located by key path in the message.

**Sacrifices**
- No line/column on declaration errors (the YAML loader keeps no positions).

**Open**
- Unit token validity (G16: ISO 4217 list or any 3 capitals).

## E5.4 export (G41)
**Decisions**
- Generic containers fail unless `optional="true"`.
- `if`/`for`/`{{var}}`/`![[embed]]` must be evaluated before export.
- Profiles: `commonmark`, `gfm` (gfm keeps `$` math).

**Open**
- Block-ID markers `^id` and wiki links `[[x]]` are left untouched; decide in E8.1 (`compile`).

## Earlier, still open
- G16 ISO 4217 vs any 3 capitals.
- G24 / G25 offered for veto.
- Per-member encryption layout (spec gap 4): xfail in Epic 7.
- G37 unknown callout type fails; G38 partial-embed footnote definitions, unused defs allowed; G39 citation format `(Label year)`, `## References`, malformed bibs MDQ_SOURCE_INVALID; G40 no TOC level range.
