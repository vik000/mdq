---
sources:
  quotes:
    kind: http
    url: https://example.invalid/quotes/{symbol}
    format: json
    select: /price
variables:
  team: ./data/team.yaml
  quarter:
    type: string
  share_price:
    type: number
    unit: USD
    source: quotes
    arguments: {symbol: ACME}
---
# {{quarter}} report

Team lead: {{team.lead}} ({{team.size}} people).

```calc
holding = share_price * 100
```
Share price: {{share_price}}; 100 shares are worth {{calc.holding}}.
