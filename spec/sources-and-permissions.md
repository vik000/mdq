# MDQ Sources, Metadata and Permissions — 0.1.0-draft

**Normative for source declarations and optional source resolution.** The format describes dependencies; it never grants network permission. Implementations MAY omit the optional HTTP resolver, but MUST report unsupported required capabilities rather than silently fetching or dropping values.

## 1. One schema, multiple metadata locations

YAML frontmatter, document `<stem>.meta.yaml`, directory `.mdq.yaml`, project `.mdq.yaml` and `.mdq` `manifest.yaml` share the same `sources`, `variables` and `requirements` keys. No declaration needs to appear in two files. The metadata cascade in Extended Markdown §5 applies to ordinary values. A manifest describes package-wide defaults and requirements, while the entrypoint frontmatter may override non-security values. Manifest `requirements` do not constitute grants.

Example **standalone** document (no sidecar):

````markdown
---
variables:
  apple_price:
    type: number
    unit: USD
    source:
      kind: http
      url: https://example.invalid/stocks/AAPL
      format: json
      select: /price
---
```calc
shares = 10
value = apple_price * shares
```
````

Example **project** `.mdq.yaml` (documents need not repeat this):

```yaml
sources:
  prices:
    kind: http
    url: https://example.invalid/prices/{symbol}
    method: GET
    format: json
    select: /price
variables:
  apple_price:
    type: number
    unit: USD
    source: prices
    arguments: {symbol: AAPL}
requirements:
  network: optional
```

## 2. Source declarations

A source is either an inline mapping in `variables.<name>.source` or a named definition in `sources`. `source: <name>` refers to a named source in the effective metadata. Named source definitions are reusable and are not executable function bodies. `arguments` substitute named placeholders in an explicitly declared URL template, with URL-component encoding; unknown placeholders or arguments fail validation. No string interpolation of environment variables, secrets or arbitrary expressions is allowed in URLs.

HTTP source fields in 0.1: `kind: http`, `url` (absolute HTTPS URL), optional `method: GET` (only GET), `format: json|text|csv`, optional `select` (JSON Pointer per RFC 6901 for JSON; omitted for text; CSV selection is reserved), optional `arguments`. `variables.<name>` additionally declares `type: number|string|boolean|date`, optional `unit`, and a `source` reference or mapping. Source outputs MUST be type-checked before injection. No code, scripts or custom extraction expressions are accepted. Non-HTTP `kind: reader:<identifier>` MAY identify reader-provided integrations; their semantics require a separately registered reader contract and explicit authorisation. Unrecognised required kinds fail.

JSON Pointer is used instead of JSONPath to avoid ambiguous or executable selection syntax. CSV extraction and authenticated HTTP configuration are deferred to reader-specific adapters for 0.1. The core format MUST NOT contain credentials. Source values may also be supplied directly by a trusted host, with no source fetch.

## 3. Reader / library boundary

The core Rust/Python libraries MUST parse and validate declarations, accept resolved typed variables, calculate without I/O, and emit structured diagnostics. An **optional** resolver module MAY implement HTTPS GET, response decoding, JSON Pointer extraction and value validation. It MUST default to disabled and MUST NOT run automatically on document open. The reader app controls when/if to call the resolver, credentials, cache, network permissions, provider allowlists, timeouts and refresh. The library MUST offer an injection interface so readers can supply their own resolver or pre-resolved values. `test_source` is an API concept, not MDQ syntax.

Required offline operation: `validate_source(declaration)` checks schema, URL form, type and selection syntax without I/O. Optional live operation: `test_source(declaration, host_policy, allow_network=true)` performs one authorised retrieval and returns a structured result or diagnostic. A successful test MUST include value, type, unit if any, retrieval time and source identity; failures MUST distinguish permission denial, HTTP/transport errors, selection failure, invalid data and type mismatch. Both operations MUST be separately callable. Tests MUST use a local stub/mock transport by default, not public APIs.

## 4. Permissions and safe defaults

A package manifest MAY request:

```yaml
requirements:
  network: optional
  hosts: [api.example.org]
security:
  network: request
```

This is a **request**, never an authorisation. The effective network policy is the intersection of (a) trusted reader/user policy, (b) trusted library policy, and (c) document requests. The most restrictive policy wins. The library MAY ship its own policy engine and host allowlist; the reader MUST be able to further restrict it. A package cannot elevate host permissions, even when signed or encrypted. `network: required` means fail clearly when denied; it does not override denial.

By default: network disabled; no implicit HTTP fetch on opening or building a document; HTTPS only; no redirects unless independently reauthorised and revalidated; reject credentials in URLs; reject loopback, private, link-local, multicast and metadata-service addresses, including after DNS resolution and on every connection; block local file, `data:` and non-HTTPS schemes; bound response bytes, redirects, timeouts and concurrency; do not forward credentials to different origins; no automatic secret disclosure. Revalidate the actual connection destination to mitigate DNS rebinding. Host-specific permissions MAY be more restrictive. An approved host allowlist is necessary but not sufficient to bypass IP-range checks unless a trusted enterprise policy explicitly allows a private endpoint.

## 5. Reproducibility, provenance and cache

Resolved external values MUST carry source identity, retrieved-at timestamp, value, type, unit and, when relevant, observation/valuation timestamp, currency and market. Cached values MUST be keyed by normalised declaration, arguments, date and provider identity; a cache is not permission to make a new request. Offline deterministic builds MUST use explicit resolved snapshots. A failed fetch MUST NOT silently substitute stale data or today's price. Host policy determines whether an explicitly authorised cached value is acceptable. Errors: `MDQ_SOURCE_INVALID`, `MDQ_SOURCE_UNRESOLVED`, `MDQ_SOURCE_FETCH_FAILED`, `MDQ_SOURCE_TYPE_ERROR`, `MDQ_SECURITY_DENIED`.

## 6. Conformance

The conformance suite MUST include: inline frontmatter source, named inherited project source, document override, offline validation, network-denied live test, mocked successful JSON Pointer retrieval, invalid pointer, bad type, unsupported scheme, private address, redirect to private address, oversized response, timeout, missing valuation rate and reproducible snapshot evaluation. Real network calls are never required for conformance.
