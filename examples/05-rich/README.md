# 05 Rich features and their portable fallbacks

Callouts, highlights, maths, footnotes, citations, details and columns. The `commonmark` profile turns
them into plain CommonMark; `--profile gfm` keeps `$maths$`. Citations come from the `bibliography:` file.

```
mdq build notes.md --root examples/05-rich
mdq build notes.md --root examples/05-rich --profile gfm
```

## Expected output

* [`commonmark`](expected/commonmark.txt)
* [`gfm`](expected/gfm.txt)
