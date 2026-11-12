"""Epic 3 / E3.1 (F02): strict UTF-8 decoding of input bytes (spec 1, G28).

`mdq._internal.utf8_decode(data: bytes) -> str`. Malformed UTF-8 is
MDQ_INVALID_SYNTAX naming the byte offset (message) with line/column of the
first bad byte; one leading BOM is dropped; NUL is rejected; line ends are kept.
"""
import re

import pytest
from hypothesis import given, strategies as st

import mdq
from mdq import _internal as mi

pytestmark = pytest.mark.unit

dec = mi.utf8_decode


def offset_of(err):
    return int(re.search(r"byte offset (\d+)", str(err)).group(1))


# ---------------------------------------------------------------- valid


@pytest.mark.parametrize("text", [
    "", "a", "plain ascii\n", "é", "€", "😀", "\U0010ffff", "ࠀ￿￾", "mixed é€😀 text\r\nline\rthree\n",
    "\u2028\u2029\u0085",
])
def test_valid_round_trip(text):
    assert dec(text.encode()) == text


def test_every_utf8_length_and_boundary():
    for cp in (0x7F, 0x80, 0x7FF, 0x800, 0xD7FF, 0xE000, 0xFFFD, 0xFFFF, 0x10000, 0x10FFFF):
        ch = chr(cp)
        assert dec(ch.encode()) == ch


def test_bom_policy():
    assert dec(b"\xef\xbb\xbfabc") == "abc"
    assert dec(b"\xef\xbb\xbf") == ""
    assert dec(b"\xef\xbb\xbf\xef\xbb\xbfabc") == "﻿abc"  # only ONE is dropped
    assert dec(b"abc\xef\xbb\xbf") == "abc﻿"  # not at the start: content


def test_line_ends_are_untouched():
    assert dec(b"a\r\nb\rc\nd") == "a\r\nb\rc\nd"


# ---------------------------------------------------------------- invalid

INVALID = [
    (b"\x80", 0), (b"\xbf", 0), (b"ab\x80cd", 2),                 # lone continuation
    (b"\xc0\x80", 0), (b"\xc1\xbf", 0), (b"a\xc0\xaf", 1),        # overlong 2-byte (incl. '/' as C0 AF)
    (b"\xe0\x80\x80", 0), (b"\xe0\x9f\xbf", 0),                   # overlong 3-byte
    (b"\xf0\x80\x80\x80", 0), (b"\xf0\x8f\xbf\xbf", 0),           # overlong 4-byte
    (b"\xed\xa0\x80", 0), (b"\xed\xbf\xbf", 0), (b"x\xed\xa0\x80y", 1),  # surrogates U+D800 / U+DFFF
    (b"\xf4\x90\x80\x80", 0), (b"\xf5\x80\x80\x80", 0), (b"\xf8\x88\x80\x80\x80", 0),  # above U+10FFFF, 5-byte
    (b"\xff", 0), (b"\xfe", 0), (b"\xff\xfe", 0), (b"\xfe\xff", 0),  # UTF-16 BOMs / never-valid bytes
    (b"\xc3", 0), (b"ab\xc3", 2), (b"\xe2\x82", 0), (b"\xf0\x9f\x98", 0), (b"ok\xf0\x9f\x98", 2),  # truncated at the end
    (b"\xe2\x82A", 0), (b"\xc3(", 0), (b"\xf0\x9f\x98(", 0),      # sequence cut short by ASCII
]


@pytest.mark.parametrize("data,offset", INVALID)
def test_malformed_is_rejected_with_offset(data, offset, assert_mdq_error):
    with assert_mdq_error("MDQ_INVALID_SYNTAX") as info:
        dec(data)
    assert offset_of(info.value) == offset
    assert "UTF-8" in str(info.value)


def test_truncated_message_says_so(assert_mdq_error):
    with assert_mdq_error("MDQ_INVALID_SYNTAX") as info:
        dec(b"abc\xe2\x82")
    assert "truncated" in str(info.value)


def test_positions(assert_mdq_error):
    for data, line, col in [
        (b"\xff", 1, 1), (b"ab\xff", 1, 3), (b"ab\ncd\xff", 2, 3), (b"ab\r\ncd\xff", 2, 3), (b"ab\rcd\xff", 2, 3),
        ("é€😀".encode() + b"\xff", 1, 4), ("é\n😀€".encode() + b"\xff", 2, 3), (b"\n\n\xff", 3, 1),
        (b"\xef\xbb\xbfab\xff", 1, 3),  # the BOM does not count as a column
    ]:
        with assert_mdq_error("MDQ_INVALID_SYNTAX", line=line, column=col):
            dec(data)


def test_offset_counts_bom_bytes(assert_mdq_error):
    with assert_mdq_error("MDQ_INVALID_SYNTAX") as info:
        dec(b"\xef\xbb\xbfab\xff")
    assert offset_of(info.value) == 5


def test_first_error_wins(assert_mdq_error):
    with assert_mdq_error("MDQ_INVALID_SYNTAX") as info:
        dec(b"a\xff b \xfe")
    assert offset_of(info.value) == 1


# ---------------------------------------------------------------- NUL


@pytest.mark.parametrize("data,line,col,offset", [
    (b"\0", 1, 1, 0), (b"ab\0", 1, 3, 2), (b"a\nb\0c", 2, 2, 3), ("é\0".encode(), 1, 2, 2), (b"\xef\xbb\xbfa\0", 1, 2, 4),
])
def test_nul_is_rejected(data, line, col, offset, assert_mdq_error):
    with assert_mdq_error("MDQ_INVALID_SYNTAX", line=line, column=col) as info:
        dec(data)
    assert "NUL" in str(info.value) and offset_of(info.value) == offset


def test_malformed_reported_before_nul(assert_mdq_error):
    with assert_mdq_error("MDQ_INVALID_SYNTAX") as info:
        dec(b"\0\xff")
    assert "UTF-8" in str(info.value)


# ---------------------------------------------------------------- types and limits


@pytest.mark.parametrize("bad", ["text", None, 1, ["a"], bytearray(b"a"), memoryview(b"a")])
def test_requires_bytes(bad):
    with pytest.raises(TypeError):
        dec(bad)


def test_size_limit(assert_mdq_error):
    assert dec(b"a" * (16 * 1024 * 1024)) == "a" * (16 * 1024 * 1024)
    with assert_mdq_error("MDQ_RESOURCE_LIMIT"):
        dec(b"a" * (16 * 1024 * 1024 + 1))


def test_size_checked_before_content(assert_mdq_error):
    with assert_mdq_error("MDQ_RESOURCE_LIMIT"):
        dec(b"\xff" * (16 * 1024 * 1024 + 1))


# ---------------------------------------------------------------- differential vs Python's decoder


def oracle(data):
    """('ok', text) or ('err', offset): Python's strict decoder plus the BOM/NUL policy."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as e:
        return "err", e.start
    if text.startswith("﻿"):
        text = text[1:]
    if "\0" in text:
        return "err", None
    return "ok", text


@given(st.binary(max_size=40))
def test_random_bytes_match_python(data):
    kind, val = oracle(data)
    if kind == "ok":
        assert dec(data) == val
    else:
        with pytest.raises(mdq.MdqError) as info:
            dec(data)
        assert info.value.code == "MDQ_INVALID_SYNTAX"
        if val is not None:
            assert offset_of(info.value) == val


UTF8_PIECES = [b"a", b"\n", "é".encode(), "€".encode(), "😀".encode(), b"\xef\xbb\xbf", b"\xc0", b"\x80", b"\xe2\x82", b"\xf0\x9f",
               b"\xed\xa0", b"\xff", b"\xf4\x90", b"\0", b"\r"]


@given(st.lists(st.sampled_from(UTF8_PIECES), max_size=10).map(b"".join))
def test_structured_bytes_match_python(data):
    kind, val = oracle(data)
    if kind == "ok":
        assert dec(data) == val
    else:
        with pytest.raises(mdq.MdqError) as info:
            dec(data)
        if val is not None:
            assert offset_of(info.value) == val


@given(st.text(st.characters(blacklist_categories=("Cs",), blacklist_characters="\0"), max_size=30))
def test_any_valid_text_round_trips(s):
    data = s.encode()
    expected = s[1:] if s.startswith("﻿") else s
    assert dec(data) == expected
    assert dec(b"\xef\xbb\xbf" + data) == s
