"""Epic 1 / E1.6 (F16): currency observation selection (spec 8.8).

Hooks: `fx_parse_instant(text) -> (utc_seconds, nanos)`,
`fx_select(observations, from, to, valuation=None, id=None, market=None)`,
and the `observations / valuation / market / pins` arguments of `calc_eval`.
Observation records are dicts of strings: from, to, rate, observed_at,
provider, optional market and id. Malformed host data is MDQ_SOURCE_INVALID;
a missing or ambiguous observation is MDQ_SOURCE_UNRESOLVED.
Decisions where the spec is silent: G6 (timestamps need an explicit offset,
`EUR to EUR` is the identity) and G20 (market is a request requirement, id wins
over valuation, id schema is a unique string).
"""
import random

import pytest
from hypothesis import given, strategies as st

import mdq
from mdq import _internal as mi
from oracles import fx_oracle

pytestmark = pytest.mark.unit


def ob(frm="EUR", to="USD", rate="1.1", at="2026-01-01T00:00:00Z", provider="ecb", **extra):
    rec = {"from": frm, "to": to, "rate": rate, "observed_at": at, "provider": provider}
    rec.update(extra)
    return rec


T0, T1, T2 = "2026-01-01T00:00:00Z", "2026-01-01T00:10:00Z", "2026-01-01T00:20:00Z"
THREE = [ob(rate="1.1", at=T0), ob(rate="1.2", at=T1), ob(rate="1.3", at=T2)]


def rate_at(valuation, observations=THREE):
    return mi.fx_select(observations, "EUR", "USD", valuation=valuation)["rate"]


# ----------------------------------------------------------- timestamps


@pytest.mark.parametrize("text, expected", [
    ("1970-01-01T00:00:00Z", (0, 0)),
    ("1970-01-01T00:00:01Z", (1, 0)),
    ("1969-12-31T23:59:59Z", (-1, 0)),
    ("2000-01-01T00:00:00Z", (946684800, 0)),
    ("2000-01-01T01:00:00+01:00", (946684800, 0)),
    ("1999-12-31T23:00:00-01:00", (946684800, 0)),
    ("2000-01-01T05:30:00+05:30", (946684800, 0)),
    ("2000-01-01T00:00:00.5Z", (946684800, 500_000_000)),
    ("2000-01-01T00:00:00.123456789Z", (946684800, 123_456_789)),
    ("2000-01-01T00:00:00.000000001Z", (946684800, 1)),
    ("2024-02-29T12:00:00Z", (1709208000, 0)),            # leap day
    ("2000-02-29T00:00:00Z", (951782400, 0)),             # divisible by 400
    ("9999-12-31T23:59:59Z", (253402300799, 0)),
    ("0001-01-01T00:00:00Z", (-62135596800, 0)),
    ("2000-01-01T00:00:00+23:59", (946684800 - 86340, 0)),
])
def test_valid_timestamps(text, expected):
    assert mi.fx_parse_instant(text) == expected
    assert fx_oracle.instant(text) == expected


@pytest.mark.parametrize("text", [
    "2024-01-01T00:00:00",              # no offset: never assumed UTC (G6)
    "2024-01-01", "2024-01-01T00:00Z", "2024-01-01 00:00:00Z", "2024-01-01t00:00:00Z",
    "2024-01-01T00:00:00z", "2023-02-29T00:00:00Z", "2100-02-29T00:00:00Z",
    "2024-02-30T00:00:00Z", "2024-04-31T00:00:00Z", "2024-13-01T00:00:00Z",
    "2024-00-10T00:00:00Z", "2024-01-00T00:00:00Z", "2024-01-01T24:00:00Z",
    "2024-01-01T00:60:00Z", "2024-01-01T00:00:60Z",   # no leap seconds
    "2024-01-01T00:00:00.Z", "2024-01-01T00:00:00.1234567890Z",
    "2024-01-01T00:00:00+0100", "2024-01-01T00:00:00+01", "2024-01-01T00:00:00+24:00",
    "2024-01-01T00:00:00+01:60", "2024-01-01T00:00:00 +01:00", "0000-01-01T00:00:00Z",
    "2024-1-1T0:0:0Z", " 2024-01-01T00:00:00Z", "2024-01-01T00:00:00Z ", "2024-01-01T00:00:00ZZ",
    "２０２４-01-01T00:00:00Z", "", "Z", "now", "2024-01-01T00:00:00+01:00Z",
])
def test_invalid_timestamps(assert_mdq_error, text):
    with assert_mdq_error("MDQ_SOURCE_INVALID"):
        mi.fx_parse_instant(text)


def test_timestamp_type_error():
    with pytest.raises(TypeError):
        mi.fx_parse_instant(None)


_ts_strategy = st.builds(
    lambda y, mo, d, h, mi_, s, frac, tz: (
        f"{y:04d}-{mo:02d}-{d:02d}T{h:02d}:{mi_:02d}:{s:02d}"
        + (f".{frac}" if frac else "") + tz
    ),
    st.integers(1970, 2100), st.integers(1, 12), st.integers(1, 28), st.integers(0, 23),
    st.integers(0, 59), st.integers(0, 59),
    st.one_of(st.just(""), st.text("0123456789", min_size=1, max_size=9)),
    st.one_of(
        st.just("Z"),
        st.builds(lambda sg, h, m: f"{sg}{h:02d}:{m:02d}", st.sampled_from("+-"), st.integers(0, 14), st.integers(0, 59)),
    ),
)


@given(_ts_strategy)
def test_timestamps_match_the_datetime_oracle(text):
    assert mi.fx_parse_instant(text) == fx_oracle.instant(text)


# ------------------------------------------------------------ selection


def test_latest_observation_at_or_before_the_valuation():
    assert rate_at("2026-01-01T00:05:00Z") == "1.1"
    assert rate_at(T1) == "1.2"                          # exactly at the valuation counts
    assert rate_at("2026-01-01T00:09:59Z") == "1.1"      # one second earlier excludes it
    assert rate_at("2026-01-01T00:10:01Z") == "1.2"
    assert rate_at("2030-01-01T00:00:00Z") == "1.3"      # after all: the last one


def test_no_observation_at_or_before_the_valuation(assert_mdq_error):
    with assert_mdq_error("MDQ_SOURCE_UNRESOLVED") as info:
        rate_at("2025-12-31T23:59:59Z")
    assert "at or before 2025-12-31T23:59:59Z" in str(info.value)


def test_valuation_and_observations_may_use_different_offsets():
    assert rate_at("2026-01-01T01:10:00+01:00") == "1.2"   # same instant as T1
    assert rate_at("2025-12-31T19:10:00-05:00") == "1.2"
    assert mi.fx_select([ob(at="2026-01-01T01:00:00+01:00")], "EUR", "USD", valuation=T0)["rate"] == "1.1"


def test_fractional_seconds_are_compared_exactly():
    o = [ob(at="2026-01-01T00:00:00.500Z")]
    with pytest.raises(mdq.MdqError):
        mi.fx_select(o, "EUR", "USD", valuation="2026-01-01T00:00:00Z")
    assert mi.fx_select(o, "EUR", "USD", valuation="2026-01-01T00:00:00.5Z")["rate"] == "1.1"
    assert mi.fx_select(o, "EUR", "USD", valuation="2026-01-01T00:00:00.500000001Z")["rate"] == "1.1"


def test_selection_returns_the_whole_record():
    rec = ob(rate="1.0834", at=T1, provider="ecb", market="spot", id="a1")
    assert mi.fx_select([rec], "EUR", "USD", valuation=T2) == {
        "id": "a1", "from": "EUR", "to": "USD", "rate": "1.0834",
        "observed_at": T1, "provider": "ecb", "market": "spot",
    }
    assert mi.fx_select([ob()], "EUR", "USD", valuation=T0)["id"] is None
    assert mi.fx_select([ob()], "EUR", "USD", valuation=T0)["market"] is None


def test_rate_is_returned_canonically():
    assert mi.fx_select([ob(rate="01.500")], "EUR", "USD", valuation=T0)["rate"] == "1.5"
    assert mi.fx_select([ob(rate=".25")], "EUR", "USD", valuation=T0)["rate"] == "0.25"


def test_explicit_observation_id():
    obs = [ob(rate="1.1", at=T0, id="old"), ob(rate="1.3", at=T2, id="new")]
    assert mi.fx_select(obs, "EUR", "USD", id="old")["rate"] == "1.1"
    assert mi.fx_select(obs, "EUR", "USD", id="new")["rate"] == "1.3"
    # the id wins over a valuation, and is not bounded by it
    assert mi.fx_select(obs, "EUR", "USD", valuation=T0, id="new")["rate"] == "1.3"


def test_unknown_id_or_id_for_another_pair_is_unresolved(assert_mdq_error):
    obs = [ob(id="a"), ob(frm="USD", to="EUR", id="b")]
    for bad in ("zzz", "b"):
        with assert_mdq_error("MDQ_SOURCE_UNRESOLVED") as info:
            mi.fx_select(obs, "EUR", "USD", id=bad)
        assert f'"{bad}"' in str(info.value)


def test_equal_timestamps_are_ambiguous_without_an_id(assert_mdq_error):
    obs = [ob(rate="1.1", provider="a", id="x"), ob(rate="1.2", provider="b", id="y")]
    with assert_mdq_error("MDQ_SOURCE_UNRESOLVED") as info:
        mi.fx_select(obs, "EUR", "USD", valuation=T0)
    assert "specify an observation id" in str(info.value) and "2" in str(info.value)
    assert mi.fx_select(obs, "EUR", "USD", id="y")["rate"] == "1.2"


def test_a_tie_only_matters_at_the_latest_eligible_timestamp():
    obs = [ob(rate="1.1", at=T0, provider="a"), ob(rate="1.2", at=T0, provider="b"), ob(rate="1.3", at=T1)]
    assert rate_at(T1, obs) == "1.3"                    # earlier tie is irrelevant
    with pytest.raises(mdq.MdqError):
        rate_at(T0, obs)


def test_ties_across_different_offsets_are_still_ties(assert_mdq_error):
    obs = [ob(at="2026-01-01T00:00:00Z", provider="a"), ob(at="2026-01-01T01:00:00+01:00", provider="b")]
    with assert_mdq_error("MDQ_SOURCE_UNRESOLVED"):
        mi.fx_select(obs, "EUR", "USD", valuation=T0)


def test_market_is_a_requirement_of_the_request(assert_mdq_error):
    obs = [ob(rate="1.1", market="spot"), ob(rate="1.2", market="fwd"), ob(rate="1.3")]
    with assert_mdq_error("MDQ_SOURCE_UNRESOLVED"):        # three observations tie
        mi.fx_select(obs, "EUR", "USD", valuation=T0)
    assert mi.fx_select(obs, "EUR", "USD", valuation=T0, market="fwd")["rate"] == "1.2"
    assert mi.fx_select(obs, "EUR", "USD", valuation=T0, market="spot")["rate"] == "1.1"
    with assert_mdq_error("MDQ_SOURCE_UNRESOLVED"):        # nothing in that market
        mi.fx_select(obs, "EUR", "USD", valuation=T0, market="nope")


def test_market_also_filters_an_id_request(assert_mdq_error):
    obs = [ob(id="a", market="spot")]
    assert mi.fx_select(obs, "EUR", "USD", id="a", market="spot")["id"] == "a"
    with assert_mdq_error("MDQ_SOURCE_UNRESOLVED"):
        mi.fx_select(obs, "EUR", "USD", id="a", market="fwd")


def test_no_inversion_and_no_triangulation(assert_mdq_error):
    with assert_mdq_error("MDQ_SOURCE_UNRESOLVED"):
        mi.fx_select([ob(frm="USD", to="EUR")], "EUR", "USD", valuation=T0)
    legs = [ob(frm="EUR", to="USD"), ob(frm="USD", to="GBP")]
    with assert_mdq_error("MDQ_SOURCE_UNRESOLVED"):
        mi.fx_select(legs, "EUR", "GBP", valuation=T0)


def test_no_request_means_unresolved(assert_mdq_error):
    with assert_mdq_error("MDQ_SOURCE_UNRESOLVED") as info:
        mi.fx_select(THREE, "EUR", "USD")
    assert "no valuation timestamp or observation id" in str(info.value)


def test_empty_observation_list(assert_mdq_error):
    with assert_mdq_error("MDQ_SOURCE_UNRESOLVED"):
        mi.fx_select([], "EUR", "USD", valuation=T0)


# ------------------------------------------------------ invalid input


@pytest.mark.parametrize("record, fragment", [
    ({k: v for k, v in ob().items() if k != "from"}, "`from`"),
    ({k: v for k, v in ob().items() if k != "to"}, "`to`"),
    ({k: v for k, v in ob().items() if k != "rate"}, "`rate`"),
    ({k: v for k, v in ob().items() if k != "observed_at"}, "`observed_at`"),
    ({k: v for k, v in ob().items() if k != "provider"}, "`provider`"),
    (ob(extra="x"), "unknown observation field"),
    (ob(rate="0"), "positive"),
    (ob(rate="0.000"), "positive"),
    (ob(rate="-1"), "positive"),
    (ob(rate="+1"), "positive"),
    (ob(rate="abc"), "invalid rate"),
    (ob(rate="1e3"), "invalid rate"),
    (ob(rate="NaN"), "invalid rate"),
    (ob(rate="1/2"), "invalid rate"),
    (ob(rate="1,5"), "invalid rate"),
    (ob(rate=""), "must not be empty"),
    (ob(frm="eur"), "invalid currency pair"),
    (ob(to="EURO"), "invalid currency pair"),
    (ob(frm="EUR", to="EUR"), "same currency"),
    (ob(provider=""), "must not be empty"),
    (ob(id=""), "must not be empty"),
    (ob(market=""), "must not be empty"),
    (ob(at="2026-01-01T00:00:00"), "invalid timestamp"),
])
def test_invalid_observation_records(assert_mdq_error, record, fragment):
    with assert_mdq_error("MDQ_SOURCE_INVALID") as info:
        mi.fx_select([record], "EUR", "USD", valuation=T0)
    assert fragment in str(info.value)


def test_duplicate_observation_ids_are_invalid(assert_mdq_error):
    with assert_mdq_error("MDQ_SOURCE_INVALID") as info:
        mi.fx_select([ob(id="a"), ob(id="a", frm="USD", to="EUR")], "EUR", "USD", valuation=T0)
    assert "duplicate observation id" in str(info.value)


def test_invalid_records_are_rejected_even_when_not_selected(assert_mdq_error):
    with assert_mdq_error("MDQ_SOURCE_INVALID"):
        mi.fx_select([ob(), ob(frm="GBP", to="JPY", rate="0")], "EUR", "USD", valuation=T0)


def test_invalid_valuation_is_source_invalid(assert_mdq_error):
    with assert_mdq_error("MDQ_SOURCE_INVALID"):
        mi.fx_select(THREE, "EUR", "USD", valuation="2026-01-01")


def test_hook_type_errors():
    with pytest.raises(TypeError):
        mi.fx_select([ob(rate=1.1)], "EUR", "USD", valuation=T0)      # floats are never accepted
    with pytest.raises(TypeError):
        mi.fx_select([ob(rate=1)], "EUR", "USD", valuation=T0)
    with pytest.raises(TypeError):
        mi.fx_select("not a list", "EUR", "USD", valuation=T0)
    with pytest.raises(TypeError):
        mi.fx_select([["from", "EUR"]], "EUR", "USD", valuation=T0)


# ---------------------------------------------------- in calc blocks


EUR_USD = [ob(rate="1.1", at=T0, id="old"), ob(rate="1.2", at=T1, id="new")]


def test_money_conversion_applies_amount_times_rate():
    r = mi.calc_eval("x = 100 EUR to USD", observations=EUR_USD, valuation=T1)[0]
    assert (r["type"], r["value"], r["currency"]) == ("money", "120", "USD")
    assert r["observations"] == [{
        "id": "new", "from": "EUR", "to": "USD", "rate": "1.2",
        "observed_at": T1, "provider": "ecb", "market": None,
    }]


@pytest.mark.parametrize("src, rate, expected", [
    ("1 EUR", "1.0834", "1.0834"),
    ("3 EUR", "0.3333333333333333333333333333", "0.9999999999999999999999999999"),
    ("0 EUR", "1.5", "0"),
    ("-100 EUR", "1.1", "-110"),
    ("0.5 EUR", "2", "1"),
    ("1234567.89 EUR", "0.000001", "1.23456789"),
])
def test_rate_application_and_rounding(src, rate, expected):
    out = mi.calc_eval(f"x = {src} to USD", observations=[ob(rate=rate)], valuation=T0)[0]
    assert out["value"] == expected and out["currency"] == "USD"


def test_provenance_is_reported_per_line():
    src = "a = 100 EUR\nb = a to USD\nc = b + 5 USD"
    out = mi.calc_eval(src, observations=EUR_USD, valuation=T1)
    assert [len(r["observations"]) for r in out] == [0, 1, 0]
    assert [r["value"] for r in out] == ["100", "120", "125"]


def test_several_conversions_on_one_line_are_listed_in_order():
    obs = EUR_USD + [ob(frm="GBP", to="USD", rate="1.5", id="g")]
    out = mi.calc_eval("x = (100 EUR to USD) + (10 GBP to USD)", observations=obs, valuation=T1)[0]
    assert out["value"] == "135"
    assert [o["id"] for o in out["observations"]] == ["new", "g"]


def test_same_currency_is_the_identity_and_needs_no_observation():
    out = mi.calc_eval("x = 5 EUR to EUR")[0]
    assert (out["value"], out["currency"], out["observations"]) == ("5", "EUR", [])


def test_host_money_variable_can_be_converted():
    out = mi.calc_eval("x = m to USD", {"m": "10 EUR"}, observations=EUR_USD, valuation=T0)[0]
    assert out["value"] == "11"


def test_pins_override_the_default_request():
    out = mi.calc_eval(
        "x = 100 EUR to USD", observations=EUR_USD, valuation=T1, pins={"EUR>USD": {"id": "old"}}
    )[0]
    assert out["value"] == "110" and out["observations"][0]["id"] == "old"
    out = mi.calc_eval(
        "x = 100 EUR to USD", observations=EUR_USD, valuation=T1, pins={"EUR>USD": {"valuation": T0}}
    )[0]
    assert out["value"] == "110"


def test_pins_alone_are_a_request_for_that_pair(assert_mdq_error):
    obs = EUR_USD + [ob(frm="GBP", to="USD", rate="1.5")]
    out = mi.calc_eval("x = 10 EUR to USD", observations=obs, pins={"EUR>USD": {"id": "new"}})[0]
    assert out["value"] == "12"
    with assert_mdq_error("MDQ_SOURCE_UNRESOLVED") as info:     # GBP has no request at all
        mi.calc_eval("x = 10 GBP to USD", observations=obs, pins={"EUR>USD": {"id": "new"}})
    assert "no valuation timestamp or observation id" in str(info.value)


def test_default_market():
    obs = [ob(rate="1.1", market="spot"), ob(rate="1.2", market="fwd")]
    out = mi.calc_eval("x = 10 EUR to USD", observations=obs, valuation=T0, market="fwd")[0]
    assert out["value"] == "12"


def test_unresolved_conversions_report_the_position(assert_mdq_error):
    with assert_mdq_error("MDQ_SOURCE_UNRESOLVED", line=1, column=13) as info:
        mi.calc_eval("x = 100 EUR to USD")
    assert "EUR" in str(info.value) and "USD" in str(info.value)
    with assert_mdq_error("MDQ_SOURCE_UNRESOLVED", line=12, column=11):
        mi.calc_eval("a = 1\nb = 1 EUR to USD", None, 11, [], T0)
    with assert_mdq_error("MDQ_SOURCE_UNRESOLVED", line=1, column=13):
        mi.calc_eval("x = 100 EUR to USD", observations=[ob(frm="USD", to="EUR")], valuation=T0)


def test_ambiguity_is_reported_at_the_conversion(assert_mdq_error):
    obs = [ob(provider="a"), ob(provider="b")]
    with assert_mdq_error("MDQ_SOURCE_UNRESOLVED", line=1, column=13) as info:
        mi.calc_eval("x = 100 EUR to USD", observations=obs, valuation=T0)
    assert "specify an observation id" in str(info.value)


def test_a_conversion_does_not_change_physical_errors(assert_mdq_error):
    with assert_mdq_error("MDQ_CALC_ERROR"):
        mi.calc_eval("x = 100 EUR to km", observations=EUR_USD, valuation=T1)
    with assert_mdq_error("MDQ_CALC_ERROR"):
        mi.calc_eval("x = 100 km to USD", observations=EUR_USD, valuation=T1)


@pytest.mark.parametrize("kwargs, fragment", [
    ({"pins": {"EURUSD": {"id": "a"}}}, "invalid pin key"),
    ({"pins": {"eur>usd": {"id": "a"}}}, "invalid pin key"),
    ({"pins": {"EUR>": {"id": "a"}}}, "invalid pin key"),
    ({"pins": {"EUR>USD": {}}}, "needs an `id` or a `valuation`"),
    ({"pins": {"EUR>USD": {"id": "a", "extra": "x"}}}, "unknown pin field"),
    ({"pins": {"EUR>USD": {"valuation": "yesterday"}}}, "invalid timestamp"),
    ({"pins": {"EUR>USD": {"id": ""}}}, "must not be empty"),
    ({"market": "spot"}, "`market` needs a `valuation`"),
    ({"valuation": "2026-01-01"}, "invalid timestamp"),
    ({"observations": [ob(rate="0")]}, "positive"),
])
def test_invalid_fx_arguments_are_rejected_up_front(assert_mdq_error, kwargs, fragment):
    with assert_mdq_error("MDQ_SOURCE_INVALID") as info:
        mi.calc_eval("x = 1", **kwargs)          # even when no conversion happens
    assert fragment in str(info.value)


def test_fx_argument_type_errors():
    with pytest.raises(TypeError):
        mi.calc_eval("x = 1", observations=[ob(rate=1.1)])
    with pytest.raises(TypeError):
        mi.calc_eval("x = 1", valuation=5)
    with pytest.raises(TypeError):
        mi.calc_eval("x = 1", pins={"EUR>USD": {"id": 1}})
    with pytest.raises(TypeError):
        mi.calc_eval("x = 1", pins=["EUR>USD"])


# ---------------------------------------------------------- properties


def stamp(k, style):
    """Minute `k` of 2026-01-01 UTC, written either in UTC or at +01:00."""
    return f"2026-01-01T00:{k * 10:02d}:00Z" if style == 0 else f"2026-01-01T01:{k * 10:02d}:00+01:00"


_pairs = st.sampled_from([("EUR", "USD"), ("USD", "EUR"), ("EUR", "GBP")])
_record = st.builds(
    lambda pair, k, style, rate, market, with_id: dict(
        ob(frm=pair[0], to=pair[1], rate=rate, at=stamp(k, style)),
        **({"market": market} if market else {}),
        **({"_id": True} if with_id else {}),
    ),
    _pairs, st.integers(0, 3), st.integers(0, 1), st.sampled_from(["1.1", "0.9", "2", "1.25"]),
    st.sampled_from([None, "spot", "fwd"]), st.booleans(),
)


def finish(records):
    """Give the records unique ids o0, o1, ... where requested."""
    out = []
    for i, rec in enumerate(records):
        rec = dict(rec)
        if rec.pop("_id", False):
            rec["id"] = f"o{i}"
        out.append(rec)
    return out


_requests = st.tuples(
    st.one_of(st.none(), st.builds(stamp, st.integers(0, 4), st.integers(0, 1))),
    st.sampled_from([None, "o0", "o1", "o3", "zzz"]),
    st.sampled_from([None, "spot", "fwd"]),
)


def actual(observations, valuation, obs_id, market):
    try:
        return mi.fx_select(observations, "EUR", "USD", valuation=valuation, id=obs_id, market=market)
    except mdq.MdqError as e:
        return e.code


def expected(observations, valuation, obs_id, market):
    if market is not None and valuation is None and obs_id is None:
        return "MDQ_SOURCE_INVALID"          # a market alone is not a request (G20)
    try:
        hit = fx_oracle.select(observations, "EUR", "USD", valuation, obs_id, market)
    except fx_oracle.Unresolved:
        return "MDQ_SOURCE_UNRESOLVED"
    return hit


def same(a, b):
    if isinstance(a, str) or isinstance(b, str):
        return a == b
    return all(a[k] == b.get(k) for k in ("id", "from", "to", "observed_at", "provider", "market")) \
        and float(a["rate"]) == float(b["rate"])


@given(st.lists(_record, max_size=7), _requests)
def test_selection_matches_the_oracle(records, request):
    observations = finish(records)
    assert same(actual(observations, *request), expected(observations, *request)), (observations, request)


@given(st.lists(_record, max_size=7), _requests, st.randoms(use_true_random=False))
def test_shuffling_the_observations_never_changes_the_selection(records, request, rnd):
    observations = finish(records)
    shuffled = list(observations)
    rnd.shuffle(shuffled)
    a, b = actual(observations, *request), actual(shuffled, *request)
    assert same(a, b)


@given(st.lists(_record, max_size=6), st.builds(stamp, st.integers(0, 2), st.integers(0, 1)), st.lists(_record, max_size=3))
def test_observations_after_the_valuation_never_change_the_result(records, valuation, extra):
    base = finish(records)
    later = [
        dict(e, observed_at="2026-01-01T00:50:00Z", **{"from": "EUR", "to": "USD"})
        for e in finish(extra)
    ]
    for e in later:
        e.pop("id", None)
    assert same(actual(base, valuation, None, None), actual(base + later, valuation, None, None))
