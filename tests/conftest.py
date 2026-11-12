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
