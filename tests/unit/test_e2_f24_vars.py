"""Epic 2 / E2.4 (F08): variable formatting, Markdown escaping, `{{ }}` interpolation (spec 5).

Hooks: `var_format_scalar(value)`, `md_escape_inline(text, at_line_start=True)`,
`interpolate(text, variables, first_line=1)`. Decisions G26/G27 (docs/TEST_PLAN.md):
names are dotted ASCII identifiers, blanks inside the braces are allowed, a malformed
`{{` is MDQ_INVALID_SYNTAX, an odd number of backslashes escapes the delimiter,
lists/mappings are MDQ_UNSUPPORTED_FEATURE, code/HTML are never expanded (block
detection covers top-level constructs; container-nested code is Epic 3).
The escaping and the code/HTML detection are checked against markdown-it-py.
"""
import re
from decimal import Decimal as D

import pytest
from hypothesis import assume, given, strategies as st

import mdq
from mdq import _internal as mi
from oracles.md_plain import analyse, live_and_literal, norm
from oracles.yaml_gen import documents

pytestmark = pytest.mark.unit

fmt = mi.var_format_scalar
esc = mi.md_escape_inline
interp = mi.interpolate


# ------------------------------------------------------------ format_scalar


@pytest.mark.parametrize("value,text", [
    (None, ""), (True, "true"), (False, "false"), ("", ""), ("a b", "a b"),
    ("*not escaped*", "*not escaped*"), ("é€😀\n", "é€😀\n"),
    (0, "0"), (-7, "-7"), (10**30, "1" + "0" * 30),
    (D("1.50"), "1.5"), (D("1E+3"), "1000"), (D("1E-3"), "0.001"), (D("-0"), "0"), (D("0.00"), "0"),
    (D("12345678901234567890.123456789012345678901234567890"), "12345678901234567890.12345678901234567890123456789"),
])
def test_scalars(value, text):
    assert fmt(value) == text


def test_bool_is_not_a_number():
    assert fmt(True) == "true" and fmt(1) == "1"


def oracle_decimal(d):
    t = format(d, "f")
    if "." in t:
        t = t.rstrip("0").rstrip(".")
    return "0" if t in ("-0", "") else t


@given(
    st.sampled_from(["", "-", "+"]), st.text("0123456789", min_size=1, max_size=40),
    st.one_of(st.just(""), st.text("0123456789", min_size=1, max_size=40)),
    st.one_of(st.just(None), st.integers(-300, 300)),
)
def test_decimal_matches_oracle(sign, ip, fp, exp):
    txt = sign + ip + ("." + fp if fp else "") + ("" if exp is None else f"E{exp}")
    d = D(txt)
    assert fmt(d) == oracle_decimal(d)


@given(st.integers(-10**40, 10**40))
def test_int_matches_str(n):
    assert fmt(n) == str(n)


def test_number_limits():
    assert fmt(D("1E+400")) == "1" + "0" * 400
    assert fmt(D("1E-400")) == "0." + "0" * 399 + "1"
    for bad in (D("1E+401"), D("1E-401"), D("1E+9999999")):
        with pytest.raises(mdq.MdqError) as e:
            fmt(bad)
        assert e.value.code == "MDQ_RESOURCE_LIMIT"
    with pytest.raises(mdq.MdqError) as e:
        fmt(10**400)
    assert e.value.code == "MDQ_RESOURCE_LIMIT"


@pytest.mark.parametrize("bad", [D("NaN"), D("Infinity"), D("-Infinity"), D("sNaN")])
def test_non_finite(bad, assert_mdq_error):
    with assert_mdq_error("MDQ_METADATA_ERROR"):
        fmt(bad)


@pytest.mark.parametrize("value", [[], [1], {}, {"a": 1}, (1,)])
def test_collections_are_rejected(value, assert_mdq_error):
    with assert_mdq_error("MDQ_UNSUPPORTED_FEATURE"):
        fmt(value)


@pytest.mark.parametrize("value", [1.5, b"x", object(), {1: 2}, 1j])
def test_unsupported_python_types(value):
    with pytest.raises(TypeError):
        fmt(value)


def test_yaml_numbers_are_already_canonical():
    doc = mi.yaml_load_strict("a: 1.50\nb: 1e3\nc: -0.0\nd: 12\ne: 0.001\n")
    for k, v in doc.items():
        assert fmt(v) == oracle_decimal(v)


# ------------------------------------------------------------ escaping


def test_escape_table():
    assert esc("") == ""
    assert esc("plain words 123") == "plain words 123"
    assert esc("a_b*c") == r"a\_b\*c"
    assert esc("`x`") == r"\`x\`"
    assert esc("[a](b)") == r"\[a\](b)"
    assert esc("<b>") == r"\<b>"
    assert esc("a|b") == r"a\|b"
    assert esc("{{x}}") == r"\{\{x}}"
    assert esc("$1 & 2") == r"\$1 \& 2"
    assert esc("a@b") == r"a\@b"
    assert esc("~~x~~") == r"\~\~x\~\~"
    assert esc("\\") == "\\\\"


def test_escape_line_start_rules():
    for s, out in [("# h", r"\# h"), ("- a", r"\- a"), ("+ a", r"\+ a"), ("> q", r"\> q"), ("1. a", r"1\. a"),
                   ("2) a", r"2\) a"), ("=", r"\="), (":::x", r"\:::x"), (" x", "&#32;x"), ("\tx", "&#9;x")]:
        assert esc(s) == out
        assert esc("w\n" + s) == "w\n" + out
    # mid-line the same characters are plain
    assert esc("a # b - c + d > e 1. f : g") == "a # b - c + d > e 1. f : g"
    assert esc("a 3.5 and v1.2") == "a 3.5 and v1.2" and esc("3.5") == r"3\.5"
    assert esc("a=b") == "a=b" and esc("a==b") == r"a\=\=b"
    assert esc("https://x.y") == r"https\://x.y"
    assert esc("see www.x.com") == r"see www\.x.com"


def test_at_line_start_false():
    assert esc("- a", False) == "- a"
    assert esc("1. a", False) == "1. a"
    assert esc(" x", False) == " x"
    assert esc("a\n- b", False) == "a\n\\- b"
    assert esc("a ", False) == "a&#32;"


def test_whitespace_protection():
    assert esc("a  \nb") == "a &#32;\nb"
    assert esc("a\n\n    code") == "a\n\n&#32;   code"
    assert esc("a\\\nb") == "a\\\\\nb"


def test_nul_rejected(assert_mdq_error):
    with assert_mdq_error("MDQ_INVALID_SYNTAX"):
        esc("a\0b")


def test_escape_types():
    with pytest.raises(TypeError):
        esc(1)
    with pytest.raises(TypeError):
        esc("a", 1)


NASTY = [
    "# h", "## h ##", "- a", "* a", "+ a", "1. a", "10) a", "> q", ">> q", "```", "~~~", "---", "***", "___", "===",
    "a\n---", "a\n===", "| a | b |\n|---|---|\n| c | d |", "[x]: http://e.com", "[x](y)", "![i](u)", "<div>", "<!-- c -->",
    "<http://x.y>", "<a@b.co>", "&amp; &#35; &copy;", "\\*", "\\\\", "**b** _i_ ~~s~~", "`c` ``d``", "$$x$$", "$a$",
    "==hl==", "[^1]", "[^1]: n", "[[toc]]", "[@key, p. 12]", ":::details {title=\"x\"}", "::: columns", "{{x}}",
    "{{ x.y }}", "http://e.com", "www.e.com", "WWW.E.COM", "a@b.com", "ftp://x", "    code", "\tcode", "  - a",
    " # h", "a  \nb", "a\\\nb", "\n\n# h", "a\n\n\n\nb", "1.", "1)", "-", "+", "*", "#", ">", "=", "- - a", "> > > q",
    "* * *", "- - -", "a\r\nb", "a\rb", "é€😀 *x*", "x" * 30 + "_" * 5, "&", "&#32;", "&nbsp;", "\u00a0 x", "\u2028 x",
]


def block_skeleton_ok(src, expected_text, linkify=False):
    skel, texts = analyse(src, linkify)
    assert all(
        s in ("paragraph_open", "paragraph_close", "inline[]") for s in skel
    ), f"structure injected: {skel} from {src!r}"
    assert norm("\n\n".join(texts)) == norm(expected_text), (src, texts)


@pytest.mark.parametrize("s", NASTY)
def test_escape_renders_literally(s):
    block_skeleton_ok(esc(s), s)
    block_skeleton_ok(pre(s) + esc(s, False) + post(s), pre(s) + s + post(s))


@pytest.mark.parametrize("s", NASTY)
def test_escape_renders_literally_with_autolinks(s):
    pytest.importorskip("linkify_it")
    block_skeleton_ok(esc(s), s, linkify=True)
    block_skeleton_ok(pre(s) + esc(s, False) + post(s), pre(s) + s + post(s), linkify=True)


def pre(s):
    """Text before a mid-line value; no trailing blank when the value starts a new line."""
    return "x" if s[:1] in ("\n", "\r") else "x "


def post(s):
    return "y" if s[-1:] in ("\n", "\r") else " y"


ALPHABET = list("\\`*_~[]<>|{}$&@#+-=:./()!;") + list("ab19wW ") + ["\t", "\n", "\r", "é", "😀", "\u00a0", "http", "www", "1."]
values = st.lists(st.sampled_from(ALPHABET), max_size=24).map("".join)


@given(values)
def test_escape_differential(s):
    assume("\0" not in s)
    block_skeleton_ok(esc(s), s)
    block_skeleton_ok(pre(s) + esc(s, False) + post(s), pre(s) + s + post(s))


@given(values)
def test_escape_differential_with_autolinks(s):
    pytest.importorskip("linkify_it")
    block_skeleton_ok(esc(s), s, linkify=True)
    block_skeleton_ok(pre(s) + esc(s, False) + post(s), pre(s) + s + post(s), linkify=True)


PREFIXES = ["", "> ", ">> ", "- ", "* ", "+ ", "1. ", "12) ", "# ", "### ", "- > ", "> - ", "1. - "]


@given(st.sampled_from(PREFIXES), values)
def test_escape_in_block_contexts(prefix, s):
    s = s.replace("\r", "\n")
    assume(s and "\n\n" not in s and not s.startswith("\n") and not s.endswith("\n") and "\0" not in s)
    if prefix.startswith("#"):
        assume("\n" not in s)
    base_skel, _ = analyse(prefix + "q")
    skel, texts = analyse(prefix + esc(s))
    assert skel == base_skel, (prefix, s, skel)
    assert norm("".join(texts)) == norm(s) or "".join(texts) == s, (prefix, s, texts)


# ------------------------------------------------------------ interpolate


V = {"a": {"b": "x-y"}, "s": "text", "t": True, "f": False, "n": None, "i": 42, "d": D("1.50"), "l": [1], "m": {"k": 1}, "e": ""}


def run(text, **kw):
    return interp(text, V, **kw)


def test_basic():
    assert run("") == ""
    assert run("no directives\n") == "no directives\n"
    assert run("{{s}}") == "text"
    assert run("a {{ s }} b {{\ts\t}}") == "a text b text"
    assert run("{{a.b}}") == "x-y"
    assert run("{{t}} {{f}} [{{n}}] {{i}} {{d}} [{{e}}]") == "true false [] 42 1.5 []"
    assert run("{{s}}{{s}}") == "texttext"


def test_dotted_and_underscore_names():
    v = {"_x": {"y_1": "ok"}, "A": {"B": {"C": "deep"}}}
    assert interp("{{_x.y_1}} {{A.B.C}}", v) == "ok deep"


def test_values_are_escaped():
    assert interp("{{x}}", {"x": "a*b"}) == r"a\*b"
    assert interp("w {{x}}", {"x": "- a"}) == "w - a"
    assert interp("- {{x}}", {"x": "- a"}) == r"- \- a"
    assert interp("{{x}}", {"x": "1. a"}) == r"1\. a"
    assert interp("3 {{x}} 4", {"x": "3.5"}) == "3 3.5 4"
    assert interp("{{x}}", {"x": "{{y}}"}) == r"\{\{y}}"
    # an empty value earlier on the line keeps the line-start rules for the next one
    assert interp("{{e}}{{x}}", {"e": "", "x": "- a"}) == r"\- a"
    assert interp("{{e}} {{x}}", {"e": "", "x": "- a"}) == r" \- a"


def test_no_second_expansion():
    assert interp("{{x}} {{y}}", {"x": "{{y}}", "y": "Y"}) == r"\{\{y}} Y"


@pytest.mark.parametrize("text,out", [
    (r"\{{s}}", r"\{{s}}"), (r"a \{{s}} b", r"a \{{s}} b"), (r"\\{{s}}", r"\\text"), (r"\\\{{s}}", r"\\\{{s}}"),
    (r"\\\\{{s}}", r"\\\\text"), (r"\{{{s}}", r"\{{{s}}"), (r"\{ {{s}}", r"\{ text"), (r"\a{{s}}", r"\atext"),
    ("\\", "\\"), ("a\\", "a\\"),
])
def test_backslash_escapes(text, out):
    assert run(text) == out


@pytest.mark.parametrize("bad", [
    "{{", "{{}}", "{{ }}", "{{a", "{{a}", "{{a }", "{{a b}}", "{{1a}}", "{{a.}}", "{{.a}}", "{{a..b}}", "{{é}}",
    "{{a-b}}", "{{a\n}}", "{{\na}}", "{{{s}}}", "{{s}}}}{{", "{{ s . t }}", "{{a.1}}", "{{s|f}}", "{{ s + 1 }}", "{{#if s}}",
])
def test_malformed(bad, assert_mdq_error):
    with assert_mdq_error("MDQ_INVALID_SYNTAX", line=1, column=bad.index("{{") + 1 if not bad.startswith("{{s}}}}") else None):
        run(bad)


def test_malformed_position(assert_mdq_error):
    with assert_mdq_error("MDQ_INVALID_SYNTAX", line=3, column=4):
        run("ok\n\nab {{a b}}\n")
    with assert_mdq_error("MDQ_INVALID_SYNTAX", line=12, column=1):
        run("{{s}}\n{{", first_line=11)


@pytest.mark.parametrize("name", ["zzz", "a.zzz", "s.x", "a.b.c", "S", "i.x", "n.x", "A.b"])
def test_undefined(name, assert_mdq_error):
    with assert_mdq_error("MDQ_UNDEFINED_VARIABLE", line=2, column=3) as info:
        run("ab\n  {{" + name + "}}")
    assert name in str(info.value)


def test_undefined_is_not_raised_in_code_or_escapes():
    assert run("`{{zzz}}` \\{{zzz}}\n\n```\n{{zzz}}\n```\n") == "`{{zzz}}` \\{{zzz}}\n\n```\n{{zzz}}\n```\n"


def test_defined_but_none_is_not_undefined():
    assert run("[{{n}}]") == "[]"


@pytest.mark.parametrize("name", ["l", "m"])
def test_collections_cannot_interpolate(name, assert_mdq_error):
    with assert_mdq_error("MDQ_UNSUPPORTED_FEATURE", line=1, column=4):
        run("ab {{" + name + "}}")


def test_positions_in_chars_crlf_and_first_line(assert_mdq_error):
    with assert_mdq_error("MDQ_UNDEFINED_VARIABLE", line=3, column=3):
        run("é€\r\n😀\r\n😀😀{{zz}}")
    with assert_mdq_error("MDQ_UNDEFINED_VARIABLE", line=7, column=1):
        run("a\rb\n{{zz}}", first_line=5)
    with assert_mdq_error("MDQ_UNDEFINED_VARIABLE", line=105, column=1):
        run("a\n\n```\nx\n```\n{{zz}}", first_line=100)


def test_variables_argument_types():
    with pytest.raises(TypeError):
        interp("a", [])
    with pytest.raises(TypeError):
        interp("a", {"x": 1.5})
    with pytest.raises(TypeError):
        interp(1, {})
    with pytest.raises(TypeError):
        interp("a", {1: 2})
    with pytest.raises(TypeError):
        interp("a", {}, first_line="1")


def test_input_limit(assert_mdq_error):
    with assert_mdq_error("MDQ_RESOURCE_LIMIT"):
        interp("a" * (16 * 1024 * 1024 + 1), {})


def test_yaml_values_flow_through():
    v = mi.yaml_load_strict("project:\n  name: 'A*B'\n  price: 2.50\n  tags: [a]\nflag: true\nnone: null\n")
    assert interp("{{project.name}} {{project.price}} {{flag}} [{{none}}]", v) == r"A\*B 2.5 true []"
    with pytest.raises(mdq.MdqError):
        interp("{{project.tags}}", v)


# ---- code and HTML are never expanded

CODE_CASES = [
    ("```\n{{s}}\n```\n", None),
    ("~~~ py\n{{s}}\n~~~\n", None),
    ("````\n```\n{{s}}\n```\n````\n", None),
    ("   ```\n   {{s}}\n   ```\n", None),
    ("```\n{{s}}\n", None),  # unterminated fence runs to the end
    ("```\n{{s}}\n~~~\n{{s}}\n```\n", None),
    ("```text {{s}}\n{{s}}\n```\n", None),
    ("```\n{{s}}\n``` x\n{{s}}\n```\n", None),
    ("para\n\n    {{s}}\n    {{s}}\n", None),
    ("para\n\n\t{{s}}\n", None),
    ("`{{s}}`", None), ("``{{s}}``", None), ("`` `{{s}}` ``", None), ("a `{{s}}\nb {{s}}` c", None),
    ("<div>\n{{s}}\n</div>\n", None), ("<div>{{s}}</div>\n", None), ("<!-- {{s}}\n{{s}} -->\n", None),
    ("<pre>\n\n{{s}}\n\n</pre>\n", None), ("<?php {{s}} ?>\n", None), ("<!DOCTYPE {{s}}>\n", None),
    ("<![CDATA[\n{{s}}\n]]>\n", None), ("<script>\n{{s}}\n</script>\n", None), ("<style>{{s}}</style>\n", None),
    ("<custom-tag attr=\"{{s}}\">\n", None),
    ("a <span title=\"{{s}}\">b</span>", None), ("a <!-- {{s}} --> b", None), ("a <http://e.com/{{s}}> b", None),
    ("a <b x='{{s}}'/> b", None), ("a <?x {{s}} ?> b", None),
]


@pytest.mark.parametrize("src,_", CODE_CASES)
def test_code_and_html_stay_literal(src, _):
    assert run(src) == src.replace("{{s}}", "{{s}}")  # nothing at all is expanded


def test_expansion_resumes_after_code():
    assert run("`{{s}}` {{s}}") == "`{{s}}` text"
    assert run("```\n{{s}}\n```\n{{s}}\n") == "```\n{{s}}\n```\ntext\n"
    assert run("<div>\n{{s}}\n</div>\n\n{{s}}\n") == "<div>\n{{s}}\n</div>\n\ntext\n"
    assert run("<!-- c -->\n{{s}}\n") == "<!-- c -->\ntext\n"  # type 2 ends on its line
    assert run("```\nx\n```\n\n    {{s}}\n\n{{s}}") == "```\nx\n```\n\n    {{s}}\n\ntext"


def test_unmatched_backticks_are_text():
    assert run("`{{s}}") == "`text" and run("``{{s}}`") == "``text`" and run("{{s}}``") == "text``"
    assert run("`a` {{s}} `b`") == "`a` text `b`"


def test_indented_continuation_is_text():
    assert run("para\n    {{s}}\n") == "para\n    text\n"


def test_headings_and_lists_and_quotes_expand():
    assert run("# H {{s}}\n") == "# H text\n"
    assert run("> q {{s}}\n- l {{s}}\n1. n {{s}}\n") == "> q text\n- l text\n1. n text\n"
    assert run("Title {{s}}\n===\n") == "Title text\n===\n"
    assert run("| {{s}} |\n|---|\n| {{s}} |\n") == "| text |\n|---|\n| text |\n"


def test_line_ends_are_preserved():
    assert run("a\r\n{{s}}\r\n\r\n```\r\n{{s}}\r\n```\r\n") == "a\r\ntext\r\n\r\n```\r\n{{s}}\r\n```\r\n"
    assert run("{{s}}\r{{s}}") == "text\rtext"


# ---- differential against markdown-it-py

X = {"x": "MARK"}

PARA = ["text {{x}} more", "a `{{x}}` b", "``{{x}}`` c", "lit \\{{y}} d", "<span a=\"{{x}}\">{{x}}</span> t", "<http://e.com/{{x}}> u",
        "  {{x}}", "{{x}} {{x}}", "a <!-- {{x}} --> {{x}}", "**{{x}}** and *{{x}}*", "[l {{x}}](http://e.com)", "x `a\nb {{x}}` y {{x}}"]
FENCE = ["```\n{{x}}\n```", "~~~py\n{{x}}\n~~~~", "````\n```\n{{x}}\n````", "```\n{{x}}", "  ```\n  {{x}}\n  ```"]
INDENT = ["    {{x}}", "\t{{x}}\n    {{x}}"]
HTML = ["<div>\n{{x}}\n</div>", "<!-- {{x}} -->", "<pre>\n\n{{x}}\n</pre>", "<p>{{x}}</p>", "<span>{{x}}</span>", "<span>\n{{x}}\n</span>",
        "<?x {{x}} ?>", "<script>\n{{x}}\n</script>", "<custom a=\"{{x}}\">", "</div>\n{{x}}"]
HEAD = ["# H {{x}}", "## {{x}} ##", "T {{x}}\n===", "T {{x}}\n---", "---", "***"]
block = st.one_of(*[st.sampled_from(g) for g in (PARA, PARA, FENCE, INDENT, HTML, HEAD)])


def check_against_markdown_it(doc):
    live, literal = live_and_literal(doc)
    out = interp(doc, X)
    assert out.count("MARK") == live, (doc, out, live)
    assert out.count("{{x}}") == literal, (doc, out, literal)


@given(st.lists(st.tuples(block, st.sampled_from(["\n\n", "\n", "\n\n\n"])), min_size=1, max_size=6))
def test_differential_block_documents(parts):
    doc = "".join(b + sep for b, sep in parts)
    check_against_markdown_it(doc)


LINES = ["{{x}}", "text", "", "", "    {{x}}", "  {{x}}", "```", "~~~", "````", "<div>", "</div>", "<!--", "-->", "<pre>",
         "</pre>", "# h {{x}}", "===", "---", "***", "`", "``", "<b>", "<b x='{{x}}'>", "\\{{y}}", "<?", "?>", "<![CDATA[", "]]>",
         "<!X", "a {{x}} `b {{x}}", "`code", "tick` {{x}}", "<span>", "<custom>", "<p>", "</p>", "\t{{x}}", "text {{x}}"]


@given(st.lists(st.sampled_from(LINES), min_size=1, max_size=12))
def test_differential_line_fuzz(lines):
    check_against_markdown_it("\n".join(lines) + "\n")


@given(st.lists(st.sampled_from(LINES), min_size=1, max_size=12))
def test_documents_without_directives_are_unchanged(lines):
    doc = "\n".join(l for l in lines if "{{" not in l) + "\n"
    assert interp(doc, {}) == doc


@given(st.sampled_from(["", "> ", ">> ", "- ", "1. ", "# ", "{{e}}", "{{e}}> ", "  "]), values)
def test_interpolated_value_renders_literally(prefix, s):
    s = s.replace("\r", "\n")
    assume(s and "\n\n" not in s and not s.startswith("\n") and not s.endswith("\n") and "\0" not in s)
    if prefix.startswith("#"):
        assume("\n" not in s)
    src = prefix + "{{x}}"
    base_skel, _ = analyse(interp(prefix + "{{q}}", {"q": "q", "e": ""}))
    skel, texts = analyse(interp(src, {"x": s, "e": ""}))
    assert skel == base_skel, (prefix, s, skel)
    assert norm("".join(texts)) == norm(s) or "".join(texts) == s, (prefix, s, texts)
    skel, texts = analyse(interp("w " + src, {"x": s, "e": ""}))
    assert skel == analyse("w q")[0] or prefix, (prefix, s, skel)


@given(values)
def test_interpolated_value_in_paragraph(s):
    assume("\0" not in s)
    assume("\n\n" not in s.replace("\r", "\n") and s[:1] not in "\n\r" and s[-1:] not in "\n\r" or not s)
    out = interp("pre {{x}} post", {"x": s})
    skel, texts = analyse(out)
    assert skel == ["paragraph_open", "inline[]", "paragraph_close"], (s, out, skel)
    assert norm(texts[0]) == norm("pre " + s + " post"), (s, out, texts)
