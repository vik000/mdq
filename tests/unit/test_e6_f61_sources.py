"""Epic 6 / E6.1 (F16): source declarations and URL templates (sources-and-permissions.md section 2). G42.

`source_validate(text, first_line=1)` takes the YAML of the effective metadata and returns one dict per
declared variable (`name, type, unit, source, url`); `url_template_expand(template, arguments)` fills
`{name}` placeholders with URL-component encoding. Both are offline: no I/O, no host or IP checks (E6.3/E6.4).
"""
import urllib.parse
from decimal import Decimal

import pytest
from hypothesis import given, strategies as st

import mdq
from mdq import _internal as mi

pytestmark = pytest.mark.unit

sv = mi.source_validate
ex = mi.url_template_expand
BAD = "MDQ_SOURCE_INVALID"

PROJECT = """sources:
  prices:
    kind: http
    url: https://example.invalid/prices/{symbol}
    method: GET
    format: json
    select: /price
variables:
  apple_price:
    type: number
    unit: USD
    source: prices
    arguments: {symbol: AAPL}
"""


def one(text):
    out = sv(text)
    assert len(out) == 1
    return out[0]


# ---------------------------------------------------------------- declarations
def test_named_source_with_arguments():
    v = one(PROJECT)
    assert v == {
        "name": "apple_price", "type": "number", "unit": "USD",
        "url": "https://example.invalid/prices/AAPL",
        "source": {"kind": "http", "reader": None, "url": "https://example.invalid/prices/{symbol}", "method": "GET",
                   "format": "json", "select": "/price", "arguments": {"symbol": "AAPL"}, "named": "prices"},
    }


def test_inline_source_defaults():
    v = one("variables:\n  p:\n    type: number\n    source:\n      kind: http\n      url: https://example.invalid/stocks/AAPL\n      format: json\n      select: /price\n")
    assert v["source"]["method"] == "GET" and v["source"]["named"] is None and v["url"] == "https://example.invalid/stocks/AAPL"
    assert v["unit"] is None and v["source"]["arguments"] == {}


def test_text_and_whole_json_sources_need_no_select():
    for fmt in ("json", "text", "csv"):
        v = one(f"variables:\n  p:\n    type: string\n    source: {{kind: http, url: 'https://e.x/a', format: {fmt}}}\n")
        assert v["source"]["select"] is None


def test_source_arguments_are_defaults_and_variable_arguments_override():
    t = ("sources:\n  s:\n    kind: http\n    url: https://e.x/{a}/{b}\n    format: text\n    arguments: {a: one, b: two}\n"
         "variables:\n  x:\n    type: string\n    source: s\n    arguments: {b: three}\n  y:\n    type: string\n    source: s\n")
    out = sv(t)
    assert [v["url"] for v in out] == ["https://e.x/one/three", "https://e.x/one/two"]
    assert out[0]["source"]["arguments"] == {"a": "one", "b": "three"}


def test_two_variables_share_a_named_source():
    t = PROJECT + "  google:\n    type: number\n    source: prices\n    arguments: {symbol: GOOG}\n"
    assert [v["url"] for v in sv(t)] == ["https://example.invalid/prices/AAPL", "https://example.invalid/prices/GOOG"]


def test_variable_without_source_is_a_host_supplied_declaration():
    v = one("variables:\n  fx:\n    type: number\n    unit: EUR\n")
    assert v["source"] is None and v["url"] is None and v["unit"] == "EUR"


def test_plain_values_and_namespaces_are_not_declarations():
    t = "variables:\n  name: Ann\n  n: 3\n  group:\n    inner: x\n    price:\n      type: number\n      source: {kind: 'reader:db', arguments: {k: 1}}\n"
    out = sv(t)
    assert [v["name"] for v in out] == ["group.price"]


def test_reader_kind_is_accepted_without_an_adapter():
    v = one("variables:\n  p:\n    type: number\n    source:\n      kind: reader:my-db.v2\n      arguments: {key: AAPL, n: 3, f: true}\n")
    s = v["source"]
    assert s["kind"] == "reader:my-db.v2" and s["reader"] == "my-db.v2" and s["url"] is None and v["url"] is None
    assert s["arguments"] == {"key": "AAPL", "n": "3", "f": "true"}


def test_unused_named_sources_are_validated_too():
    with pytest.raises(mdq.MdqError):
        sv("sources:\n  a:\n    kind: ftp\n")
    assert sv("sources:\n  a: {kind: http, url: 'https://e.x/{z}', format: text}\n") == []


def test_other_keys_are_ignored():
    assert sv("title: x\nrequirements:\n  network: optional\n") == []
    assert sv("") == []


def test_declaration_order_is_document_order():
    t = "variables:\n  b: {type: string}\n  a: {type: string}\n  c: {type: string}\n"
    assert [v["name"] for v in sv(t)] == ["b", "a", "c"]


def test_first_line_is_used_for_yaml_errors(assert_mdq_error):
    with assert_mdq_error("MDQ_METADATA_ERROR", line=12):
        sv("a: 1\na: 2\n", first_line=11)


@pytest.mark.parametrize("bad", [
    "sources: 3\n", "sources: [a]\n", "variables: [a]\n", "sources:\n  a: 3\n", "sources:\n  a: {}\n",
    "sources:\n  a: {kind: 3}\n", "sources:\n  a: {kind: ftp}\n", "sources:\n  a: {kind: HTTP, url: 'https://e.x', format: text}\n",
    "sources:\n  a: {kind: 'reader:'}\n", "sources:\n  a: {kind: 'reader:1x'}\n", "sources:\n  a: {kind: 'reader:a b'}\n",
    "sources:\n  a: {kind: 'reader:x', url: 'https://e.x'}\n", "sources:\n  a: {kind: 'reader:x', format: json}\n",
    "sources:\n  a: {kind: http, format: json}\n", "sources:\n  a: {kind: http, url: 3, format: json}\n",
    "sources:\n  a: {kind: http, url: 'https://e.x'}\n", "sources:\n  a: {kind: http, url: 'https://e.x', format: xml}\n",
    "sources:\n  a: {kind: http, url: 'https://e.x', format: json, method: POST}\n",
    "sources:\n  a: {kind: http, url: 'https://e.x', format: json, method: get}\n",
    "sources:\n  a: {kind: http, url: 'https://e.x', format: text, select: /a}\n",
    "sources:\n  a: {kind: http, url: 'https://e.x', format: csv, select: /a}\n",
    "sources:\n  a: {kind: http, url: 'https://e.x', format: json, select: price}\n",
    "sources:\n  a: {kind: http, url: 'https://e.x', format: json, select: '/a~2'}\n",
    "sources:\n  a: {kind: http, url: 'https://e.x', format: json, select: 3}\n",
    "sources:\n  a: {kind: http, url: 'https://e.x', format: json, script: x}\n",
    "sources:\n  a: {kind: http, url: 'https://e.x', format: json, arguments: [1]}\n",
    "sources:\n  a: {kind: http, url: 'https://e.x', format: json, arguments: {'a b': 1}}\n",
    "sources:\n  a: {kind: http, url: 'https://e.x', format: json, arguments: {a: [1]}}\n",
    "sources:\n  a: {kind: http, url: 'https://e.x', format: json, arguments: {a: null}}\n",
    "sources:\n  a: {kind: http, url: 'https://e.x', format: json, arguments: {a: ''}}\n",
])
def test_invalid_declarations(bad, assert_mdq_error):
    with assert_mdq_error(BAD):
        sv(bad)


@pytest.mark.parametrize("key", ["headers", "Authorization", "token", "password", "api_key", "secret", "username", "cookie"])
def test_credential_keys_are_rejected_with_a_clear_message(key, assert_mdq_error):
    with assert_mdq_error(BAD) as info:
        sv(f"sources:\n  a: {{kind: http, url: 'https://e.x', format: json, {key}: x}}\n")
    assert "credentials" in str(info.value)
    with assert_mdq_error(BAD) as info:
        sv(f"variables:\n  v: {{type: string, {key}: x}}\n")
    assert "credentials" in str(info.value)


@pytest.mark.parametrize("bad", [
    "variables:\n  v: {source: {kind: http, url: 'https://e.x', format: text}}\n",
    "variables:\n  v: {type: money}\n", "variables:\n  v: {type: 3}\n",
    "variables:\n  v: {type: string, unit: USD}\n", "variables:\n  v: {type: boolean, unit: USD}\n",
    "variables:\n  v: {type: number, unit: ''}\n", "variables:\n  v: {type: number, unit: 3}\n",
    "variables:\n  v: {type: string, source: nope}\n", "variables:\n  v: {type: string, source: 3}\n",
    "variables:\n  v: {type: string, arguments: {a: 1}}\n", "variables:\n  v: {type: string, code: x}\n",
    "variables:\n  v: {type: string, source: {kind: ftp}}\n",
    "sources:\n  s: {kind: http, url: 'https://e.x/{a}', format: text}\nvariables:\n  v: {type: string, source: s}\n",
    "sources:\n  s: {kind: http, url: 'https://e.x/', format: text}\nvariables:\n  v: {type: string, source: s, arguments: {a: 1}}\n",
])
def test_invalid_variables(bad, assert_mdq_error):
    with assert_mdq_error(BAD):
        sv(bad)


def test_the_message_names_the_location(assert_mdq_error):
    with assert_mdq_error(BAD) as info:
        sv("variables:\n  apple:\n    type: number\n    source: {kind: http, url: 'http://e.x', format: json}\n")
    assert "variables.apple.source.url" in str(info.value)
    with assert_mdq_error(BAD) as info:
        sv("sources:\n  prices: {kind: x}\n")
    assert "sources.prices.kind" in str(info.value)


def test_source_validate_argument_errors(assert_mdq_error):
    with pytest.raises(TypeError):
        sv(1)
    with pytest.raises(TypeError):
        sv("a: 1", "1")
    with assert_mdq_error("MDQ_RESOURCE_LIMIT"):
        sv("a: " + "x" * (2 * 1024 * 1024))


# ---------------------------------------------------------------- URL templates
@pytest.mark.parametrize("value,encoded", [
    ("AAPL", "AAPL"), ("a b", "a%20b"), ("a/b", "a%2Fb"), ("a?b", "a%3Fb"), ("a#b", "a%23b"), ("100%", "100%25"),
    ("é", "%C3%A9"), ("日本", "%E6%97%A5%E6%9C%AC"), ("😀", "%F0%9F%98%80"), ("a&b=c", "a%26b%3Dc"), ("a+b", "a%2Bb"),
    ("a:b@c", "a%3Ab%40c"), ("a-b_c.d~e", "a-b_c.d~e"), ("{x}", "%7Bx%7D"), ("..", "%2E%2E"), (".", "%2E"),
    ("...", "..."), ("a..b", "a..b"), ("../x", "..%2Fx"), ("%2e%2e", "%252e%252e"), ("a\nb", "a%0Ab"), ("\\", "%5C"),
])
def test_argument_encoding(value, encoded):
    assert ex("https://e.x/{v}", {"v": value}) == "https://e.x/" + encoded
    assert ex("https://e.x/?q={v}", {"v": value}) == "https://e.x/?q=" + encoded


def test_numbers_and_booleans_are_rendered_as_text():
    assert ex("https://e.x/{a}/{b}", {"a": 12, "b": -3}) == "https://e.x/12/-3"
    assert sv("variables:\n  v: {type: string, source: {kind: http, url: 'https://e.x/{a}/{b}/{c}', format: text, arguments: {a: 1.50, b: true, c: 1e3}}}\n")[0]["url"] == "https://e.x/1.5/true/1000"


def test_repeated_placeholders_and_adjacent_placeholders():
    assert ex("https://e.x/{a}/{a}-{b}{a}", {"a": "1", "b": "2"}) == "https://e.x/1/1-21"


def test_template_without_placeholders():
    assert ex("https://e.x/a/b?c=d", {}) == "https://e.x/a/b?c=d"
    assert ex("HTTPS://e.x/a%20b", {}) == "HTTPS://e.x/a%20b"
    assert ex("https://e.x:8443/{a}", {"a": "x"}) == "https://e.x:8443/x"
    assert ex("https://[::1]:8443/{a}", {"a": "x"}) == "https://[::1]:8443/x"


def test_a_value_cannot_change_the_url_structure():
    out = ex("https://e.x/{a}/z?k={b}", {"a": "../..", "b": "1&evil=2#frag"})
    p = urllib.parse.urlsplit(out)
    assert p.netloc == "e.x" and p.path == "/..%2F../z" and p.fragment == "" and urllib.parse.parse_qs(p.query) == {"k": ["1&evil=2#frag"]}


@pytest.mark.parametrize("tpl,args", [
    ("https://e.x/{a}", {}), ("https://e.x/", {"a": "1"}), ("https://e.x/{a}", {"a": "1", "b": "2"}), ("https://e.x/{a}/{b}", {"a": "1"}),
    ("https://e.x/{a}", {"a": ""}), ("https://e.x/{}", {}), ("https://e.x/{a b}", {"a b": "1"}), ("https://e.x/{1a}", {"1a": "1"}),
    ("https://e.x/{a", {"a": "1"}), ("https://e.x/a}", {}), ("https://e.x/{{a}}", {"a": "1"}), ("https://e.x/{a{b}}", {"a": "1"}),
    ("https://e.x/${HOME}", {"HOME": "1"}), ("https://e.x/{é}", {"é": "1"}),
    ("https://{h}/x", {"h": "e.x"}), ("https://e.{h}/x", {"h": "x"}), ("{s}://e.x/", {"s": "https"}), ("https://e.x{p}", {"p": "/a"}),
    ("https://{h}", {"h": "e.x"}), ("https://e.x:{p}/", {"p": "80"}),
    ("http://e.x/", {}), ("ftp://e.x/", {}), ("file:///etc/passwd", {}), ("data:text/plain,hi", {}), ("//e.x/", {}), ("e.x/a", {}), ("", {}),
    ("https:/e.x/", {}), ("https:e.x", {}), ("javascript:alert(1)", {}),
    ("https://user@e.x/", {}), ("https://user:pw@e.x/", {}), ("https://e.x@evil/", {}), ("https://e.x/a@b", None),
    ("https:///a", {}), ("https://:80/a", {}), ("https://?a", {}), ("https://e.x:99999/", {}), ("https://e.x:ab/", {}), ("https://[::1/", {}),
    ("https://e.x/#f", {}), ("https://e.x/a#{f}", {"f": "1"}),
    ("https://e.x/a b", {}), ("https://e.x/a\tb", {}), ("https://e.x/a\nb", {}), ("https://e.x/é", {}), ("https://é.x/", {}),
    ("https://e.x/\\a", {}), ("https://e.x/<a>", {}), ("https://e.x/a|b", {}), ("https://e.x/a\x00b", {}),
    ("https://e.x/%", {}), ("https://e.x/%zz", {}), ("https://e.x/%4", {}),
])
def test_invalid_templates(tpl, args, assert_mdq_error):
    if args is None:  # a literal "@" after the authority is legal
        assert ex(tpl, {}) == tpl
        return
    with assert_mdq_error(BAD):
        ex(tpl, args)


def test_length_limits(assert_mdq_error):
    ok = "https://e.x/" + "a" * (8192 - 12)
    assert ex(ok, {}) == ok
    with assert_mdq_error("MDQ_RESOURCE_LIMIT"):
        ex(ok + "a", {})
    with assert_mdq_error("MDQ_RESOURCE_LIMIT"):
        ex("https://e.x/{a}", {"a": "é" * 3000})


def test_argument_type_errors():
    for bad in ({"a": 1.5}, {"a": None}, {"a": [1]}, {"a": True}, {"a": b"x"}, {1: "x"}, {"a": Decimal("1")}):
        with pytest.raises(TypeError):
            ex("https://e.x/{a}", bad)
    with pytest.raises(TypeError):
        ex(1, {})
    with pytest.raises(TypeError):
        ex("https://e.x/", [])
    with pytest.raises(TypeError):
        ex("https://e.x/", None)


# ---------------------------------------------------------------- properties
_val = st.text(st.characters(blacklist_categories=("Cs",)), min_size=1, max_size=40).filter(lambda s: set(s) != {"."})


@given(_val)
def test_single_argument_matches_urllib_quote(v):
    assert ex("https://e.x/p/{v}", {"v": v}) == "https://e.x/p/" + urllib.parse.quote(v, safe="")


@given(_val, _val)
def test_structure_is_preserved_and_values_round_trip(a, b):
    out = ex("https://h.x:81/p/{a}/q?k={b}", {"a": a, "b": b})
    assert out.isascii() and " " not in out and "#" not in out
    p = urllib.parse.urlsplit(out)
    assert (p.scheme, p.netloc, p.fragment) == ("https", "h.x:81", "")
    segs = p.path.split("/")
    assert len(segs) == 4 and segs[1] == "p" and segs[3] == "q"
    assert urllib.parse.unquote(segs[2]) == a
    assert urllib.parse.parse_qsl(p.query, keep_blank_values=True, strict_parsing=True) == [("k", b)]
    assert ex("https://h.x:81/p/{a}/q?k={b}", {"b": b, "a": a}) == out


@given(st.text(max_size=60))
def test_arbitrary_templates_never_crash_and_valid_ones_stay_valid(t):
    try:
        out = ex(t, {})
    except mdq.MdqError as e:
        assert e.code in (BAD, "MDQ_RESOURCE_LIMIT")
        return
    assert out == t and t[:8].lower() == "https://" and t.isascii()
    assert urllib.parse.urlsplit(t).hostname
