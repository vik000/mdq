"""Epic 7 / E7.1 (F20): hostile-zip reader for `.mdq` packages (package.md). G47.

`zip_read(data, limits=None)` takes the whole archive as bytes and returns
`{members: [{name, method, size, compressed_size, crc32, data}], directories: [...]}` or fails with ONE
diagnostic. `inflate(data, max_size)` is our own RFC 1951 decoder (oracle: zlib raw inflate), `crc32` our
own CRC-32 (oracle: zlib.crc32), `zip_limits(limits=None)` the effective limits.

Hostile fixtures are built by `build()` below (a tiny zip writer that can break every rule); valid ones also
with the standard `zipfile` module.
"""
import io
import os
import struct
import zipfile
import zlib

import pytest
from hypothesis import HealthCheck, assume, given, settings, strategies as st

import mdq
from mdq import _internal as mi

pytestmark = pytest.mark.unit

SYNTAX, DENIED, UNSUP, LIMIT = "MDQ_INVALID_SYNTAX", "MDQ_SECURITY_DENIED", "MDQ_UNSUPPORTED_FEATURE", "MDQ_RESOURCE_LIMIT"
MIB = 1024 * 1024
DEFAULTS = {"max_entries": 1000, "max_member_size": 16 * MIB, "max_total_size": 64 * MIB,
            "max_archive_size": 64 * MIB, "max_ratio": 100}
CAPS = {"max_entries": 10000, "max_member_size": 64 * MIB, "max_total_size": 256 * MIB,
        "max_archive_size": 256 * MIB, "max_ratio": 100}
REG = 0o100644 << 16  # regular file, Unix mode in the high 16 bits of external_attr
DIR = (0o040755 << 16) | 0x10


# ---------------------------------------------------------------- test-only zip writer
def deflate(data, level=6, strategy=zlib.Z_DEFAULT_STRATEGY):
    c = zlib.compressobj(level, zlib.DEFLATED, -15, 9, strategy)
    return c.compress(data) + c.flush()


def E(name, data=b"", method=8, **kw):
    """One entry for `build`. Keyword overrides: flags, ext_attr, extra, local_extra, crc, csize, usize (central
    values), local (dict of local-header overrides: flags, method, name, crc, csize, usize), descriptor
    (None, 'sig', 'nosig'), desc (dict of descriptor overrides), raw (already-compressed bytes), offset."""
    return {"name": name, "data": data, "method": method, **kw}


def build(entries, comment=b"", prefix=b"", suffix=b"", between=b"", eocd=None, cd_extra=b"", gap_after=None):
    out = bytearray(prefix)
    central = bytearray()
    for i, e in enumerate(entries):
        name = e["name"].encode() if isinstance(e["name"], str) else e["name"]
        data = e["data"]
        raw = e.get("raw", deflate(data) if e["method"] == 8 else data)
        flags = e.get("flags", 0x800 if not name.isascii() else 0)
        if e.get("descriptor"):
            flags |= 0x08
        crc = e.get("crc", zlib.crc32(data))
        csize, usize = e.get("csize", len(raw)), e.get("usize", len(data))
        loc = e.get("local", {})
        lname = loc.get("name", name)
        lextra = e.get("local_extra", b"")
        offset = e.get("offset", len(out))
        zeroed = bool(e.get("descriptor"))
        lcrc, lcs, lus = (0, 0, 0) if zeroed else (crc, csize, usize)
        if not e.get("skip_local"):  # skip_local: central entry only, `offset` points at another entry's bytes
            out += struct.pack("<IHHHHHIIIHH", 0x04034B50, 20, loc.get("flags", flags), loc.get("method", e["method"]), 0,
                               0x21, loc.get("crc", lcrc), loc.get("csize", lcs), loc.get("usize", lus), len(lname), len(lextra))
            out += lname + lextra + raw
        if e.get("descriptor") and not e.get("skip_local"):
            dd = e.get("desc", {})
            vals = struct.pack("<III", dd.get("crc", crc), dd.get("csize", csize), dd.get("usize", usize))
            out += (struct.pack("<I", 0x08074B50) if e["descriptor"] == "sig" else b"") + vals
        if gap_after == i:
            out += b"JUNK"
        extra = e.get("extra", b"")
        central += struct.pack("<IHHHHHHIIIHHHHHII", 0x02014B50, 0x031E, 20, flags, e["method"], 0, 0x21, crc, csize,
                               usize, len(name), len(extra), 0, e.get("disk", 0), 0, e.get("ext_attr", REG), offset)
        central += name + extra
    out += between
    cd_off = len(out)
    out += central + cd_extra
    ev = {"disk": 0, "cd_disk": 0, "n_disk": len(entries), "n_total": len(entries), "cd_size": len(central) + len(cd_extra),
          "cd_off": cd_off, **(eocd or {})}
    out += struct.pack("<IHHHHIIH", 0x06054B50, ev["disk"], ev["cd_disk"], ev["n_disk"], ev["n_total"], ev["cd_size"],
                       ev["cd_off"], len(comment)) + comment
    return bytes(out + suffix)


def stdzip(files, method=zipfile.ZIP_DEFLATED, stream=False, **kw):
    """Build with the standard library. `stream=True` writes to a non-seekable stream (data descriptors)."""
    buf = io.BytesIO()

    class NoSeek(io.RawIOBase):
        def writable(self):
            return True

        def write(self, b):
            return buf.write(b)

    target = NoSeek() if stream else buf
    with zipfile.ZipFile(target, "w", method, **kw) as z:
        for name, data in files:
            z.writestr(name, data)
    return buf.getvalue()


def names(archive):
    return [m["name"] for m in archive["members"]]


# ---------------------------------------------------------------- crc32
@pytest.mark.parametrize("data", [b"", b"a", b"abc", b"123456789", bytes(range(256)) * 9])
def test_crc32_matches_zlib(data):
    assert mi.crc32(data) == zlib.crc32(data)
    assert mi.crc32(b"123456789") == 0xCBF43926  # the standard check value


@given(st.binary(max_size=4096))
def test_crc32_property(data):
    assert mi.crc32(data) == zlib.crc32(data)


# ---------------------------------------------------------------- inflate
DATA = [b"", b"a", b"hello world " * 50, bytes(range(256)) * 40, os.urandom(5000), b"\x00" * 70000,
        b"abcabcabd" * 3000]


@pytest.mark.parametrize("data", DATA, ids=range(len(DATA)))
@pytest.mark.parametrize("level,strategy", [(0, 0), (1, 0), (6, 0), (9, 0), (6, zlib.Z_HUFFMAN_ONLY),
                                            (6, zlib.Z_RLE), (6, zlib.Z_FIXED), (9, zlib.Z_FILTERED)])
def test_inflate_matches_zlib(data, level, strategy):
    comp = deflate(data, level, strategy)
    assert mi.inflate(comp, len(data)) == data
    assert mi.inflate(comp, len(data) + 10) == data


def zlib_raw(data):
    """The oracle: zlib raw inflate, requiring the stream to end exactly at the end of the input."""
    d = zlib.decompressobj(-15)
    try:
        out = d.decompress(data) + d.flush()
    except zlib.error:
        return None
    return out if d.eof and not d.unused_data else None


@settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow])
@given(st.binary(min_size=1, max_size=3000), st.sampled_from([1, 6, 9]),
       st.sampled_from([zlib.Z_DEFAULT_STRATEGY, zlib.Z_FIXED, zlib.Z_HUFFMAN_ONLY, zlib.Z_RLE]),
       st.lists(st.tuples(st.integers(0, 10**6), st.integers(0, 255)), min_size=1, max_size=3))
def test_inflate_differential_on_mutated_streams(data, level, strategy, flips):
    comp = bytearray(deflate(data, level, strategy))
    for pos, val in flips:
        comp[pos % len(comp)] = val
    comp = bytes(comp)
    want = zlib_raw(comp)
    try:
        got = mi.inflate(comp, 1 << 24)
    except mdq.MdqError as e:
        assert e.code == SYNTAX
        assert want is None, "zlib accepted a stream we rejected"
        return
    assert got == want


@given(st.binary(max_size=200))
def test_inflate_differential_on_random_bytes(data):
    want = zlib_raw(data)
    try:
        got = mi.inflate(data, 1 << 20)
    except mdq.MdqError as e:
        assert e.code in (SYNTAX, LIMIT)
        assert want is None or len(want) > 1 << 20
        return
    assert got == want


def far_distance_stream():
    """A fixed-Huffman block whose FIRST symbol is a match (length 3, distance 1): nothing to copy from."""
    bits = []

    def lsb(value, n):  # header fields and extra bits: least significant bit first
        bits.extend((value >> i) & 1 for i in range(n))

    def msb(code, n):  # Huffman codes: most significant bit first
        bits.extend((code >> (n - 1 - i)) & 1 for i in range(n))

    lsb(1, 1)      # BFINAL
    lsb(1, 2)      # BTYPE = fixed
    msb(1, 7)      # symbol 257 (length 3): 7-bit code 0000001
    msb(0, 5)      # distance symbol 0 (distance 1)
    msb(0, 7)      # end of block (256)
    bits += [0] * (-len(bits) % 8)
    return bytes(sum(b << i for i, b in enumerate(bits[k:k + 8])) for k in range(0, len(bits), 8))


@pytest.mark.parametrize("comp,why", [
    (b"", "empty"),
    (deflate(b"hello world" * 10)[:-1], "truncated"),
    (deflate(b"hello") + b"\x00", "byte after the final block"),
    (b"\x07", "block type 3"),
    (b"\x01\x05\x00\xfb\xff" + b"hello", "stored LEN/NLEN mismatch"),
    (b"\x01\x05\x00\xfa\xff" + b"hel", "stored block truncated"),
    (None, "distance before the start of the output"),
])
def test_inflate_rejects_malformed(assert_mdq_error, comp, why):
    if comp is None:
        comp = far_distance_stream()
    assert zlib_raw(comp) is None, why
    with assert_mdq_error(SYNTAX):
        mi.inflate(comp, 1000)


def test_inflate_output_limit(assert_mdq_error):
    comp = deflate(b"\x00" * 100000)
    assert mi.inflate(comp, 100000) == b"\x00" * 100000
    with assert_mdq_error(LIMIT):
        mi.inflate(comp, 99999)
    with assert_mdq_error(LIMIT):
        mi.inflate(deflate(b"x"), 0)
    assert mi.inflate(deflate(b""), 0) == b""


def test_inflate_bomb_stops_at_the_limit(assert_mdq_error):
    # 200 MB of zeros compressed to ~200 KB: decoding stops at 1 MiB instead of expanding everything.
    c = zlib.compressobj(9, zlib.DEFLATED, -15)
    comp = b"".join(c.compress(b"\x00" * (10 * MIB)) for _ in range(20)) + c.flush()
    with assert_mdq_error(LIMIT):
        mi.inflate(comp, MIB)


@pytest.mark.parametrize("args,exc", [(("x", 10), TypeError), ((bytearray(b"x"), 10), TypeError),
                                      ((b"x", -1), TypeError), ((b"x", True), TypeError), ((b"x", 1.0), TypeError)])
def test_inflate_argument_types(args, exc):
    with pytest.raises(exc):
        mi.inflate(*args)


# ---------------------------------------------------------------- limits
def test_limit_defaults():
    assert mi.zip_limits() == DEFAULTS and mi.zip_limits(None) == DEFAULTS and mi.zip_limits({}) == DEFAULTS


def test_limits_lowered_and_clamped():
    assert mi.zip_limits({"max_entries": 5})["max_entries"] == 5
    assert mi.zip_limits({k: 10**30 for k in CAPS}) == CAPS
    assert mi.zip_limits({"max_ratio": 1000})["max_ratio"] == 100
    assert mi.zip_limits({"max_member_size": 1}) == {**DEFAULTS, "max_member_size": 1}


@pytest.mark.parametrize("limits,exc", [
    ({"max_files": 3}, ValueError), ({"max_entries": 0}, ValueError), ({"max_entries": -5}, ValueError),
    ({"max_entries": True}, TypeError), ({"max_entries": "5"}, TypeError), ({"max_entries": 5.0}, TypeError),
    ({1: 5}, TypeError), ([("max_entries", 5)], TypeError), ("x", TypeError),
])
def test_limits_bad_values(limits, exc):
    with pytest.raises(exc):
        mi.zip_limits(limits)
    with pytest.raises(exc):
        mi.zip_read(stdzip([("a", b"x")]), limits)


# ---------------------------------------------------------------- valid archives
def test_stdlib_round_trip_deflate_and_stored():
    files = [("manifest.yaml", b"format: mdq\n"), ("document.md", b"# Hi\n" * 100), ("assets/logo.png", os.urandom(300))]
    for method in (zipfile.ZIP_DEFLATED, zipfile.ZIP_STORED):
        a = mi.zip_read(stdzip(files, method))
        assert [(m["name"], m["data"]) for m in a["members"]] == files
        for m in a["members"]:
            assert m["method"] == ("deflate" if method == zipfile.ZIP_DEFLATED else "stored")
            assert m["size"] == len(m["data"]) and m["crc32"] == zlib.crc32(m["data"])
        assert a["directories"] == []


def test_member_fields_and_central_directory_order():
    data = b"x" * 1000
    z = build([E("b.md", data), E("a.md", b"", method=0)])
    a = mi.zip_read(z)
    assert a["members"] == [
        {"name": "b.md", "method": "deflate", "size": 1000, "compressed_size": len(deflate(data)), "crc32": zlib.crc32(data), "data": data},
        {"name": "a.md", "method": "stored", "size": 0, "compressed_size": 0, "crc32": 0, "data": b""},
    ]


def test_empty_archive_and_empty_members():
    assert mi.zip_read(stdzip([])) == {"members": [], "directories": []}
    assert mi.zip_read(stdzip([("e", b"")]))["members"][0]["data"] == b""


def test_directories_listed_separately():
    z = stdzip([("sections/", b""), ("sections/a.md", b"a")])
    assert mi.zip_read(z) == {"members": [{"name": "sections/a.md", "method": "deflate", "size": 1, "compressed_size": 3,
                                           "crc32": zlib.crc32(b"a"), "data": b"a"}], "directories": ["sections/"]}
    with pytest.raises(mdq.MdqError) as info:
        mi.zip_read(build([E("d/", b"data", ext_attr=DIR)]))
    assert info.value.code == SYNTAX


def test_utf8_names():
    files = [("café/naïve.md", b"1"), ("日本語.md", b"2"), ("emoji-😀.md", b"3")]
    assert names(mi.zip_read(stdzip(files))) == [f for f, _ in files]


def test_data_descriptors_from_a_streaming_writer():
    files = [("a.md", b"alpha" * 100), ("b.md", b"")]
    z = stdzip(files, stream=True)
    assert struct.unpack_from("<H", z, 6)[0] & 0x08  # really uses descriptors
    assert [(m["name"], m["data"]) for m in mi.zip_read(z)["members"]] == files
    assert [(m["name"], m["data"]) for m in mi.zip_read(stdzip(files, zipfile.ZIP_STORED, stream=True))["members"]] == files


@pytest.mark.parametrize("form", ["sig", "nosig"])
def test_data_descriptor_forms(form):
    assert names(mi.zip_read(build([E("a", b"hello", descriptor=form), E("b", b"x", descriptor=form)]))) == ["a", "b"]


def test_archive_comment_is_allowed():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("a", b"1")
        z.comment = b"made by a test"
    assert names(mi.zip_read(buf.getvalue())) == ["a"]


def test_nested_zip_is_returned_as_opaque_bytes():
    bomb = stdzip([("zeros", b"\x00" * (50 * 1000 * 1000))])
    inner = stdzip([("inner.md", b"x")])
    a = mi.zip_read(stdzip([("data/bomb.zip", bomb), ("data/inner.zip", inner)], zipfile.ZIP_STORED))
    assert [m["data"] for m in a["members"]] == [bomb, inner]


def test_small_members_are_never_refused_for_ratio():
    z = stdzip([("zeros", b"\x00" * MIB)])  # ratio ~1000:1 but only 1 MiB
    assert mi.zip_read(z)["members"][0]["size"] == MIB


_name = st.lists(st.text(st.characters(blacklist_categories=("Cs", "Cc"), blacklist_characters="/\\:"), min_size=1, max_size=8)
                 .filter(lambda s: s not in (".", "..")), min_size=1, max_size=3).map("/".join)


@settings(max_examples=60, suppress_health_check=[HealthCheck.too_slow])
@given(st.dictionaries(_name, st.binary(max_size=300), max_size=6), st.booleans())
def test_round_trip_property(files, stored):
    import unicodedata
    keys = [unicodedata.normalize("NFC", n).lower() for n in files]
    assume(len(set(keys)) == len(keys))
    assume(not any(a != b and b.startswith(a + "/") for a in keys for b in keys))
    items = list(files.items())
    a = mi.zip_read(stdzip(items, zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED))
    assert [(m["name"], m["data"]) for m in a["members"]] == items


# ---------------------------------------------------------------- hostile names
@pytest.mark.parametrize("name,code", [
    ("../x", DENIED), ("a/../../x", DENIED), ("a/..", DENIED), ("/abs", DENIED), ("//host/x", DENIED),
    ("C:\\x", DENIED), ("C:x", DENIED), ("a\\..\\x", DENIED), ("dir\\file", DENIED), ("file.md:stream", DENIED),
    ("", SYNTAX), ("/", SYNTAX), ("a//b", SYNTAX), ("./a", SYNTAX), ("a/./b", SYNTAX), ("a/.", SYNTAX),
    ("a\nb", SYNTAX), ("a\x00b", SYNTAX), ("a\x7fb", SYNTAX), ("a\x1b[31mb", SYNTAX),
    ("x" * 256, LIMIT), ("a/" * 2100 + "b", LIMIT),
])
def test_hostile_names(assert_mdq_error, name, code):
    with assert_mdq_error(code):
        mi.zip_read(build([E("ok.md", b"1"), E(name, b"x")]))


def test_messages_never_contain_names():
    secret = "SECRET-7f3a"
    for name in [f"../{secret}", f"/{secret}", f"{secret}\\x", f"{secret}\x01", f"a/../{secret}"]:
        with pytest.raises(mdq.MdqError) as info:
            mi.zip_read(build([E(name, b"x")]))
        assert secret not in str(info.value) and "member 1" in str(info.value)
    with pytest.raises(mdq.MdqError) as info:
        mi.zip_read(build([E(secret, b"x"), E(secret, b"y")]))
    assert secret not in str(info.value) and "member 2" in str(info.value)


@pytest.mark.parametrize("names_,why", [
    (["a.md", "a.md"], "exact duplicate"),
    (["dir/a.md", "dir/a.md"], "exact duplicate in a directory"),
    (["caf\u00e9.md", "cafe\u0301.md"], "same name in NFC"),
    (["README.md", "readme.md"], "case collision"),
    (["a", "a/b"], "file used as a directory"),
    (["a/b", "A"], "file used as a directory, other case"),
    (["a/b/c", "a/b"], "deeper parent is a file"),
    (["a/", "a"], "directory and file with the same name"),
])
def test_duplicates_and_collisions(assert_mdq_error, names_, why):
    entries = [E(n, b"", ext_attr=DIR if n.endswith("/") else REG) for n in names_]
    with assert_mdq_error(DENIED):
        mi.zip_read(build(entries))


def test_directory_entries_may_repeat_implicit_parents():
    a = mi.zip_read(build([E("a/", ext_attr=DIR), E("a/b/", ext_attr=DIR), E("a/b/c.md", b"c")]))
    assert names(a) == ["a/b/c.md"] and a["directories"] == ["a/", "a/b/"]


@pytest.mark.parametrize("ext_attr,code", [
    (0o120777 << 16, DENIED),  # symlink
    (0o010644 << 16, DENIED),  # fifo
    (0o020644 << 16, DENIED),  # character device
    (0o140644 << 16, DENIED),  # socket
    (DIR, SYNTAX),             # directory mode on a file name
    (0, None), (0x20, None), (REG, None),
])
def test_file_types(assert_mdq_error, ext_attr, code):
    z = build([E("link", b"target", ext_attr=ext_attr)])
    if code is None:
        assert names(mi.zip_read(z)) == ["link"]
    else:
        with assert_mdq_error(code):
            mi.zip_read(z)


def test_symlink_from_stdlib_attrs():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        info = zipfile.ZipInfo("evil")
        info.create_system = 3
        info.external_attr = 0o120777 << 16
        z.writestr(info, "../../etc/passwd")
    with pytest.raises(mdq.MdqError) as e:
        mi.zip_read(buf.getvalue())
    assert e.value.code == DENIED


def test_regular_mode_on_a_directory_name(assert_mdq_error):
    with assert_mdq_error(SYNTAX):
        mi.zip_read(build([E("d/", ext_attr=REG)]))


def test_non_utf8_names(assert_mdq_error):
    with assert_mdq_error(SYNTAX):  # cp437 'é' without flag bit 11
        mi.zip_read(build([E(b"caf\x82.md", b"x", flags=0)]))
    with assert_mdq_error(SYNTAX):  # flag set but not UTF-8
        mi.zip_read(build([E(b"caf\xff.md", b"x", flags=0x800)]))
    with assert_mdq_error(SYNTAX):  # overlong encoding
        mi.zip_read(build([E(b"\xc0\xaf.md", b"x", flags=0x800)]))
    # stdlib writes cp437-compatible ASCII names without the flag: fine
    assert names(mi.zip_read(build([E(b"plain.md", b"x", flags=0)]))) == ["plain.md"]


# ---------------------------------------------------------------- unsupported features
@pytest.mark.parametrize("flags", [0x01, 0x41, 0x2000, 0x01 | 0x800])
def test_encryption_flags(assert_mdq_error, flags):
    with assert_mdq_error(UNSUP):
        mi.zip_read(build([E("a", b"x", flags=flags)]))


@pytest.mark.parametrize("flags", [0x10, 0x20, 0x80, 0x1000, 0x4000, 0x8000])
def test_unknown_flags(assert_mdq_error, flags):
    with assert_mdq_error(UNSUP):
        mi.zip_read(build([E("a", b"x", flags=flags)]))


def test_harmless_flags_accepted():
    for flags in (0x02, 0x04, 0x06):
        assert names(mi.zip_read(build([E("a", b"x", flags=flags)]))) == ["a"]


@pytest.mark.parametrize("method", [1, 6, 9, 12, 14, 93, 95, 99])
def test_other_methods(assert_mdq_error, method):
    with assert_mdq_error(UNSUP):
        mi.zip_read(build([E("a", b"x", method=method, raw=b"x")]))


def test_stdlib_bzip2_and_lzma(assert_mdq_error):
    for method in (zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA):
        with assert_mdq_error(UNSUP):
            mi.zip_read(stdzip([("a", b"x" * 100)], method))


def test_zip64_forced_by_stdlib(assert_mdq_error):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        with z.open("a", "w", force_zip64=True) as f:
            f.write(b"hello")
    with assert_mdq_error(UNSUP):
        mi.zip_read(buf.getvalue())


def test_zip64_variants(assert_mdq_error):
    z64extra = struct.pack("<HHQQ", 1, 16, 5, 5)
    cases = [
        build([E("a", b"x", extra=z64extra)]),
        build([E("a", b"x", local_extra=z64extra)]),
        build([E("a", b"x", csize=0xFFFFFFFF)]),
        build([E("a", b"x", usize=0xFFFFFFFF)]),
        build([E("a", b"x")], eocd={"n_disk": 0xFFFF, "n_total": 0xFFFF}),
        build([E("a", b"x")], eocd={"cd_off": 0xFFFFFFFF}),
        build([E("a", b"x")], eocd={"cd_size": 0xFFFFFFFF}),
    ]
    # zip64 end record + locator inserted between the central directory and the EOCD, with real EOCD values
    base = build([E("a", b"x")])
    eocd_at = len(base) - 22
    z64 = struct.pack("<IQHHIIQQQQ", 0x06064B50, 44, 45, 45, 0, 0, 1, 1, 0, 0)
    loc = struct.pack("<IIQI", 0x07064B50, 0, eocd_at, 1)
    cases.append(base[:eocd_at] + z64 + loc + base[eocd_at:])
    for z in cases:
        with assert_mdq_error(UNSUP):
            mi.zip_read(z)


def test_multi_disk(assert_mdq_error):
    for ev in ({"disk": 1}, {"cd_disk": 1}):
        with assert_mdq_error(UNSUP):
            mi.zip_read(build([E("a", b"x")], eocd=ev))
    with assert_mdq_error(UNSUP):
        mi.zip_read(build([E("a", b"x", disk=1)]))
    with assert_mdq_error(SYNTAX):
        mi.zip_read(build([E("a", b"x"), E("b", b"y")], eocd={"n_disk": 1}))


# ---------------------------------------------------------------- structure
def test_not_a_zip(assert_mdq_error):
    for z in (b"", b"PK", b"hello world" * 10, b"PK\x03\x04" + b"\x00" * 40):
        with assert_mdq_error(SYNTAX):
            mi.zip_read(z)


def test_trailing_garbage(assert_mdq_error):
    for suffix in (b"x", b"\x00" * 100, b"PK\x05\x06"):
        with assert_mdq_error(SYNTAX):
            mi.zip_read(build([E("a", b"x")], suffix=suffix))


def test_prefix_data_self_extractor(assert_mdq_error):
    z = build([E("a", b"x")])
    with assert_mdq_error(SYNTAX):
        mi.zip_read(b"MZ-stub" + z)  # offsets now point 7 bytes too early
    with assert_mdq_error(SYNTAX):
        mi.zip_read(build([E("a", b"x", offset=7)], prefix=b"MZ-stub"))  # offsets fixed, but a gap at the start


def test_multiple_eocd_records(assert_mdq_error):
    inner_eocd = struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, 0, 0, 0, 0, 0)
    with assert_mdq_error(SYNTAX):
        mi.zip_read(build([E("a", b"x")], comment=b"hi" + inner_eocd))
    with assert_mdq_error(SYNTAX):  # two archives concatenated
        mi.zip_read(build([E("a", b"x")]) + build([E("b", b"y")]))


@settings(max_examples=150)
@given(st.data())
def test_every_truncation_is_rejected(data):
    z = stdzip([("a.md", b"alpha" * 40), ("b/", b""), ("b/c.md", b"gamma")])
    cut = data.draw(st.integers(0, len(z) - 1))
    with pytest.raises(mdq.MdqError) as e:
        mi.zip_read(z[:cut])
    assert e.value.code == SYNTAX


def test_gaps_and_overlaps(assert_mdq_error):
    with assert_mdq_error(SYNTAX):
        mi.zip_read(build([E("a", b"x"), E("b", b"y")], gap_after=0))
    with assert_mdq_error(SYNTAX):
        mi.zip_read(build([E("a", b"x")], between=b"junk"))
    # two central entries pointing at the same local record
    with assert_mdq_error(SYNTAX):
        mi.zip_read(build([E("a", b"x"), E("a2", b"x", offset=0, local={"name": b"a"})]))


def test_overlapping_members(assert_mdq_error):
    # Member "b" is a complete, valid local record hidden INSIDE the stored data of member "a" (the trick behind
    # non-recursive zip bombs: many central entries reusing the same compressed bytes). Each record checks out
    # on its own; only the tiling rule catches it.
    inner = build([E("b", b"x", method=0)])
    inner_local = inner[:inner.index(b"PK\x01\x02")]
    z = build([E("a", inner_local, method=0), E("b", b"x", method=0, skip_local=True, offset=30 + 1)])
    with pytest.raises(mdq.MdqError) as e:
        mi.zip_read(z)
    assert e.value.code == SYNTAX and "overlap" in str(e.value)


def test_central_directory_count_and_size(assert_mdq_error):
    with assert_mdq_error(SYNTAX):  # one more entry declared than present
        mi.zip_read(build([E("a", b"x")], eocd={"n_disk": 2, "n_total": 2}))
    with assert_mdq_error(SYNTAX):  # extra bytes after the last entry
        mi.zip_read(build([E("a", b"x")], cd_extra=b"\x00\x00\x00\x00"))
    with assert_mdq_error(SYNTAX):
        mi.zip_read(build([E("a", b"x")], eocd={"cd_off": 1}))


@pytest.mark.parametrize("over", [{"usize": 3}, {"usize": 0}, {"crc": 1234}, {"csize": 2}])
def test_declared_size_and_crc_mismatch(assert_mdq_error, over):
    # all values consistent between local and central header, but wrong for the data
    e = E("a", b"hello" * 10, **over)
    e["local"] = {k: v for k, v in over.items()}
    with assert_mdq_error(SYNTAX):
        mi.zip_read(build([e]))


def test_declared_larger_than_real(assert_mdq_error):
    e = E("a", b"hello", usize=6, local={"usize": 6})
    with assert_mdq_error(SYNTAX):
        mi.zip_read(build([e]))


def test_stored_sizes_must_agree(assert_mdq_error):
    with assert_mdq_error(SYNTAX):
        mi.zip_read(build([E("a", b"hello", method=0, usize=4, local={"usize": 4})]))


@pytest.mark.parametrize("local", [{"name": b"b"}, {"method": 0}, {"flags": 0x02}, {"crc": 7}, {"csize": 1}, {"usize": 99}])
def test_local_header_must_agree(assert_mdq_error, local):
    with assert_mdq_error(SYNTAX):
        mi.zip_read(build([E("a", b"hello", local=local)]))


def test_bad_local_signature(assert_mdq_error):
    z = bytearray(build([E("a", b"x")]))
    z[0:4] = b"PK\x09\x09"
    with assert_mdq_error(SYNTAX):
        mi.zip_read(bytes(z))


@pytest.mark.parametrize("desc", [{"crc": 1}, {"csize": 0}, {"usize": 7}])
def test_data_descriptor_mismatch(assert_mdq_error, desc):
    with assert_mdq_error(SYNTAX):
        mi.zip_read(build([E("a", b"hello", descriptor="sig", desc=desc)]))


def test_descriptor_local_fields_zero_or_equal(assert_mdq_error):
    ok = E("a", b"hello", descriptor="sig")
    ok["local"] = {"crc": zlib.crc32(b"hello"), "csize": len(deflate(b"hello")), "usize": 5}
    assert names(mi.zip_read(build([ok]))) == ["a"]
    bad = E("a", b"hello", descriptor="sig", local={"usize": 3})
    with assert_mdq_error(SYNTAX):
        mi.zip_read(build([bad]))


@pytest.mark.parametrize("extra", [b"\x01", b"\x55\x54\x09\x00abc", b"\x00\x00\x02\x00a"])
def test_malformed_extra_fields(assert_mdq_error, extra):
    with assert_mdq_error(SYNTAX):
        mi.zip_read(build([E("a", b"x", extra=extra)]))
    with assert_mdq_error(SYNTAX):
        mi.zip_read(build([E("a", b"x", local_extra=extra)]))


def test_well_formed_extra_fields_are_ignored():
    ut = struct.pack("<HHB I", 0x5455, 5, 1, 0)
    assert names(mi.zip_read(build([E("a", b"x", extra=ut, local_extra=ut)]))) == ["a"]


def test_corrupt_deflate_data(assert_mdq_error):
    with assert_mdq_error(SYNTAX):
        mi.zip_read(build([E("a", b"hello", raw=b"\x07\x00\x00")]))


@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
@given(st.data())
def test_mutated_archives_never_panic(data):
    z = bytearray(stdzip([("a.md", b"alpha" * 40), ("b/", b""), ("b/c.md", bytes(range(7, 250, 5)))], stream=data.draw(st.booleans())))
    for _ in range(data.draw(st.integers(1, 4))):
        z[data.draw(st.integers(0, len(z) - 1))] = data.draw(st.integers(0, 255))
    try:
        a = mi.zip_read(bytes(z))
    except mdq.MdqError as e:
        assert e.code in (SYNTAX, DENIED, UNSUP, LIMIT) and str(e)
        return
    for m in a["members"]:
        assert zlib.crc32(m["data"]) == m["crc32"] and len(m["data"]) == m["size"]


@given(st.binary(max_size=300))
def test_random_bytes_never_panic(data):
    try:
        mi.zip_read(data)
    except mdq.MdqError as e:
        assert e.code in (SYNTAX, DENIED, UNSUP, LIMIT)


# ---------------------------------------------------------------- resource limits
def test_zero_bomb_refused_by_ratio(assert_mdq_error):
    z = stdzip([("zeros", b"\x00" * (50 * 1000 * 1000))])
    assert len(z) < 100 * 1000
    with assert_mdq_error(LIMIT):
        mi.zip_read(z)
    with assert_mdq_error(LIMIT):  # also over the member limit when the ratio would allow it
        mi.zip_read(stdzip([("zeros", b"\x00" * (17 * MIB))], zipfile.ZIP_STORED))


def test_ratio_boundary(assert_mdq_error):
    n = 2_000_000  # above the 1 MiB threshold and divisible by 100
    with assert_mdq_error(LIMIT):
        mi.zip_read(build([E("z", b"\x00" * n)]))  # ~1000:1
    # exactly 100:1 passes, 100:1 plus one byte fails (declared sizes are what count)
    raw = os.urandom(n // 100)
    ok = build([E("z", b"", method=8, raw=raw, usize=n, local={"usize": n})])
    with pytest.raises(mdq.MdqError) as e:
        mi.zip_read(ok)
    assert e.value.code == SYNTAX  # passed the ratio check, then the data is wrong
    over = build([E("z", b"", method=8, raw=raw, usize=n + 1, local={"usize": n + 1})])
    with assert_mdq_error(LIMIT):
        mi.zip_read(over)
    with assert_mdq_error(LIMIT):
        mi.zip_read(stdzip([("z", b"\x00" * MIB * 2)]), {"max_ratio": 1})


def test_lying_declared_size_is_bounded(assert_mdq_error):
    # the central directory says 1000 bytes, the stream expands to 20 MB: decoding stops at 1001
    raw = deflate(b"\x00" * (20 * 1000 * 1000), 9)
    data_crc = 0
    z = build([E("z", b"", raw=raw, usize=1000, crc=data_crc, local={"usize": 1000, "crc": data_crc})])
    with assert_mdq_error(SYNTAX):
        mi.zip_read(z)


def test_member_size_limit(assert_mdq_error):
    z = stdzip([("a", b"x" * 1000)], zipfile.ZIP_STORED)
    assert mi.zip_read(z, {"max_member_size": 1000})
    with assert_mdq_error(LIMIT):
        mi.zip_read(z, {"max_member_size": 999})


def test_total_size_limit(assert_mdq_error):
    z = stdzip([(f"f{i}", b"x" * 1000) for i in range(5)], zipfile.ZIP_STORED)
    assert mi.zip_read(z, {"max_total_size": 5000})
    with assert_mdq_error(LIMIT):
        mi.zip_read(z, {"max_total_size": 4999})


def test_entry_limit(assert_mdq_error):
    z = stdzip([(f"f{i}", b"") for i in range(1001)])
    with assert_mdq_error(LIMIT):
        mi.zip_read(z)
    assert len(mi.zip_read(z, {"max_entries": 5000})["members"]) == 1001
    small = stdzip([(f"f{i}", b"") for i in range(6)])
    assert mi.zip_read(small, {"max_entries": 6})
    with assert_mdq_error(LIMIT):
        mi.zip_read(small, {"max_entries": 5})
    with assert_mdq_error(LIMIT):  # directories count as entries
        mi.zip_read(stdzip([("d/", b""), ("d/a", b"")]), {"max_entries": 1})


def test_archive_size_limit(assert_mdq_error):
    z = stdzip([("a", b"x")])
    assert mi.zip_read(z, {"max_archive_size": len(z)})
    with assert_mdq_error(LIMIT):
        mi.zip_read(z, {"max_archive_size": len(z) - 1})


def test_limits_checked_before_decompressing(assert_mdq_error):
    # the second member is corrupt, but the first one's size limit fails first: nothing is decoded
    z = build([E("big", b"x" * 5000), E("bad", b"hello", raw=b"\x07")])
    with assert_mdq_error(LIMIT):
        mi.zip_read(z, {"max_member_size": 4000})


# ---------------------------------------------------------------- arguments
@pytest.mark.parametrize("data", ["PK", bytearray(b"PK"), memoryview(b"PK"), None, 5])
def test_data_must_be_bytes(data):
    with pytest.raises(TypeError):
        mi.zip_read(data)
