"""Epic 6 / E6.3 (F17, F18): URL and IP classification (sources-and-permissions.md section 4). G44.

`ip_is_blocked(address)` takes a canonical IPv4/IPv6 literal. `url_classify(url)` takes an absolute URL and
either returns `{scheme, host, kind, port, origin}` (host normalised: lower-case, IPv4 dotted, IPv6 compressed)
or fails with MDQ_SECURITY_DENIED (not https, credentials, blocked IP or name) or MDQ_SOURCE_INVALID (malformed).
Offline: no DNS. The transport re-checks every resolved address with `ip_is_blocked`.
"""
import ipaddress
import socket

import pytest
from hypothesis import given, strategies as st

import mdq
from mdq import _internal as mi

pytestmark = pytest.mark.unit

blocked = mi.ip_is_blocked
cls = mi.url_classify
DENIED, BAD = "MDQ_SECURITY_DENIED", "MDQ_SOURCE_INVALID"

BLOCKED4 = [
    "0.0.0.0", "0.1.2.3", "0.255.255.255", "10.0.0.0", "10.255.255.255", "100.64.0.0", "100.100.100.200", "100.127.255.255",
    "127.0.0.1", "127.255.255.254", "169.254.0.1", "169.254.169.254", "169.254.255.255", "172.16.0.0", "172.31.255.255",
    "192.0.0.0", "192.0.0.192", "192.0.0.255", "192.0.2.1", "192.88.99.1", "192.168.0.1", "192.168.255.255", "198.18.0.0",
    "198.19.255.255", "198.51.100.7", "203.0.113.9", "224.0.0.1", "239.255.255.255", "240.0.0.1", "255.255.255.255", "168.63.129.16",
]
PUBLIC4 = [
    "1.1.1.1", "8.8.8.8", "9.255.255.255", "11.0.0.0", "100.63.255.255", "100.128.0.0", "126.255.255.255", "128.0.0.1",
    "169.253.255.255", "169.255.0.0", "172.15.255.255", "172.32.0.0", "191.255.255.255", "192.0.1.1", "192.88.98.255",
    "192.88.100.0", "192.167.255.255", "192.169.0.0", "198.17.255.255", "198.20.0.0", "198.51.99.255", "198.51.101.0",
    "203.0.112.255", "203.0.114.0", "223.255.255.255", "168.63.129.15", "168.63.129.17", "93.184.216.34",
]
BLOCKED6 = [
    "::", "::1", "::2", "::7f00:1", "::8.8.8.8", "::ffff:127.0.0.1", "::ffff:7f00:1", "::ffff:10.0.0.1", "::ffff:169.254.169.254",
    "::ffff:192.168.1.1", "::ffff:100.64.0.1", "::ffff:0.0.0.0", "::ffff:224.0.0.1", "64:ff9b::7f00:1", "64:ff9b::a00:1",
    "64:ff9b::169.254.169.254", "64:ff9b:1::1", "100::", "100::1", "2001::", "2001:0:4136:e378:8000:63bf:3fff:fdd2", "2001:2::1",
    "2001:10::1", "2001:1ff:ffff::1", "2001:db8::1", "2002:7f00:1::", "2002:a00:1::1", "2002:a9fe:a9fe::1", "3fff::1", "3fff:fff::1",
    "5f00::1", "fc00::1", "fd00::1", "fd00:ec2::254", "fdff:ffff::1", "fe80::1", "febf::1", "fec0::1", "ff00::1", "ff02::1", "ffff::1",
    "4000::1", "8000::1", "e000::1", "0:0:0:0:0:0:0:1", "0000:0000:0000:0000:0000:ffff:7f00:0001", "::FFFF:7F00:1",
]
PUBLIC6 = [
    "2606:4700::1111", "2001:4860:4860::8888", "2a00:1450:4001::1", "2001:200::1", "2001:200:ffff::1", "2001:db9::1", "2001:db7::1",
    "2002:808:808::1", "2400:cb00::1", "2620:fe::fe", "3ffe::1", "3ffe:ffff::1", "2000::1", "3fff:1000::1",
    "::ffff:8.8.8.8", "::ffff:1.1.1.1", "64:ff9b::808:808", "2002:808:808::",
]


@pytest.mark.parametrize("ip", BLOCKED4 + BLOCKED6)
def test_blocked_addresses(ip):
    assert blocked(ip) is True


@pytest.mark.parametrize("ip", PUBLIC4 + PUBLIC6)
def test_public_addresses(ip):
    assert blocked(ip) is False


@pytest.mark.parametrize("bad", [
    "", " ", "localhost", "example.com", "127.1", "127.0.1", "2130706433", "0x7f.0.0.1", "0177.0.0.1", "010.0.0.1", "01.2.3.4", "1.2.3", "1.2.3.4.5",
    "256.1.1.1", "-1.0.0.1", "1.2.3.4 ", " 1.2.3.4", "1.2.3.4/8", "1.2.3.4:80", "[::1]", "::1%eth0", "fe80::1%25eth0", "fe80::1%1", ":::", "1::2::3", "12345::1",
    "g::1", "::1/128", "127.0.0.1\n", "١.٢.٣.٤", "1.2.3.٤", "::ffff:1.2.3", "1:2:3:4:5:6:7:8:9",
])
def test_non_canonical_literals_are_value_errors(bad):
    with pytest.raises(ValueError):
        blocked(bad)


def test_ip_argument_types():
    for v in (None, 1, b"1.1.1.1", ipaddress.ip_address("1.1.1.1")):
        with pytest.raises(TypeError):
            blocked(v)


def _oracle(ip):
    nets4 = ["0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16", "172.16.0.0/12", "192.0.0.0/24", "192.0.2.0/24",
             "192.88.99.0/24", "192.168.0.0/16", "198.18.0.0/15", "198.51.100.0/24", "203.0.113.0/24", "224.0.0.0/4", "240.0.0.0/4", "168.63.129.16/32"]
    a = ipaddress.ip_address(ip)
    if a.version == 4:
        return any(a in ipaddress.ip_network(n) for n in nets4)
    if a.ipv4_mapped is not None:
        return _oracle(str(a.ipv4_mapped))
    n = int(a)
    if (n >> 32) == int(ipaddress.IPv6Address("64:ff9b::")) >> 32:  # 64:ff9b::/96
        return _oracle(str(ipaddress.IPv4Address(n & 0xFFFFFFFF)))
    if (n >> 80) == 0x0064 and ((n >> 64) & 0xFFFF) == 0xFF9B:
        return True
    if (n >> 32) == 0:
        return True
    if (n >> 125) != 1:  # outside 2000::/3
        return True
    if (n >> 112) == 0x2002:
        return _oracle(str(ipaddress.IPv4Address((n >> 80) & 0xFFFFFFFF)))
    return a in ipaddress.ip_network("2001::/23") or a in ipaddress.ip_network("2001:db8::/32") or a in ipaddress.ip_network("3fff::/20")


@given(st.integers(0, 2**32 - 1))
def test_ipv4_matches_independent_table(n):
    ip = str(ipaddress.IPv4Address(n))
    assert blocked(ip) == _oracle(ip)


@given(st.integers(0, 2**128 - 1))
def test_ipv6_matches_independent_table(n):
    ip = str(ipaddress.IPv6Address(n))
    assert blocked(ip) == _oracle(ip)


@given(st.integers(0, 2**32 - 1))
def test_mapped_nat64_and_6to4_follow_the_embedded_ipv4(n):
    v4 = ipaddress.IPv4Address(n)
    mapped = str(ipaddress.IPv6Address((0xFFFF << 32) | n))
    nat64 = str(ipaddress.IPv6Address(int(ipaddress.IPv6Address("64:ff9b::")) | n))
    sixto4 = str(ipaddress.IPv6Address((0x2002 << 112) | (n << 80)))
    assert blocked(mapped) == blocked(nat64) == blocked(sixto4) == blocked(str(v4))


# ---------------------------------------------------------------- url_classify: success
@pytest.mark.parametrize("url,host,kind,port", [
    ("https://example.invalid/prices/AAPL", "example.invalid", "name", 443),
    ("https://api.example.org:8443/x?y=1", "api.example.org", "name", 8443),
    ("HTTPS://API.Example.ORG/x", "api.example.org", "name", 443),
    ("https://Example.COM.:8443/a", "example.com", "name", 8443),
    ("https://example.com./a", "example.com", "name", 443),
    ("https://xn--bcher-kva.example/", "xn--bcher-kva.example", "name", 443),
    ("https://my_host.example.com/", "my_host.example.com", "name", 443),
    ("https://a.b.c.d.e.f.example.com", "a.b.c.d.e.f.example.com", "name", 443),
    ("https://127.0.0.1.example.com/", "127.0.0.1.example.com", "name", 443),
    ("https://example.com:443", "example.com", "name", 443),
    ("https://example.com:65535/", "example.com", "name", 65535),
    ("https://example.com:0443/", "example.com", "name", 443),
    ("https://example.com?x=1", "example.com", "name", 443),
    ("https://example.com#frag", "example.com", "name", 443),
    ("https://8.8.8.8/", "8.8.8.8", "ipv4", 443),
    ("https://8.8.8.8:8443/", "8.8.8.8", "ipv4", 8443),
    ("https://8.8.8.8./", "8.8.8.8", "ipv4", 443),
    ("https://134744072/", "8.8.8.8", "ipv4", 443),
    ("https://0x8080808/", "8.8.8.8", "ipv4", 443),
    ("https://010.010.010.010/", "8.8.8.8", "ipv4", 443),
    ("https://8.8.2056/", "8.8.8.8", "ipv4", 443),
    ("https://[2606:4700::1111]/", "2606:4700::1111", "ipv6", 443),
    ("https://[2606:4700:0:0:0:0:0:1111]:8443/p", "2606:4700::1111", "ipv6", 8443),
    ("https://[2001:4860:4860::8888]", "2001:4860:4860::8888", "ipv6", 443),
    ("https://[2A00:1450:4001:0:0:0:0:1]/", "2a00:1450:4001::1", "ipv6", 443),
    ("https://[::ffff:8.8.8.8]/", "::ffff:808:808", "ipv6", 443),
])
def test_allowed_urls(url, host, kind, port):
    r = cls(url)
    assert r["scheme"] == "https" and r["host"] == host and r["kind"] == kind and r["port"] == port
    assert r["origin"] == (f"https://[{host}]:{port}" if kind == "ipv6" else f"https://{host}:{port}")


def test_origin_ignores_path_query_case_and_default_port():
    a = cls("https://Example.com/a?x=1")["origin"]
    assert a == cls("https://example.com:443/b#f")["origin"] == "https://example.com:443"
    assert a != cls("https://example.com:444/")["origin"] != cls("https://other.example.com/")["origin"]


# ---------------------------------------------------------------- url_classify: denied
@pytest.mark.parametrize("url", [
    "http://example.com/", "HTTP://example.com/", "ftp://example.com/", "file:///etc/passwd", "file://localhost/etc/passwd", "data:text/plain,hi",
    "javascript:alert(1)", "ws://example.com/", "wss://example.com/", "gopher://example.com/", "mailto:a@b.c", "about:blank", "blob:https://example.com/x",
    "httpss://example.com/", "https+x://example.com/", "e.x:80/a",
])
def test_non_https_schemes_are_denied(url):
    with pytest.raises(mdq.MdqError) as e:
        cls(url)
    assert e.value.code == DENIED


@pytest.mark.parametrize("url", [
    "https://user@example.com/", "https://user:pw@example.com/", "https://:pw@example.com/", "https://@example.com/", "https://good.com@evil.com/",
    "https://example.com@evil.com/", "https://a@b@example.com/", "https://user@[::1]/", "https://user@8.8.8.8/",
])
def test_credentials_are_denied(url):
    with pytest.raises(mdq.MdqError) as e:
        cls(url)
    assert e.value.code == DENIED and "credentials" in str(e.value)


@pytest.mark.parametrize("url", [
    "https://127.0.0.1/", "https://127.1/", "https://127.0.1/", "https://0x7f.1/", "https://0x7f.0x0.0x0.0x1/", "https://0177.0.0.1/", "https://0177.0.0.01/",
    "https://2130706433/", "https://0x7f000001/", "https://017700000001/", "https://127.0.0.1./", "https://0/", "https://0.0.0.0/", "https://00/",
    "https://10.0.0.1/", "https://172.16.0.1/", "https://192.168.1.1/", "https://169.254.169.254/latest/meta-data/", "https://2852039166/",
    "https://0xa9fea9fe/", "https://100.64.0.1/", "https://100.100.100.200/", "https://224.0.0.1/", "https://255.255.255.255/", "https://4294967295/",
    "https://168.63.129.16/", "https://192.0.0.192/",
    "https://[::1]/", "https://[0:0:0:0:0:0:0:1]/", "https://[::]/", "https://[::ffff:127.0.0.1]/", "https://[::ffff:7f00:1]/", "https://[::FFFF:7F00:1]:8443/",
    "https://[::ffff:10.0.0.1]/", "https://[::ffff:169.254.169.254]/", "https://[64:ff9b::7f00:1]/", "https://[2002:7f00:1::]/", "https://[fe80::1]/",
    "https://[fc00::1]/", "https://[fd00:ec2::254]/", "https://[ff02::1]/", "https://[2001:db8::1]/",
    "https://localhost/", "https://LOCALHOST/", "https://localhost./", "https://localhost:8443/", "https://app.localhost/", "https://a.b.localhost./",
    "https://printer.local/", "https://metadata.google.internal/", "https://x.internal./", "https://host.localdomain/", "https://router.home.arpa/",
    "https://nas.lan/", "https://intranet/", "https://metadata/", "https://instance-data/", "https://a/", "https://corp/", "https://wiki.corp/", "https://x.home/",
])
def test_blocked_hosts_are_denied(url):
    with pytest.raises(mdq.MdqError) as e:
        cls(url)
    assert e.value.code == DENIED


def test_userinfo_and_fragment_tricks_use_the_real_host():
    assert cls("https://evil.com#@good.com/")["host"] == "evil.com"
    assert cls("https://evil.com?@good.com/")["host"] == "evil.com"
    assert cls("https://evil.com/@good.com")["host"] == "evil.com"
    with pytest.raises(mdq.MdqError) as e:
        cls("https://127.0.0.1#@good.com/")
    assert e.value.code == DENIED
    with pytest.raises(mdq.MdqError) as e:
        cls("https://good.com@127.0.0.1/")
    assert e.value.code == DENIED


# ---------------------------------------------------------------- url_classify: malformed
@pytest.mark.parametrize("url", [
    "", " ", "example.com", "example.com/a", "//example.com/", "/a/b", "?x=1", "#f", ":", ":https://x.y", "1http://x.y", "ht tp://x.y/", "https:/example.com/",
    "https:example.com", "https:\\\\example.com", "https://example.com\\@evil.com/", "https://exa mple.com/", "https://example.com/a b", "https://example.com/\t",
    "https://example.com/\n", "https://127.0.0.1\n/", "https://example.com/é", "https://é.example.com/", "https://ex\u3002ample.com/", "https://１２７.０.０.１/",
    "https://\u00a0example.com/", "https://example.com/\u0000", "https:///a", "https://", "https://?x", "https://#f", "https://:443/", "https://./", "https://../",
    "https://a..b.example.com/", "https://.example.com/", "https://example..com/", "https://example.com../", "https://-.example.com/x", "https://exa$mple.com/",
    "https://exa mple/", "https://example.com:/", "https://example.com:0/", "https://example.com:65536/", "https://example.com:99999999999/", "https://example.com:-1/",
    "https://example.com:80a/", "https://example.com:+80/", "https://example.com:8_0/", "https://example.com:80:80/", "https://::1/", "https://[::1/", "https://[::1]x/",
    "https://[::1]:/", "https://[::1]:0/", "https://[::1]:abc/", "https://[]/", "https://[127.0.0.1]/", "https://[example.com]/", "https://[fe80::1%25eth0]/",
    "https://[fe80::1%eth0]/", "https://[:::1]/", "https://[1:2:3:4:5:6:7:8:9]/", "https://[g::1]/", "https://[::1]]/", "https://[[::1]/", "https://a[b]c.com/",
    "https://%31%32%37.0.0.1/", "https://127.0.0.%31/", "https://example%2Ecom/", "https://exa%mple.com/", "https://256.1.1.1/", "https://1.2.3.4.5/", "https://1.2.3.256/",
    "https://999999999999/", "https://4294967296/", "https://0x100000000/", "https://08.0.0.1/", "https://0xg.1/", "https://1..2/", "https://1.2.3.4../",
    "https://0x/", "https://example.com.1/", "https://" + "a" * 64 + ".com/", "https://" + ".".join(["a" * 60] * 5) + "/",
])
def test_malformed_urls(url):
    with pytest.raises(mdq.MdqError) as e:
        cls(url)
    assert e.value.code in (BAD, DENIED)
    if not url.lower().startswith(("http:", "https://") ) and url[:1].isalpha() is False:
        assert e.value.code == BAD


@pytest.mark.parametrize("url", ["", "example.com", "//example.com/", "https:/example.com/", "https://", "https:///a", "https://:443/", "https://exa mple.com/",
                                 "https://example..com/", "https://256.1.1.1/", "https://example.com:0/", "https://[1:2:3:4:5:6:7:8:9]/", "https://é.example.com/"])
def test_malformed_urls_are_source_invalid(url):
    with pytest.raises(mdq.MdqError) as e:
        cls(url)
    assert e.value.code == BAD


def test_exact_boundaries():
    assert cls("https://" + "a" * 63 + ".com/")["kind"] == "name"
    assert cls("https://example.com:65535")["port"] == 65535
    with pytest.raises(mdq.MdqError) as e:
        cls("https://" + "a" * 63 + "." + "b" * 63 + "." + "c" * 63 + "." + "d" * 63 + ".com/")
    assert e.value.code == BAD
    with pytest.raises(mdq.MdqError) as e:
        cls("https://e.x/" + "a" * 8200)
    assert e.value.code == "MDQ_RESOURCE_LIMIT"


def test_classify_argument_errors():
    for v in (None, 1, b"https://e.x/"):
        with pytest.raises(TypeError):
            cls(v)


def test_diagnostics_never_echo_the_url(assert_mdq_error):
    secret = "S3CRET-TOKEN-123"
    for url in (f"https://127.0.0.1/?k={secret}", f"http://example.com/?k={secret}", f"https://u:{secret}@example.com/", f"https://localhost/{secret}",
                f"https://example.com:99999/?k={secret}", f"https://exa mple.com/?k={secret}", f"https://[::1]/?k={secret}", f"ftp://x.y/{secret}"):
        with pytest.raises(mdq.MdqError) as e:
            cls(url)
        assert secret not in str(e.value) and secret not in repr(e.value) and "127.0.0.1" not in str(e.value)


# ---------------------------------------------------------------- properties
def _forms(n):
    a, b, c, d = n >> 24, (n >> 16) & 255, (n >> 8) & 255, n & 255
    return [
        f"{a}.{b}.{c}.{d}", str(n), f"0x{n:x}", f"0X{n:X}", f"0{n:o}", f"{a}.{b}.{(c << 8) | d}", f"{a}.{(b << 16) | (c << 8) | d}",
        f"0x{a:x}.0{b:o}.{c}.0x{d:x}", f"0{a:o}.0{b:o}.0{c:o}.0{d:o}", f"{a}.{b}.{c}.{d}.",
    ]


@given(st.integers(0, 2**32 - 1))
def test_every_legacy_ipv4_notation_resolves_like_inet_aton(n):
    expected = str(ipaddress.IPv4Address(n))
    for form in _forms(n):
        assert socket.inet_aton(form.rstrip(".")) == ipaddress.IPv4Address(n).packed
        try:
            r = cls(f"https://{form}/x")
            assert r["host"] == expected and r["kind"] == "ipv4" and not _oracle(expected)
        except mdq.MdqError as e:
            assert e.code == DENIED and _oracle(expected), (form, e)


@given(st.integers(0, 2**128 - 1))
def test_ipv6_urls_agree_with_ip_is_blocked(n):
    ip = ipaddress.IPv6Address(n)
    for text in (str(ip), ip.exploded, ip.exploded.upper()):
        try:
            r = cls(f"https://[{text}]:8443/")
            assert not blocked(str(ip)) and r["kind"] == "ipv6" and ipaddress.IPv6Address(r["host"]) == ip
        except mdq.MdqError as e:
            assert e.code == DENIED and blocked(str(ip))


@given(st.text(max_size=40))
def test_arbitrary_input_never_crashes(s):
    for u in (s, "https://" + s, "https://" + s + "/x", "https://[" + s + "]/"):
        try:
            r = cls(u)
        except mdq.MdqError as e:
            assert e.code in (BAD, DENIED, "MDQ_RESOURCE_LIMIT")
        else:
            assert r["scheme"] == "https" and r["host"] and 1 <= r["port"] <= 65535 and r["origin"].startswith("https://")
            assert r["host"] == r["host"].lower() and u.isascii()
