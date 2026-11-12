"""Epic 2 / E2.2 (F03): the strict YAML subset loader (spec 5).

`mdq._internal.yaml_load_strict(text, mapping_only=True, first_line=1)` returns
None / bool / str / `decimal.Decimal` / list / dict. Numbers are exact
(canonical decimals, never floats); mapping keys are always strings; YAML 1.2
core types (`yes`/`no` are strings). Anything outside the supported subset, and
every duplicate key, is MDQ_METADATA_ERROR with line and column (offset by
`first_line`); size/depth/node limits are MDQ_RESOURCE_LIMIT. Decisions: G23.
"""
from decimal import Decimal as D

import pytest
import yaml as pyyaml
from hypothesis import assume, given, strategies as st

import mdq
from mdq import _internal as mi

pytestmark = pytest.mark.unit


def load(text, **kw):
    return mi.yaml_load_strict(text, **kw)


def val(text):
    """The value of `x: <text>`."""
    return load(f"x: {text}")["x"]


# ------------------------------------------------------------------ scalars


@pytest.mark.parametrize("text", ["~", "null", "Null", "NULL", ""])
def test_nulls(text):
    assert val(text) is None


@pytest.mark.parametrize("text, expected", [
    ("true", True), ("True", True), ("TRUE", True), ("false", False), ("False", False), ("FALSE", False),
])
def test_booleans(text, expected):
    assert val(text) is expected


@pytest.mark.parametrize("text", [
    "yes", "no", "on", "off", "y", "n", "Yes", "NO", "nULL", "truE", "tRUE",     # YAML 1.1 words are strings
    "2026-01-01", "2026-01-01T10:00:00Z", "12:30", "1.2.3", "1_000", "1e", "e5", "-x", "-1x", ".", "..", ".e5",
    "v1.0", "0.5.1", "1,000", "1 000", "hello world", "a#b", "http://example.com/a#b", "a:b", "it's", 'say "hi"',
    "1e3x", "12abc", "0b101", "1__0", "--", "-- x", "a - b", "x\ty",
])
def test_plain_strings(text):
    assert val(text) == text


@pytest.mark.parametrize("text, expected", [
    ("0", "0"), ("-0", "0"), ("5", "5"), ("-5", "-5"), ("3.14", "3.14"), ("0.5", "0.5"), ("10.50", "10.5"),
    ("10.0", "10"), ("1e3", "1000"), ("1E3", "1000"), ("1.5e-3", "0.0015"), ("1e-2", "0.01"), ("2e+2", "200"),
    ("0e5", "0"), ("-1.5E2", "-150"), ("0.0", "0"), ("100", "100"), ("1e0", "1"), ("12.345e2", "1234.5"),
    ("123456789012345678901234567890", "123456789012345678901234567890"),
    ("0.0000000000000000000000000001", "0.0000000000000000000000000001"),
    ("1e400", "1" + "0" * 400), ("1e-400", "0." + "0" * 399 + "1"),
])
def test_numbers_are_exact_decimals(text, expected):
    got = val(text)
    assert isinstance(got, D) and not isinstance(got, bool)
    assert got == D(expected)


def test_numbers_are_never_floats():
    assert not isinstance(val("0.1"), float)
    assert val("0.1") + val("0.2") == D("0.3")


@pytest.mark.parametrize("text", [
    "007", "-007", "0123", "00", "+1", "+1.5", "+0", ".5", "-.5", "5.", "1.e5", "0x1F", "0xff", "0o17", "01.5",
    ".inf", "-.inf", "+.inf", ".Inf", ".INF", ".nan", ".NaN", ".NAN", "+.5",
])
def test_ambiguous_numbers_are_rejected(assert_mdq_error, text):
    with assert_mdq_error("MDQ_METADATA_ERROR", line=1, column=4) as info:
        load(f"x: {text}")
    assert "ambiguous number" in str(info.value) and "quote" in str(info.value)


@pytest.mark.parametrize("text", ["007", "0x1F", "+1", ".5", "5.", ".inf", "1e3"])
def test_quoting_keeps_numeric_looking_text_as_a_string(text):
    assert val(f'"{text}"') == text and val(f"'{text}'") == text


@pytest.mark.parametrize("text", ["1e401", "1e-401", "1" + "0" * 400 + ".5", "9" * 401])
def test_number_out_of_range(assert_mdq_error, text):
    with assert_mdq_error("MDQ_METADATA_ERROR"):
        val(text)


def test_nan_and_infinity_words_are_ordinary_strings():
    assert val("nan") == "nan" and val("inf") == "inf" and val("Infinity") == "Infinity" and val("NaN") == "NaN"


# --------------------------------------------------------------- quoting


@pytest.mark.parametrize("text, expected", [
    ('"plain"', "plain"), ('""', ""), ("''", ""), ("'single'", "single"), ("'it''s'", "it's"), ("''''", "'"),
    ('"a\\"b"', 'a"b'), ('"a\\\\b"', "a\\b"), ('"tab\\tend"', "tab\tend"), ('"nl\\nend"', "nl\nend"),
    ('"\\0"', "\0"), ('"\\a\\b\\e\\f\\r\\v"', "\x07\x08\x1b\x0c\r\x0b"), ('"\\/"', "/"), ('"\\ "', " "),
    ('"\\N\\_\\L\\P"', "\u0085\u00a0\u2028\u2029"), ('"\\x41"', "A"), ('"\\u00e9"', "é"), ('"\\u65E5"', "日"),
    ('"\\U0001F600"', "😀"), ('"é 日本 😀"', "é 日本 😀"), ("'back\\slash'", "back\\slash"),
    ('"# not a comment"', "# not a comment"), ('"a: b"', "a: b"), ('"- x"', "- x"), ('"[x]"', "[x]"),
    ("'single \"double\" inside'", 'single "double" inside'), ('"  padded  "', "  padded  "),
])
def test_quoted_scalars(text, expected):
    assert val(text) == expected


@pytest.mark.parametrize("text", [
    '"unterminated', "'unterminated", '"a\\"', '"a\\', '"\\q"', '"\\x4"', '"\\xZZ"', '"\\u12"', '"\\u12G4"',
    '"\\ud800"', '"\\udfff"', '"\\U00110000"', '"\\U0000D800"', '"a" b', "'a' b", '"a"#c', '"a""b"',
])
def test_bad_quoted_scalars(assert_mdq_error, text):
    with assert_mdq_error("MDQ_METADATA_ERROR", line=1):
        val(text)


def test_quoted_scalars_may_not_span_lines(assert_mdq_error):
    with assert_mdq_error("MDQ_METADATA_ERROR", line=1, column=4) as info:
        load('x: "line one\n  line two"\n')
    assert "one line" in str(info.value)


def test_a_comment_may_follow_a_quoted_scalar():
    assert val('"a" # note') == "a" and val("'a'\t# note") == "a"


# ---------------------------------------------------------------- mappings


def test_nested_mapping_and_value_kinds():
    text = "name: demo\ncount: 3\nratio: 0.5\non: true\nnothing:\nnested:\n  deep:\n    deeper: x\n  other: y\n"
    assert load(text) == {
        "name": "demo", "count": D(3), "ratio": D("0.5"), "on": True, "nothing": None,
        "nested": {"deep": {"deeper": "x"}, "other": "y"},
    }


def test_key_order_is_document_order():
    assert list(load("b: 1\na: 2\nc: 3\n")) == ["b", "a", "c"]


@pytest.mark.parametrize("key_text, key", [
    ("plain", "plain"), ("with space", "with space"), ("with-dash", "with-dash"), ("a.b.c", "a.b.c"),
    ("true", "true"), ("null", "null"), ("1", "1"), ("1.50", "1.50"), ("007", "007"), ("~", "~"), ("a#b", "a#b"),
    ('"quoted: key"', "quoted: key"), ("'single'", "single"), ('"é日"', "é日"), ("'it''s'", "it's"),
    ("http://x", "http://x"), ("a\tb", "a\tb"),
])
def test_keys_are_always_strings(key_text, key):
    assert load(f"{key_text}: 1\n") == {key: D(1)}


def test_trailing_blanks_before_the_colon_are_not_part_of_the_key():
    assert load("key  : 1\n") == {"key": D(1)}
    assert load('"k"  : 1\n') == {"k": D(1)}


def test_value_forms_after_a_colon():
    assert load("a:    spaced\nb:\ttabbed\nc: # comment only\nd:   \n") == {"a": "spaced", "b": "tabbed", "c": None, "d": None}


def test_empty_and_comment_only_documents():
    for text in ["", "\n", "   \n", "# just a comment\n", "---\n", "---\n# c\n", "\ufeff", "\n\n# a\n\n"]:
        assert load(text) == {}
        assert load(text, mapping_only=False) is None


def test_optional_document_start_marker():
    assert load("---\na: 1\n") == {"a": D(1)}
    assert load("--- # comment\na: 1\n") == {"a": D(1)}
    assert load("# lead\n---\na: 1\n") == {"a": D(1)}


def test_leading_bom_crlf_and_missing_final_newline():
    assert load("\ufeffa: 1\r\nb: 2") == {"a": D(1), "b": D(2)}
    assert load("a: 1\r\nb:\r\n  - x\r\n") == {"a": D(1), "b": ["x"]}


def test_whole_document_may_be_indented_consistently():
    assert load("   a: 1\n   b:\n     c: 2\n") == {"a": D(1), "b": {"c": D(2)}}


def test_url_like_and_colon_containing_plain_values():
    assert load("url: http://example.com:8080/a?b=c#frag\nt: 12:30:45\nk: a:b\n") == {
        "url": "http://example.com:8080/a?b=c#frag", "t": "12:30:45", "k": "a:b"}


# ---------------------------------------------------------------- sequences


def test_block_sequences():
    assert load("a:\n  - 1\n  - two\n  - [3]\n  - {k: v}\nb: x\n") == {
        "a": [D(1), "two", [D(3)], {"k": "v"}], "b": "x"}


def test_sequence_at_the_same_indent_as_its_key():
    assert load("a:\n- 1\n- 2\nb: 3\n") == {"a": [D(1), D(2)], "b": D(3)}


def test_nested_sequences_and_empty_items():
    assert load("a:\n  - - 1\n    - 2\n  -\n    - 3\n  - \n  -\n") == {"a": [[D(1), D(2)], [D(3)], None, None]}


def test_sequence_items_with_compact_mappings():
    text = "items:\n  - name: a\n    n: 1\n  - name: b\n    sub:\n      - x\n    n: 2\n  - {name: c}\n"
    assert load(text) == {"items": [
        {"name": "a", "n": D(1)}, {"name": "b", "sub": ["x"], "n": D(2)}, {"name": "c"}]}


def test_sequence_item_followed_by_a_nested_block_on_the_next_line():
    assert load("a:\n  -\n    k: v\n    j: w\n  - z\n") == {"a": [{"k": "v", "j": "w"}, "z"]}


def test_root_sequence_needs_mapping_only_false():
    assert load("- a\n- b\n", mapping_only=False) == ["a", "b"]
    assert load("hello", mapping_only=False) == "hello"
    assert load("42", mapping_only=False) == D(42)
    assert load("[1, 2]", mapping_only=False) == [D(1), D(2)]


@pytest.mark.parametrize("text, kind", [
    ("- a\n", "a sequence"), ("hello\n", "a string"), ("42\n", "a number"), ("true\n", "a boolean"),
    ("[1]\n", "a sequence"), ("null\n", "null"), ('"s"\n', "a string"),
])
def test_root_must_be_a_mapping(assert_mdq_error, text, kind):
    with assert_mdq_error("MDQ_METADATA_ERROR", line=1, column=1) as info:
        load(text)
    assert "must be a mapping" in str(info.value) and kind in str(info.value)


def test_root_error_position_skips_comments_and_markers():
    with pytest.raises(mdq.MdqError) as info:
        load("# c\n---\n\n- x\n")
    assert (info.value.line, info.value.column) == (4, 1)


# --------------------------------------------------------------- flow style


def test_flow_collections():
    assert load("a: [1, two, [3, 4], {k: v, n: 5}]\nb: {x: 1, y: [a, b]}\nc: []\nd: {}\n") == {
        "a": [D(1), "two", [D(3), D(4)], {"k": "v", "n": D(5)}], "b": {"x": D(1), "y": ["a", "b"]}, "c": [], "d": {}}


def test_flow_trailing_commas_whitespace_and_comments():
    assert load("a: [ 1 ,2 , 3 , ]\nb: { x : 1 , }\n") == {"a": [D(1), D(2), D(3)], "b": {"x": D(1)}}
    assert load("a: [1, # one\n  2,\n# a whole-line comment\n  3]\nb: 1\n") == {"a": [D(1), D(2), D(3)], "b": D(1)}


def test_flow_spans_lines_and_the_next_key_follows():
    text = "a: {\n  x: 1,\n  y: [\n    2,\n    3\n  ]\n}\nb: after\n"
    assert load(text) == {"a": {"x": D(1), "y": [D(2), D(3)]}, "b": "after"}


def test_flow_values_and_null_entries():
    assert load("a: {x: , y: ~}\n") == {"a": {"x": None, "y": None}}
    assert val("[~, null, true, 'q', \"w\"]") == [None, None, True, "q", "w"]


def test_flow_plain_scalars_with_colons_and_spaces():
    assert val("[a b, http://x/y, a:b, 1:2, 1 2]") == ["a b", "http://x/y", "a:b", "1:2", "1 2"]
    assert val("{a:b: c}") == {"a:b": "c"}


def test_json_style_is_accepted_as_a_document():
    text = '{"a": 1, "b": [true, null, "x"], "c": {"d": 1.5e2}, "e": "\\u00e9\\n"}'
    assert load(text) == {"a": D(1), "b": [True, None, "x"], "c": {"d": D(150)}, "e": "é\n"}
    assert load('{"a":1,"b":"x"}') == {"a": D(1), "b": "x"}          # JSON writes no space after the colon
    assert load('{\n  "a": [\n    1,\n    2\n  ]\n}\n') == {"a": [D(1), D(2)]}


@pytest.mark.parametrize("text", [
    "[1, 2", "{a: 1", "[1, 2}", "{a: 1]", "[,]", "[1,,2]", "{,}", "{a: 1,, b: 2}", "{a 1}", "{a: 1 b: 2}",
    "[a: 1]", "{[a]: 1}", "{{a: 1}: 2}", "[|]", "[>]", "[&a x]", "[*a]", "[!t x]", "{&a x: 1}", "[a]b", "[a] b",
    "[a]#c", "{a:1}", "[}", "{]", "]", "}", "[[[1]]", "[a, ]b",
])
def test_bad_flow_collections(assert_mdq_error, text):
    with assert_mdq_error("MDQ_METADATA_ERROR", line=1):
        val(text)


def test_unterminated_flow_across_lines_reports_where_it_started(assert_mdq_error):
    with assert_mdq_error("MDQ_METADATA_ERROR", line=2, column=4):
        load("a: 1\nb: [1,\n  2\n")


# --------------------------------------------------------------- block scalars


def test_literal_block_scalar():
    assert load("t: |\n  line one\n  line two\n") == {"t": "line one\nline two\n"}
    assert load("t: |\n  a\n\n  b\n\n\nu: 1\n") == {"t": "a\n\nb\n", "u": D(1)}
    assert load("t: |\n    indented\n      more\n    back\n") == {"t": "indented\n  more\nback\n"}


@pytest.mark.parametrize("header, expected", [
    ("|", "a\nb\n"), ("|-", "a\nb"), ("|+", "a\nb\n\n\n"), (">", "a b\n"), (">-", "a b"), (">+", "a b\n\n\n"),
])
def test_chomping(header, expected):
    assert load(f"t: {header}\n  a\n  b\n\n\nu: 1\n")["t"] == expected


def test_folded_block_scalar():
    assert load("t: >\n  one\n  two\n\n  three\n") == {"t": "one two\nthree\n"}
    assert load("t: >\n  a\n   b\n  c\n") == {"t": "a\n b\nc\n"}                  # more-indented lines keep breaks
    assert load("t: >\n  a\n\n\n  b\n") == {"t": "a\n\nb\n"}
    assert load("t: >\n\n  lead\n") == {"t": "\nlead\n"}


def test_block_scalar_edge_cases():
    assert load("t: |\nu: 1\n") == {"t": "", "u": D(1)}
    assert load("t: |-\nu: 1\n") == {"t": "", "u": D(1)}
    assert load("t: |+\n\n\nu: 1\n") == {"t": "\n\n", "u": D(1)}
    assert load("t: |  # comment\n  x\n") == {"t": "x\n"}
    assert load("t: |\n  # not a comment\n  - not a sequence\n  k: not a key\n") == {
        "t": "# not a comment\n- not a sequence\nk: not a key\n"}
    assert load("t: |\n  tab\there\n  \tx\n") == {"t": "tab\there\n\tx\n"}
    assert load("t: |\n  --- not a marker\n  ... nor this\n") == {"t": "--- not a marker\n... nor this\n"}


def test_block_scalar_in_sequences_and_nested_maps():
    assert load("a:\n  - |\n    one\n    two\n  - >-\n    x\n    y\n") == {"a": ["one\ntwo\n", "x y"]}
    assert load("a:\n  b: |\n    deep\n  c: 1\n") == {"a": {"b": "deep\n", "c": D(1)}}


@pytest.mark.parametrize("text", ["t: |2\n  x\n", "t: >1-\n x\n", "t: | x\n  y\n", "t: |#x\n  y\n"])
def test_bad_block_scalar_headers(assert_mdq_error, text):
    with assert_mdq_error("MDQ_METADATA_ERROR", line=1):
        load(text)


def test_block_scalar_line_less_indented_than_the_first(assert_mdq_error):
    with assert_mdq_error("MDQ_METADATA_ERROR", line=3, column=3):
        load("t: |\n    deep\n  shallow\n")


# ------------------------------------------------------------------ comments


def test_comments_everywhere():
    text = ("# top\na: 1 # trailing\n  # indented comment line\nb: # after key\n  - x # item\n"
            "  # between\n  - y\nc: 'q' # after quote\nd: [1, # in flow\n 2]\n")
    assert load(text) == {"a": D(1), "b": ["x", "y"], "c": "q", "d": [D(1), D(2)]}


def test_hash_without_a_preceding_blank_is_text():
    assert load("a: x#y\nb: a #c\nc: '#q'\n") == {"a": "x#y", "b": "a", "c": "#q"}


# ------------------------------------------------------------------- unsupported


@pytest.mark.parametrize("text", [
    "a: &x 1", "a: *x", "a: !!str 1", "a: !tag x", "&a b: 1", "*a : 1", "!t k: 1", "<<: *x", "a: &x\n  b: 1",
])
def test_anchors_aliases_and_tags(assert_mdq_error, text):
    if text.startswith("<<"):
        with assert_mdq_error("MDQ_METADATA_ERROR"):
            load(text)
        return
    with assert_mdq_error("MDQ_METADATA_ERROR", line=1) as info:
        load(text)
    assert "anchors, aliases and tags" in str(info.value)


@pytest.mark.parametrize("text", ["? a\n: b\n", "?\n", "a: ? b", "a: - b", "a: -", "a: ?"])
def test_complex_keys_and_misplaced_indicators(assert_mdq_error, text):
    with assert_mdq_error("MDQ_METADATA_ERROR", line=1):
        load(text)


@pytest.mark.parametrize("text", ["a: %x", "a: @x", "a: `x", "a: ,x", "a: ]", "a: }", "%YAML 1.2\na: 1", "@k: 1", "`k: 1"])
def test_reserved_leading_characters(assert_mdq_error, text):
    with assert_mdq_error("MDQ_METADATA_ERROR", line=1):
        load(text)


@pytest.mark.parametrize("text, line", [
    ("a: 1\n---\nb: 2\n", 2), ("a: 1\n...\n", 2), ("---\n---\na: 1\n", 2), ("---\na: 1\n---\n", 3),
    ("--- a\nb: 1\n", 1), ("...\n", 1), ("a: 1\n--- \n", 2), ("---\n...\n", 2),
])
def test_multiple_documents_are_rejected(assert_mdq_error, text, line):
    with assert_mdq_error("MDQ_METADATA_ERROR", line=line, column=1):
        load(text)


def test_dashes_that_are_not_markers_are_fine():
    assert load("a: ---\nb: ----\n") == {"a": "---", "b": "----"}
    assert load("---- x: 1\n") == {"---- x": D(1)}


def test_multi_line_plain_scalars_are_rejected(assert_mdq_error):
    with assert_mdq_error("MDQ_METADATA_ERROR", line=2, column=3) as info:
        load("a: one\n  two\n")
    assert "unexpected indentation" in str(info.value)


@pytest.mark.parametrize("text, line, column", [
    ("a: 1\n\tb: 2\n", 2, 1), ("a:\n \tb: 2\n", 2, 2), ("a:\n  \t- x\n", 2, 3),
])
def test_tabs_in_indentation(assert_mdq_error, text, line, column):
    with assert_mdq_error("MDQ_METADATA_ERROR", line=line, column=column) as info:
        load(text)
    assert "tab" in str(info.value)


def test_tabs_elsewhere_are_fine():
    assert load("a:\t1\nb: x\ty\nc: [1,\t2]\n\t# comment\n") == {"a": D(1), "b": "x\ty", "c": [D(1), D(2)]}


@pytest.mark.parametrize("char", ["\x00", "\x01", "\x08", "\x0b", "\x0c", "\r", "\x1b", "\x7f", "\x80", "\x85", "\x9f", "\ufeff", "\ufffe", "\uffff"])
def test_control_characters_are_rejected(assert_mdq_error, char):
    with assert_mdq_error("MDQ_METADATA_ERROR", line=2, column=5):
        load(f"a: 1\nb: x{char}y\n")


def test_other_unicode_is_fine():
    assert load("a: x\u00a0y\u2028z\u2029w \u3000é日🙂\n") == {"a": "x\u00a0y\u2028z\u2029w \u3000é日🙂"}


# --------------------------------------------------------------------- errors


@pytest.mark.parametrize("text, line, column", [
    ("a: 1\na: 2\n", 2, 1),
    ("a: 1\nb: 2\na: 3\n", 3, 1),
    ("a:\n  b: 1\n  b: 2\n", 3, 3),
    ("a:\n  - x: 1\n    x: 2\n", 3, 5),
    ("a: 1\n'a': 2\n", 2, 1),
    ('"a": 1\na: 2\n', 2, 1),
    ("a: {x: 1, x: 2}\n", 1, 11),
    ("a: {\n  x: 1,\n  \"x\": 2\n}\n", 3, 3),
    ("1: a\n1: b\n", 2, 1),
    ("1: a\n1.0: b\n", None, None),          # different strings: allowed
])
def test_duplicate_keys(assert_mdq_error, text, line, column):
    if line is None:
        assert len(load(text)) == 2
        return
    with assert_mdq_error("MDQ_METADATA_ERROR", line=line, column=column) as info:
        load(text)
    assert "duplicate mapping key" in str(info.value)


def test_keys_that_differ_in_case_or_whitespace_are_distinct():
    assert len(load("a: 1\nA: 2\n'a ': 3\n")) == 3


@pytest.mark.parametrize("text, line, column", [
    ("a: 1\n  b: 2\n", 2, 3),
    ("a:\n  b: 1\n c: 2\n", 3, 2),
    ("a: 1\nb\n", 2, 1),
    ("a: 1\n- b\n", 2, 1),
    ("a:\n  - 1\n  b: 2\n", 3, 3),
    ("a: b: c\n", 1, 5),
    ("a: x: y\n", 1, 5),
    ("a: 1 # c\n  b: 2\n", 2, 3),
    (": x\n", 1, 1),
    ("a:b\n", 1, 1),
    ("a: 1\n[x]\n", 2, 1),
    ('a: 1\n"x"\n', 2, 1),
])
def test_structure_errors_and_their_positions(assert_mdq_error, text, line, column):
    with assert_mdq_error("MDQ_METADATA_ERROR", line=line, column=column):
        load(text)


def test_positions_are_offset_by_first_line():
    with pytest.raises(mdq.MdqError) as info:
        load("a: 1\nb: 2\nb: 3\n", first_line=11)
    assert (info.value.line, info.value.column) == (13, 1)
    with pytest.raises(mdq.MdqError) as info:
        load("a: [1,\n", first_line=7)
    assert (info.value.line, info.value.column) == (7, 4)


def test_columns_count_characters_not_bytes():
    with pytest.raises(mdq.MdqError) as info:
        load('é: "日本" x\n')
    assert (info.value.line, info.value.column) == (1, 9)


def test_error_precedence_is_document_order_within_a_phase():
    # character and marker checks run over the whole text first, then parsing
    with pytest.raises(mdq.MdqError) as info:
        load("a: [1\nb: 1\n---\n")
    assert (info.value.line, info.value.column) == (3, 1)


# -------------------------------------------------------------------- limits


def nested(depth, flow):
    if flow:
        return "x: " + "[" * depth + "1" + "]" * depth + "\n"
    return "".join("  " * i + f"k{i}:\n" for i in range(depth)) + "  " * depth + "v: 1\n"


def test_nesting_depth_limit_flow(assert_mdq_error):
    assert load(nested(60, flow=True))
    with assert_mdq_error("MDQ_RESOURCE_LIMIT") as info:
        load(nested(200, flow=True))
    assert "nesting" in str(info.value)


def test_nesting_depth_limit_block(assert_mdq_error):
    assert load(nested(20, flow=False))
    with assert_mdq_error("MDQ_RESOURCE_LIMIT"):
        load(nested(200, flow=False))


def test_very_deep_input_does_not_overflow_the_stack(assert_mdq_error):
    with assert_mdq_error("MDQ_RESOURCE_LIMIT"):
        load("x: " + "[" * 200_000)
    with assert_mdq_error("MDQ_RESOURCE_LIMIT"):
        load("".join("  " * i + "- " for i in range(300)) + "x\n", mapping_only=False)


def test_node_limit(assert_mdq_error):
    assert load("x: [" + ",".join(["1"] * 50_000) + "]")
    with assert_mdq_error("MDQ_RESOURCE_LIMIT") as info:
        load("x: [" + ",".join(["1"] * 100_001) + "]")
    assert "nodes" in str(info.value)


def test_input_size_limit(assert_mdq_error):
    assert load("a: " + "x" * 1_000_000) == {"a": "x" * 1_000_000}
    with assert_mdq_error("MDQ_RESOURCE_LIMIT"):
        load("a: " + "x" * (2 * 1024 * 1024))


# --------------------------------------------------------------------- types


@pytest.mark.parametrize("bad", [None, 5, b"a: 1", ["a: 1"]])
def test_text_must_be_a_string(bad):
    with pytest.raises(TypeError):
        load(bad)


def test_keyword_argument_types():
    with pytest.raises(TypeError):
        load("a: 1", mapping_only="yes")
    with pytest.raises(TypeError):
        load("a: 1", first_line="1")


# ---------------------------------------------------------------- properties


def normalise(value):
    """PyYAML result -> our representation (numbers as Decimal)."""
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return D(repr(value))
    if isinstance(value, list):
        return [normalise(v) for v in value]
    return {str(k): normalise(v) for k, v in value.items()}


_alnum = st.text(alphabet="abcxyz019 _-", min_size=1, max_size=6).map(lambda s: "k" + s.strip())
_strings = st.text(
    alphabet=st.one_of(st.sampled_from(list("ab 1.-:#'\"\\{}[],&*!|>%@`?~\t\n")), st.characters(
        blacklist_categories=("Cs", "Cc"), blacklist_characters="\u2028\u2029\u0085\ufeff\ufffe\uffff")),
    max_size=12,
)
_number_texts = st.sampled_from(["0", "7", "-3", "3.14", "10.50", "1e3", "-2.5E-2", "123456789012345678901234567890", "0.001"])
leaves = st.one_of(
    st.none(), st.booleans(), _number_texts.map(D), _strings,
)
values = st.recursive(
    leaves,
    lambda inner: st.one_of(
        st.lists(inner, max_size=4),
        st.dictionaries(_alnum, inner, max_size=4),
    ),
    max_leaves=12,
)
documents = st.dictionaries(_alnum, values, max_size=5)


def _quote(s, ascii_only=True):
    """Double-quoted YAML/JSON-style string; non-BMP characters use `\\U`, never surrogate pairs."""
    out = ['"']
    for ch in s:
        o = ord(ch)
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif 0x20 <= o < 0x7F or (not ascii_only and o >= 0xA0 and o not in (0xFEFF, 0xFFFE, 0xFFFF)):
            out.append(ch)
        elif o <= 0xFFFF:
            out.append(f"\\u{o:04X}")
        else:
            out.append(f"\\U{o:08X}")
    out.append('"')
    return "".join(out)


def json_text(value, indent=None, ensure_ascii=True):
    def conv(v):
        if isinstance(v, D):
            return JsonNumber(str(v))
        if isinstance(v, list):
            return [conv(x) for x in v]
        if isinstance(v, dict):
            return {k: conv(x) for k, x in v.items()}
        return v

    class JsonNumber(float):
        def __new__(cls, text):
            obj = float.__new__(cls, 0.0)
            obj.text = text
            return obj

    return _dump(conv(value), indent, ensure_ascii)


def _dump(v, indent, ensure_ascii, level=0):
    pad = "" if indent is None else "\n" + " " * (indent * (level + 1))
    end = "" if indent is None else "\n" + " " * (indent * level)
    sep = "," if indent is None else ","
    if hasattr(v, "text"):
        return v.text
    if isinstance(v, list):
        if not v:
            return "[]"
        return "[" + sep.join(pad + _dump(x, indent, ensure_ascii, level + 1) for x in v) + end + "]"
    if isinstance(v, dict):
        if not v:
            return "{}"
        colon = ":" if indent is None else ": "
        return "{" + sep.join(pad + _quote(k, ensure_ascii) + colon + _dump(x, indent, ensure_ascii, level + 1)
                              for k, x in v.items()) + end + "}"
    if v is None:
        return "null"
    if v is True:
        return "true"
    if v is False:
        return "false"
    return _quote(v, ensure_ascii)


@given(documents, st.sampled_from([None, 2, 4]))
def test_json_documents_round_trip_exactly(doc, indent):
    assert load(json_text(doc, indent)) == doc


@given(documents)
def test_json_with_raw_non_ascii_round_trips(doc):
    text = json_text(doc, 2, ensure_ascii=False)
    assume(all(not (0x7F <= ord(c) <= 0x9F) for c in text))
    assert load(text) == doc


def emit(value, indent, rnd, width):
    """Independent block-style emitter. Returns lines."""
    pad = " " * indent
    if isinstance(value, dict) and value:
        out = []
        for k, v in value.items():
            key = k if rnd.random() < 0.5 else _quote(k)
            if _is_block(v):
                if isinstance(v, list) and rnd.random() < 0.4:
                    out.append(f"{pad}{key}:")
                    out += emit(v, indent, rnd, width)                       # sequence at the key's indent
                else:
                    out.append(f"{pad}{key}:")
                    out += emit(v, indent + width, rnd, width)
            else:
                out.append(f"{pad}{key}: {scalar(v, rnd)}{comment(rnd)}")
            if rnd.random() < 0.15:
                out.append(" " * rnd.choice([0, indent, indent + 1]) + "# interleaved comment")
        return out
    if isinstance(value, list) and value:
        out = []
        for item in value:
            if _is_block(item):
                if rnd.random() < 0.6:                                         # compact `- key: v` / `- - x`
                    sub = emit(item, indent + 2, rnd, width)
                    sub[0] = pad + "- " + sub[0][indent + 2:]
                    out += sub
                else:
                    out.append(f"{pad}-")
                    out += emit(item, indent + width, rnd, width)
            else:
                out.append(f"{pad}- {scalar(item, rnd)}{comment(rnd)}")
        return out
    return [pad + scalar(value, rnd)]


def _is_block(v):
    return isinstance(v, (dict, list)) and len(v) > 0


def comment(rnd):
    return " # note" if rnd.random() < 0.2 else ""


def scalar(v, rnd):
    if v is None:
        return rnd.choice(["null", "~", "Null"])
    if isinstance(v, bool):
        return rnd.choice(["true", "True", "TRUE"] if v else ["false", "False", "FALSE"])
    if isinstance(v, D):
        return json_number(v)
    if isinstance(v, list):
        return "[]"
    if isinstance(v, dict):
        return "{}"
    if "\n" not in v and "\t" not in v and rnd.random() < 0.5:
        return "'" + v.replace("'", "''") + "'"
    return _quote(v)


def json_number(v):
    return str(v) if "E" not in str(v) else format(v, "f")


@given(documents, st.randoms(use_true_random=False), st.sampled_from([2, 3, 4]))
def test_block_documents_round_trip_and_agree_with_pyyaml(doc, rnd, width):
    lines = emit(doc, 0, rnd, width) if doc else []
    text = "\n".join(lines) + ("\n" if lines else "")
    assert load(text) == doc
    if "\t" not in text:
        assert normalise(pyyaml.safe_load(text) or {}) == doc


@given(st.lists(st.text(alphabet="abc xyz.:#-", max_size=8).map(lambda s: s.rstrip()), min_size=1, max_size=6),
       st.sampled_from(["|", "|-", "|+", ">", ">-", ">+"]), st.sampled_from([2, 4]),
       st.integers(0, 2))
def test_block_scalars_agree_with_pyyaml(lines, header, width, trailing_blank):
    assume(lines[0] != "" and not lines[0].startswith(" "))
    assume(all(not ln.startswith(" ") for ln in lines))
    text = f"k: {header}\n" + "".join((" " * width + ln if ln else "") + "\n" for ln in lines) + "\n" * trailing_blank + "z: 1\n"
    assert load(text) == normalise(pyyaml.safe_load(text))


_flow_leaf = st.sampled_from(["a", "b c", "1", "2.50", "true", "null", "'q'", '"w"', "x-y"])
_flow = st.recursive(
    _flow_leaf,
    lambda inner: st.one_of(
        st.lists(inner, max_size=3).map(lambda xs: "[" + ", ".join(xs) + "]"),
        st.lists(st.tuples(st.sampled_from(["k1", "k2", "k3", "'k4'", '"k5"']), inner), max_size=3, unique_by=lambda t: t[0].strip("'\""))
        .map(lambda ps: "{" + ", ".join(f"{k}: {v}" for k, v in ps) + "}"),
    ),
    max_leaves=8,
)


@given(_flow)
def test_flow_collections_agree_with_pyyaml(text):
    doc = f"x: {text}\n"
    assert load(doc) == normalise(pyyaml.safe_load(doc))


_fuzz = st.text(alphabet=list("ab:#-[]{}|>'\" \n,.?&*!%@`019\t\\xyz~=+eE_/"), max_size=60)


@given(_fuzz, st.booleans())
def test_arbitrary_text_gives_a_value_or_a_diagnostic_never_a_crash(text, mapping_only):
    try:
        result = load(text, mapping_only=mapping_only)
    except mdq.MdqError as err:
        assert err.code in ("MDQ_METADATA_ERROR", "MDQ_RESOURCE_LIMIT")
        assert err.line is not None and err.line >= 1 and err.column >= 1
        return
    if mapping_only:
        assert isinstance(result, dict)


@given(_fuzz)
def test_whatever_loads_survives_a_json_round_trip(text):
    try:
        result = load(text, mapping_only=False)
    except mdq.MdqError:
        return
    assume(result is not None)

    def has_ctrl(v):
        if isinstance(v, str):
            return any(ord(c) < 0x20 or 0x7F <= ord(c) <= 0x9F for c in v)
        if isinstance(v, list):
            return any(has_ctrl(x) for x in v)
        if isinstance(v, dict):
            return any(has_ctrl(k) or has_ctrl(x) for k, x in v.items())
        return False

    assume(isinstance(result, (list, dict)))
    assert load(json_text(result), mapping_only=False) == result


@given(documents, st.randoms(use_true_random=False))
def test_a_duplicate_key_is_always_an_error(doc, rnd):
    assume(doc)
    key = rnd.choice(list(doc))
    pos = rnd.randrange(len(doc) + 1)
    items = list(doc.items())
    items.insert(pos, (key, "dup"))
    lines = []
    for k, v in items:
        lines.append(f"{_quote(k)}: {json_text(v)}")
    with pytest.raises(mdq.MdqError) as info:
        load("\n".join(lines) + "\n")
    assert info.value.code == "MDQ_METADATA_ERROR" and "duplicate" in str(info.value)
