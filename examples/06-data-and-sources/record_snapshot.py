"""Record snapshot.json the way a host would: one authorised live test per source variable.

The transport here is a FAKE (no network): a real host passes its own HTTPS transport and policy.
Run: python examples/06-data-and-sources/record_snapshot.py
"""
import json
import re
from decimal import Decimal
from pathlib import Path

import mdq

HERE = Path(__file__).resolve().parent


def fake_transport(request):
    if request["op"] == "resolve":
        return {"addresses": ["93.184.216.34"]}
    return {"status": 200, "headers": {}, "body": b'{"price": 187.25}', "peer": request["address"]}


def snapshot_json(entries) -> str:
    """JSON with Decimals written as exact JSON numbers (never through float)."""
    text = json.dumps(entries, indent=2, default=lambda d: f"__DEC__{d}__" if isinstance(d, Decimal) else str(d))
    return re.sub(r'"__DEC__(-?[0-9.]+)__"', r"\1", text) + "\n"


def record() -> str:
    fs = mdq.DirectoryFS(HERE)
    entries = [mdq.test_source("report.md", fs, name=d.name, transport=fake_transport,
                               retrieved_at="2026-11-14T09:00:00Z",
                               host_policy={"network": "allow"}, library_policy={"network": "allow"})
               for d in mdq.validate_sources("report.md", fs) if d.source is not None]
    return snapshot_json(entries)


if __name__ == "__main__":
    (HERE / "snapshot.json").write_text(record())
    print("wrote snapshot.json")
