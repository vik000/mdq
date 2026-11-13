"""Epic 6 / E6.5 (F16-F19): source resolver, fake transport and snapshots (sources-and-permissions.md 2-4). G46.

`source_fetch(text, name, policy, transport, retrieved_at, allow_network=True, headers=None)` fetches ONE declared
variable through a host-supplied transport (a callable taking and returning dicts; here the `fake_transport`
fixture of conftest.py). The core checks everything the transport says: policy, every DNS answer, the pinned
address, size and time limits, redirects, UTF-8, selection and type. `snapshot_apply(text, snapshot)` replays
recorded values offline. `url_join(base, ref)` resolves redirect targets (RFC 3986 section 5.2).
Messages never contain URLs, hosts, header values, response bodies or transport error text.
"""
import hashlib
import json
from decimal import Decimal
from urllib.parse import urljoin

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

import mdq
from mdq import _internal as mi

pytestmark = pytest.mark.unit

FETCH, DENIED, BAD = "MDQ_SOURCE_FETCH_FAILED", "MDQ_SECURITY_DENIED", "MDQ_SOURCE_INVALID"
UNRES, TYPE, UNSUP = "MDQ_SOURCE_UNRESOLVED", "MDQ_SOURCE_TYPE_ERROR", "MDQ_UNSUPPORTED_FEATURE"
TS = "2026-11-13T12:00:00Z"
IP = "93.184.216.34"
URL = "https://example.invalid/stocks/AAPL"
ORIGIN = "https://example.invalid:443"


def eff(**kw):
    layer = {"network": "allow", **kw}
    return mi.policy_intersect(layer, layer)


def decl(url=URL, type="number", fmt="json", select="/price", unit=None, name="p"):
    t = f"variables:\n  {name}:\n    type: {type}\n"
    if unit is not None:
        t += f"    unit: {unit}\n"
    t += f"    source:\n      kind: http\n      url: '{url}'\n      format: {fmt}\n"
    if select is not None:
        t += f"      select: {select}\n"
    return t


def fetch(text, t, policy=None, name="p", **kw):
    return mi.source_fetch(text, name, eff() if policy is None else policy, t, TS, **kw)


def py_key(text, name="p"):
    """The declaration key computed independently in Python (the contract's formula)."""
    d = next(v for v in mi.source_validate(text) if v["name"] == name)
    s = {k: d["source"][k] for k in ("arguments", "format", "kind", "method", "reader", "select", "url")}
    doc = {"source": s, "type": d["type"], "unit": d["unit"]}
    return hashlib.sha256(json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def raw(resp, addresses=(IP,)):
    """A transport that answers every GET with `resp` as is (no defaults filled in)."""
    calls = []

    def t(req):
        calls.append(req)
        if req["op"] == "resolve":
            return {"addresses": list(addresses)}
        return resp

    t.calls = calls
    return t


# ---------------------------------------------------------------- success
def test_json_source_result_and_requests(fake_transport):
    text = decl(unit="USD")
    t = fake_transport({URL: fake_transport.reply(body=b'{"price": 1.50, "x": [1]}')})
    r = fetch(text, t)
    assert r == {
        "name": "p", "type": "number", "value": Decimal("1.5"), "unit": "USD", "retrieved_at": TS,
        "source": {"kind": "http", "named": None, "origin": ORIGIN, "key": py_key(text)},
    }
    assert t.resolves == [{"op": "resolve", "host": "example.invalid"}]
    assert t.gets == [{"op": "get", "url": URL, "address": IP, "headers": {}, "timeout_ms": 10000, "max_bytes": 1048576}]


@pytest.mark.parametrize("ty,body,value", [
    ("number", b"42\n", Decimal(42)), ("number", b" -1.25e2 ", Decimal("-125")), ("string", b"hello\r\n", "hello"),
    ("string", b"", ""), ("boolean", b"true\n", True), ("boolean", b"false", False), ("date", b"2024-02-29\n", "2024-02-29"),
])
def test_text_sources(fake_transport, ty, body, value):
    t = fake_transport({URL: fake_transport.reply(body=body)})
    r = fetch(decl(type=ty, fmt="text", select=None), t)
    assert r["value"] == value and type(r["value"]) is type(value) and r["type"] == ty


@pytest.mark.parametrize("ty,doc,value", [
    ("boolean", {"v": True}, True), ("date", {"v": "2026-01-02"}, "2026-01-02"), ("string", {"v": "é😀"}, "é😀"),
    ("number", {"v": 10**30}, Decimal(10**30)),
])
def test_json_types(fake_transport, ty, doc, value):
    t = fake_transport({URL: fake_transport.reply(body=json.dumps(doc).encode())})
    assert fetch(decl(type=ty, select="/v"), t)["value"] == value


def test_whole_json_document_without_select(fake_transport):
    t = fake_transport({URL: fake_transport.reply(body=b" 7 ")})
    assert fetch(decl(select=None), t)["value"] == 7


def test_named_source_with_arguments_and_namespaced_variable(fake_transport):
    text = ("sources:\n  prices:\n    kind: http\n    url: https://example.invalid/prices/{symbol}\n    format: json\n"
            "    select: /price\nvariables:\n  stocks:\n    apple:\n      type: number\n      source: prices\n"
            "      arguments: {symbol: AAPL}\n")
    t = fake_transport({"https://example.invalid/prices/AAPL": fake_transport.reply(body=b'{"price": 3}')})
    r = mi.source_fetch(text, "stocks.apple", eff(), t, TS)
    assert r["name"] == "stocks.apple" and r["value"] == 3
    assert r["source"]["named"] == "prices" and r["source"]["key"] == py_key(text, "stocks.apple")


@pytest.mark.parametrize("url,address", [
    ("https://93.184.216.34/x", "93.184.216.34"),
    ("https://[2606:2800:0220:0001:0:0:0:1]/x", "2606:2800:220:1::1"),
    ("https://1572395042/x", "93.184.216.34"),
])
def test_ip_literal_hosts_skip_dns(fake_transport, url, address):
    t = fake_transport({url: fake_transport.reply(body=b"1")})
    fetch(decl(url=url, select=None), t)
    assert t.resolves == [] and [g["address"] for g in t.gets] == [address]


def test_host_name_is_canonical_and_connection_pinned_to_first_address(fake_transport):
    url = "https://Example.INVALID./x"
    t = fake_transport({url: fake_transport.reply(body=b"1")},
                       dns={"example.invalid": ["2606:2800:0220:0001:0000:0000:0000:0001", "93.184.216.34"]})
    r = fetch(decl(url=url, select=None), t)
    assert t.resolves[0]["host"] == "example.invalid"
    assert t.gets[0]["address"] == "2606:2800:220:1::1" and r["source"]["origin"] == ORIGIN


def test_equivalent_peer_spelling_is_accepted(fake_transport):
    t = fake_transport({URL: fake_transport.reply(body=b'{"price": 1}', peer="2606:2800:220:1:0:0:0:1")},
                       dns={"example.invalid": ["2606:2800:220:1::1"]})
    assert fetch(decl(), t)["value"] == 1


def test_policy_limits_reach_the_transport(fake_transport):
    t = fake_transport({URL: fake_transport.reply(body=b'{"price": 1}')})
    fetch(decl(), t, policy=eff(timeout=3, max_bytes=500))
    assert (t.gets[0]["timeout_ms"], t.gets[0]["max_bytes"]) == (3000, 500)


@pytest.mark.parametrize("status", [200, 201, 203, 299])
def test_any_2xx_is_success(fake_transport, status):
    t = fake_transport({URL: fake_transport.reply(status=status, body=b'{"price": 1}')})
    assert fetch(decl(), t)["value"] == 1


def test_unknown_response_keys_are_ignored_and_elapsed_defaults_to_zero():
    t = raw({"status": 200, "headers": {}, "body": b'{"price": 2}', "peer": IP, "extra": object()})
    assert fetch(decl(), t)["value"] == 2


# ---------------------------------------------------------------- consent and policy: no transport call at all
@pytest.mark.parametrize("policy,kw", [
    (eff(), {"allow_network": False}),
    (mi.policy_intersect(), {}),
    (mi.policy_intersect({"network": "allow"}, {"network": "allow"}, {"requirements": {"network": "none"}}), {}),
    (eff(hosts=["other.example"]), {}),
])
def test_denied_before_any_transport_call(fake_transport, assert_mdq_error, policy, kw):
    t = fake_transport({URL: fake_transport.reply(body=b'{"price": 1}')})
    with assert_mdq_error(DENIED):
        fetch(decl(), t, policy=policy, **kw)
    assert t.calls == []


@pytest.mark.parametrize("url", ["https://127.0.0.1/x", "https://localhost/x", "https://10.1.2.3/x", "https://[::1]/x",
                                 "https://169.254.169.254/latest", "https://example.invalid:8443/x", "https://db.internal/x"])
def test_blocked_urls_are_denied_without_calls(fake_transport, assert_mdq_error, url):
    t = fake_transport()
    with assert_mdq_error(DENIED):
        fetch(decl(url=url), t)
    assert t.calls == []


def test_allow_network_false_wins_even_with_open_policy(fake_transport, assert_mdq_error):
    t = fake_transport()
    with assert_mdq_error(DENIED) as e:
        fetch(decl(), t, allow_network=False)
    assert "not authorised" in str(e.value) and t.calls == []


def test_invalid_policy_is_rejected_without_calls(fake_transport, assert_mdq_error):
    t = fake_transport()
    with assert_mdq_error(BAD):
        fetch(decl(), t, policy={"network": "allow", "bogus": 1})
    assert t.calls == []


def test_private_endpoints_allow_listed_private_hosts(fake_transport):
    t = fake_transport({"https://10.0.0.5/x": fake_transport.reply(body=b"1")})
    assert fetch(decl(url="https://10.0.0.5/x", select=None), t, policy=eff(private_endpoints=["10.0.0.5"]))["value"] == 1
    u = "https://db.example.invalid/x"
    t = fake_transport({u: fake_transport.reply(body=b"2")}, dns={"db.example.invalid": ["10.0.0.5"]})
    assert fetch(decl(url=u, select=None), t, policy=eff(private_endpoints=["db.example.invalid"]))["value"] == 2
    assert t.gets[0]["address"] == "10.0.0.5"


# ---------------------------------------------------------------- DNS and rebinding
@pytest.mark.parametrize("answer", [["10.0.0.1"], [IP, "127.0.0.1"], ["::ffff:192.168.0.1"], ["169.254.169.254"], ["::1"]])
def test_resolved_private_address_is_denied_before_get(fake_transport, assert_mdq_error, answer):
    t = fake_transport({URL: fake_transport.reply(body=b'{"price": 1}')}, dns={"example.invalid": answer})
    with assert_mdq_error(DENIED):
        fetch(decl(), t)
    assert t.gets == []


@pytest.mark.parametrize("answer", [[], ["not-an-ip"], ["93.184.216.34/32"], ["fe80::1%en0"], ["0x7f.0.0.1"], [IP, ""]])
def test_empty_or_invalid_dns_answer(fake_transport, assert_mdq_error, answer):
    t = fake_transport({URL: fake_transport.reply(body=b'{"price": 1}')}, dns={"example.invalid": answer})
    with assert_mdq_error(FETCH) as e:
        fetch(decl(), t)
    assert str(e.value) == "DNS resolution failed" and t.gets == []


@pytest.mark.parametrize("answer", [None, [], "x", {}, {"addresses": IP}, {"addresses": [1]}, {"addresses": (IP,)},
                                    {"addresses": None}])
def test_malformed_dns_answer(assert_mdq_error, answer):
    def t(req):
        return answer
    with assert_mdq_error(FETCH) as e:
        fetch(decl(), t)
    assert str(e.value) == "DNS resolution failed"


@pytest.mark.parametrize("peer", ["1.1.1.1", "127.0.0.1", "93.184.216.35", "::ffff:93.184.216.34"])
def test_peer_must_be_the_validated_address(fake_transport, assert_mdq_error, peer):
    t = fake_transport({URL: fake_transport.reply(body=b'{"price": 1}', peer=peer)})
    with assert_mdq_error(DENIED) as e:
        fetch(decl(), t)
    assert "validated address" in str(e.value)


# ---------------------------------------------------------------- time limits
def test_transport_timeout(fake_transport, assert_mdq_error):
    for t in (fake_transport({URL: TimeoutError("slow")}), fake_transport(dns={"example.invalid": TimeoutError()})):
        with assert_mdq_error(FETCH) as e:
            fetch(decl(), t)
        assert str(e.value) == "the request timed out"


def test_slow_loris_elapsed_over_budget(fake_transport, assert_mdq_error):
    t = fake_transport({URL: fake_transport.reply(body=b'{"price": 1}', elapsed_ms=10000)})
    assert fetch(decl(), t)["value"] == 1  # exactly the budget is fine
    t = fake_transport({URL: fake_transport.reply(body=b'{"price": 1}', elapsed_ms=10001)})
    with assert_mdq_error(FETCH) as e:
        fetch(decl(), t)
    assert str(e.value) == "the request timed out"


def test_budget_shrinks_over_a_redirect_chain(fake_transport, assert_mdq_error):
    a, b, c = URL, "https://example.invalid/b", "https://example.invalid/c"
    t = fake_transport({a: fake_transport.reply(302, headers={"Location": b}, elapsed_ms=6000),
                        b: fake_transport.reply(302, headers={"Location": c}, elapsed_ms=3000),
                        c: fake_transport.reply(body=b'{"price": 1}', elapsed_ms=1000)})
    assert fetch(decl(), t, policy=eff(max_redirects=2))["value"] == 1
    assert [g["timeout_ms"] for g in t.gets] == [10000, 4000, 1000]
    t.routes[c] = fake_transport.reply(body=b'{"price": 1}', elapsed_ms=1001)
    with assert_mdq_error(FETCH):
        fetch(decl(), t, policy=eff(max_redirects=2))


def test_exhausted_budget_stops_before_the_next_request(fake_transport, assert_mdq_error):
    b = "https://example.invalid/b"
    t = fake_transport({URL: fake_transport.reply(302, headers={"Location": b}, elapsed_ms=10000),
                        b: fake_transport.reply(body=b'{"price": 1}')})
    with assert_mdq_error(FETCH) as e:
        fetch(decl(), t, policy=eff(max_redirects=1))
    assert str(e.value) == "the request timed out" and len(t.gets) == 1


# ---------------------------------------------------------------- size limits
def test_body_size_limit(fake_transport, assert_mdq_error):
    p = eff(max_bytes=10)
    text = decl(type="string", fmt="text", select=None)
    t = fake_transport({URL: fake_transport.reply(body=b"x" * 10)})
    assert fetch(text, t, policy=p)["value"] == "x" * 10
    for reply in (fake_transport.reply(body=b"x" * 11), fake_transport.reply(body=b"x", headers={"Content-Length": "11"}),
                  fake_transport.reply(body=b"x", headers={"content-length": "9" * 40})):
        t = fake_transport({URL: reply})
        with assert_mdq_error(FETCH) as e:
            fetch(text, t, policy=p)
        assert str(e.value) == "the response is too large"


@pytest.mark.parametrize("cl", ["", "abc", "-1", "1e3", " 5", "5 ", "+5", "\u0661", "0x10", "1.0"])
def test_malformed_content_length(fake_transport, assert_mdq_error, cl):
    t = fake_transport({URL: fake_transport.reply(body=b'{"price": 1}', headers={"Content-Length": cl})})
    with assert_mdq_error(FETCH) as e:
        fetch(decl(), t)
    assert str(e.value) == "malformed transport response"


def test_matching_content_length_is_fine(fake_transport):
    body = b'{"price": 1}'
    t = fake_transport({URL: fake_transport.reply(body=body, headers={"Content-Length": str(len(body))})})
    assert fetch(decl(), t)["value"] == 1


# ---------------------------------------------------------------- redirects
@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_relative_redirect_is_followed_when_allowed(fake_transport, status):
    target = "https://example.invalid/other/q?x=1"
    t = fake_transport({URL: fake_transport.reply(status, headers={"Location": "../other/q?x=1#frag"}),
                        target: fake_transport.reply(body=b'{"price": 5}')})
    r = fetch(decl(), t, policy=eff(max_redirects=1))
    assert r["value"] == 5 and [g["url"] for g in t.gets] == [URL, target]
    assert r["source"]["origin"] == ORIGIN


def test_redirects_are_off_by_default(fake_transport, assert_mdq_error):
    t = fake_transport({URL: fake_transport.reply(302, headers={"Location": "/b"}),
                        "https://example.invalid/b": fake_transport.reply(body=b'{"price": 5}')})
    with assert_mdq_error(DENIED) as e:
        fetch(decl(), t)
    assert str(e.value) == "redirect limit exceeded" and len(t.gets) == 1


def test_chain_longer_than_max_redirects(fake_transport, assert_mdq_error):
    routes = {URL: fake_transport.reply(302, headers={"Location": "/r1"})}
    for i in range(1, 4):
        routes[f"https://example.invalid/r{i}"] = fake_transport.reply(302, headers={"Location": f"/r{i + 1}"})
    t = fake_transport(routes)
    with assert_mdq_error(DENIED):
        fetch(decl(), t, policy=eff(max_redirects=2))
    assert len(t.gets) == 3


def test_redirect_to_other_host_resolves_and_checks_it(fake_transport, assert_mdq_error):
    other = "https://cdn.example.org/p"
    t = fake_transport({URL: fake_transport.reply(301, headers={"Location": other}),
                        other: fake_transport.reply(body=b'{"price": 9}')}, dns={"cdn.example.org": ["1.1.1.1"]})
    r = fetch(decl(), t, policy=eff(max_redirects=1))
    assert r["value"] == 9 and r["source"]["origin"] == ORIGIN
    assert [c["host"] for c in t.resolves] == ["example.invalid", "cdn.example.org"]
    assert t.gets[1]["address"] == "1.1.1.1"
    # the allowlist applies to every hop
    with assert_mdq_error(DENIED):
        fetch(decl(), t, policy=eff(max_redirects=1, hosts=["example.invalid"]))


@pytest.mark.parametrize("location,dns", [
    ("https://10.0.0.1/x", {}), ("https://127.0.0.1/", {}), ("https://[::1]/", {}), ("https://localhost/", {}),
    ("https://evil.example.org/", {"evil.example.org": ["192.168.1.1"]}),
    ("https://evil.example.org/", {"evil.example.org": ["1.1.1.1", "10.0.0.1"]}),
    ("http://example.invalid/x", {}), ("ftp://example.invalid/x", {}), ("https://u:p@example.invalid/", {}),
])
def test_redirect_to_private_or_insecure_target_is_denied(fake_transport, assert_mdq_error, location, dns):
    t = fake_transport({URL: fake_transport.reply(302, headers={"Location": location})}, dns=dns)
    with assert_mdq_error(DENIED):
        fetch(decl(), t, policy=eff(max_redirects=3))
    assert len(t.gets) == 1


@pytest.mark.parametrize("routes", [
    {URL: (302, URL)},
    {URL: (302, "/b"), "https://example.invalid/b": (307, URL)},
    {URL: (302, "/b"), "https://example.invalid/b": (308, "/c"), "https://example.invalid/c": (301, "/b#x")},
])
def test_redirect_loop(fake_transport, assert_mdq_error, routes):
    t = fake_transport({u: fake_transport.reply(s, headers={"Location": loc}) for u, (s, loc) in routes.items()})
    with assert_mdq_error(FETCH) as e:
        fetch(decl(), t, policy=eff(max_redirects=5))
    assert str(e.value) == "redirect loop"


@pytest.mark.parametrize("headers", [{}, {"Location": ""}, {"X-Location": "/b"}])
def test_redirect_without_location(fake_transport, assert_mdq_error, headers):
    t = fake_transport({URL: fake_transport.reply(302, headers=headers)})
    with assert_mdq_error(FETCH) as e:
        fetch(decl(), t, policy=eff(max_redirects=1))
    assert str(e.value) == "redirect without a location"


@pytest.mark.parametrize("location", ["/a b", "/\u00e9", "https://exa mple.org/", "https:no-slashes", "https://[::1/",
                                      "https://example.invalid:99999/"])
def test_redirect_to_malformed_location(fake_transport, assert_mdq_error, location):
    t = fake_transport({URL: fake_transport.reply(302, headers={"Location": location})})
    with assert_mdq_error(FETCH) as e:
        fetch(decl(), t, policy=eff(max_redirects=1))
    assert str(e.value) == "the redirect location is not a valid URL"


@pytest.mark.parametrize("status", [100, 199, 300, 304, 305, 306, 400, 404, 418, 500, 503, 599])
def test_non_success_status(fake_transport, assert_mdq_error, status):
    t = fake_transport({URL: fake_transport.reply(status, headers={"Location": "/b"}, body=b'{"price": 1}')})
    with assert_mdq_error(FETCH) as e:
        fetch(decl(), t, policy=eff(max_redirects=1))
    assert str(e.value) == f"HTTP status {status}"


# ---------------------------------------------------------------- headers
def test_headers_go_to_the_first_origin_only(fake_transport):
    h = {"X-Api-Key": "k-123", "Accept": "application/json"}
    same, other = "https://example.invalid/b", "https://cdn.example.org/c"
    t = fake_transport({URL: fake_transport.reply(302, headers={"Location": "/b"}),
                        same: fake_transport.reply(302, headers={"Location": other}),
                        other: fake_transport.reply(body=b'{"price": 1}')})
    fetch(decl(), t, policy=eff(max_redirects=2), headers=h)
    assert [g["headers"] for g in t.gets] == [h, h, {}]


def test_a_different_port_is_a_different_origin(fake_transport):
    other = "https://example.invalid:8443/c"
    t = fake_transport({URL: fake_transport.reply(302, headers={"Location": other}),
                        other: fake_transport.reply(body=b'{"price": 1}')})
    fetch(decl(), t, policy=eff(max_redirects=1, ports=[443, 8443]), headers={"K": "v"})
    assert [g["headers"] for g in t.gets] == [{"K": "v"}, {}]


def test_no_headers_means_empty(fake_transport):
    t = fake_transport({URL: fake_transport.reply(body=b'{"price": 1}')})
    fetch(decl(), t, headers=None)
    assert t.gets[0]["headers"] == {}


# ---------------------------------------------------------------- body, selection, type
@pytest.mark.parametrize("body", [b"{", b'{"price": 1,}', b"\xef\xbb\xbf\xef\xbb\xbf{\"price\": 1}", b"NaN",
                                  b'{"price": 1, "price": 2}', b""])
def test_invalid_json(fake_transport, assert_mdq_error, body):
    t = fake_transport({URL: fake_transport.reply(body=body)})
    with assert_mdq_error(BAD) as e:
        fetch(decl(), t)
    assert str(e.value).startswith("the response is not valid JSON") and e.value.line is not None


@pytest.mark.parametrize("fmt,select", [("json", "/price"), ("text", None)])
@pytest.mark.parametrize("body", [b"\xff", b"\xc3", b"\xed\xa0\x80", b"\xc0\xaf", b"ok\x80"])
def test_invalid_utf8(fake_transport, assert_mdq_error, body, fmt, select):
    t = fake_transport({URL: fake_transport.reply(body=body)})
    with assert_mdq_error(BAD) as e:
        fetch(decl(fmt=fmt, select=select, type="string"), t)
    assert str(e.value) == "the response is not valid UTF-8"


def test_one_leading_bom_is_dropped_as_everywhere_else(fake_transport):
    t = fake_transport({URL: fake_transport.reply(body=b'\xef\xbb\xbf{"price": 1}')})
    assert fetch(decl(), t)["value"] == 1


def test_selection_failure_is_unresolved(fake_transport, assert_mdq_error):
    t = fake_transport({URL: fake_transport.reply(body=b'{"cost": 1}')})
    with assert_mdq_error(UNRES):
        fetch(decl(), t)


@pytest.mark.parametrize("ty,body", [("number", b'{"price": "1.5"}'), ("boolean", b'{"price": "true"}'),
                                     ("date", b'{"price": "2026-02-30"}'), ("string", b'{"price": 1}')])
def test_type_mismatch(fake_transport, assert_mdq_error, ty, body):
    t = fake_transport({URL: fake_transport.reply(body=body)})
    with assert_mdq_error(TYPE):
        fetch(decl(type=ty), t)


def test_text_type_mismatch(fake_transport, assert_mdq_error):
    t = fake_transport({URL: fake_transport.reply(body=b"twelve")})
    with assert_mdq_error(TYPE):
        fetch(decl(fmt="text", select=None), t)


# ---------------------------------------------------------------- unsupported and unresolved declarations
@pytest.mark.parametrize("text,name,code", [
    ("variables:\n  p:\n    type: number\n    source: {kind: 'reader:db', arguments: {k: 1}}\n", "p", UNSUP),
    ("variables:\n  p:\n    type: string\n    source: {kind: http, url: 'https://e.example/a', format: csv}\n", "p", UNSUP),
    ("variables:\n  p:\n    type: number\n    unit: EUR\n", "p", UNRES),
    (decl(), "q", UNRES),
    (decl(), "", UNRES),
    ("variables:\n  plain: 3\n", "plain", UNRES),
    ("sources:\n  s:\n    kind: ftp\n", "p", BAD),
])
def test_declaration_problems_make_no_calls(fake_transport, assert_mdq_error, text, name, code):
    t = fake_transport()
    with assert_mdq_error(code):
        mi.source_fetch(text, name, eff(), t, TS)
    assert t.calls == []


def test_unsupported_is_reported_even_without_consent(fake_transport, assert_mdq_error):
    t = fake_transport()
    with assert_mdq_error(UNSUP):
        mi.source_fetch("variables:\n  p:\n    type: number\n    source: {kind: 'reader:db'}\n", "p", eff(), t, TS,
                        allow_network=False)


# ---------------------------------------------------------------- transport failures
@pytest.mark.parametrize("exc,cls", [(ConnectionError("x"), "ConnectionError"), (OSError(5, "io"), "OSError"),
                                     (ValueError(), "ValueError"), (mdq.MdqError("m"), "MdqError"),
                                     (RuntimeError(), "RuntimeError")])
def test_transport_exception_becomes_fetch_failed(fake_transport, assert_mdq_error, exc, cls):
    for t in (fake_transport({URL: exc}), fake_transport(dns={"example.invalid": exc})):
        with assert_mdq_error(FETCH) as e:
            fetch(decl(), t)
        assert str(e.value) == f"the transport failed ({cls})"
        assert e.value.__cause__ is None and e.value.__context__ is None


def test_exception_class_names_are_sanitised(fake_transport, assert_mdq_error):
    weird = type("Bad Name<script>", (Exception,), {})
    t = fake_transport({URL: weird()})
    with assert_mdq_error(FETCH) as e:
        fetch(decl(), t)
    assert str(e.value) == "the transport failed (Exception)"


def test_no_route_in_the_fake_is_a_transport_failure(fake_transport, assert_mdq_error):
    with assert_mdq_error(FETCH) as e:
        fetch(decl(), fake_transport())
    assert str(e.value) == "the transport failed (ConnectionError)"


def test_base_exceptions_propagate_unchanged(fake_transport):
    t = fake_transport({URL: KeyboardInterrupt()})
    with pytest.raises(KeyboardInterrupt):
        fetch(decl(), t)


GOOD = {"status": 200, "headers": {}, "body": b'{"price": 1}', "peer": IP}


@pytest.mark.parametrize("resp", [
    None, [], "x", b"x", 200,
    {**GOOD, "status": None}, {**GOOD, "status": True}, {**GOOD, "status": "200"}, {**GOOD, "status": 200.0},
    {**GOOD, "status": 99}, {**GOOD, "status": 600}, {**GOOD, "status": -200}, {**GOOD, "status": 2 ** 70},
    {k: v for k, v in GOOD.items() if k != "status"},
    {**GOOD, "headers": None}, {**GOOD, "headers": [("a", "b")]}, {**GOOD, "headers": {"a": 1}},
    {**GOOD, "headers": {1: "a"}}, {**GOOD, "headers": {"Location": "/a", "location": "/b"}},
    {k: v for k, v in GOOD.items() if k != "headers"},
    {**GOOD, "body": '{"price": 1}'}, {**GOOD, "body": bytearray(b'{"price": 1}')}, {**GOOD, "body": None},
    {**GOOD, "body": memoryview(b"1")}, {k: v for k, v in GOOD.items() if k != "body"},
    {k: v for k, v in GOOD.items() if k != "peer"}, {**GOOD, "peer": "not-ip"}, {**GOOD, "peer": 5},
    {**GOOD, "peer": f"[{IP}]"}, {**GOOD, "peer": ""}, {**GOOD, "peer": None},
    {**GOOD, "elapsed_ms": -1}, {**GOOD, "elapsed_ms": "5"}, {**GOOD, "elapsed_ms": True}, {**GOOD, "elapsed_ms": 1.0},
    {**GOOD, "elapsed_ms": None}, {**GOOD, "elapsed_ms": 2 ** 70},
])
def test_malformed_transport_response(assert_mdq_error, resp):
    with assert_mdq_error(FETCH) as e:
        fetch(decl(), raw(resp))
    assert str(e.value) == "malformed transport response"


def test_good_raw_response_for_reference():
    assert fetch(decl(), raw(dict(GOOD)))["value"] == 1


# ---------------------------------------------------------------- canaries: secrets never leak into diagnostics
SECRET = "c4n4ry5ecret"
SURL = f"https://example.invalid/{SECRET}/q?k={SECRET}"


def _canary_cases(FT):
    ok = FT.reply(body=b'{"price": 1}')
    return {
        "http-500-body": (decl(url=SURL), FT({SURL: FT.reply(500, body=SECRET.encode())}), {}),
        "exception-text": (decl(url=SURL), FT({SURL: ConnectionError(f"GET {SURL} failed: {SECRET}")}), {}),
        "timeout-text": (decl(url=SURL), FT({SURL: TimeoutError(SECRET)}), {}),
        "dns-exception": (decl(url=SURL), FT(dns={"example.invalid": RuntimeError(SECRET)}), {}),
        "header-value": (decl(url=SURL), FT({SURL: FT.reply(404)}), {"headers": {"Authorization": SECRET}}),
        "duplicate-key": (decl(url=SURL), FT({SURL: FT.reply(body=f'{{"{SECRET}":1,"{SECRET}":2}}'.encode())}), {}),
        "bad-json": (decl(url=SURL), FT({SURL: FT.reply(body=f'{{"a": {SECRET}}}'.encode())}), {}),
        "selection": (decl(url=SURL), FT({SURL: FT.reply(body=f'{{"{SECRET}": "{SECRET}"}}'.encode())}), {}),
        "type": (decl(url=SURL), FT({SURL: FT.reply(body=f'{{"price": "{SECRET}"}}'.encode())}), {}),
        "text-type": (decl(url=SURL, fmt="text", select=None), FT({SURL: FT.reply(body=SECRET.encode())}), {}),
        "utf8": (decl(url=SURL), FT({SURL: FT.reply(body=SECRET.encode() + b"\xff")}), {}),
        "too-large": (decl(url=SURL), FT({SURL: FT.reply(body=SECRET.encode() * 100000)}), {}),
        "location": (decl(url=SURL), FT({SURL: FT.reply(302, headers={"Location": f"/{SECRET}?{SECRET}"})}), {}),
        "bad-location": (decl(url=SURL), FT({SURL: FT.reply(302, headers={"Location": f"/{SECRET} x"})}),
                         {"policy": eff(max_redirects=1)}),
        "content-length": (decl(url=SURL), FT({SURL: FT.reply(body=b"1", headers={"Content-Length": SECRET})}), {}),
        "peer": (decl(url=SURL), FT({SURL: FT.reply(body=b"1", peer="1.1.1.1")}), {}),
        "host-allowlist": (decl(url=f"https://{SECRET}.example.org/x"), FT(), {"policy": eff(hosts=["a.example"])}),
        "host-private": (decl(url=f"https://{SECRET}.example.org/x"), FT(dns={f"{SECRET}.example.org": ["10.0.0.1"]}), {}),
        "host-dns-empty": (decl(url=f"https://{SECRET}.example.org/x"), FT(dns={f"{SECRET}.example.org": []}), {}),
        "host-blocked": (decl(url=f"https://{SECRET}.internal/x"), FT(), {}),
        "no-consent": (decl(url=SURL), FT({SURL: ok}), {"allow_network": False}),
    }


class _Names:
    reply = staticmethod(lambda *a, **k: None)

    def __init__(self, *a, **k):
        pass


CANARY_IDS = list(_canary_cases(_Names))


def _clean(*texts):
    for s in texts:
        assert SECRET not in str(s).lower(), s


@pytest.mark.security
@pytest.mark.parametrize("case", CANARY_IDS)
def test_canary_never_in_diagnostics(fake_transport, case):
    text, t, kw = _canary_cases(fake_transport)[case]
    with pytest.raises(mdq.MdqError) as info:
        fetch(text, t, **{"policy": None, **kw})
    e = info.value
    _clean(str(e), repr(e), *e.args, e.code, e.path)
    assert e.__cause__ is None and e.__context__ is None


@pytest.mark.security
def test_canary_never_in_a_successful_result(fake_transport):
    body = json.dumps({"price": 1, "note": SECRET}).encode()
    t = fake_transport({SURL: fake_transport.reply(body=body, headers={"Set-Cookie": SECRET})})
    r = fetch(decl(url=SURL, unit="USD"), t, headers={"X-Api-Key": SECRET})
    _clean(repr(r))
    assert t.gets[0]["headers"] == {"X-Api-Key": SECRET}  # sent, but never echoed


# ---------------------------------------------------------------- snapshots
def _entry(r):
    return {k: r[k] for k in ("name", "type", "value", "unit", "retrieved_at", "source")}


TWO = decl(unit="USD") + "  flag:\n    type: boolean\n    source: {kind: http, url: 'https://example.invalid/f', format: text}\n" \
                         "  host_value:\n    type: string\n"


def _two(FT):
    t = FT({URL: FT.reply(body=b'{"price": 2.50}'), "https://example.invalid/f": FT.reply(body=b"true\n")})
    return t, [_entry(mi.source_fetch(TWO, n, eff(), t, TS)) for n in ("p", "flag")]


def test_snapshot_replay_is_identical_and_offline(fake_transport):
    t, snap = _two(fake_transport)
    n = len(t.calls)
    out = mi.snapshot_apply(TWO, snap)
    assert list(out) == ["p", "flag"]
    for e in snap:
        assert out[e["name"]] == {k: v for k, v in e.items() if k != "name"}
    assert out["p"]["value"] == Decimal("2.5") and out["flag"]["value"] is True
    mi.source_validate(TWO)
    assert len(t.calls) == n  # neither validation nor replay touches a transport


def test_snapshot_entry_order_does_not_matter(fake_transport):
    _, snap = _two(fake_transport)
    assert mi.snapshot_apply(TWO, snap[::-1]) == mi.snapshot_apply(TWO, snap)


def test_failed_fetch_never_falls_back_to_a_snapshot(fake_transport, assert_mdq_error):
    t, snap = _two(fake_transport)
    t.routes[URL] = fake_transport.reply(503)
    with assert_mdq_error(FETCH):
        mi.source_fetch(TWO, "p", eff(), t, TS)
    t.routes[URL] = TimeoutError()
    with assert_mdq_error(FETCH):
        mi.source_fetch(TWO, "p", eff(), t, TS)


def test_snapshot_without_source_variables():
    assert mi.snapshot_apply("variables:\n  a:\n    type: number\n  b: 2\n", []) == {}
    assert mi.snapshot_apply("title: x\n", ()) == {}


def _mut(snap, i, path, value):
    import copy
    s = copy.deepcopy(snap)
    d = s[i]
    for k in path[:-1]:
        d = d[k]
    if value is KeyError:
        del d[path[-1]]
    else:
        d[path[-1]] = value
    return s


@pytest.mark.parametrize("change", [
    ("extra", 1), ("source", "x"), ("name", 3), ("name", "nope"), ("name", "host_value"), ("type", "integer"),
    ("type", None), ("unit", ""), ("unit", 5), ("retrieved_at", "2026-11-13"), ("retrieved_at", "2026-13-01T00:00:00Z"),
    ("retrieved_at", None), (("source", "key"), "ABC"), (("source", "key"), "g" * 64), (("source", "key"), "A" * 64),
    (("source", "extra"), 1), (("source", "kind"), None), (("source", "named"), 1), (("source", "origin"), 1),
    ("name", KeyError), ("value", KeyError), ("unit", KeyError), (("source", "key"), KeyError),
])
def test_snapshot_malformed_entries(fake_transport, assert_mdq_error, change):
    _, snap = _two(fake_transport)
    path, value = change
    path = path if isinstance(path, tuple) else (path,)
    with assert_mdq_error(BAD):
        mi.snapshot_apply(TWO, _mut(snap, 0, path, value))


@pytest.mark.parametrize("bad", [None, 1, "entry", [1], {1: "x"}])
def test_snapshot_entry_must_be_a_mapping_with_str_keys(fake_transport, assert_mdq_error, bad):
    _, snap = _two(fake_transport)
    if isinstance(bad, dict):
        bad = {**snap[0], **bad}
    with assert_mdq_error(BAD):
        mi.snapshot_apply(TWO, [bad, snap[1]])


def test_snapshot_duplicate_name(fake_transport, assert_mdq_error):
    _, snap = _two(fake_transport)
    with assert_mdq_error(BAD):
        mi.snapshot_apply(TWO, snap + [snap[0]])


def test_snapshot_missing_entry(fake_transport, assert_mdq_error):
    _, snap = _two(fake_transport)
    with assert_mdq_error(UNRES) as e:
        mi.snapshot_apply(TWO, snap[:1])
    assert "flag" in str(e.value)


@pytest.mark.parametrize("change", [
    ("type", "string"), ("unit", "EUR"), ("unit", None), (("source", "key"), "0" * 64),
    (("source", "origin"), "https://other.example:443"), (("source", "origin"), None),
    (("source", "kind"), "reader:x"),
])
def test_snapshot_mismatch_is_unresolved(fake_transport, assert_mdq_error, change):
    _, snap = _two(fake_transport)
    path, value = change
    path = path if isinstance(path, tuple) else (path,)
    with assert_mdq_error(UNRES):
        mi.snapshot_apply(TWO, _mut(snap, 0, path, value))


def test_snapshot_of_another_declaration_does_not_apply(fake_transport, assert_mdq_error):
    _, snap = _two(fake_transport)
    changed = TWO.replace("select: /price", "select: /cost")
    with assert_mdq_error(UNRES):
        mi.snapshot_apply(changed, snap)


@pytest.mark.parametrize("value", ["2.5", 2.5, None, True, [1], {"a": 1}, Decimal("NaN"), Decimal("Infinity"), object()])
def test_snapshot_values_are_type_checked(fake_transport, assert_mdq_error, value):
    _, snap = _two(fake_transport)
    with assert_mdq_error(TYPE):
        mi.snapshot_apply(TWO, _mut(snap, 0, ("value",), value))


@pytest.mark.parametrize("value,want", [(3, Decimal(3)), (Decimal("2.50"), Decimal("2.5")), (Decimal("1E+3"), Decimal(1000))])
def test_snapshot_numbers_are_canonical(fake_transport, value, want):
    _, snap = _two(fake_transport)
    v = mi.snapshot_apply(TWO, _mut(snap, 0, ("value",), value))["p"]["value"]
    assert v == want and str(v) == str(want)


def test_snapshot_named_is_not_identity(fake_transport):
    _, snap = _two(fake_transport)
    assert mi.snapshot_apply(TWO, _mut(snap, 0, ("source", "named"), "renamed"))["p"]["source"]["named"] is None


def test_snapshot_argument_types():
    with pytest.raises(TypeError):
        mi.snapshot_apply(TWO, {"p": {}})
    with pytest.raises(TypeError):
        mi.snapshot_apply(TWO, None)
    with pytest.raises(TypeError):
        mi.snapshot_apply(None, [])


# ---------------------------------------------------------------- argument errors
@pytest.mark.parametrize("args,kw", [
    ((1, "p", {}, print, TS), {}), ((decl(), None, {}, print, TS), {}), ((decl(), "p", [], print, TS), {}),
    ((decl(), "p", {}, "not callable", TS), {}), ((decl(), "p", {}, None, TS), {}), ((decl(), "p", {}, print, 5), {}),
    ((decl(), "p", {}, print, TS), {"allow_network": 1}), ((decl(), "p", {}, print, TS), {"allow_network": None}),
    ((decl(), "p", {}, print, TS), {"allow_network": "yes"}), ((decl(), "p", {}, print, TS), {"headers": [("a", "b")]}),
    ((decl(), "p", {}, print, TS), {"headers": {1: "a"}}), ((decl(), "p", {}, print, TS), {"headers": {"a": 1}}),
    ((decl(), "p", {}, print, TS), {"headers": {"a": b"x"}}),
])
def test_type_errors(args, kw):
    with pytest.raises(TypeError):
        mi.source_fetch(*args, **kw)


@pytest.mark.parametrize("ts", [
    "", "2026-11-13", "2026-11-13T12:00:00", "2026-11-13 12:00:00Z", "2026-11-13t12:00:00z", "2026-11-13T12:00:00+00:00",
    "2026-02-29T00:00:00Z", "2026-02-30T00:00:00Z", "2026-13-01T00:00:00Z", "0000-01-01T00:00:00Z",
    "2026-11-13T24:00:00Z", "2026-11-13T12:60:00Z", "2026-11-13T12:00:60Z", "2026-11-13T12:00:00.5Z",
    " 2026-11-13T12:00:00Z", "2026-11-13T12:00:00Z ", "\uff12026-11-13T12:00:00Z", "2026-1-13T12:00:00Z",
    "2026-11-13T1:00:00Z", "+026-11-13T12:00:00Z", "2026-11-13T12:00:0\u0661Z",
])
def test_bad_retrieved_at(fake_transport, ts):
    t = fake_transport()
    with pytest.raises(ValueError):
        mi.source_fetch(decl(), "p", eff(), t, ts)
    with pytest.raises(ValueError):  # argument errors come before the document is even read
        mi.source_fetch("sources: [", "p", eff(), t, ts)
    assert t.calls == []


@pytest.mark.parametrize("ts", ["2024-02-29T23:59:59Z", "0001-01-01T00:00:00Z", "9999-12-31T23:59:59Z", "2000-02-29T00:00:00Z"])
def test_good_retrieved_at_is_echoed(fake_transport, ts):
    t = fake_transport({URL: fake_transport.reply(body=b'{"price": 1}')})
    assert mi.source_fetch(decl(), "p", eff(), t, ts)["retrieved_at"] == ts


@pytest.mark.parametrize("headers", [
    {"": "x"}, {"X Key": "v"}, {"X:Key": "v"}, {"X\r\nInjected": "v"}, {"K\n": "v"}, {"\u00e9": "v"}, {"K\x00": "v"},
    {"K": "a\r\nInjected: 1"}, {"K": "a\nb"}, {"K": "\x00"}, {"K": "\u00e9"}, {"K": "a\tb"}, {"K": "\x7f"},
    {"Accept": "a", "accept": "b"},
])
def test_bad_headers(fake_transport, headers):
    t = fake_transport()
    with pytest.raises(ValueError):
        fetch(decl(), t, headers=headers)
    assert t.calls == []


def test_unusual_but_legal_headers(fake_transport):
    h = {"X-A!#$%&'*+.^_`|~": "v a l u e ~!@#", "Empty": ""}
    t = fake_transport({URL: fake_transport.reply(body=b'{"price": 1}')})
    fetch(decl(), t, headers=h)
    assert t.gets[0]["headers"] == h


# ---------------------------------------------------------------- declaration key and SHA-256
@given(st.binary(max_size=300))
def test_sha256_matches_hashlib(data):
    assert mi.sha256_hex(data) == hashlib.sha256(data).hexdigest()


@pytest.mark.parametrize("n", [0, 1, 55, 56, 57, 63, 64, 65, 119, 120, 128, 1000])
def test_sha256_padding_boundaries(n):
    data = bytes(range(256)) * 4
    assert mi.sha256_hex(data[:n]) == hashlib.sha256(data[:n]).hexdigest()


_ARG = st.text(st.characters(codec="utf-8", exclude_categories=("Cc", "Cs", "Zl", "Zp", "Cf", "Co", "Cn")), min_size=1,
               max_size=8).filter(lambda s: s.strip() == s and s.strip() != "")


@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])  # the fixture is a stateless class
@given(arg=_ARG, unit=_ARG)
def test_declaration_key_matches_python_json(fake_transport, arg, unit):
    q = arg.replace("'", "''")
    u = unit.replace("'", "''")
    text = ("sources:\n  s:\n    kind: http\n    url: https://example.invalid/{a}\n    format: json\n"
            f"variables:\n  p:\n    type: number\n    unit: '{u}'\n    source: s\n    arguments: {{a: '{q}'}}\n")
    url = mi.source_validate(text)[0]["url"]
    t = fake_transport({url: fake_transport.reply(body=b"1")})
    assert fetch(text, t)["source"]["key"] == py_key(text)


@pytest.mark.parametrize("value", ["\u00e9", "\U0001F600", '"q"', "back\\slash", "a/b", "a\u00a0b", "\u0100\uffef"])
def test_declaration_key_escapes(fake_transport, value):
    text = ("variables:\n  p:\n    type: number\n    unit: " + json.dumps(value, ensure_ascii=False) + "\n    source:\n      kind: http\n"
            "      url: https://example.invalid/x\n      format: json\n")
    t = fake_transport({"https://example.invalid/x": fake_transport.reply(body=b"1")})
    assert fetch(text, t)["source"]["key"] == py_key(text)


def test_key_is_identity_not_url(fake_transport):
    # Same declaration, different argument -> different URL and different key; identical declarations -> same key.
    a = decl(url="https://example.invalid/a")
    b = decl(url="https://example.invalid/b")
    assert py_key(a) != py_key(b) and py_key(a) == py_key(decl(url="https://example.invalid/a"))


# ---------------------------------------------------------------- url_join
BASE = "http://a/b/c/d;p?q"
RFC_3986_5_4 = [
    ("g:h", "g:h"), ("g", "http://a/b/c/g"), ("./g", "http://a/b/c/g"), ("g/", "http://a/b/c/g/"), ("/g", "http://a/g"),
    ("//g", "http://g"), ("?y", "http://a/b/c/d;p?y"), ("g?y", "http://a/b/c/g?y"), ("#s", "http://a/b/c/d;p?q"),
    ("g#s", "http://a/b/c/g"), ("g?y#s", "http://a/b/c/g?y"), (";x", "http://a/b/c/;x"), ("g;x", "http://a/b/c/g;x"),
    ("g;x?y#s", "http://a/b/c/g;x?y"), ("", "http://a/b/c/d;p?q"), (".", "http://a/b/c/"), ("./", "http://a/b/c/"),
    ("..", "http://a/b/"), ("../", "http://a/b/"), ("../g", "http://a/b/g"), ("../..", "http://a/"),
    ("../../", "http://a/"), ("../../g", "http://a/g"),
    # 5.4.2 abnormal examples
    ("../../../g", "http://a/g"), ("../../../../g", "http://a/g"), ("/./g", "http://a/g"), ("/../g", "http://a/g"),
    ("g.", "http://a/b/c/g."), (".g", "http://a/b/c/.g"), ("g..", "http://a/b/c/g.."), ("..g", "http://a/b/c/..g"),
    ("./../g", "http://a/b/g"), ("./g/.", "http://a/b/c/g/"), ("g/./h", "http://a/b/c/g/h"), ("g/../h", "http://a/b/c/h"),
    ("g;x=1/./y", "http://a/b/c/g;x=1/y"), ("g;x=1/../y", "http://a/b/c/y"), ("g?y/./x", "http://a/b/c/g?y/./x"),
    ("g?y/../x", "http://a/b/c/g?y/../x"), ("g#s/./x", "http://a/b/c/g"), ("g#s/../x", "http://a/b/c/g"),
    ("http:g", "http:g"),
]


@pytest.mark.parametrize("ref,want", RFC_3986_5_4)
def test_url_join_rfc_examples(ref, want):
    assert mi.url_join(BASE, ref) == want


@pytest.mark.parametrize("base,ref,want", [
    ("https://h.example", ".", "https://h.example/"), ("https://h.example", "..", "https://h.example/"),
    ("https://h.example", "g", "https://h.example/g"), ("https://h.example/x#f", "", "https://h.example/x"),
    ("https://h.example/x", "?", "https://h.example/x?"), ("HTTPS://h.example/x", "//o.example/p", "HTTPS://o.example/p"),
    ("https://h.example/a/b", "/..", "https://h.example/"),
])
def test_url_join_edges(base, ref, want):
    assert mi.url_join(base, ref) == want


@pytest.mark.parametrize("base", ["ftp://a/b", "a/b", "", "https:/a", "mailto:x@y", "//a/b"])
def test_url_join_needs_an_absolute_http_base(base):
    with pytest.raises(ValueError):
        mi.url_join(base, "g")


_BASES = ["https://a/b/c/d;p?q", "https://h.example/x", "https://h.example", "https://h.example/x/y/",
          "https://h.example:8443/a/b?c=d", "http://a/b/c/d;p?q"]
_PIECES = st.sampled_from(["a", "b", ".", "..", "/", "?", "=", "#", "x", "%41", "-", "~"])


def _plain_ref(r):
    # Where Python's urljoin knowingly differs from RFC 3986: ';' parameters, collapsed '//', empty query dropped.
    path = r.split("#")[0].split("?")[0]
    q = r.split("#")[0]
    return "//" not in path and not ("?" in q and q.split("?", 1)[1] == "")


@pytest.mark.property
@pytest.mark.differential
@given(base=st.sampled_from(_BASES), ref=st.lists(_PIECES, max_size=10).map("".join).filter(_plain_ref))
def test_url_join_matches_urllib(base, ref):
    assert mi.url_join(base, ref) == urljoin(base, ref).split("#")[0]
