# 06 Data files and declared sources

* `variables: team: ./data/team.yaml` loads a YAML/JSON file as a namespace (`{{team.lead}}`).
* `type:` declares a typed variable. With a `source:` its value comes from a recorded **snapshot**
  (compile never fetches); without one, the host supplies it via `--input`/`inputs=`.
* `mdq sources` lists the declarations offline. A live fetch is `mdq.test_source(...)` in Python, with an
  explicit transport and policy (see 08-python-api).

`snapshot.json` was recorded with `python examples/06-data-and-sources/record_snapshot.py` (a fake
transport, so it runs offline; a real host passes its own HTTPS transport).

```
mdq sources report.md --root examples/06-data-and-sources
mdq build report.md --root examples/06-data-and-sources --input quarter=Q4 --snapshot examples/06-data-and-sources/snapshot.json
```

## Expected output

* [`sources`](expected/sources.txt)
* [`no-snapshot`](expected/no-snapshot.txt)
* [`build`](expected/build.txt)
