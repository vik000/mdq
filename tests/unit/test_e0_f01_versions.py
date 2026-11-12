"""Epic 0 / F01: versions, profiles, required-feature checks."""
import re

import pytest

import mdq
from mdq import _internal as mi

KNOWN = ["core", "rich", "calc", "package"]


class TestGeneral:
    @pytest.mark.spec("extended-markdown §1")
    def test_version_info_values(self):
        info = mdq.version_info()
        assert set(info) == {"package", "spec", "syntax", "manifest", "unit_catalogue"}
        assert info["spec"] == "0.1.0-draft"
        assert info["syntax"] == "0.1"
        assert info["manifest"] == "0.1"
        assert info["unit_catalogue"] == "0.1.0-draft"

    def test_package_version_is_semver_and_matches_dunder(self):
        v = mdq.version_info()["package"]
        assert re.fullmatch(r"\d+\.\d+\.\d+([-+].+)?", v)
        assert mdq.__version__ == v

    def test_supported_profiles_are_known_and_unique(self):
        got = mi.supported_profiles()
        assert isinstance(got, list)
        assert all(isinstance(p, str) for p in got)
        assert len(got) == len(set(got))
        assert set(got) <= set(KNOWN)

    def test_no_requirements_is_ok(self):
        assert mi.check_required([], "0.1") is None

    @pytest.mark.parametrize("profile", KNOWN)
    def test_check_required_agrees_with_supported_profiles(self, profile, assert_mdq_error):
        if profile in mi.supported_profiles():
            assert mi.check_required([profile], "0.1") is None
        else:
            with assert_mdq_error("MDQ_UNSUPPORTED_FEATURE"):
                mi.check_required([profile], "0.1")


class TestEdge:
    def test_profile_names_are_case_sensitive(self, assert_mdq_error):
        with assert_mdq_error("MDQ_UNSUPPORTED_FEATURE"):
            mi.check_required(["Core"], "0.1")

    def test_one_bad_profile_among_many_fails(self, assert_mdq_error):
        with assert_mdq_error("MDQ_UNSUPPORTED_FEATURE"):
            mi.check_required(["bogus"], "0.1")


class TestNegative:
    @pytest.mark.parametrize("version", ["0.2", "1.0", "", "abc", "0"])
    def test_unsupported_version(self, version, assert_mdq_error):
        with assert_mdq_error("MDQ_UNSUPPORTED_FEATURE"):
            mi.check_required([], version)

    def test_unknown_profile_names_the_profile(self, assert_mdq_error):
        with assert_mdq_error("MDQ_UNSUPPORTED_FEATURE") as info:
            mi.check_required(["bogus"], "0.1")
        assert "bogus" in str(info.value)

    def test_string_instead_of_list_is_type_error(self):
        # A bare str must not be silently treated as a list of characters.
        with pytest.raises(TypeError):
            mi.check_required("core", "0.1")

    def test_non_string_version_is_type_error(self):
        with pytest.raises(TypeError):
            mi.check_required([], 0.1)
