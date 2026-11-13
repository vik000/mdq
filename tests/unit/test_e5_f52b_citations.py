"""Epic 5 / E5.2b (F10): citations (spec section 7). G39.

- `bib_parse(text, format)` with format `bibtex` or `csl-json` returns one dict per entry:
  `key`, `type`, `authors` (`family`, `given`), `et_al`, `year` (str or None), `title`, `container`
  (journal, booktitle, container-title or publisher). Malformed input is MDQ_SOURCE_INVALID, JSON of the
  wrong shape MDQ_SOURCE_TYPE_ERROR, a key or id defined twice MDQ_DUPLICATE_ID. LaTeX is never executed:
  braces are dropped and `\\& \\% \\$ \\# \\_ \\{ \\}` unescaped, anything else is kept as text.
- `citation_resolve(text, bibs, first_line=1)`: `[@key]` / `[@key, locator]` become `(Smith 2020)` /
  `(Smith 2020, p. 12)`; a bibliography of the cited entries replaces a paragraph `[[bibliography]]`, or is
  appended under `## References`. A missing key is MDQ_UNRESOLVED_REFERENCE.
"""
import json
import re

import pytest
from hypothesis import given, strategies as st
from markdown_it import MarkdownIt

import mdq
from mdq import _internal as mi

pytestmark = pytest.mark.unit

bp = mi.bib_parse
cr = mi.citation_resolve
MD = MarkdownIt("commonmark")

SMITH = "@article{smith20, author={Smith, John and Jane Doe}, title={A {Great} Title}, journal={J. Stuff}, year=2020}\n"
ZED = "@book{zed19, author=\"Zed, A. and Y, B. and X, C.\", title=\"Book\", publisher=\"Pub\", year={2019}}\n"
BIB = SMITH + ZED


def csl(*items):
    return json.dumps(list(items))


# ---------------------------------------------------------------- BibTeX
def test_bibtex_basic_entry():
    (e,) = bp(SMITH, "bibtex")
    assert e == {"key": "smith20", "type": "article", "authors": [{"family": "Smith", "given": "John"}, {"family": "Doe", "given": "Jane"}],
                 "et_al": False, "year": "2020", "title": "A Great Title", "container": "J. Stuff"}


@pytest.mark.parametrize("src,field,expected", [
    ("@misc{k, title = {x}}", "title", "x"),
    ("@misc{k, title = \"x\"}", "title", "x"),
    ("@misc{k, year = 1999}", "year", "1999"),
    ("@MISC{k, TITLE = {x}, Year = {2001}}", "title", "x"),
    ("@misc(k, title = {x})", "title", "x"),
    ("@misc{k, title = {x},}", "title", "x"),
    ("@misc{k,\n  title\n =\n {x\n   y}\n}", "title", "x y"),
    ("@misc{k, title = {a {B} c}}", "title", "a B c"),
    ("@misc{k, title = {a {B {C}} c}}", "title", "a B C c"),
    ("@misc{k, title = \"a {\\\"o} b\"}", "title", "a \\\"o b"),
    ("@misc{k, title = \"a {\"} b\"}", "title", "a \" b"),
    ("@misc{k, title = {R\\&D 50\\% \\$ \\# \\_ \\{x\\}}}", "title", "R&D 50% $ # _ {x}"),
    ("@misc{k, title = {\\textit{x}}}", "title", "\\textitx"),
    ("@misc{k, title = {a} # { } # {b}}", "title", "a b"),
    ("@misc{k, title = \"a\" # \"b\"}", "title", "ab"),
    ("@string{s = \"Name\"}\n@misc{k, title = s}", "title", "Name"),
    ("@string{S = {A}} @misc{k, title = s # \" b\"}", "title", "A b"),
    ("@misc{k, title = jan # \" \" # 2000}", "title", "January 2000"),
    ("@misc{k, year = {c. 1999}}", "year", "1999"),
    ("@misc{k, year = {12345}}", "year", None),
    ("@misc{k, date = {2005-03-01}}", "year", "2005"),
    ("@misc{k, year = {1990}, date = {2005}}", "year", "1990"),
    ("@misc{k, journal = {J}, publisher = {P}}", "container", "J"),
    ("@misc{k, booktitle = {B}, publisher = {P}}", "container", "B"),
    ("@misc{k, publisher = {P}}", "container", "P"),
    ("@misc{k}", "container", None),
    ("@misc{k, title = {}}", "title", None),
    ("@misc{k, title = {  }}", "title", None),
])
def test_bibtex_fields(src, field, expected):
    (e,) = bp(src, "bibtex")
    assert e[field] == expected


@pytest.mark.parametrize("raw,authors,et_al", [
    ("Smith, John", [("Smith", "John")], False),
    ("John Smith", [("Smith", "John")], False),
    ("John Q. Public", [("Public", "John Q.")], False),
    ("Madonna", [("Madonna", "")], False),
    ("Smith, John and Doe, Jane and Roe, R.", [("Smith", "John"), ("Doe", "Jane"), ("Roe", "R.")], False),
    ("Smith, John AND Doe, Jane", [("Smith", "John"), ("Doe", "Jane")], False),
    ("{Barnes and Noble}", [("Barnes and Noble", "")], False),
    ("{Barnes and Noble} and Ann Lee", [("Barnes and Noble", ""), ("Lee", "Ann")], False),
    ("Smith, John and others", [("Smith", "John")], True),
    ("Ludwig {van} Beethoven", [("Beethoven", "Ludwig van")], False),
    ("{van Gogh}, Vincent", [("van Gogh", "Vincent")], False),
    ("  Ann   Lee  ", [("Lee", "Ann")], False),
    ("Sandy and Randy", [("Sandy", ""), ("Randy", "")], False),
    ("", [], False),
])
def test_bibtex_authors(raw, authors, et_al):
    (e,) = bp("@misc{k, author = {" + raw + "}}", "bibtex")
    assert [(a["family"], a["given"]) for a in e["authors"]] == authors and e["et_al"] is et_al


def test_bibtex_ignores_text_comments_and_preamble_and_keeps_order():
    src = ("junk before\n@comment{ignored {nested} @misc{no, title={x}} }\n@preamble{\"\\newcommand{\\x}{y}\"}\n"
           "@misc{b, title={B}} text between @misc{a, title={A}}\n@comment(x)\n")
    assert [e["key"] for e in bp(src, "bibtex")] == ["b", "a"]


def test_bibtex_empty_and_bom():
    assert bp("", "bibtex") == [] and bp("no entries here", "bibtex") == []
    assert bp("\ufeff@misc{k}", "bibtex")[0]["key"] == "k"


@pytest.mark.parametrize("src,line,col", [
    ("@misc{k, title = {x}", 1, 21),
    ("@misc{k, title = {x", 1, 18),
    ("@misc{k, title = \"x}", 1, 18),
    ("@misc{k, title = }", 1, 18),
    ("@misc{k, title {x}}", 1, 16),
    ("@misc{k, title = {x} year = 1}", 1, 22),
    ("@{k}", 1, 1),
    ("@misc k", 1, 7),
    ("@misc{, title={x}}", 1, 7),
    ("@misc{k title = {x}}", 1, 9),
    ("@misc{k, = {x}}", 1, 10),
    ("@misc{k, title = foo}", 1, 18),
    ("\n\n@misc{k, title = {x} # }", 3, 24),
    ("@string{ = {x}}", 1, 10),
    ("@string{a {x}}", 1, 11),
    ("@string{a = {x}", 1, 16),
])
def test_bibtex_syntax_errors(src, line, col, assert_mdq_error):
    with assert_mdq_error("MDQ_SOURCE_INVALID", line=line, column=col):
        bp(src, "bibtex")


def test_bibtex_duplicate_key_and_field(assert_mdq_error):
    with assert_mdq_error("MDQ_DUPLICATE_ID", line=2, column=7):
        bp("@misc{k}\n@misc{k}\n", "bibtex")
    with assert_mdq_error("MDQ_SOURCE_INVALID", line=1, column=21):
        bp("@misc{k, title={a}, title={b}}", "bibtex")


# ---------------------------------------------------------------- CSL JSON
def test_csl_basic_item():
    (e,) = bp(csl({"id": "a", "type": "book", "title": "  T  x ", "author": [{"family": "Q", "given": "R"}, {"literal": "UN"}],
                   "issued": {"date-parts": [[2001, 2, 3]]}, "container-title": "C"}), "csl-json")
    assert e == {"key": "a", "type": "book", "authors": [{"family": "Q", "given": "R"}, {"family": "UN", "given": ""}],
                 "et_al": False, "year": "2001", "title": "T x", "container": "C"}


@pytest.mark.parametrize("issued,year", [
    ({"date-parts": [["1999"]]}, "1999"),
    ({"date-parts": [[1999, 12]]}, "1999"),
    ({"date-parts": [[2000]], "raw": "1950"}, "2000"),
    ({"raw": "March 1980"}, "1980"),
    ({"literal": "ca. 1812"}, "1812"),
    ({"raw": "no year"}, None),
    ({}, None),
])
def test_csl_years(issued, year):
    assert bp(csl({"id": "a", "issued": issued}), "csl-json")[0]["year"] == year


def test_csl_container_falls_back_to_publisher_and_family_only_author():
    (e,) = bp(csl({"id": "a", "publisher": "P", "author": [{"family": "Solo"}]}), "csl-json")
    assert e["container"] == "P" and e["authors"] == [{"family": "Solo", "given": ""}]


def test_csl_numeric_id_is_its_text():
    assert bp('[{"id": 12}]', "csl-json")[0]["key"] == "12"


@pytest.mark.parametrize("src", [
    "{}", "[1]", '[{"title": "x"}]', '[{"id": ""}]', '[{"id": null}]', '[{"id": "a", "author": {}}]',
    '[{"id": "a", "author": [1]}]', '[{"id": "a", "author": [{"given": "x"}]}]', '[{"id": "a", "issued": []}]',
    '[{"id": "a", "issued": {"date-parts": []}}]', '[{"id": "a", "issued": {"date-parts": [[]]}}]',
    '[{"id": "a", "issued": {"date-parts": [{}]}}]', '[{"id": "a", "title": 1}]', '[{"id": "a", "title": ["x"]}]',
    "null", '"x"', "1",
])
def test_csl_wrong_shapes(src, assert_mdq_error):
    with assert_mdq_error("MDQ_SOURCE_TYPE_ERROR"):
        bp(src, "csl-json")


def test_csl_duplicate_id(assert_mdq_error):
    with assert_mdq_error("MDQ_DUPLICATE_ID"):
        bp('[{"id": "a"}, {"id": "a"}]', "csl-json")


@pytest.mark.parametrize("src,line,col", [
    ("", 1, 1),
    ("[", 1, 2),
    ("[1,]", 1, 4),
    ("[,1]", 1, 2),
    ('[{"id": "a",}]', 1, 13),
    ('[{"id": "a" "b": 1}]', 1, 13),
    ("[{id: 1}]", 1, 3),
    ("['a']", 1, 2),
    ('[{"id": "a", "id": "b"}]', 1, 14),
    ('[{"id": "a\nb"}]', 1, 11),
    ('[{"id": "\\x"}]', 1, 10),
    ('[{"id": "\\u12"}]', 1, 10),
    ('[{"id": "\\ud800"}]', 1, 10),
    ('[{"id": "\\udc00"}]', 1, 10),
    ('[{"id": "\\ud800\\u0041"}]', 1, 10),
    ('[{"id": "a"}] x', 1, 15),
    ("[01]", 1, 3),
    ("[1.]", 1, 2),
    ("[.5]", 1, 2),
    ("[+1]", 1, 2),
    ("[1e]", 1, 2),
    ("[-]", 1, 2),
    ("[tru]", 1, 2),
    ("[nan]", 1, 2),
    ("[NaN]", 1, 2),
    ("[\n\n  ]]", 3, 4),
    ('[{"id": "a"}\n}', 2, 1),
    ("[\u00a01]", 1, 2),
])
def test_json_syntax_errors(src, line, col, assert_mdq_error):
    with assert_mdq_error("MDQ_SOURCE_INVALID", line=line, column=col):
        bp(src, "csl-json")


def test_json_depth_limit(assert_mdq_error):
    ok = "[" * 64 + "]" * 64
    with pytest.raises(mdq.MdqError) as e:
        bp(ok, "csl-json")
    assert e.value.code == "MDQ_SOURCE_TYPE_ERROR"
    with assert_mdq_error("MDQ_RESOURCE_LIMIT"):
        bp("[" * 66 + "]" * 66, "csl-json")


def test_json_accepts_valid_forms():
    assert bp('\ufeff [ ] ', "csl-json") == []
    src = '[{"id":"a","title":"\\u00e9\\ud83d\\ude00\\n\\t\\/","x":[1,-0,2.5e+10,1E-2,{"y":null,"z":true,"w":false}]}]'
    assert bp(src, "csl-json")[0]["title"] == "é😀 /"


ODD_WS = "\x0b\x1c\x1d\x1e\x1f\x85\u2028\u2029"


@given(st.text(alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters=ODD_WS), max_size=30), st.booleans())
def test_json_strings_match_python_json(s, ascii_only):
    src = json.dumps([{"id": "k", "title": s}], ensure_ascii=ascii_only)
    (e,) = bp(src, "csl-json")
    assert e["title"] == (" ".join(s.split()) or None)


@given(st.recursive(st.none() | st.booleans() | st.integers() | st.floats(allow_nan=False, allow_infinity=False) | st.text(max_size=5),
                    lambda c: st.lists(c, max_size=3) | st.dictionaries(st.text(max_size=3), c, max_size=3), max_leaves=12))
def test_json_accepts_whatever_python_serialises(v):
    src = json.dumps([{"id": "k", "extra": v}])
    assert bp(src, "csl-json")[0]["key"] == "k"


@given(st.text(alphabet='[]{}",:0-9a-z \\-.', max_size=14))
def test_json_agrees_with_python_on_validity(s):
    try:
        json.loads(s, parse_constant=lambda x: (_ for _ in ()).throw(ValueError()), object_pairs_hook=_no_dups)
        valid = True
    except (ValueError, RecursionError):
        valid = False
    try:
        bp(s, "csl-json")
        mine = True
    except mdq.MdqError as e:
        mine = e.code not in ("MDQ_SOURCE_INVALID", "MDQ_RESOURCE_LIMIT")
    assert mine == valid


def _no_dups(pairs):
    d = {}
    for k, v in pairs:
        if k in d:
            raise ValueError("dup")
        d[k] = v
    return d


# ---------------------------------------------------------------- bib_parse arguments
def test_bib_parse_argument_errors():
    with pytest.raises(TypeError):
        bp(1, "bibtex")
    with pytest.raises(TypeError):
        bp("", 1)
    with pytest.raises(ValueError):
        bp("", "yaml")


# ---------------------------------------------------------------- citation_resolve
@pytest.mark.parametrize("src,expected_text", [
    ("See [@smith20].\n", "See (Smith and Doe 2020)."),
    ("See [@smith20, p. 12].\n", "See (Smith and Doe 2020, p. 12)."),
    ("[@smith20,p.1]\n", "(Smith and Doe 2020, p.1)\n"),
    ("a [@zed19] b [@smith20] c\n", "a (Zed et al. 2019) b (Smith and Doe 2020) c"),
    ("# H [@zed19]\n", "# H (Zed et al. 2019)"),
    ("> q [@zed19]\n", "> q (Zed et al. 2019)"),
    ("- i [@zed19]\n", "- i (Zed et al. 2019)"),
    ("[@zed19] and [@zed19]\n", "(Zed et al. 2019) and (Zed et al. 2019)"),
    ("[[@zed19]]\n", "[(Zed et al. 2019)]"),
    ("[see [@zed19]](u)\n", "[see (Zed et al. 2019)](u)"),
])
def test_citation_forms(src, expected_text):
    out = cr(src, [("bibtex", BIB)])
    assert expected_text in out


@pytest.mark.parametrize("src", [
    "`[@zed19]`\n", "``a [@zed19] ` ``\n", "\\[@zed19]\n", "[@zed19](u)\n", "[@zed19][r]\n", "[@zed19]: u\n",
    "<span title=\"[@zed19]\">x</span>\n", "<http://x/[@zed19]>\n", "```\n[@zed19]\n```\n", "    [@zed19]\n",
    "[@zed19 x]\n", "[@zed19,]\n", "[@zed19, ]\n", "[@zed19, a\nb]\n", "[@zed19.]\n", "[@]\n", "[@ zed19]\n", "[@-a]\n",
    "[@zed19; @smith20]\n", "[@zed19 , x]\n", "[ @zed19]\n", "[@zed19\n]\n", "<div>\n[@zed19]\n</div>\n",
])
def test_non_citations_are_untouched_and_need_no_bibliography(src):
    assert cr(src, [("bibtex", BIB)]) == src
    assert cr(src, []) == src


def test_key_characters():
    bib = "@misc{a.b-c_1:2/3+4, author={Q, R}, year=2000}"
    assert cr("[@a.b-c_1:2/3+4]\n", [("bibtex", bib)]).startswith("(Q 2000)\n")
    assert cr("x [@a.b-c_1:2/3+4], y\n", [("bibtex", bib)]).startswith("x (Q 2000), y\n")


def test_label_variants():
    bib = ("@misc{one, author={A, a}, year=2001}\n@misc{two, author={A, a and B, b}, year=2001}\n"
           "@misc{three, author={A, a and B, b and C, c}, year=2001}\n@misc{etal, author={A, a and others}, year=2001}\n"
           "@misc{none, title={The Quick Brown Fox}, year=2001}\n@misc{nodate, author={A, a}}\n@misc{bare}\n")
    out = cr("[@one] [@two] [@three] [@etal] [@none] [@nodate] [@bare]\n", [("bibtex", bib)])
    assert out.split("\n")[0] == "(A 2001) (A and B 2001) (A et al. 2001a) (A et al. 2001b) (The Quick Brown 2001) (A n.d.) (bare n.d.)"


def test_same_label_is_disambiguated_in_order_of_first_citation():
    bib = "@misc{x, author={S, a}, year=2001, title={T1}}\n@misc{y, author={S, b}, year=2001, title={T2}}\n@misc{z, author={S, c}, year=2002}\n"
    out = cr("[@y] [@x] [@y] [@z]\n", [("bibtex", bib)])
    assert out.split("\n")[0] == "(S 2001a) (S 2001b) (S 2001a) (S 2002)"
    assert "- S, a (2001b). T1." in out and "- S, b (2001a). T2." in out and "- S, c (2002)." in out


def test_markdown_in_labels_locators_and_titles_is_escaped():
    bib = "@misc{k, author={A*b_c, d}, title={T *x* [y] `z`}, year=2000}"
    out = cr("[@k, p. *1*]\n", [("bibtex", bib)])
    assert out.startswith("(A\\*b\\_c 2000, p. \\*1\\*)\n")
    assert "- A\\*b\\_c, d (2000). T \\*x\\* \\[y\\] \\`z\\`." in out
    html = MD.render(out)
    assert "(A*b_c 2000, p. *1*)" in html and "T *x* [y] <code>z</code>" not in html


def test_bibliography_appended_with_heading_and_sorted():
    out = cr("a [@zed19] b [@smith20]\n", [("bibtex", BIB)])
    assert out == ("a (Zed et al. 2019) b (Smith and Doe 2020)\n\n## References\n\n"
                   "- Smith, John and Doe, Jane (2020). A Great Title. J. Stuff.\n"
                   "- Zed, A., Y, B. and X, C. (2019). Book. Pub.\n")


def test_bibliography_placeholder_replaces_its_paragraph():
    out = cr("a [@zed19]\n\n[[bibliography]]\n\nend\n", [("bibtex", BIB)])
    assert out == "a (Zed et al. 2019)\n\n- Zed, A., Y, B. and X, C. (2019). Book. Pub.\n\nend\n"
    assert "References" not in out


def test_placeholder_inside_a_quote_or_with_other_text_is_not_a_placeholder():
    out = cr("a [@zed19]\n\n> [[bibliography]]\n\nx [[bibliography]]\n", [("bibtex", BIB)])
    assert out.endswith("## References\n\n- Zed, A., Y, B. and X, C. (2019). Book. Pub.\n") and "> [[bibliography]]" in out


def test_placeholder_without_citations_is_removed_and_nothing_appended():
    assert cr("a\n\n[[bibliography]]\n\nb\n", [("bibtex", BIB)]) == "a\n\n\n\nb\n"


def test_no_citations_means_unchanged_text():
    assert cr("plain\n", [("bibtex", BIB)]) == "plain\n"
    assert cr("", [("bibtex", BIB)]) == ""


def test_line_endings_are_kept():
    out = cr("a [@zed19]\r\nb\r\n", [("bibtex", BIB)])
    assert out == "a (Zed et al. 2019)\r\nb\r\n\r\n## References\r\n\r\n- Zed, A., Y, B. and X, C. (2019). Book. Pub.\r\n"
    assert cr("a [@zed19]", [("bibtex", BIB)]) == "a (Zed et al. 2019)\n\n## References\n\n- Zed, A., Y, B. and X, C. (2019). Book. Pub.\n"


def test_several_resources_merge_and_duplicate_keys_fail(assert_mdq_error):
    c = csl({"id": "j", "author": [{"family": "Json", "given": "J"}], "issued": {"date-parts": [[2010]]}, "title": "From JSON"})
    out = cr("[@zed19] [@j]\n", [("bibtex", BIB), ("csl-json", c)])
    assert out.startswith("(Zed et al. 2019) (Json 2010)\n") and "- Json, J (2010). From JSON." in out
    with assert_mdq_error("MDQ_DUPLICATE_ID"):
        cr("a\n", [("bibtex", BIB), ("bibtex", ZED)])


def test_unresolved_key_error_position(assert_mdq_error):
    with assert_mdq_error("MDQ_UNRESOLVED_REFERENCE", line=13, column=4):
        cr("a [@zed19]\n\nx  [@nope] [@zed19]\n".replace("x  [@nope]", "x  [@nope]"), [("bibtex", BIB)], first_line=11)
    with assert_mdq_error("MDQ_UNRESOLVED_REFERENCE", line=1, column=1):
        cr("[@zed19]\n", [])


def test_bad_bibliography_propagates(assert_mdq_error):
    with assert_mdq_error("MDQ_SOURCE_INVALID"):
        cr("a [@x]\n", [("bibtex", "@misc{x")])
    with assert_mdq_error("MDQ_SOURCE_TYPE_ERROR"):
        cr("a [@x]\n", [("csl-json", "{}")])


def test_citation_argument_errors(assert_mdq_error):
    with pytest.raises(TypeError):
        cr(1, [])
    with pytest.raises(TypeError):
        cr("a", [], "1")
    with pytest.raises(TypeError):
        cr("a", "bibtex")
    with pytest.raises((TypeError, ValueError)):  # PyO3 reports a wrong tuple length as ValueError
        cr("a", [("bibtex",)])
    with pytest.raises(TypeError):
        cr("a", [("bibtex", 1)])
    with pytest.raises(ValueError):
        cr("a", [("yaml", "")])
    with assert_mdq_error("MDQ_RESOURCE_LIMIT"):
        cr("a" * (16 * 1024 * 1024 + 1), [])


def test_rendered_output_has_text_citations_and_a_list():
    html = MD.render(cr("a [@zed19, p. 3]\n", [("bibtex", BIB)]))
    assert "<p>a (Zed et al. 2019, p. 3)</p>" in html
    assert "<h2>References</h2>" in html and "<li>Zed, A., Y, B. and X, C. (2019). Book. Pub.</li>" in html


def test_resolve_is_idempotent():
    once = cr("a [@zed19]\n\n[[bibliography]]\n", [("bibtex", BIB)])
    assert cr(once, [("bibtex", BIB)]) == once
    once = cr("a [@zed19]\n", [("bibtex", BIB)])
    assert cr(once, [("bibtex", BIB)]) == once


@given(st.lists(st.sampled_from(["[@zed19]", "[@smith20, p. 1]", " ", "x", "`[@zed19]`", "[@zed19](u)", "\n"]), max_size=10).map("".join))
def test_resolved_text_has_no_citation_left_except_in_literal_contexts(s):
    src = s + "\n"
    if src.startswith("    ") or "][" in src:
        return
    out = cr(src, [("bibtex", BIB)])
    body = out.split("\n## References")[0]
    literal = re.findall(r"`\[@zed19\]`|\[@zed19\]\(u\)", src)
    assert len(re.findall(r"\[@", body)) == len(literal)
    assert cr(out, [("bibtex", BIB)]) == out
