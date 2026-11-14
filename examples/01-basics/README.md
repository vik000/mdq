# 01 Basics: metadata and variables

A plain Markdown file is valid MDQ. Frontmatter holds metadata; `{{document.key}}` reads it and
`{{name}}` reads an entry under `variables:`. Values are escaped so they can never inject Markdown (`*not*` stays literal text).
`\{{x}}` is left exactly as written; the renderer shows it as `{{x}}`.

```
mdq build report.md --root examples/01-basics
mdq build report.md --root examples/01-basics --input reader=Ada
```

## Expected output

* [`build`](expected/build.txt)
* [`build-input`](expected/build-input.txt)
