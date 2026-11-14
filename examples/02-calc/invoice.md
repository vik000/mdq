---
variables:
  unit_price: 120 EUR
  quantity: 4
---
# Invoice

```calc
subtotal = unit_price * quantity
tax = subtotal * 21%
total = subtotal + tax
```
Subtotal: {{calc.subtotal}}, VAT: {{calc.tax}}, total: **{{calc.total}}**.

```calc
distance = 12 miles
distance_km = distance to km
oven = 350 degF
oven_c = oven to degC
third = 1 / 3
```
The delivery drove {{calc.distance_km}} ({{calc.distance}}); bake at {{calc.oven_c}}.
Exact decimals: one third is {{calc.third}}.
