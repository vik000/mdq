# MDQ — Decisions, sacrifices, open questions

Per feature. Veto by editing or telling me. Test-plan gap rows (G-numbers) in `docs/TEST_PLAN.md` hold the full wording.

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
