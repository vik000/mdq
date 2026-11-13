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
