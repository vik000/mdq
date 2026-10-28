# MDQ

**MDQ** is a Markdown-compatible authoring, processing and packaging format. It extends CommonMark and GitHub Flavoured Markdown with document metadata, transclusion, cross-document references, conditional content, a deliberately small calculation language, optional externally resolved variables and a ZIP-based `.mdq` package.

> **Project status:** MDQ 0.1.0 **draft specification**. This repository currently contains specification documents, examples and conformance fixtures. It does **not** yet contain a working Rust parser, evaluator, Python binding or network resolver. The specification is an implementation target, not a claim of a released interoperable standard.

## Design principles

- **Markdown first.** Ordinary `.md` remains valid; MDQ-specific constructs are additive.
- **One implementation.** Rust is the canonical core; Python uses that core through PyO3/maturin, not an independent implementation.
- **Predictable evaluation.** `calc` supports assignments, arithmetic, percentages, typed quantities and conversions; it is not a general-purpose programming language.
- **Declarative data dependencies.** YAML frontmatter, sidecars, project settings and package manifests share a metadata schema. Declarations can name external data sources, but YAML does not contain executable functions.
- **Reader-controlled I/O.** The core performs no network access. An optional HTTP resolver may be invoked by a reader with explicit host-supplied permissions. A document or manifest can request network capabilities but cannot grant them.
- **Native capabilities first.** `.mdq` is ZIP-based and preserves MDQ functionality. MDZip import and best-effort export are interoperability goals, not restrictions on the native format.
- **Specification-led changes.** Behavioural changes require updated normative text and conformance cases.

## Example

````markdown
---
variables:
  unit_price: 120
---

# Invoice

```calc
quantity = 4
subtotal = unit_price * quantity
tax = subtotal * 21%
total = subtotal + tax

distance = 12 miles
distance_km = distance to km
```

Total: {{calc.total}}
````

The exact grammar, scoping and diagnostics are defined in [`spec/extended-markdown.md`](spec/extended-markdown.md), not by this example.

## Repository layout

```text
README.md
spec/
  extended-markdown.md         # Syntax, semantics, references, calc grammar
  calculation-units.md         # Unit catalogue draft and required conversions
  sources-and-permissions.md   # Metadata, HTTP sources, permissions and testing
  package.md                   # Native .mdq packaging and MDZip interoperability goals
conformance/
  cases/core.json              # Language-neutral expected outputs and errors
tests/
  test_conformance.py          # Fixture checks and optional candidate CLI checks
docs/
  cheatsheet.html              # Offline HTML syntax cheatsheet
```

**Planned implementation directories:** `crates/mdq-core/`, `crates/mdq-http/`, `crates/mdq-cli/`, `python/`, and `.github/workflows/`. These are intentionally not represented as implemented code.

## Specification and compatibility

The current specification consists of four documents:

1. [Extended Markdown](spec/extended-markdown.md): parsing, metadata cascade, imports, references, rich features and normative `calc` grammar.
2. [Calculation units](spec/calculation-units.md): dimensions, symbols and conversion rules. **The full canonical factor table is still outstanding.**
3. [Sources and permissions](spec/sources-and-permissions.md): shared YAML schema, optional external sources, offline validation, authorised live testing and network policy.
4. [Package format](spec/package.md): `.mdq` ZIP layout, manifest, encrypted resources and planned MDZip import/export.

The [HTML cheatsheet](docs/cheatsheet.html) is a convenience reference. In any disagreement, normative specification text takes precedence over examples and the cheatsheet. The words **MUST**, **MUST NOT**, **SHOULD** and **MAY** state requirements; examples illustrate them and do not silently add requirements.

### Heading and import rules

A source reference such as `[[report.md#Results]]` selects the first matching heading in the original source file. Source identity remains stable when content is imported for rendering. Exporters must preserve or rewrite relative asset links and cross-file references so that generated output points to the correct destination. See the extended Markdown specification for details.

### External data and security

Readers decide whether and when to resolve declared sources. The optional library resolver should offer separate **offline declaration validation** and **explicitly authorised live source testing**, with structured diagnostics. Network access is denied by default; host policy takes precedence over document metadata. URL/redirect validation, SSRF protection, timeouts, limits and credential handling are mandatory concerns for any network-enabled reader.

## Roadmap

The phases below are the proposed implementation order; they are **not** release dates or claims of completion.

| Phase | Deliverable | Exit criteria |
| --- | --- | --- |
| **0 — Repository foundation** | Commit spec, cheatsheet, fixtures, licence decision, contribution guidelines and CI | All fixtures parse; documentation links work; CI runs offline |
| **1 — Rust document core** | CommonMark/GFM ingestion, document model, frontmatter and metadata cascade | Deterministic parsing and metadata tests pass |
| **2 — Calculations and units** | `calc` parser/evaluator, scopes, diagnostics, unit conversions and currency observations supplied by host | Normative grammar cases and canonical unit vectors pass |
| **3 — Composition and rich syntax** | Imports, references, block IDs, anchors, conditionals, loops, containers and export rewriting | Cross-file and error-path conformance cases pass |
| **4 — Native packaging** | `.mdq` ZIP read/write, manifest validation, resource controls and encryption integration | Round-trip and malicious-archive tests pass |
| **5 — Optional source resolver** | HTTP GET resolver, injected network policy, offline validation and live test interface | Mock-transport, denied-network, redirect and SSRF tests pass |
| **6 — Python bindings** | PyO3/maturin API exposing Rust core and optional resolver | Rust/Python parity tests for outputs, errors and data structures pass |
| **7 — Interoperability and release candidate** | MDZip import/export mappings, full unit catalogue, security review, release documentation | Documented conversion diagnostics, security review and release conformance pass |

Some tasks can proceed in parallel; for example, the canonical unit table and MDZip research need not block initial Rust parser work.

## Running the existing tests

The included Python tests currently validate **fixture structure**. Candidate implementation checks are skipped unless an implementation CLI is supplied.

```bash
python -m pip install pytest
python -m pytest -q
```

When a CLI implementing `build <entry> --root <root>` exists:

```bash
MDQ_COMMAND='path/to/mdq' python -m pytest -q
```

The test runner expects compiled Markdown on stdout and diagnostic codes on stderr for failures. A green fixture-only test run **does not** establish implementation conformance.

## Specification work still open

- Populate and review the complete versioned unit catalogue, including exact factors, affine conversions and precision rules.
- Verify MDZip's authoritative format and define import/export mappings with explicit loss reporting.
- Complete edge-case conformance coverage for anchors, links, calculations, currency observations and rich export.
- Review package extraction, network permissions, redirects, credential handling, encryption and renderer security.
- Confirm licensing, contribution policy and release versioning before publishing as a stable standard.

## Contributing and versioning

Until a formal contribution policy is added, propose changes as issues or pull requests that identify the affected specification section and include a corresponding conformance fixture. Avoid introducing implementation-only behaviour not reflected in the spec.

Specification version: **0.1.0-draft**. This draft is subject to change. No open-source licence has been selected in these starter files; add an explicit `LICENSE` before inviting external contributions.
