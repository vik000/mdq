import contextlib
import os

import pytest
from hypothesis import HealthCheck, settings

import mdq


# Hypothesis profiles: `dev` is quick; CI sets HYPOTHESIS_PROFILE=ci (more examples, reproducible).
settings.register_profile("dev", max_examples=100, deadline=None)
settings.register_profile(
    "ci", max_examples=1000, deadline=None, derandomize=True,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "dev"))


@pytest.fixture
def assert_mdq_error():
    """`with assert_mdq_error("MDQ_CALC_ERROR", line=3):` asserts code and location.

    Only catches `mdq.MdqError`. A Rust panic (`PanicException`, a BaseException)
    is NOT caught, so it always fails the test.
    """

    @contextlib.contextmanager
    def _ctx(code, *, path=None, line=None, column=None):
        with pytest.raises(mdq.MdqError) as info:
            yield info
        err = info.value
        assert err.code == code, f"expected {code}, got {err.code}: {err}"
        assert str(err), "diagnostic message must not be empty"
        if path is not None:
            assert err.path == path
        if line is not None:
            assert err.line == line
        if column is not None:
            assert err.column == column

    return _ctx


class _FakeTransport:
    """Scripted transport for the resolver (E6.5). `routes` maps a URL to a response dict, a callable(request)
    returning one, or an exception to raise; `dns` maps a host to a list of addresses (or an exception).
    Every request is recorded (deep-copied) in `calls`."""

    def __init__(self, routes=None, dns=None, default_ip="93.184.216.34"):
        self.routes = dict(routes or {})
        self.dns = dict(dns or {})
        self.default_ip = default_ip
        self.calls = []

    @staticmethod
    def reply(status=200, body=b"", headers=None, peer=None, elapsed_ms=0):
        r = {"status": status, "headers": dict(headers or {}), "body": body, "elapsed_ms": elapsed_ms}
        if peer is not None:
            r["peer"] = peer
        return r

    @property
    def gets(self):
        return [c for c in self.calls if c["op"] == "get"]

    @property
    def resolves(self):
        return [c for c in self.calls if c["op"] == "resolve"]

    def __call__(self, req):
        import copy
        self.calls.append(copy.deepcopy(req))
        if req["op"] == "resolve":
            a = self.dns.get(req["host"], [self.default_ip])
            if isinstance(a, BaseException):
                raise a
            return {"addresses": list(a(req) if callable(a) else a)}
        r = self.routes.get(req["url"])
        if r is None:
            raise ConnectionError("no route for " + req["url"])
        if isinstance(r, BaseException):
            raise r
        r = r(req) if callable(r) else dict(r)
        r.setdefault("peer", req["address"])
        return r


@pytest.fixture
def fake_transport():
    return _FakeTransport
