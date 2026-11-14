# 03 Composition: imports, references, anchors, cascade

* `![[file.md]]`, `![[file.md#Heading]]`, `![[file.md#^id]]` embed content (frontmatter removed).
* `[[file.md#Heading|label]]` links: to the rendered copy when the target is embedded, otherwise to the file.
* `^id` marks a block; the output keeps an `<a id="id"></a>` anchor. `[[toc]]` lists the headings.
* Metadata cascades: `.mdq.yaml` (project, then each folder) < frontmatter < `<name>.meta.yaml` sidecar.
* Links and images inside embedded files are rebased to stay correct.

```
mdq build book/book.md --root examples/03-composition
```

## Expected output

* [`build`](expected/build.txt)
