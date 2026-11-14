# 02 Calculations

A ```calc block holds one assignment per line: exact decimals (28 digits, half-even), percentages, unit
quantities with conversions, and money. Results are used with `{{calc.name}}`; the block itself is left out
of the output (`--calc-blocks keep` keeps it). Currency conversion needs an exchange-rate observation
supplied by the host: the library never fetches one.

```
mdq build invoice.md --root examples/02-calc
mdq build invoice.md --root examples/02-calc --observations examples/02-calc/rates.json --valuation 2026-11-14T00:00:00Z
mdq check invoice.md --root examples/02-calc --format json
```

## Expected output

* [`invoice`](expected/invoice.txt)
* [`invoice-keep-calc`](expected/invoice-keep-calc.txt)
* [`convert-no-rates`](expected/convert-no-rates.txt)
* [`convert`](expected/convert.txt)
* [`check-json`](expected/check-json.txt)
