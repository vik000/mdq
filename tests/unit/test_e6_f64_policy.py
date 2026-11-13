"""Epic 6 / E6.4 (F18, F19): network policy (sources-and-permissions.md section 4). G45.

`policy_intersect(host=None, library=None, document=None)` combines the two trusted layers (reader/user and
library) with the document's REQUESTS: the most restrictive result wins and a document can only narrow.
`policy_decide(policy, url, redirect_count=0)` applies the effective policy to one URL (after `url_classify`
rules); `policy_ip_allowed(policy, host, address)` re-checks a resolved address (DNS rebinding).
"""
import itertools

import pytest
from hypothesis import given, strategies as st

import mdq
from mdq import _internal as mi

pytestmark = pytest.mark.unit

inter = mi.policy_intersect
decide = mi.policy_decide
ip_ok = mi.policy_ip_allowed
DENIED, BAD = "MDQ_SECURITY_DENIED", "MDQ_SOURCE_INVALID"
ALLOW = {"network": "allow"}
DEFAULTS = {"max_bytes": 1048576, "timeout": 10, "max_redirects": 0, "max_concurrency": 4}


def eff(**kw):
    layer = {"network": "allow", **kw}
    return inter(layer, layer)


# ---------------------------------------------------------------- network switch
def test_default_is_deny():
    p = inter()
    assert p == {"network": "deny", "hosts": None, "ports": [443], "private_endpoints": [], **DEFAULTS, "network_required": False}
    assert inter({}, {})["network"] == "deny"
    assert inter(ALLOW)["network"] == "deny" and inter(None, ALLOW)["network"] == "deny"
    assert inter({"network": "deny"}, ALLOW)["network"] == "deny" and inter(ALLOW, {"network": "deny"})["network"] == "deny"


def test_both_trusted_layers_must_allow():
    p = inter(ALLOW, ALLOW)
    assert p["network"] == "allow" and p["hosts"] is None and p["ports"] == [443] and p["network_required"] is False
    for k, v in DEFAULTS.items():
        assert p[k] == v


@pytest.mark.parametrize("doc,network,required", [
    (None, "allow", False), ({}, "allow", False), ({"requirements": {"network": "optional"}}, "allow", False),
    ({"requirements": {"network": "required"}}, "allow", True), ({"requirements": {"network": "none"}}, "deny", False),
    ({"security": {"network": "request"}}, "allow", False), ({"security": {"network": "none"}}, "deny", False),
    ({"requirements": {"network": "required"}, "security": {"network": "request"}}, "allow", True),
    ({"requirements": {"network": "required"}, "security": {"network": "none"}}, "deny", True),
    ({"requirements": None, "security": None}, "allow", False),
])
def test_document_requests_never_grant_and_can_only_narrow(doc, network, required):
    p = inter(ALLOW, ALLOW, doc)
    assert (p["network"], p["network_required"]) == (network, required)
    assert inter(None, ALLOW, doc)["network"] == "deny" and inter({"network": "deny"}, ALLOW, doc)["network"] == "deny"


@pytest.mark.parametrize("doc", [
    {"network": "allow"}, {"hosts": ["x.org"]}, {"private_endpoints": ["localhost"]}, {"requirements": {"private_endpoints": ["x"]}},
    {"requirements": {"ports": [8443]}}, {"requirements": {"max_bytes": 5}}, {"security": {"private_endpoints": ["x"]}},
    {"security": {"allow_private": True}}, {"requirements": {"network": "maybe"}}, {"security": {"network": "allow"}},
    {"requirements": [1]}, {"security": "x"}, {"requirements": {"network": 1}}, {"x": 1},
])
def test_documents_cannot_carry_trusted_keys_or_bad_values(doc, assert_mdq_error):
    with assert_mdq_error(BAD):
        inter(ALLOW, ALLOW, doc)


# ---------------------------------------------------------------- host lists
@pytest.mark.parametrize("a,b,expected", [
    ("a.com", "a.com", "a.com"), ("a.com", "b.com", None), ("*.a.com", "*.a.com", "*.a.com"), ("*.a.com", "x.a.com", "x.a.com"),
    ("x.a.com", "*.a.com", "x.a.com"), ("*.a.com", "a.com", None), ("a.com", "*.a.com", None), ("*.a.com", "x.y.a.com", "x.y.a.com"),
    ("*.a.com", "*.b.a.com", "*.b.a.com"), ("*.b.a.com", "*.a.com", "*.b.a.com"), ("*.a.com", "*.b.com", None), ("*.a.com", "*.ba.com", None),
    ("*.a.com", "xa.com", None), ("8.8.8.8", "8.8.8.8", "8.8.8.8"), ("8.8.8.8", "*.a.com", None), ("2606:4700::1111", "2606:4700::1111", "2606:4700::1111"),
])
def test_host_pattern_intersection(a, b, expected):
    h = inter({**ALLOW, "hosts": [a]}, {**ALLOW, "hosts": [b]})["hosts"]
    assert h == ([] if expected is None else [expected])


def test_host_lists_none_means_unrestricted_and_empty_means_nothing():
    assert inter(ALLOW, {**ALLOW, "hosts": ["a.com"]})["hosts"] == ["a.com"]
    assert inter({**ALLOW, "hosts": ["a.com"]}, ALLOW)["hosts"] == ["a.com"]
    assert inter({**ALLOW, "hosts": []}, {**ALLOW, "hosts": ["a.com"]})["hosts"] == []
    assert inter(ALLOW, ALLOW)["hosts"] is None
    assert inter(ALLOW, ALLOW, {"requirements": {"hosts": ["a.com"]}})["hosts"] == ["a.com"]


def test_document_hosts_narrow_but_never_widen():
    h, l = {**ALLOW, "hosts": ["*.a.com", "b.org"]}, {**ALLOW, "hosts": ["*.a.com", "b.org", "c.net"]}
    assert inter(h, l, {"requirements": {"hosts": ["x.a.com", "c.net", "evil.io"]}})["hosts"] == ["x.a.com"]
    assert inter(h, l, {"requirements": {"hosts": []}})["hosts"] == []


def test_host_lists_are_normalised_deduplicated_and_sorted():
    p = inter({**ALLOW, "hosts": ["B.com", "a.com", "A.COM", "*.Z.org"]}, ALLOW)
    assert p["hosts"] == ["*.z.org", "a.com", "b.com"]
    assert inter({**ALLOW, "hosts": ["2606:4700:0:0:0:0:0:1111", "8.8.8.8"]}, ALLOW)["hosts"] == ["2606:4700::1111", "8.8.8.8"]


@pytest.mark.parametrize("bad", ["", "*", "*.", "*.com", "a..com", ".com", "a.com.", "-a.com", "a-.com", "a b.com", "é.com", "a.com/x", "a.com:443", "http://a.com",
                                 "*.*.a.com", "a.*.com", "**.a.com", "*a.com", "1.2.3", "127.1", "0x7f.1", "1.2.3.4.5", "::g", "1.2.3.04", "[::1]", "a" * 64 + ".com"])
def test_invalid_host_patterns(bad, assert_mdq_error):
    with assert_mdq_error(BAD):
        inter({**ALLOW, "hosts": [bad]}, ALLOW)


@pytest.mark.parametrize("bad", [5, None, b"a.com", ["a.com"], {"a": 1}])
def test_host_entries_must_be_strings(bad, assert_mdq_error):
    with assert_mdq_error(BAD):
        inter({**ALLOW, "hosts": [bad]}, ALLOW)


def test_host_list_type_and_size(assert_mdq_error):
    for v in ("a.com", 5, {"a.com": 1}):
        with assert_mdq_error(BAD):
            inter({**ALLOW, "hosts": v}, ALLOW)
    assert len(inter({**ALLOW, "hosts": [f"h{i}.com" for i in range(256)]}, ALLOW)["hosts"]) == 256
    with assert_mdq_error("MDQ_RESOURCE_LIMIT"):
        inter({**ALLOW, "hosts": [f"h{i}.com" for i in range(257)]}, ALLOW)


_lab = st.sampled_from(["a", "b", "ab"])
_dom = st.lists(_lab, min_size=2, max_size=4).map(".".join)
_pat = st.one_of(_dom, _dom.map(lambda d: "*." + d))


def _m(host, p):
    return host.endswith(p[1:]) and len(host) > len(p) - 1 if p.startswith("*.") else host == p


@given(_pat, _pat, st.lists(_lab, min_size=1, max_size=5).map(".".join))
def test_intersection_is_exactly_the_hosts_matched_by_both(a, b, host):
    both = inter({**ALLOW, "hosts": [a]}, {**ALLOW, "hosts": [b]})["hosts"]
    assert any(_m(host, p) for p in both) == (_m(host, a) and _m(host, b))


@given(st.lists(_pat, max_size=4), st.lists(_pat, max_size=4), st.lists(_pat, max_size=4), _dom)
def test_intersection_is_commutative_and_narrowing(a, b, c, host):
    h, l, d = {**ALLOW, "hosts": a}, {**ALLOW, "hosts": b}, {"requirements": {"hosts": c}}
    p1, p2 = inter(h, l, d), inter(l, h, d)
    assert p1 == p2
    allowed = lambda p: any(_m(host, x) for x in p["hosts"])
    assert allowed(p1) == (any(_m(host, x) for x in a) and any(_m(host, x) for x in b) and any(_m(host, x) for x in c))
    assert (not allowed(p1)) or allowed(inter(h, l))


# ---------------------------------------------------------------- ports, private endpoints, limits
def test_ports():
    assert inter(ALLOW, ALLOW)["ports"] == [443]
    assert inter({**ALLOW, "ports": [443, 8443]}, ALLOW)["ports"] == [443]
    assert inter({**ALLOW, "ports": [8443, 443, 443]}, {**ALLOW, "ports": [443, 8443, 9000]})["ports"] == [443, 8443]
    assert inter({**ALLOW, "ports": [8443]}, ALLOW)["ports"] == []
    assert inter({**ALLOW, "ports": []}, ALLOW)["ports"] == []


@pytest.mark.parametrize("bad", [0, 65536, -1, 1.5, True, "443", None, [443]])
def test_invalid_ports(bad, assert_mdq_error):
    with assert_mdq_error(BAD):
        inter({**ALLOW, "ports": [bad]}, ALLOW)
    with assert_mdq_error(BAD):
        inter({**ALLOW, "ports": 443}, ALLOW)


def test_private_endpoints_need_both_trusted_layers():
    pe = {"private_endpoints": ["intranet.corp", "*.dev.corp"]}
    assert inter({**ALLOW, **pe}, {**ALLOW, **pe})["private_endpoints"] == ["*.dev.corp", "intranet.corp"]
    assert inter({**ALLOW, **pe}, ALLOW)["private_endpoints"] == [] and inter(ALLOW, {**ALLOW, **pe})["private_endpoints"] == []
    assert inter({**ALLOW, **pe}, {**ALLOW, "private_endpoints": ["x.dev.corp"]})["private_endpoints"] == ["x.dev.corp"]
    assert inter({**ALLOW, "private_endpoints": ["10.0.0.5"]}, {**ALLOW, "private_endpoints": ["10.0.0.5"]})["private_endpoints"] == ["10.0.0.5"]


def test_limits_take_the_minimum_with_defaults_and_caps():
    p = inter({**ALLOW, "max_bytes": 5000, "timeout": 30, "max_redirects": 3, "max_concurrency": 2}, {**ALLOW, "max_bytes": 9000, "timeout": 5})
    assert (p["max_bytes"], p["timeout"], p["max_redirects"], p["max_concurrency"]) == (5000, 5, 0, 2)
    p = inter({**ALLOW, "max_redirects": 3}, {**ALLOW, "max_redirects": 4})
    assert p["max_redirects"] == 3
    big = {**ALLOW, "max_bytes": 10**12, "timeout": 10**6, "max_redirects": 99, "max_concurrency": 999}
    p = inter(big, big)
    assert (p["max_bytes"], p["timeout"], p["max_redirects"], p["max_concurrency"]) == (16 << 20, 60, 5, 16)


@pytest.mark.parametrize("key,bad", [(k, v) for k, vs in {"max_bytes": [0, -1, 1.5, True, "5"], "timeout": [0, -3, 2.5, False],
                                                          "max_redirects": [-1, 1.0, True], "max_concurrency": [0, 1.5]}.items() for v in vs])
def test_invalid_limits(key, bad, assert_mdq_error):
    with assert_mdq_error(BAD):
        inter({**ALLOW, key: bad}, ALLOW)


def test_layer_validation(assert_mdq_error):
    for bad in ({"network": "yes"}, {"network": True}, {"nework": "allow"}, {"network_required": True}, {"hosts": None, "x": 1}):
        with assert_mdq_error(BAD):
            inter(bad, ALLOW)
    for v in ("allow", 5, [("network", "allow")]):
        with pytest.raises(TypeError):
            inter(v)
    with pytest.raises(TypeError):
        inter(ALLOW, ALLOW, "x")
    with pytest.raises(TypeError):
        inter({1: 2})


def test_intersect_is_deterministic_and_pure():
    h = {**ALLOW, "hosts": ["b.com", "a.com"], "ports": [8443, 443]}
    snapshot = dict(h)
    outs = {repr(inter(h, h, {"requirements": {"hosts": ["a.com"]}})) for _ in range(50)}
    assert len(outs) == 1 and h == snapshot and h["hosts"] == ["b.com", "a.com"]


# ---------------------------------------------------------------- policy_decide
def open_policy(**kw):
    return inter({**ALLOW, **kw}, {**ALLOW, **kw})


def test_allowed_decision():
    d = decide(open_policy(), "https://api.example.org/prices?x=1")
    assert d == {"allow": True, "scheme": "https", "host": "api.example.org", "kind": "name", "port": 443, "origin": "https://api.example.org:443",
                 "private": False, "limits": dict(DEFAULTS)}


def test_network_disabled_is_denied_and_says_when_required(assert_mdq_error):
    with assert_mdq_error(DENIED) as info:
        decide(inter(), "https://api.example.org/")
    assert "required" not in str(info.value)
    with assert_mdq_error(DENIED) as info:
        decide(inter(ALLOW, {"network": "deny"}, {"requirements": {"network": "required"}}), "https://api.example.org/")
    assert "requires" in str(info.value)
    with assert_mdq_error(DENIED):
        decide(inter(ALLOW, ALLOW, {"requirements": {"network": "none"}}), "https://api.example.org/")


def test_required_does_not_override_denial():
    p = inter({"network": "deny"}, ALLOW, {"requirements": {"network": "required"}})
    assert p["network"] == "deny" and p["network_required"] is True
    with pytest.raises(mdq.MdqError) as e:
        decide(p, "https://api.example.org/")
    assert e.value.code == DENIED


def test_allowlist_wildcards_and_apex(assert_mdq_error):
    p = open_policy(hosts=["*.example.org", "exact.net"])
    assert decide(p, "https://a.example.org/")["host"] == "a.example.org"
    assert decide(p, "https://a.b.example.org/")["host"] == "a.b.example.org"
    assert decide(p, "https://EXACT.net./")["host"] == "exact.net"
    for u in ("https://example.org/", "https://evil.org/", "https://example.org.evil.org/", "https://xexample.org/", "https://sub.exact.net/", "https://notexact.net/"):
        with assert_mdq_error(DENIED):
            decide(p, u)


def test_ip_hosts_only_match_exact_ip_patterns(assert_mdq_error):
    p = open_policy(hosts=["8.8.8.8", "*.example.org"])
    assert decide(p, "https://8.8.8.8/")["kind"] == "ipv4" and decide(p, "https://0x8080808/")["host"] == "8.8.8.8"
    with assert_mdq_error(DENIED):
        decide(p, "https://8.8.4.4/")
    with assert_mdq_error(DENIED):
        decide(open_policy(hosts=["x.org"]), "https://8.8.8.8/")


def test_ports_are_enforced(assert_mdq_error):
    p = open_policy(ports=[443, 8443])
    assert decide(p, "https://a.example.org:8443/")["port"] == 8443
    for u in ("https://a.example.org:8444/", "https://a.example.org:80/", "https://a.example.org:65535/"):
        with assert_mdq_error(DENIED):
            decide(p, u)
    with assert_mdq_error(DENIED):
        decide(open_policy(), "https://a.example.org:8443/")


def test_allowlist_does_not_bypass_ip_rules(assert_mdq_error):
    p = open_policy(hosts=["127.0.0.1", "localhost", "169.254.169.254", "a.localhost", "::1", "10.0.0.5", "intranet"])
    for u in ("https://127.0.0.1/", "https://localhost/", "https://169.254.169.254/", "https://a.localhost/", "https://[::1]/", "https://10.0.0.5/", "https://intranet/",
              "https://2130706433/", "https://0x7f.1/", "https://[::ffff:7f00:1]/"):
        with assert_mdq_error(DENIED):
            decide(p, u)


def test_private_endpoint_override_only_from_trusted_policy():
    pe = {"private_endpoints": ["intranet.corp", "10.0.0.5", "*.dev.corp"]}
    p = inter({**ALLOW, **pe}, {**ALLOW, **pe})
    d = decide(p, "https://10.0.0.5/x")
    assert d["private"] is True and d["host"] == "10.0.0.5" and d["kind"] == "ipv4"
    assert decide(p, "https://x.dev.corp/")["private"] is True
    for u in ("https://10.0.0.6/", "https://127.0.0.1/", "https://dev.corp/", "https://other.corp/", "https://169.254.169.254/"):
        with pytest.raises(mdq.MdqError) as e:
            decide(p, u)
        assert e.value.code == DENIED
    one_sided = inter({**ALLOW, **pe}, ALLOW)
    with pytest.raises(mdq.MdqError):
        decide(one_sided, "https://10.0.0.5/")
    with pytest.raises(mdq.MdqError):
        decide(inter(ALLOW, ALLOW, {"requirements": {"hosts": ["10.0.0.5"]}}), "https://10.0.0.5/")
    assert decide(p, "https://8.8.8.8/")["private"] is False


def test_private_endpoint_still_needs_the_allowlist_and_ports(assert_mdq_error):
    pe = {"private_endpoints": ["10.0.0.5"], "hosts": ["intranet.corp"]}
    with assert_mdq_error(DENIED):
        decide(inter({**ALLOW, **pe}, {**ALLOW, **pe}), "https://10.0.0.5/")
    pe = {"private_endpoints": ["10.0.0.5"], "ports": [8443]}
    p = inter({**ALLOW, **pe}, {**ALLOW, **pe})
    assert decide(p, "https://10.0.0.5:8443/")["private"] is True
    with assert_mdq_error(DENIED):
        decide(p, "https://10.0.0.5/")


def test_redirects(assert_mdq_error):
    p = open_policy(max_redirects=2)
    assert decide(p, "https://a.example.org/", 0)["limits"]["max_redirects"] == 2
    assert decide(p, "https://a.example.org/", 2)
    with assert_mdq_error(DENIED):
        decide(p, "https://a.example.org/", 3)
    with assert_mdq_error(DENIED):
        decide(open_policy(), "https://a.example.org/", 1)
    with assert_mdq_error(DENIED):
        decide(p, "https://127.0.0.1/", 1)
    for bad in (-1, 1.5, True, "1", None):
        with pytest.raises(TypeError):
            decide(p, "https://a.example.org/", bad)


@pytest.mark.parametrize("url,code", [
    ("http://a.example.org/", DENIED), ("file:///etc/passwd", DENIED), ("https://u:p@a.example.org/", DENIED), ("https://a.example.org:0/", BAD),
    ("a.example.org", BAD), ("https://", BAD), ("https://exa mple.org/", BAD), ("https://256.1.1.1/", BAD), ("", BAD),
])
def test_url_errors_propagate(url, code, assert_mdq_error):
    with assert_mdq_error(code):
        decide(open_policy(), url)


def test_decide_validates_the_policy(assert_mdq_error):
    for bad in ({"network": "allow", "bogus": 1}, {"network": "maybe"}, {"network": "allow", "hosts": ["*.com"]}, {"network": "allow", "ports": [0]}):
        with assert_mdq_error(BAD):
            decide(bad, "https://a.example.org/")
    with pytest.raises(TypeError):
        decide("x", "https://a.example.org/")
    with pytest.raises(TypeError):
        decide(open_policy(), 5)
    assert decide({"network": "allow"}, "https://a.example.org/")["limits"] == DEFAULTS
    with pytest.raises(mdq.MdqError):
        decide({}, "https://a.example.org/")


def test_decision_limits_come_from_the_effective_policy():
    d = decide(open_policy(max_bytes=2048, timeout=3, max_concurrency=1), "https://a.example.org/")
    assert d["limits"] == {"max_bytes": 2048, "timeout": 3, "max_redirects": 0, "max_concurrency": 1}


def test_diagnostics_never_echo_urls_or_hosts():
    secret = "S3CRET-TOKEN-123"
    p = open_policy(hosts=["*.example.org"], ports=[443])
    for url in (f"https://evil.io/?k={secret}", f"https://a.example.org:8443/?k={secret}", f"https://127.0.0.1/{secret}", f"http://a.example.org/{secret}",
                f"https://u:{secret}@a.example.org/", f"https://a.example.org/{secret}"):
        try:
            decide(p, url)
        except mdq.MdqError as e:
            assert secret not in str(e) and secret not in repr(e) and "evil.io" not in str(e) and "127.0.0.1" not in str(e)
    for url in (f"https://evil.io/?k={secret}",):
        with pytest.raises(mdq.MdqError) as e:
            decide(inter(), url)
        assert secret not in str(e.value)


# ---------------------------------------------------------------- resolved-address check
def test_resolved_addresses_are_rechecked():
    p = open_policy()
    assert ip_ok(p, "api.example.org", "8.8.8.8") is True and ip_ok(p, "api.example.org", "2606:4700::1111") is True
    for ip in ("127.0.0.1", "10.1.2.3", "169.254.169.254", "100.64.0.1", "::1", "::ffff:10.0.0.1", "fe80::1", "fd00::1"):
        assert ip_ok(p, "api.example.org", ip) is False


def test_resolved_private_address_is_allowed_only_for_private_endpoints():
    pe = {"private_endpoints": ["intranet.corp", "*.dev.corp"]}
    p = inter({**ALLOW, **pe}, {**ALLOW, **pe})
    assert ip_ok(p, "intranet.corp", "10.0.0.5") is True and ip_ok(p, "INTRANET.corp.", "10.0.0.5") is True and ip_ok(p, "x.dev.corp", "192.168.1.1") is True
    assert ip_ok(p, "evil.example.org", "10.0.0.5") is False and ip_ok(p, "dev.corp", "10.0.0.5") is False
    assert ip_ok(p, "intranet.corp", "8.8.8.8") is True


def test_ip_check_is_false_when_the_network_is_denied():
    assert ip_ok(inter(), "a.example.org", "8.8.8.8") is False
    assert ip_ok(inter(ALLOW, {"network": "deny"}), "a.example.org", "8.8.8.8") is False


def test_ip_check_argument_errors():
    p = open_policy()
    for bad in ("localhost", "127.1", "1.2.3", "", "::1%eth0"):
        with pytest.raises(ValueError):
            ip_ok(p, "a.example.org", bad)
    for v in (None, 5):
        with pytest.raises(TypeError):
            ip_ok(p, v, "8.8.8.8")
        with pytest.raises(TypeError):
            ip_ok(p, "a.example.org", v)


@given(st.integers(0, 2**32 - 1))
def test_ip_allowed_matches_ip_is_blocked_without_overrides(n):
    import ipaddress
    ip = str(ipaddress.IPv4Address(n))
    assert ip_ok(open_policy(), "a.example.org", ip) == (not mi.ip_is_blocked(ip))


def test_names_that_look_like_ips_still_match_wildcards():
    p = open_policy(hosts=["*.example.org"])
    assert decide(p, "https://1.a.b.example.org/")["host"] == "1.a.b.example.org"
