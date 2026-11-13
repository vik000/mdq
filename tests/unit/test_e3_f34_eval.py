"""Epic 3 / E3.4 (F09): `container_eval` resolves `:::if` and `:::for`.

`container_eval(text, variables, first_line=1, max_iterations=10000)` returns the document with
every `if`/`for` container resolved and every `{{ }}` interpolated (loop variables in scope).
Other containers are kept as written, their bodies evaluated. Excluded branches are never
evaluated (no undefined-variable or syntax errors from them). `if` compares the CANONICAL
string of a scalar variable with the quoted literal (G31); `for` binds the loop variable
lexically; `max_iterations` caps the total number of loop iterations.
"""
from decimal import Decimal as D

import pytest
from hypothesis import given, strategies as st

import mdq
from mdq import _internal as mi

pytestmark = pytest.mark.unit

ev = mi.container_eval


def run(src, **v):
    return ev(src, v)


# ---------------------------------------------------------------- if
def test_if_true_and_false():
    assert run(':::if {var="a" equals="x"}\nyes\n:::\nafter\n', a="x") == "yes\nafter\n"
    assert run(':::if {var="a" equals="x"}\nyes\n:::\nafter\n', a="y") == "after\n"
    assert run("before\n:::if {var=\"a\" equals=\"\"}\nyes\n:::\n", a="") == "before\nyes\n"


@pytest.mark.parametrize("value,literal,hit", [
    (True, "true", True), (False, "false", True), (True, "True", False), (True, "1", False),
    ("true", "true", True),
    (1, "1", True), (D("1.0"), "1", True), (D("1.0"), "1.0", False), (D("-0"), "0", True), (D("0.50"), "0.5", True),
    (10, "10", True), (10, "010", False), (10, "10.0", False),
    (None, "", True), (None, "null", False),
    ("1", "1", True), ("1.0", "1", False),
    ("é", "é", True), ("a b", "a b", True), (" x", "x", False),
])
def test_if_compares_canonical_strings(value, literal, hit):
    out = run(f':::if {{var="v" equals="{literal}"}}\nyes\n:::\n', v=value)
    assert out == ("yes\n" if hit else "")


def test_if_dotted_variable():
    assert run(':::if {var="a.b.c" equals="1"}\nyes\n:::\n', a={"b": {"c": 1}}) == "yes\n"


def test_if_body_is_interpolated_and_nested():
    src = ':::if {var="a" equals="1"}\nx={{a}}\n::::if {var="b" equals="2"}\ny={{b}}\n::::\n:::\n'
    assert run(src, a=1, b=2) == "x=1\ny=2\n"
    assert run(src, a=1, b=3) == "x=1\n"
    assert run(src, a=0, b=2) == ""


def test_excluded_branches_are_never_evaluated():
    src = (':::if {var="a" equals="x"}\n{{nope}} {{ bad\n::::for {each="nope" as="i"}\n::::\n'
           '::::if {var="nope" equals="1"}\n::::\n:::\nok\n')
    assert run(src, a="y") == "ok\n"


def test_included_branch_errors_surface(assert_mdq_error):
    with assert_mdq_error("MDQ_UNDEFINED_VARIABLE", line=2, column=1):
        run(':::if {var="a" equals="x"}\n{{nope}}\n:::\n', a="x")


def test_if_errors(assert_mdq_error):
    with assert_mdq_error("MDQ_UNDEFINED_VARIABLE", line=3, column=1):
        run('a\n\n:::if {var="zz" equals="x"}\n:::\n')
    with assert_mdq_error("MDQ_UNSUPPORTED_FEATURE", line=1, column=1):
        run(':::if {var="l" equals="x"}\n:::\n', l=[1])
    with assert_mdq_error("MDQ_UNSUPPORTED_FEATURE", line=1, column=1):
        run(':::if {var="m" equals="x"}\n:::\n', m={"a": 1})


@pytest.mark.parametrize("opener", [
    ":::if", ':::if {var="a"}', ':::if {equals="x"}', ':::if {var="a" equals="x" extra="y"}',
    ':::if {var="a b" equals="x"}', ':::if {var="" equals="x"}', ':::if {var="1a" equals="x"}',
    ':::if {var="a." equals="x"}', ':::if {var="é" equals="x"}', ':::if {var="a..b" equals="x"}',
])
def test_if_attribute_errors(opener, assert_mdq_error):
    with assert_mdq_error("MDQ_INVALID_SYNTAX", line=1, column=1):
        run(opener + "\n:::\n", a="x")


def test_container_names_are_case_sensitive():
    src = ':::IF {var="a" equals="x"}\n{{a}}\n:::\n'
    assert run(src, a="x") == src.replace("{{a}}", "x")


# ---------------------------------------------------------------- for
def test_for_in_order_and_empty():
    src = ':::for {each="xs" as="x"}\n- {{x}}\n:::\n'
    assert run(src, xs=["b", "a", "c"]) == "- b\n- a\n- c\n"
    assert run(src, xs=[]) == ""
    assert run("t\n" + src + "u\n", xs=[]) == "t\nu\n"


def test_for_scalars_of_every_type():
    assert run(':::for {each="xs" as="x"}\n[{{x}}]\n:::\n', xs=[1, D("2.50"), True, None, "s"]) == "[1]\n[2.5]\n[true]\n[]\n[s]\n"


def test_for_over_maps_and_dotted_each():
    src = ':::for {each="d.rows" as="r"}\n{{r.name}}={{r.v.w}}\n:::\n'
    assert run(src, d={"rows": [{"name": "a", "v": {"w": 1}}, {"name": "b", "v": {"w": 2}}]}) == "a=1\nb=2\n"


def test_nested_loops_and_inner_lists():
    src = ':::for {each="rows" as="r"}\n{{r.n}}:\n::::for {each="r.items" as="i"}\n- {{i}}\n::::\n:::\n'
    rows = [{"n": "A", "items": ["a1", "a2"]}, {"n": "B", "items": []}, {"n": "C", "items": ["c1"]}]
    assert run(src, rows=rows) == "A:\n- a1\n- a2\nB:\nC:\n- c1\n"


def test_loop_variable_scope_and_shadowing(assert_mdq_error):
    src = ':::for {each="xs" as="x"}\n{{x}}\n:::\n{{x}}\n'
    assert run(src, xs=["i"], x="outer") == "i\nouter\n"
    with assert_mdq_error("MDQ_UNDEFINED_VARIABLE", line=4, column=1):
        run(src, xs=["i"])
    # inner loop shadows the outer loop variable of the same name
    inner = ':::for {each="xs" as="x"}\n{{x}}\n::::for {each="ys" as="x"}\n{{x}}\n::::\n{{x}}\n:::\n'
    assert run(inner, xs=["a", "b"], ys=["1", "2"]) == "a\n1\n2\na\nb\n1\n2\nb\n"
    # a loop variable named like a mapping hides ALL of its dotted children
    with assert_mdq_error("MDQ_UNDEFINED_VARIABLE"):
        run(':::for {each="xs" as="a"}\n{{a.b}}\n:::\n', xs=["s"], a={"b": 1})


def test_if_inside_for_sees_the_loop_variable():
    src = ':::for {each="xs" as="x"}\n::::if {var="x" equals="b"}\nB\n::::\n{{x}}\n:::\n'
    assert run(src, xs=["a", "b", "c"]) == "a\nB\nb\nc\n"


def test_for_errors(assert_mdq_error):
    with assert_mdq_error("MDQ_METADATA_ERROR", line=1, column=1):
        run(':::for {each="s" as="x"}\n:::\n', s="text")
    with assert_mdq_error("MDQ_METADATA_ERROR"):
        run(':::for {each="m" as="x"}\n:::\n', m={"a": 1})
    with assert_mdq_error("MDQ_UNDEFINED_VARIABLE"):
        run(':::for {each="nope" as="x"}\n:::\n')
    for opener in (":::for", ':::for {each="xs"}', ':::for {as="x"}', ':::for {each="xs" as="x" k="v"}',
                   ':::for {each="xs" as="a.b"}', ':::for {each="xs" as="1"}', ':::for {each="xs" as=""}',
                   ':::for {each="xs" as="é"}', ':::for {each="xs." as="x"}'):
        with assert_mdq_error("MDQ_INVALID_SYNTAX", line=1, column=1):
            run(opener + "\n:::\n", xs=["a"])


def test_iteration_cap(assert_mdq_error):
    src = ':::for {each="xs" as="x"}\n{{x}}\n:::\n'
    assert ev(src, {"xs": [1, 2, 3]}, 1, 3) == "1\n2\n3\n"
    with assert_mdq_error("MDQ_RESOURCE_LIMIT", line=1, column=1):
        ev(src, {"xs": [1, 2, 3, 4]}, 1, 3)
    with assert_mdq_error("MDQ_RESOURCE_LIMIT"):
        ev(src, {"xs": [1]}, 1, 0)
    # the cap counts every iteration of every loop, nested loops included
    nested = ':::for {each="xs" as="x"}\n::::for {each="xs" as="y"}\n.\n::::\n:::\n'
    assert ev(nested, {"xs": [1, 2]}, 1, 6) == ".\n.\n.\n.\n"
    with assert_mdq_error("MDQ_RESOURCE_LIMIT"):
        ev(nested, {"xs": [1, 2]}, 1, 5)


def test_default_iteration_cap(assert_mdq_error):
    src = ':::for {each="xs" as="x"}\n.\n:::\n'
    assert len(ev(src, {"xs": list(range(10000))}).split("\n")) == 10001
    with assert_mdq_error("MDQ_RESOURCE_LIMIT"):
        ev(src, {"xs": list(range(10001))})


# ---------------------------------------------------------------- other containers and literals
def test_other_containers_are_kept_with_evaluated_bodies():
    src = (':::columns\n::::column {w="1"}\nA {{a}}\n::::\n::::column\n:::::if {var="a" equals="1"}\nB\n:::::\n::::\n'
           ':::\n:::vendor.x {k="{{a}}"}\nbody\n:::\n')
    assert run(src, a=1) == (':::columns\n::::column {w="1"}\nA 1\n::::\n::::column\nB\n::::\n'
                             ':::\n:::vendor.x {k="{{a}}"}\nbody\n:::\n')


def test_code_in_bodies_stays_literal():
    src = ':::for {each="xs" as="x"}\n```\n{{x}} {{nope}}\n```\n`{{x}}` {{x}}\n:::\n'
    assert run(src, xs=["a", "b"]) == "```\n{{x}} {{nope}}\n```\n`{{x}}` a\n```\n{{x}} {{nope}}\n```\n`{{x}}` b\n"


def test_text_outside_and_between_containers_is_interpolated():
    src = "{{a}}\n:::if {var=\"a\" equals=\"1\"}\nin\n:::\nmid {{a}}\n:::for {each=\"xs\" as=\"x\"}\n{{x}}\n:::\nend {{a}}\n"
    assert run(src, a=1, xs=["p"]) == "1\nin\nmid 1\np\nend 1\n"


def test_no_containers_equals_interpolate():
    for src in ("", "plain {{a}}\n", "```\n{{a}}\n```\n", "a\n\n> {{a}}\n"):
        assert ev(src, {"a": "v"}) == mi.interpolate(src, {"a": "v"})


def test_escaping_applies_per_iteration():
    src = ':::for {each="xs" as="x"}\n{{x}}\n:::\n'
    assert run(src, xs=["# h", "- i", "a*b"]) == "\\# h\n\\- i\na\\*b\n"


def test_line_numbers_follow_the_source(assert_mdq_error):
    src = ':::for {each="xs" as="x"}\nz\n:::\n' + "\n" * 9 + "ab {{y}}\n"
    with assert_mdq_error("MDQ_UNDEFINED_VARIABLE", line=13, column=4):
        ev(src, {"xs": [1]}, 1)
    with assert_mdq_error("MDQ_UNDEFINED_VARIABLE", line=22, column=4):
        ev(src, {"xs": [1]}, 10)
    with assert_mdq_error("MDQ_UNDEFINED_VARIABLE", line=3, column=1):
        ev('\n\n:::if {var="y" equals="1"}\n:::\n', {}, 1)


def test_structure_errors_propagate(assert_mdq_error):
    with assert_mdq_error("MDQ_INVALID_SYNTAX", line=1, column=1):
        run(":::if\nunclosed\n")
    with assert_mdq_error("MDQ_INVALID_SYNTAX", line=2, column=1):
        run("a\n:::\n")


def test_type_errors():
    for bad in (b"x", None):
        with pytest.raises(TypeError):
            ev(bad, {})
    with pytest.raises(TypeError):
        ev("x", [])
    with pytest.raises(TypeError):
        ev("x", {}, "1")
    with pytest.raises(TypeError):
        ev("x", {}, 1, "3")
    with pytest.raises(TypeError):
        ev("x", {"f": 1.5})


# ---------------------------------------------------------------- differential against an independent evaluator
VARS = {"flag": True, "n": 2, "s": "x", "items": ["a", "b", "c"], "empty": [], "obj": {"k": "kk", "l": ["p", "q"]}}
GLOBAL_IF = [("flag", "true"), ("flag", "false"), ("n", "2"), ("n", "3"), ("s", "x"), ("s", "y"), ("obj.k", "kk")]
GLOBAL_FOR = ["items", "empty", "obj.l"]


@st.composite
def docs(draw, scope=(), depth=0, colons=3):
    out = []
    for _ in range(draw(st.integers(0, 3))):
        kind = draw(st.integers(0, 4 if depth < 3 else 1))
        if kind == 0:
            out.append(("text", draw(st.sampled_from(["t", "u v", "- li", ""]))))
        elif kind == 1:
            names = ["s", "n", "obj.k"] + [name for name in scope]
            out.append(("var", draw(st.sampled_from(names))))
        elif kind == 2 or kind == 3:
            if kind == 2:
                cand = GLOBAL_IF + [(name, v) for name in scope for v in ("a", "b", "p")]
                var, eq = draw(st.sampled_from(cand))
                out.append(("if", colons, var, eq, draw(docs(scope, depth + 1, colons + 1))))
            else:
                each = draw(st.sampled_from(GLOBAL_FOR))
                nm = draw(st.sampled_from(["x", "y", "s"]))
                out.append(("for", colons, each, nm, draw(docs(scope + (nm,), depth + 1, colons + 1))))
        else:
            out.append(("text", "end"))
    return out


def render(items, out):
    for it in items:
        if it[0] == "text":
            out.append(it[1])
        elif it[0] == "var":
            out.append("{{" + it[1] + "}}")
        elif it[0] == "if":
            out.append(":" * it[1] + 'if {var="%s" equals="%s"}' % (it[2], it[3]))
            render(it[4], out)
            out.append(":" * it[1])
        else:
            out.append(":" * it[1] + 'for {each="%s" as="%s"}' % (it[2], it[3]))
            render(it[4], out)
            out.append(":" * it[1])


def getv(scope, name):
    first, _, rest = name.partition(".")
    if first in scope and name == first:
        return scope[first]
    if first in scope:
        raise KeyError(name)
    cur = VARS
    for seg in name.split("."):
        cur = cur[seg]
    return cur


def canon(v):
    return "true" if v is True else "false" if v is False else str(v)


def oracle(items, scope, out):
    for it in items:
        if it[0] == "text":
            out.append(it[1])
        elif it[0] == "var":
            out.append(canon(getv(scope, it[1])))
        elif it[0] == "if":
            if canon(getv(scope, it[2])) == it[3]:
                oracle(it[4], scope, out)
        else:
            for v in getv(scope, it[2]):
                oracle(it[4], {**scope, it[3]: v}, out)


@given(docs(), st.sampled_from(["\n", "\r\n"]))
def test_differential(items, nl):
    lines = []
    render(items, lines)
    src = nl.join(lines) + (nl if lines else "")
    exp = []
    try:
        oracle(items, {}, exp)
    except KeyError:
        return  # generator referenced a name that is shadowed away: skip
    got = ev(src, VARS)
    want = "".join(x + nl for x in exp)
    assert got == want, (src, got, want)
