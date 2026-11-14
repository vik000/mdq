# MDQ examples

Small, runnable projects, from plain metadata to encrypted packages. Every command below is run by
`examples/run_examples.py`, and its exact output is in each example's `expected/` folder
(`tests/unit/test_examples.py` fails if an example stops producing it).

Setup (once): `pip install -e '.[dev]'` in the repository, which installs the `mdq` command.

| Example | Shows |
|---|---|
| [01-basics](01-basics/) | frontmatter, `{{document.*}}`, `variables:`, host `--input`, escaping |
| [02-calc](02-calc/) | `calc` blocks: exact decimals, percentages, units and conversions, money, FX observations |
| [03-composition](03-composition/) | imports, section/block embeds, `[[...]]` references, `[[toc]]`, block anchors, metadata cascade, rebased links |
| [04-conditionals](04-conditionals/) | `:::if` and `:::for` (excluded branches are never read) |
| [05-rich](05-rich/) | callouts, highlights, maths, footnotes, citations, details, columns; `commonmark` vs `gfm` |
| [06-data-and-sources](06-data-and-sources/) | variable files, declared variables, snapshots instead of fetching, `mdq sources` |
| [07-package](07-package/) | `.mdq` packages and `.mdq.age` encryption, plaintext permission |
| [08-python-api](08-python-api/) | `mdq.compile`, `evaluate`, typed results, exceptions, `test_source`, `validate_sources` |

Run everything and compare with the recorded outputs:

```
python examples/run_examples.py --check
```

Things worth noticing:

* Sources are never modified; `mdq build -o FILE` refuses to overwrite any file the build read.
* Nothing is fetched: source values come from a snapshot you record explicitly (see 06), and currency
  conversion needs observations you supply (see 02).
* A value inserted with `{{...}}` is escaped Markdown text: `*not*` stays literal, and a value at the start of
  a line may get a backslash (e.g. `\-1.5`) that the renderer removes.
* `\{{x}}` stays exactly as written in the output; your Markdown renderer shows it as `{{x}}`.
