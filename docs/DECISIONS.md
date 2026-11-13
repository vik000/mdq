# MDQ — Decisions, sacrifices, open questions

Per feature. Veto by editing or telling me. Test-plan gap rows (G-numbers) in `docs/TEST_PLAN.md` hold the full wording.

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
