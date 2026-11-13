"""Epic 8 / E8.1b (F16, F25): data and sources in `compile`/`evaluate`. G51.

* Variable files: a string directly under `variables:` that starts with `./` or `../` and ends in .yaml/.yml/.json
  is read relative to the file that declared it (`inputs`: the root) and must be a mapping; it becomes a namespace.
* Bibliographies: `bibliography:` (path or list), relative to the declaring file; .bib = BibTeX, .json = CSL JSON.
* Declared variables (E6.1): `compile` never fetches. A source variable takes its value from `snapshot=` (E6.5
  entries); a host-supplied one (type, no source) from `inputs`, type-checked. Any declared variable without a value
  is MDQ_SOURCE_UNRESOLVED. Results in `Result.values`.
* FX (E1.6): `observations`, `valuation`, `market`, `pins` for currency conversions in calc.
"""
from decimal import Decimal

import pytest

import mdq
from mdq import _internal as mi

pytestmark = pytest.mark.unit

META, UNSUP, DENIED, SYNTAX, MISSING = ("MDQ_METADATA_ERROR", "MDQ_UNSUPPORTED_FEATURE", "MDQ_SECURITY_DENIED",
                                        "MDQ_INVALID_SYNTAX", "MDQ_MISSING_IMPORT")
CALC, UNRES, BAD, TYPE = "MDQ_CALC_ERROR", "MDQ_SOURCE_UNRESOLVED", "MDQ_SOURCE_INVALID", "MDQ_SOURCE_TYPE_ERROR"
FENCE = "```"
TS = "2026-11-13T12:00:00Z"
URL = "https://example.invalid/stocks/AAPL"
BIB = "@article{knuth84, author={Knuth, Donald}, title={Literate Programming}, year={1984}}\n"
CSL = '[{"id":"doe","type":"book","title":"T","author":[{"family":"Doe","given":"J"}],"issued":{"date-parts":[[2020]]}}]'


def calc(*lines):
    return FENCE + "calc\n" + "".join(l + "\n" for l in lines) + FENCE + "\n"


def md(entry, files, **kw):
    return mdq.compile(entry, files, **kw).markdown


class Recording:
    def __init__(self, files):
        self.files, self.reads = dict(files), []

    def read(self, path):
        self.reads.append(path)
        return self.files.get(path)


# ---------------------------------------------------------------- variable files
def test_yaml_and_json_variable_files():
    files = {
        "d.md": "---\nvariables:\n  project: ./data/project.yaml\n  team: ./data/team.json\n---\n"
                "{{project.name}} ({{project.year}}) by {{team.lead}}; {{team.size}}\n",
        "data/project.yaml": "name: Apollo\nyear: 1969\n",
        "data/team.json": '{"lead": "Ada", "size": 1e1}',
    }
    r = mdq.compile("d.md", files)
    assert r.markdown == "Apollo (1969) by Ada; 10\n"
    assert r.metadata["variables"]["project"] == {"name": "Apollo", "year": Decimal("1969")}
    assert r.metadata["variables"]["team"]["size"] == Decimal("10")


def test_paths_are_relative_to_the_declaring_file():
    files = {
        ".mdq.yaml": "variables:\n  a: ./root.yaml\n",
        "x/.mdq.yaml": "variables:\n  b: ./bx.yaml\n",
        "x/y/d.md": "---\nvariables:\n  c: ../../shared/c.json\n---\n{{a.v}} {{b.v}} {{c.v}} {{e.v}}\n",
        "x/y/d.meta.yaml": "variables:\n  e: ./side.yaml\n",
        "root.yaml": "v: root\n", "x/bx.yaml": "v: dir\n", "shared/c.json": '{"v": "fm"}', "x/y/side.yaml": "v: side\n",
    }
    assert md("x/y/d.md", files) == "root dir fm side\n"


def test_inputs_variable_files_are_relative_to_the_root():
    files = {"sub/d.md": "{{p.v}}\n", "p.yaml": "v: from-root\n"}
    assert md("sub/d.md", files, inputs={"p": "./p.yaml"}) == "from-root\n"


@pytest.mark.parametrize("value", ["data/p.yaml", "./notes.txt", "p.json", "/abs.yaml", ".hidden.yaml", "./p.yaml.bak"])
def test_other_strings_stay_strings(value):
    fs = Recording({"d.md": f"---\nvariables:\n  s: {value}\n---\n[{{{{s}}}}]\n"})
    out = mdq.compile("d.md", fs).markdown
    assert value in out.replace("\\", "") and not any(value.lstrip("./") in r for r in fs.reads if r != "d.md")


def test_variable_file_errors(assert_mdq_error):
    with assert_mdq_error(MISSING, path="d.md"):
        mdq.compile("d.md", {"d.md": "---\nvariables:\n  p: ./nope.yaml\n---\nx\n"})
    with assert_mdq_error(DENIED, path="d.md"):
        mdq.compile("d.md", {"d.md": "---\nvariables:\n  p: ../outside.yaml\n---\nx\n"})
    with assert_mdq_error(META, path="p.yaml"):
        mdq.compile("d.md", {"d.md": "---\nvariables:\n  p: ./p.yaml\n---\nx\n", "p.yaml": "- a\n- b\n"})
    with assert_mdq_error(META, path="p.yaml", line=2):
        mdq.compile("d.md", {"d.md": "---\nvariables:\n  p: ./p.yaml\n---\nx\n", "p.yaml": "a: 1\na: 2\n"})
    with pytest.raises(mdq.MdqError) as e:
        mdq.compile("d.md", {"d.md": "---\nvariables:\n  p: ./p.json\n---\nx\n", "p.json": "{bad json"})
    assert e.value.path == "p.json"
    with assert_mdq_error(SYNTAX, path="p.yaml"):
        mdq.compile("d.md", {"d.md": "---\nvariables:\n  p: ./p.yaml\n---\nx\n", "p.yaml": b"a: \xff\n"})


def test_variable_files_are_data_not_declarations_and_not_followed():
    files = {"d.md": "---\nvariables:\n  p: ./p.yaml\n---\n{{p.type}} {{p.source}} {{p.next}}\n",
             "p.yaml": "type: internal\nsource: lab\nnext: ./other.yaml\n"}
    fs = Recording(files)
    assert mdq.compile("d.md", fs).markdown == "internal lab ./other.yaml\n"
    assert "other.yaml" not in fs.reads


def test_calc_does_not_see_inside_namespaces(assert_mdq_error):
    files = {"d.md": "---\nvariables:\n  p: ./p.yaml\n---\n" + calc("x = rate * 2"), "p.yaml": "rate: 3\n"}
    with assert_mdq_error(CALC):
        mdq.compile("d.md", files)


def test_excluded_imports_still_never_read_with_data():
    fs = Recording({"d.md": "---\nvariables:\n  p: ./p.yaml\n---\n:::if {var=\"p.on\" equals=\"yes\"}\n![[secret.md]]\n:::\n\nok\n",
                    "p.yaml": "on: no\n", "secret.md": "s\n"})
    assert mdq.compile("d.md", fs).markdown == "\nok\n"
    assert "secret.md" not in fs.reads


# ---------------------------------------------------------------- bibliographies
def test_bibtex_and_csl_bibliographies():
    files = {"d.md": "---\nbibliography: refs.bib\n---\nSee [@knuth84].\n", "refs.bib": BIB}
    out = md("d.md", files)
    assert "(Knuth 1984)" in out and "## References" in out and "[@" not in out
    files = {"d.md": "---\nbibliography: [refs.bib, more/csl.json]\n---\nSee [@knuth84] and [@doe].\n",
             "refs.bib": BIB, "more/csl.json": CSL}
    out = md("d.md", files)
    assert "(Knuth 1984)" in out and "(Doe 2020)" in out


def test_bibliography_relative_to_declaring_file():
    files = {"x/.mdq.yaml": "bibliography: ./refs.bib\n", "x/d.md": "[@knuth84]\n", "x/refs.bib": BIB}
    assert "(Knuth 1984)" in md("x/d.md", files)


def test_bibliography_errors(assert_mdq_error):
    with assert_mdq_error(MISSING, path="d.md"):
        mdq.compile("d.md", {"d.md": "---\nbibliography: nope.bib\n---\nx\n"})
    with assert_mdq_error(UNSUP, path="d.md"):
        mdq.compile("d.md", {"d.md": "---\nbibliography: refs.ris\n---\nx\n", "refs.ris": "x"})
    with assert_mdq_error(META, path="d.md"):
        mdq.compile("d.md", {"d.md": "---\nbibliography: {a: 1}\n---\nx\n"})
    with assert_mdq_error(DENIED, path="d.md"):
        mdq.compile("d.md", {"d.md": "---\nbibliography: ../r.bib\n---\nx\n"})
    with pytest.raises(mdq.MdqError) as e:
        mdq.compile("d.md", {"d.md": "---\nbibliography: r.bib\n---\nx\n", "r.bib": "@article{broken"})
    assert e.value.path == "r.bib"


def test_missing_citation_key_without_bibliography(assert_mdq_error):
    with assert_mdq_error("MDQ_UNRESOLVED_REFERENCE", path="d.md", line=1):
        mdq.compile("d.md", {"d.md": "See [@nobody].\n"})


# ---------------------------------------------------------------- declared variables: snapshots
DECL = ("variables:\n  price:\n    type: number\n    unit: USD\n    source:\n      kind: http\n"
        f"      url: '{URL}'\n      format: json\n      select: /price\n")


def record(fake_transport, meta=DECL, name="price", body=b'{"price": 1.50}'):
    """Make a snapshot entry the way a host would: one live fetch through the E6.5 resolver."""
    t = fake_transport({URL: fake_transport.reply(body=body)})
    layer = {"network": "allow"}
    return mi.source_fetch(meta, name, mi.policy_intersect(layer, layer), t, TS)


def test_source_variable_from_a_snapshot(fake_transport):
    entry = record(fake_transport)
    doc = "---\n" + DECL + "---\nPrice: {{price}}\n\n" + calc("total = price * 3") + "Total: {{calc.total}}\n"
    r = mdq.compile("d.md", {"d.md": doc}, snapshot=[entry])
    assert r.markdown == "Price: 1.5 USD\n\nTotal: 4.5 USD\n"
    v = r.values[0]
    assert isinstance(v, mdq.DeclaredValue)
    assert (v.name, v.type, v.value, v.unit, v.retrieved_at) == ("price", "number", Decimal("1.5"), "USD", TS)
    assert v.source["kind"] == "http" and v.source["key"] == entry["source"]["key"]


def test_compile_never_fetches(fake_transport, assert_mdq_error):
    # there is no transport parameter at all: without a snapshot the value is unresolved
    doc = "---\n" + DECL + "---\n{{price}}\n"
    with assert_mdq_error(UNRES):
        mdq.compile("d.md", {"d.md": doc})
    with assert_mdq_error(UNRES):  # even when the text never uses it
        mdq.compile("d.md", {"d.md": "---\n" + DECL + "---\nno use\n"})
    with assert_mdq_error(UNRES):
        mdq.evaluate("d.md", {"d.md": doc})


def test_snapshot_must_match_the_declaration(fake_transport, assert_mdq_error):
    entry = record(fake_transport)
    changed = DECL.replace("select: /price", "select: /other")
    with assert_mdq_error(UNRES):
        mdq.compile("d.md", {"d.md": "---\n" + changed + "---\nx\n"}, snapshot=[entry])
    with assert_mdq_error(BAD):  # an entry for a variable that is not declared
        mdq.compile("d.md", {"d.md": "x\n"}, snapshot=[entry])
    with assert_mdq_error(TYPE):  # float values are refused
        mdq.compile("d.md", {"d.md": "---\n" + DECL + "---\nx\n"}, snapshot=[{**entry, "value": 1.5}])
    with pytest.raises(TypeError):
        mdq.compile("d.md", {"d.md": "x\n"}, snapshot={"price": 1})


def test_input_for_a_source_variable_is_refused(fake_transport, assert_mdq_error):
    with assert_mdq_error(BAD):
        mdq.compile("d.md", {"d.md": "---\n" + DECL + "---\nx\n"}, snapshot=[record(fake_transport)],
                    inputs={"price": Decimal("9")})


# ---------------------------------------------------------------- declared variables: host-supplied
HOST = ("variables:\n  qty:\n    type: number\n  label:\n    type: string\n  ok:\n    type: boolean\n"
        "  day:\n    type: date\n  cost:\n    type: number\n    unit: EUR\n  dist:\n    type: number\n    unit: km\n")


def test_host_supplied_values_from_inputs():
    doc = "---\n" + HOST + "---\n{{qty}} {{label}} {{ok}} {{day}} {{cost}} {{dist}}\n\n" + calc("t = cost * qty", "m = dist to m") + \
        "t: {{calc.t}} m: {{calc.m}}\n"
    inputs = {"qty": 4, "label": "Hi", "ok": True, "day": "2026-11-13", "cost": Decimal("2.5"), "dist": 3}
    r = mdq.compile("d.md", {"d.md": doc}, inputs=inputs)
    assert r.markdown == "4 Hi true 2026-11-13 2.5 EUR 3 km\n\nt: 10 EUR m: 3000 m\n"
    assert [(v.name, v.value, v.unit, v.source) for v in r.values] == [
        ("qty", Decimal("4"), None, None), ("label", "Hi", None, None), ("ok", True, None, None),
        ("day", "2026-11-13", None, None), ("cost", Decimal("2.5"), "EUR", None), ("dist", Decimal("3"), "km", None)]
    # the declaration is not overwritten in the metadata layers: provenance has no <inputs> entry for them
    assert not any(layer == "<inputs>" for _, layer in r.provenance)


@pytest.mark.parametrize("inputs,code", [
    ({"qty": "4"}, TYPE), ({"qty": True}, TYPE), ({"label": 5}, TYPE), ({"ok": "true"}, TYPE),
    ({"day": "2026-02-30"}, TYPE), ({"day": "13/11/2026"}, TYPE),
])
def test_host_supplied_values_are_type_checked(assert_mdq_error, inputs, code):
    full = {"qty": 1, "label": "x", "ok": False, "day": "2026-01-01", "cost": 1, "dist": 1, **inputs}
    with assert_mdq_error(code):
        mdq.compile("d.md", {"d.md": "---\n" + HOST + "---\nx\n"}, inputs=full)


def test_missing_host_value(assert_mdq_error):
    with assert_mdq_error(UNRES) as info:
        mdq.compile("d.md", {"d.md": "---\nvariables:\n  qty:\n    type: number\n---\nx\n"})
    assert "qty" in str(info.value)


def test_nested_declarations():
    doc = "---\nvariables:\n  shop:\n    name: Corner\n    qty:\n      type: number\n---\n{{shop.name}} {{shop.qty}}\n"
    assert md("d.md", {"d.md": doc}, inputs={"shop": {"qty": 7}}) == "Corner 7\n"


def test_unknown_unit_is_refused(assert_mdq_error):
    doc = "---\nvariables:\n  n:\n    type: number\n    unit: widgets\n---\nx\n"
    with assert_mdq_error(BAD):
        mdq.compile("d.md", {"d.md": doc}, inputs={"n": 1})


def test_declaration_errors_are_located(assert_mdq_error):
    with assert_mdq_error(BAD, path="x/.mdq.yaml"):
        mdq.compile("x/d.md", {"x/d.md": "x\n", "x/.mdq.yaml": "variables:\n  p:\n    type: complex\n"})


# ---------------------------------------------------------------- FX observations
OBS = [{"id": "o1", "from": "EUR", "to": "USD", "rate": "1.1", "observed_at": "2026-11-12T00:00:00Z", "provider": "test"}]


def test_currency_conversion_with_observations(assert_mdq_error):
    doc = "---\nvariables:\n  price: 100 EUR\n---\n" + calc("usd = price to USD") + "v: {{calc.usd}}\n"
    with assert_mdq_error(UNRES):
        mdq.compile("d.md", {"d.md": doc})
    assert md("d.md", {"d.md": doc}, observations=OBS, valuation="2026-11-13T00:00:00Z") == "v: 110 USD\n"
    assert md("d.md", {"d.md": doc}, observations=OBS, pins={"EUR>USD": {"id": "o1"}}) == "v: 110 USD\n"
    with assert_mdq_error(BAD):
        mdq.compile("d.md", {"d.md": doc}, observations=[{**OBS[0], "rate": "-1"}], valuation="2026-11-13T00:00:00Z")
    ev = mdq.evaluate("d.md", {"d.md": doc}, observations=OBS, valuation="2026-11-13T00:00:00Z")
    assert ev.calc[0].results[0].value == Decimal("110")
