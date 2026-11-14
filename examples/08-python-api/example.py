"""A tour of the MDQ Python API (runs offline)."""
from decimal import Decimal

import mdq

files = {
    "doc.md": "---\nvariables:\n  rate: 0.07\n  price:\n    type: number\n    unit: USD\n"
              "    source:\n      kind: http\n      url: https://example.invalid/price\n"
              "      format: json\n      select: /price\n---\n"
              "# Loan\n\n```calc\ninterest = principal * rate\n```\nInterest: {{calc.interest}}, price {{price}}.\n",
}

# 1. Declared source variables need a value: compile never fetches.
try:
    mdq.compile("doc.md", files, inputs={"principal": Decimal("10000")})
except mdq.SourceError as e:  # family of the four MDQ_SOURCE_* codes
    print("1.", type(e).__name__, e.code, "at", e.path)


# 2. A live test through YOUR transport (here a fake one) and an explicit policy, then reuse it as a snapshot.
def transport(request):
    if request["op"] == "resolve":
        return {"addresses": ["93.184.216.34"]}
    return {"status": 200, "headers": {}, "body": b'{"price": 42.5}', "peer": request["address"]}


entry = mdq.test_source("doc.md", files, name="price", transport=transport, retrieved_at="2026-11-14T09:00:00Z",
                        host_policy={"network": "allow"}, library_policy={"network": "allow"})
print("2. fetched", entry["value"], entry["unit"], "at", entry["retrieved_at"])

# 3. Compile with the snapshot; results are exact Decimals.
r = mdq.compile("doc.md", files, inputs={"principal": Decimal("10000")}, snapshot=[entry])
print("3.", r.markdown.strip().splitlines()[-1])
value = r.calc[0].results[0]
print("   calc:", value.name, "=", value.value, type(value.value).__name__, "line", value.line)

# 4. evaluate(): data only, no Markdown.
ev = mdq.evaluate("doc.md", files, inputs={"principal": 5000}, snapshot=[entry])
print("4.", [(v.name, v.value, v.unit) for v in ev.values])

# 5. Errors carry a stable code and a location.
try:
    mdq.compile("bad.md", {"bad.md": "```calc\nx = 1 / 0\n```\n"})
except mdq.CalcError as e:
    print("5.", e.code, f"{e.path}:{e.line}:{e.column}", e)

# 6. Offline validation of declarations.
print("6.", [(d.name, d.type, d.unit, d.url) for d in mdq.validate_sources("doc.md", files)])
