# 07 Packages (.mdq) and encryption (.mdq.age)

A `.mdq` package is a ZIP with a root `manifest.yaml` and a Markdown entrypoint; the manifest's
metadata is the lowest layer, and its `security`/`requirements` are requests only. `make_package.py`
builds `guide.mdq` and an encrypted `guide.mdq.age` (with a throw-away key in `key.txt`).

```
python examples/07-package/make_package.py
mdq build --package examples/07-package/guide.mdq
mdq build --package examples/07-package/guide.mdq.age --identity examples/07-package/key.txt --allow-plaintext
```

## Expected output

* [`package`](expected/package.txt)
* [`encrypted-no-permission`](expected/encrypted-no-permission.txt)
* [`encrypted`](expected/encrypted.txt)
