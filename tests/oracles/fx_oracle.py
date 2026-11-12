"""Independent FX oracle (tests only): timestamps via `datetime`, selection by
plain list filtering, written from spec section 8.8 and the decisions in
docs/TEST_PLAN.md (G6, G20)."""
import re
from datetime import datetime, timedelta, timezone

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_TS = re.compile(
    r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})", re.ASCII
)


class Invalid(Exception):
    pass


class Unresolved(Exception):
    pass


def instant(text):
    """-> (UTC seconds since the epoch, nanoseconds). Raises Invalid."""
    m = _TS.fullmatch(text)
    if not m:
        raise Invalid(text)
    y, mo, d, h, mi, s = (int(m.group(i)) for i in range(1, 7))
    frac, off = m.group(7), m.group(8)
    if off == "Z":
        tz = timezone.utc
    else:
        oh, om = int(off[1:3]), int(off[4:6])
        if oh > 23 or om > 59:
            raise Invalid(text)
        delta = timedelta(hours=oh, minutes=om)
        tz = timezone(-delta if off[0] == "-" else delta)
    try:
        dt = datetime(y, mo, d, h, mi, s, tzinfo=tz)
    except ValueError:
        raise Invalid(text)
    secs = (dt - EPOCH) // timedelta(seconds=1)
    nanos = int(frac.ljust(9, "0")) if frac else 0
    return secs, nanos


def select(observations, from_c, to_c, valuation=None, obs_id=None, market=None):
    """The matching observation dict, or raises Unresolved."""
    pool = [
        o for o in observations
        if o["from"] == from_c and o["to"] == to_c and (market is None or o.get("market") == market)
    ]
    if obs_id is not None:
        hits = [o for o in pool if o.get("id") == obs_id]
        if not hits:
            raise Unresolved("id")
        return hits[0]
    if valuation is None:
        raise Unresolved("no request")
    limit = instant(valuation)
    eligible = [o for o in pool if instant(o["observed_at"]) <= limit]
    if not eligible:
        raise Unresolved("none")
    best = max(instant(o["observed_at"]) for o in eligible)
    tied = [o for o in eligible if instant(o["observed_at"]) == best]
    if len(tied) > 1:
        raise Unresolved("ambiguous")
    return tied[0]
