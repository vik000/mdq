"""Epic 2 / E2.3 (F03): metadata merge and cascade (spec 5).

`meta_merge(layers)` folds YAML layers `(name, text[, first_line])`, lowest
precedence first, into `{"value": mapping, "provenance": {pointer: layer}}`.
`meta_cascade(defaults, project, directories, frontmatter, sidecar, overrides)`
puts the six sources in spec order (directories outermost first). Rules:
mappings merge recursively; scalars, arrays, null and values of another kind
replace; `null` is a value and stays present (G24); every ancestor `.mdq.yaml`
cascades (G25). Provenance is keyed by RFC 6901 JSON Pointer and names the layer
that supplied each leaf (scalar, null, array or empty mapping).
"""
import itertools
from decimal import Decimal as D

import pytest
from hypothesis import given, strategies as st

import mdq
from mdq import _internal as mi
from oracles.yaml_gen import documents, json_text

pytestmark = pytest.mark.unit


def merged(*layers):
    return mi.meta_merge(list(layers))


def value(*layers):
    return merged(*layers)["value"]


def ordered(v):
    """Structure with key order made visible."""
    if isinstance(v, dict):
        return [(k, ordered(x)) for k, x in v.items()]
    if isinstance(v, list):
        return [ordered(x) for x in v]
    return v


# ---------------------------------------------------------------- basics


def test_no_layers_and_empty_layers():
    assert merged() == {"value": {}, "provenance": {}}
    assert merged(("a", ""), ("b", "# nothing\n"), ("c", "---\n")) == {"value": {}, "provenance": {}}


def test_a_single_layer_is_returned_as_is():
    assert merged(("only", "a: 1\nb: [x]\nc: {d: e}\n")) == {
        "value": {"a": D(1), "b": ["x"], "c": {"d": "e"}},
        "provenance": {"/a": "only", "/b": "only", "/c/d": "only"},
    }


def test_higher_layers_replace_scalars():
    assert merged(("low", "a: 1\nb: 2\n"), ("high", "b: 3\nc: 4\n")) == {
        "value": {"a": D(1), "b": D(3), "c": D(4)},
        "provenance": {"/a": "low", "/b": "high", "/c": "high"},
    }


def test_the_same_value_in_a_higher_layer_still_takes_the_credit():
    assert merged(("low", "a: 1\n"), ("high", "a: 1\n"))["provenance"] == {"/a": "high"}


def test_mappings_merge_recursively_at_any_depth():
    low = "p:\n  q:\n    r: 1\n    s: 2\n  t: 3\nu: 4\n"
    high = "p:\n  q:\n    s: 20\n    v: 5\n  w: 6\n"
    assert merged(("low", low), ("high", high)) == {
        "value": {"p": {"q": {"r": D(1), "s": D(20), "v": D(5)}, "t": D(3), "w": D(6)}, "u": D(4)},
        "provenance": {"/p/q/r": "low", "/p/q/s": "high", "/p/q/v": "high", "/p/t": "low", "/p/w": "high", "/u": "low"},
    }


def test_arrays_replace_in_full_and_are_never_merged():
    out = merged(("low", "a: [1, 2, 3]\nb: [{x: 1, y: 2}]\n"), ("high", "a: [9]\nb: [{x: 5}]\n"))
    assert out["value"] == {"a": [D(9)], "b": [{"x": D(5)}]}
    assert out["provenance"] == {"/a": "high", "/b": "high"}
    assert value(("low", "a: [1, 2]\n"), ("high", "a: []\n")) == {"a": []}


@pytest.mark.parametrize("low, high, expected", [
    ("a: {x: 1}", "a: 5", {"a": D(5)}),                       # mapping replaced by scalar
    ("a: 5", "a: {x: 1}", {"a": {"x": D(1)}}),                # scalar replaced by mapping
    ("a: {x: 1}", "a: [1]", {"a": [D(1)]}),
    ("a: [1]", "a: {x: 1}", {"a": {"x": D(1)}}),
    ("a: {x: 1}", "a: text", {"a": "text"}),
    ("a: text", "a: true", {"a": True}),
    ("a: 1", "a: '1'", {"a": "1"}),
    ("a: [1]", "a: 5", {"a": D(5)}),
])
def test_values_of_another_kind_replace(low, high, expected):
    assert value(("l", low), ("h", high)) == expected


def test_null_replaces_and_stays_present():
    out = merged(("low", "a: 1\nb: {c: 2, d: 3}\n"), ("high", "a: ~\nb: {c: null}\n"))
    assert out["value"] == {"a": None, "b": {"c": None, "d": D(3)}}
    assert out["provenance"] == {"/a": "high", "/b/c": "high", "/b/d": "low"}


def test_null_clears_a_whole_mapping_and_a_higher_layer_can_refill_it():
    assert value(("l", "a: {x: 1, y: 2}"), ("h", "a: null")) == {"a": None}
    assert value(("l", "a: {x: 1}"), ("m", "a: null"), ("h", "a: {z: 3}")) == {"a": {"z": D(3)}}
    assert value(("l", "a: 1"), ("h", "a:\n")) == {"a": None}                      # bare `key:` is null too


def test_an_empty_mapping_changes_nothing_below_it_and_replaces_a_scalar():
    out = merged(("low", "a: {x: 1}\nb: 2\n"), ("high", "a: {}\nb: {}\n"))
    assert out["value"] == {"a": {"x": D(1)}, "b": {}}
    assert out["provenance"] == {"/a/x": "low", "/b": "high"}


def test_an_empty_mapping_leaf_is_attributed_to_the_last_layer_that_set_it():
    assert merged(("l", "a: {}"), ("h", "a: {}"))["provenance"] == {"/a": "h"}
    assert merged(("l", "a: {}"))["provenance"] == {"/a": "l"}


def test_key_order_is_first_appearance_from_the_lowest_layer():
    out = value(("low", "b: 1\na: 2\n"), ("high", "c: 3\na: 20\nd: 4\n"))
    assert list(out) == ["b", "a", "c", "d"]
    assert ordered(value(("l", "m: {y: 1, x: 2}"), ("h", "m: {z: 3, x: 9}"))) == [("m", [("y", D(1)), ("x", D(9)), ("z", D(3))])]


def test_the_same_key_may_repeat_across_layers_but_not_within_one(assert_mdq_error):
    assert value(("a", "k: 1"), ("b", "k: 2")) == {"k": D(2)}
    with assert_mdq_error("MDQ_METADATA_ERROR", path="b", line=2, column=1):
        merged(("a", "k: 1"), ("b", "k: 1\nk: 2\n"))


def test_large_mappings_merge_quickly():
    low = "\n".join(f"k{i}: {i}" for i in range(5000)) + "\n"
    high = "\n".join(f"k{i}: x" for i in range(0, 5000, 2)) + "\nnew: 1\n"
    out = merged(("low", low), ("high", high))
    assert out["value"]["k1"] == D(1) and out["value"]["k2"] == "x" and out["value"]["new"] == D(1)
    assert len(out["provenance"]) == 5001


# ------------------------------------------------------------- provenance


def test_pointers_escape_slash_and_tilde():
    out = merged(("l", '"a/b": 1\n"c~d": {"e/f~g": 2}\n"": 3\n"~/": 4\n'))
    assert out["provenance"] == {"/a~1b": "l", "/c~0d/e~1f~0g": "l", "/": "l", "/~0~1": "l"}


def test_arrays_are_single_leaves_even_when_they_hold_mappings():
    assert merged(("l", "a: [{x: 1}, {y: 2}]\nb: {c: [1]}\n"))["provenance"] == {"/a": "l", "/b/c": "l"}


def test_provenance_in_document_order():
    out = merged(("l", "b: 1\na: {z: 1, y: 2}\n"), ("h", "c: 1\n"))
    assert list(out["provenance"]) == ["/b", "/a/z", "/a/y", "/c"]


def test_a_mapping_hidden_by_a_scalar_and_rebuilt_loses_its_old_leaves():
    out = merged(("l1", "a: {x: 1, y: 1}"), ("l2", "a: 5"), ("l3", "a: {y: 3}"))
    assert out == {"value": {"a": {"y": D(3)}}, "provenance": {"/a/y": "l3"}}


# ------------------------------------------------------------------- errors


def test_a_layer_must_be_a_mapping(assert_mdq_error):
    for text, kind in [("- a\n", "sequence"), ("scalar\n", "string"), ("42", "number")]:
        with assert_mdq_error("MDQ_METADATA_ERROR", path="side.yaml", line=1, column=1) as info:
            merged(("good", "a: 1"), ("side.yaml", text))
        assert kind in str(info.value)


def test_errors_carry_the_layer_name_and_file_lines(assert_mdq_error):
    with assert_mdq_error("MDQ_METADATA_ERROR", path="doc.md", line=12, column=1):
        merged(("proj", "a: 1"), ("doc.md", "x: 1\nx: 2\n", 11))
    with assert_mdq_error("MDQ_METADATA_ERROR", path="doc.md", line=2, column=4):
        merged(("doc.md", "a: [1,\n", 2))


def test_the_first_bad_layer_in_precedence_order_is_reported(assert_mdq_error):
    with assert_mdq_error("MDQ_METADATA_ERROR", path="first"):
        merged(("ok", "a: 1"), ("first", "a: [x"), ("second", "b: [y"))


def test_loader_limits_surface_with_the_layer_name(assert_mdq_error):
    with assert_mdq_error("MDQ_RESOURCE_LIMIT", path="deep"):
        merged(("deep", "x: " + "[" * 200))


@pytest.mark.parametrize("bad", [
    "not a list", None, 5, [("a",)], [("a", "b", 1, 2)], ["a: 1"], [("a", 1)], [(1, "a: 1")], [["a", "a: 1"]],
    [("a", "a: 1", "1")], [("a", None)],
])
def test_layer_shape_type_errors(bad):
    with pytest.raises(TypeError):
        mi.meta_merge(bad)


# ------------------------------------------------------------------ cascade

SOURCES = ["defaults", "project", "dir1", "dir2", "frontmatter", "sidecar", "overrides"]   # lowest first


def cascade_of(present):
    kw = {}
    dirs = []
    for name in SOURCES:
        if name not in present:
            continue
        layer = (name, f"who: {name}\nonly_{name}: 1\n")
        if name.startswith("dir"):
            dirs.append(layer)
        else:
            kw[name] = layer
    if dirs:
        kw["directories"] = dirs
    return mi.meta_cascade(**kw)


@pytest.mark.parametrize("present", [
    subset for r in range(1, len(SOURCES) + 1) for subset in itertools.combinations(SOURCES, r)
])
def test_every_combination_of_sources_resolves_by_precedence(present):
    out = cascade_of(present)
    winner = [s for s in SOURCES if s in present][-1]
    assert out["value"]["who"] == winner
    assert out["provenance"]["/who"] == winner
    for name in present:
        assert out["value"][f"only_{name}"] == D(1) and out["provenance"][f"/only_{name}"] == name
    assert len(out["value"]) == 1 + len(present)


def test_spec_precedence_order_pairwise():
    # invocation > sidecar > frontmatter > nearest directory > outer directory > project > defaults
    order = list(reversed(SOURCES))
    for hi, lo in zip(order, order[1:]):
        assert cascade_of({hi, lo})["value"]["who"] == hi


def test_nothing_at_all_is_an_empty_mapping():
    assert mi.meta_cascade() == {"value": {}, "provenance": {}}
    assert mi.meta_cascade(directories=[]) == {"value": {}, "provenance": {}}


def test_nested_cascade_example_from_the_spec():
    out = mi.meta_cascade(
        defaults=("<defaults>", "mdq: '0.1'\nfeatures: [core]\nrules: {remote_imports: false, undefined_variables: error}\n"),
        project=("/p/.mdq.yaml", "variables: {project: ./data/project.yaml}\nfeatures: [core, rich]\n"),
        directories=[("/p/docs/.mdq.yaml", "rules: {undefined_variables: warn}\n")],
        frontmatter=("/p/docs/a.md", "title: A\nfeatures: [core, rich, calc]\n", 2),
        sidecar=None,
        overrides=("<invocation>", "rules: {undefined_variables: error}\n"),
    )
    assert out["value"] == {
        "mdq": "0.1", "features": ["core", "rich", "calc"],
        "rules": {"remote_imports": False, "undefined_variables": "error"},
        "variables": {"project": "./data/project.yaml"}, "title": "A",
    }
    assert out["provenance"] == {
        "/mdq": "<defaults>", "/features": "/p/docs/a.md", "/rules/remote_imports": "<defaults>",
        "/rules/undefined_variables": "<invocation>", "/variables/project": "/p/.mdq.yaml", "/title": "/p/docs/a.md",
    }


def test_directories_cascade_outermost_first_so_the_nearest_wins():
    dirs = [("outer", "k: outer\no: 1"), ("middle", "k: middle\nm: 1"), ("inner", "k: inner")]
    out = mi.meta_cascade(project=("project", "k: project\np: 1"), directories=dirs)
    assert out["value"] == {"k": "inner", "p": D(1), "o": D(1), "m": D(1)}
    assert out["provenance"]["/k"] == "inner"


def test_cascade_errors_name_the_source(assert_mdq_error):
    with assert_mdq_error("MDQ_METADATA_ERROR", path="a.meta.yaml", line=1, column=1):
        mi.meta_cascade(frontmatter=("a.md", "t: 1", 2), sidecar=("a.meta.yaml", "- list"))


@pytest.mark.parametrize("kwargs", [
    {"defaults": "text"}, {"project": ("p",)}, {"directories": "text"}, {"directories": [("d", 1)]},
    {"frontmatter": ["n", "t"]}, {"sidecar": (1, "t")}, {"overrides": ("o", "t", "1")},
])
def test_cascade_type_errors(kwargs):
    with pytest.raises(TypeError):
        mi.meta_cascade(**kwargs)


# --------------------------------------------------------------- properties


def deep_merge(base, inc):
    if isinstance(base, dict) and isinstance(inc, dict):
        out = dict(base)
        for k, v in inc.items():
            out[k] = deep_merge(out[k], v) if k in out else v
        return out
    return inc


def pointer(path):
    return "".join("/" + k.replace("~", "~0").replace("/", "~1") for k in path)


def leaves(v, path=()):
    if isinstance(v, dict) and v:
        for k, x in v.items():
            yield from leaves(x, path + (k,))
    else:
        yield path, v


def get(doc, path):
    for k in path:
        if not isinstance(doc, dict) or k not in doc:
            return False, None
        doc = doc[k]
    return True, doc


def oracle(layers):
    final = {}
    for _, doc in layers:
        final = deep_merge(final, doc)
    prov = {}
    for path, v in leaves(final):
        if not path:
            continue
        winner = None
        for name, doc in layers:
            found, x = get(doc, path)
            if found and (isinstance(x, dict) == isinstance(v, dict)):
                winner = name
        prov[pointer(path)] = winner
    return final, prov


layer_lists = st.lists(documents, min_size=0, max_size=5)


def as_layers(docs):
    return [(f"l{i}", json_text(d)) for i, d in enumerate(docs)], [(f"l{i}", d) for i, d in enumerate(docs)]


@given(layer_lists)
def test_merge_matches_the_independent_oracle(docs):
    texts, named = as_layers(docs)
    out = mi.meta_merge(texts)
    final, prov = oracle(named)
    assert ordered(out["value"]) == ordered(final)
    assert out["provenance"] == prov


@given(layer_lists)
def test_merging_in_two_steps_equals_merging_at_once(docs):
    texts, _ = as_layers(docs)
    once = mi.meta_merge(texts)["value"]
    cut = len(texts) // 2
    first = mi.meta_merge(texts[:cut])["value"]
    twice = mi.meta_merge([("first", json_text(first))] + texts[cut:])["value"]
    assert ordered(twice) == ordered(once)


@given(documents)
def test_merging_a_layer_with_itself_or_with_nothing_changes_nothing(doc):
    text = json_text(doc)
    base = mi.meta_merge([("a", text)])["value"]
    assert ordered(mi.meta_merge([("a", text), ("b", text)])["value"]) == ordered(base)
    assert ordered(mi.meta_merge([("a", text), ("b", "{}")])["value"]) == ordered(base)
    assert ordered(mi.meta_merge([("a", "{}"), ("b", text)])["value"]) == ordered(base)


@given(layer_lists, documents)
def test_the_highest_layer_always_wins_at_each_of_its_leaves(docs, top):
    texts, _ = as_layers(docs + [top])
    out = mi.meta_merge(texts)
    for path, v in leaves(top):
        if path and not isinstance(v, dict):                   # an empty mapping never overrides anything
            found, x = get(out["value"], path)
            assert found and x == v
            assert out["provenance"][pointer(path)] == f"l{len(docs)}"
