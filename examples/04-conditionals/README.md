# 04 Conditionals and loops

`:::if {var=... equals=...}` keeps its body only when the variable's text equals the literal; excluded
branches are never evaluated (an import inside one is not even read). `:::for {each=... as=...}` repeats its
body for every item of a list.

```
mdq build plan.md --root examples/04-conditionals
mdq build plan.md --root examples/04-conditionals --input audience=internal
```

## Expected output

* [`public`](expected/public.txt)
* [`internal`](expected/internal.txt)
