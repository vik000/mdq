# MDQ Package Specification — 0.1.0-draft

This draft defines the proposed `.mdq` package profile. MDZip **import** and **best-effort export** are interoperability goals **not yet verified against an authoritative MDZip specification**. Native `.mdq` MUST NOT be constrained to MDZip features. MDZip export MUST report unsupported MDQ features; strict export fails on loss, while explicitly requested compatible export MAY materialise computed values or apply documented transformations. No native `.mdq` file is required to be directly readable by an MDZip reader. Do not claim compatibility until a fixture and manifest comparison are completed.

- `.mdq` MUST be a ZIP-compatible archive with UTF-8 member names, a root `manifest.yaml`, and a Markdown entrypoint.
- The manifest MUST declare `format: mdq`, a supported `version`, `entrypoint`, and required `features`.
- The reader MUST verify archive member names, reject traversal/absolute paths, duplicates, symlinks unless explicitly supported, excessive compression ratios, oversized members and expansion beyond resource limits.
- The entrypoint and referenced Markdown files MUST follow Extended Markdown syntax and security semantics.
- Files may include `assets/`, `data/`, `sections/` and encrypted resources; these names are conventions, not mandatory directories.
- Encrypted resources SHOULD use established `age` file encryption. `.mdq.age` denotes whole-package encryption. A reader MUST NOT claim a ZIP archive is encrypted simply because its manifest says so.
- A package MUST NOT grant itself permissions. The trusted host controls decryption credentials, plaintext export and network access. A manifest MAY declare `requirements.network` and `requirements.hosts`, but these are requests, not grants. Effective permissions MUST be computed by the host and optional library policy engine as described in `sources-and-permissions.md`.
- The package manifest, sidecars and frontmatter share one metadata schema and cascade for non-security values. Source declarations MAY be placed in any one of them; duplication is not required.
- Opening a package MUST NOT automatically fetch remote data. A reader MAY invoke an optional library HTTP resolver only after explicit authorisation; offline operation and source validation MUST remain available.
- Signatures, content-addressed integrity, MDZip compatibility, canonical archive ordering and cryptographic manifest binding are reserved for the next draft.

Example manifest:

```yaml
format: mdq
version: '0.1'
entrypoint: document.md
features: [core, rich]
security:
  remote_imports: false
  network: deny
requirements:
  network: optional
```
