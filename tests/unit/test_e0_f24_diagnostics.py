"""Epic 0 / F24: diagnostic codes, error shape, panic safety."""
import pytest

import mdq
from mdq import _internal as mi

SPEC_CODES = {
    "MDQ_UNDEFINED_VARIABLE", "MDQ_MISSING_IMPORT", "MDQ_IMPORT_CYCLE",
    "MDQ_AMBIGUOUS_REFERENCE", "MDQ_UNRESOLVED_REFERENCE", "MDQ_DUPLICATE_ID",
    "MDQ_METADATA_ERROR", "MDQ_INVALID_SYNTAX", "MDQ_UNSUPPORTED_FEATURE",
    "MDQ_SECURITY_DENIED", "MDQ_CALC_ERROR", "MDQ_RESOURCE_LIMIT",
    "MDQ_SOURCE_UNRESOLVED", "MDQ_SOURCE_INVALID", "MDQ_SOURCE_FETCH_FAILED",
    "MDQ_SOURCE_TYPE_ERROR",
}  # extended-markdown.md §11


class TestCodes:
    @pytest.mark.spec("extended-markdown §11")
    def test_codes_match_spec_exactly(self):
        codes = mi.diagnostic_codes()
        assert len(codes) == len(set(codes)) == 16
        assert set(codes) == SPEC_CODES


class TestErrorShape:
    def _raise(self):
        mi.check_required([], "9.9")

    def test_is_exception_with_attributes(self):
        with pytest.raises(mdq.MdqError) as info:
            self._raise()
        err = info.value
        assert isinstance(err, Exception)
        assert err.code == "MDQ_UNSUPPORTED_FEATURE"
        assert str(err)
        # No location for a non-source error: attributes exist and are None.
        assert (err.path, err.line, err.column) == (None, None, None)

    def test_helper_rejects_wrong_code(self, assert_mdq_error):
        with pytest.raises(AssertionError):
            with assert_mdq_error("MDQ_CALC_ERROR"):
                self._raise()

    def test_helper_fails_when_nothing_raised(self, assert_mdq_error):
        with pytest.raises(pytest.fail.Exception):
            with assert_mdq_error("MDQ_CALC_ERROR"):
                pass


class TestPanicSafety:
    def test_panic_is_loud_base_exception(self):
        with pytest.raises(BaseException) as info:
            mi._panic_probe()
        assert type(info.value).__name__ == "PanicException"
        assert not isinstance(info.value, Exception)  # `except Exception` can't hide it

    def test_helper_does_not_swallow_panics(self, assert_mdq_error):
        with pytest.raises(BaseException) as info:
            with assert_mdq_error("MDQ_CALC_ERROR"):
                mi._panic_probe()
        assert type(info.value).__name__ == "PanicException"

    def test_library_still_works_after_a_panic(self):
        with pytest.raises(BaseException):
            mi._panic_probe()
        assert mdq.version_info()["syntax"] == "0.1"
