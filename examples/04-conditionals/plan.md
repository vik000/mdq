---
variables:
  audience: public
  steps: [Design, Build, Ship]
  owners:
    - {name: Ada, area: maths}
    - {name: Grace, area: compilers}
---
# Plan

:::if {var="audience" equals="internal"}
Internal note: budget numbers are confidential.

![[internal.md]]
:::

:::for {each="steps" as="step"}
- Step: {{step}}
:::

:::for {each="owners" as="o"}
- {{o.name}} owns {{o.area}}
:::
