"""Epic 7 / E7.3 (F21): age encryption for `.mdq.age` packages (package.md). G49.

`age_decrypt(data, identities=None, passphrase=None, max_work_factor=20)` and
`age_encrypt(data, random, recipients=None, passphrase=None, work_factor=18)` implement age v1 (X25519 and
scrypt recipients) with RustCrypto primitives and our own format code; `random` is supplied by the host (the core
never reads the OS random source). `package_open_age(data, identities, passphrase, limits, max_work_factor)` =
decrypt + `package_open`; plain `package_open` refuses age data (no implicit decryption).
Oracle: pyrage, in both directions. Header-parsing tests forge files with a correct MAC (the file key of a
deterministic encryption is `random[:16]`), so they reach the parser rather than stopping at the MAC.
"""
import base64
import hashlib
import hmac
import io
import os
import zipfile

import pytest
import pyrage
from hypothesis import given, settings, strategies as st
from pyrage import passphrase as pyrage_pw
from pyrage import x25519

import mdq
from mdq import _internal as mi

pytestmark = pytest.mark.unit

SYNTAX, DENIED, UNSUP, LIMIT = "MDQ_INVALID_SYNTAX", "MDQ_SECURITY_DENIED", "MDQ_UNSUPPORTED_FEATURE", "MDQ_RESOURCE_LIMIT"
CHUNK = 64 * 1024

ID1 = x25519.Identity.generate()
ID2 = x25519.Identity.generate()
SK1, SK2 = str(ID1), str(ID2)
PK1, PK2 = str(ID1.to_public()), str(ID2.to_public())
FIXED_SECRET = bytes(range(1, 33))
FIXED_RANDOM = bytes(range(100, 164))  # 64 bytes: file key, one ephemeral, nonce


def enc(data, recipients=(PK1,), random=None):
    random = random if random is not None else os.urandom(mi.age_random_len(len(recipients)))
    return mi.age_encrypt(data, random, list(recipients))


# ---------------------------------------------------------------- forging helpers (test-only age writer)
def b64(data):
    return base64.b64encode(data).decode().rstrip("=")


def hkdf(ikm, salt, info):
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    return hmac.new(prk, info + b"\x01", hashlib.sha256).digest()


def split(ct):
    """(header lines without the MAC line, payload bytes)."""
    end = ct.index(b"\n---") + 1
    mac_line_end = ct.index(b"\n", end) + 1
    return ct[:end].decode().splitlines(), ct[mac_line_end:]


def forge(lines, payload, file_key):
    """Reassemble a file with a correct header MAC over `lines`."""
    head = ("\n".join(lines) + "\n---").encode()
    mac = hmac.new(hkdf(file_key, b"", b"header"), head, hashlib.sha256).digest()
    return head + b" " + b64(mac).encode() + b"\n" + payload


def body_lines(raw):
    t = b64(raw)
    out = [t[i:i + 64] for i in range(0, len(t), 64)]
    if not out or len(out[-1]) == 64:
        out.append("")
    return out


def base_file(data=b"secret payload"):
    """A deterministic file to PK of FIXED_SECRET, plus its file key and pieces."""
    sk = mi.age_identity(FIXED_SECRET)
    ct = mi.age_encrypt(data, FIXED_RANDOM, [mi.age_recipient(sk)])
    lines, payload = split(ct)
    return sk, ct, lines, payload, FIXED_RANDOM[:16]


# ---------------------------------------------------------------- interoperability with pyrage
@pytest.mark.parametrize("size", [0, 1, 1000, CHUNK - 1, CHUNK, CHUNK + 1, 2 * CHUNK, 3 * CHUNK + 17])
def test_pyrage_to_us_and_back(size):
    data = os.urandom(size)
    assert mi.age_decrypt(pyrage.encrypt(data, [ID1.to_public()]), [SK1]) == data
    assert pyrage.decrypt(enc(data), [ID1]) == data


def test_multiple_recipients_both_ways():
    data = b"for two readers"
    ct = pyrage.encrypt(data, [ID1.to_public(), ID2.to_public()])
    assert mi.age_decrypt(ct, [SK2]) == data and mi.age_decrypt(ct, [SK1]) == data
    ours = enc(data, (PK1, PK2))
    assert pyrage.decrypt(ours, [ID2]) == data and pyrage.decrypt(ours, [ID1]) == data
    # any of several identities may match
    assert mi.age_decrypt(ours, [mi.age_identity(FIXED_SECRET), SK2]) == data


def test_passphrase_from_pyrage():
    ct = pyrage_pw.encrypt(b"pw protected", "correct horse")
    assert b"-> scrypt " in ct
    assert mi.age_decrypt(ct, passphrase="correct horse") == b"pw protected"


@pytest.mark.parametrize("wf", [1, 10, 14])
def test_passphrase_to_pyrage(wf):
    ct = mi.age_encrypt(b"hi there", os.urandom(mi.age_random_len(passphrase=True)), passphrase="pw", work_factor=wf)
    assert f"-> scrypt ".encode() in ct and ct.split(b"\n")[1].endswith(f" {wf}".encode())
    assert pyrage_pw.decrypt(ct, "pw") == b"hi there"
    assert mi.age_decrypt(ct, passphrase="pw") == b"hi there"


def test_identity_and_recipient_text():
    assert mi.age_recipient(SK1) == PK1
    sk = mi.age_identity(FIXED_SECRET)
    assert sk.startswith("AGE-SECRET-KEY-1") and sk == sk.upper()
    assert str(x25519.Identity.from_str(sk).to_public()) == mi.age_recipient(sk)
    assert mi.age_recipient(sk.lower()) == mi.age_recipient(sk)  # Bech32 is case-insensitive, not mixed


@settings(max_examples=40, deadline=None)
@given(st.binary(max_size=3000), st.integers(1, 3))
def test_round_trip_property(data, n):
    ids = [x25519.Identity.generate() for _ in range(n)]
    ct = enc(data, [str(i.to_public()) for i in ids])
    assert pyrage.decrypt(ct, [ids[-1]]) == data
    assert mi.age_decrypt(ct, [str(ids[0])]) == data


# ---------------------------------------------------------------- randomness comes from the host
def test_deterministic_given_the_same_random():
    a = mi.age_encrypt(b"x" * 100, FIXED_RANDOM, [PK1])
    assert a == mi.age_encrypt(b"x" * 100, FIXED_RANDOM, [PK1])
    assert a != mi.age_encrypt(b"x" * 100, bytes(64), [PK1])
    assert pyrage.decrypt(a, [ID1]) == b"x" * 100


def test_random_length():
    assert mi.age_random_len(1) == 64 and mi.age_random_len(3) == 128 and mi.age_random_len(passphrase=True) == 48
    for bad in (b"", bytes(63), bytes(65)):
        with pytest.raises(ValueError, match="64"):
            mi.age_encrypt(b"x", bad, [PK1])
    with pytest.raises(ValueError, match="48"):
        mi.age_encrypt(b"x", bytes(64), passphrase="pw")


# ---------------------------------------------------------------- keys: missing, wrong
def test_missing_key(assert_mdq_error):
    ct = enc(b"x")
    with assert_mdq_error(DENIED):
        mi.age_decrypt(ct)
    with assert_mdq_error(DENIED):
        mi.age_decrypt(ct, [])
    with assert_mdq_error(DENIED):  # a passphrase does not open an X25519 file
        mi.age_decrypt(ct, passphrase="pw")


def test_wrong_key(assert_mdq_error):
    with assert_mdq_error(DENIED):
        mi.age_decrypt(enc(b"x"), [SK2])
    with assert_mdq_error(DENIED):
        mi.age_decrypt(pyrage.encrypt(b"x", [ID1.to_public()]), [SK2])


def test_wrong_or_missing_passphrase(assert_mdq_error):
    ct = mi.age_encrypt(b"x", bytes(48), passphrase="right", work_factor=4)
    with assert_mdq_error(DENIED):
        mi.age_decrypt(ct, passphrase="wrong")
    with assert_mdq_error(DENIED):
        mi.age_decrypt(ct, [SK1])
    assert mi.age_decrypt(ct, [SK1], passphrase="right") == b"x"


def test_secrets_never_in_messages():
    ct = mi.age_encrypt(b"x", bytes(48), passphrase="CANARY-PASS", work_factor=4)
    for kwargs in ({"passphrase": "CANARY-WRONG"}, {"identities": [SK2]}):
        with pytest.raises(mdq.MdqError) as e:
            mi.age_decrypt(ct, **kwargs)
        assert "CANARY" not in str(e.value) and SK2 not in str(e.value)
    with pytest.raises(mdq.MdqError) as e:
        mi.age_decrypt(enc(b"x"), [SK2])
    assert SK2 not in str(e.value) and PK1 not in str(e.value)


# ---------------------------------------------------------------- malformed and truncated envelopes
@pytest.mark.parametrize("data,code", [
    (b"", SYNTAX), (b"hello", SYNTAX), (b"PK\x03\x04", SYNTAX),
    (b"-----BEGIN AGE ENCRYPTED FILE-----\nYWdl\n-----END AGE ENCRYPTED FILE-----\n", UNSUP),
    (b"age-encryption.org/v2\n-> X25519 x\n\n--- x\n", UNSUP),
    (b"age-encryption.org/v1", SYNTAX), (b"age-encryption.org/v1\n", SYNTAX),
    (b"age-encryption.org/v1\r\n-> X25519 a\n\n--- a\n", SYNTAX),   # CRLF: malformed, not a new version
    (b"age-encryption.org/v", SYNTAX), (b"age-encryption.org/v12\n", UNSUP), (b"age-encryption.org/x\n", SYNTAX),
])
def test_not_age_or_unsupported(assert_mdq_error, data, code):
    with assert_mdq_error(code):
        mi.age_decrypt(data, [SK1])


def test_every_truncation_fails_cleanly():
    data = os.urandom(2 * CHUNK + 100)
    ct = enc(data)
    cuts = list(range(0, 200)) + [len(ct) - k for k in (1, 15, 16, 17, 100)] + [len(ct) - (CHUNK + 16 + 100 + 16) + k for k in (0, 1)]
    for cut in cuts:
        with pytest.raises(mdq.MdqError) as e:
            mi.age_decrypt(ct[:cut], [SK1])
        assert e.value.code in (SYNTAX, DENIED)


def test_truncation_at_a_chunk_boundary_is_detected(assert_mdq_error):
    # dropping the final chunk leaves a valid-looking full chunk that was sealed as "not last"
    data = os.urandom(CHUNK + 10)
    ct = enc(data)
    with assert_mdq_error(DENIED):
        mi.age_decrypt(ct[:-(10 + 16)], [SK1])


def test_tampering(assert_mdq_error):
    sk, ct, lines, payload, fk = base_file()
    hdr_len = len(ct) - len(payload)
    for pos in (hdr_len + 20, len(ct) - 1, hdr_len - 5):  # payload, tag, MAC
        bad = bytearray(ct)
        bad[pos] ^= 0x01
        with pytest.raises(mdq.MdqError) as e:
            mi.age_decrypt(bytes(bad), [sk])
        assert e.value.code in (DENIED, SYNTAX)
    # changing the header without fixing the MAC
    bad = ct.replace(b"age-encryption.org/v1\n", b"age-encryption.org/v1\n-> grease x\n\n", 1)
    with assert_mdq_error(DENIED):
        mi.age_decrypt(bad, [sk])
    # appending data after the payload
    with assert_mdq_error(DENIED):
        mi.age_decrypt(ct + b"\x00", [sk])


def test_forged_file_with_unknown_stanza_is_accepted():
    sk, ct, lines, payload, fk = base_file()
    assert mi.age_decrypt(forge(lines, payload, fk), [sk]) == b"secret payload"  # forge() matches the real MAC
    assert forge(lines, payload, fk) == ct
    grease = ["-> grease-x a!b ~~", *body_lines(os.urandom(70))]
    assert mi.age_decrypt(forge(lines[:1] + grease + lines[1:], payload, fk), [sk]) == b"secret payload"


@pytest.mark.parametrize("mutate", [
    lambda l: l[:1] + ["-> X25519 " + l[1].split()[2] + " extra"] + l[2:],          # extra argument
    lambda l: l[:1] + ["-> X25519"] + l[2:],                                           # missing share
    lambda l: l[:1] + ["-> X25519 " + b64(bytes(31))] + l[2:],                         # short share
    lambda l: l[:1] + [l[1], *body_lines(os.urandom(33))],                             # body of 33 bytes
    lambda l: l[:1] + [l[1], l[2] + "A"],                                              # non-canonical/long body
    lambda l: l[:1] + ["-> "] + [""],                                                  # empty stanza
    lambda l: l[:1] + ["-> X25519  " + l[1].split()[2]] + l[2:],                       # double space
    lambda l: l[:1] + ["-> X25519 " + l[1].split()[2] + "="] + l[2:],                  # padding
    lambda l: l[:1],                                                                    # no stanzas
    lambda l: l[:1] + [l[1], "A" * 64],                                                # body never ends
    lambda l: l[:1] + ["junk"] + l[1:],                                                # unknown line
    lambda l: l[:1] + ["-> scrypt " + b64(bytes(16)) + " 10", *body_lines(bytes(32))] + l[1:],  # scrypt + X25519
])
def test_malformed_stanzas(assert_mdq_error, mutate):
    sk, ct, lines, payload, fk = base_file()
    with assert_mdq_error(SYNTAX):
        mi.age_decrypt(forge(mutate(lines), payload, fk), [sk])


def test_low_order_share_is_rejected(assert_mdq_error):
    sk, ct, lines, payload, fk = base_file()
    bad = lines[:1] + ["-> X25519 " + b64(bytes(32))] + lines[2:]
    with assert_mdq_error(SYNTAX):
        mi.age_decrypt(forge(bad, payload, fk), [sk])


@pytest.mark.parametrize("log_n", ["08", "0", "abc", "256", "+9", "-1", ""])
def test_malformed_scrypt_work_factor(assert_mdq_error, log_n):
    lines = ["age-encryption.org/v1", f"-> scrypt {b64(bytes(16))} {log_n}".rstrip(), *body_lines(bytes(32))]
    with assert_mdq_error(SYNTAX):
        mi.age_decrypt(forge(lines, bytes(32), bytes(16)), passphrase="pw")


def test_scrypt_work_factor_is_capped(assert_mdq_error):
    # the file chooses the cost: refuse before doing any scrypt work
    lines = ["age-encryption.org/v1", f"-> scrypt {b64(bytes(16))} 21", *body_lines(bytes(32))]
    with assert_mdq_error(LIMIT):
        mi.age_decrypt(forge(lines, bytes(32), bytes(16)), passphrase="pw")
    ct = mi.age_encrypt(b"x", bytes(48), passphrase="pw", work_factor=12)
    with assert_mdq_error(LIMIT):
        mi.age_decrypt(ct, passphrase="pw", max_work_factor=11)
    assert mi.age_decrypt(ct, passphrase="pw", max_work_factor=12) == b"x"


def test_header_limits(assert_mdq_error):
    sk, ct, lines, payload, fk = base_file()
    many = []
    for i in range(256):
        many += [f"-> grease{i}", ""]
    with assert_mdq_error(LIMIT):
        mi.age_decrypt(forge(lines[:1] + many + lines[1:], payload, fk), [sk])
    assert mi.age_decrypt(forge(lines[:1] + many[:-4] + lines[1:], payload, fk), [sk]) == b"secret payload"
    with assert_mdq_error(LIMIT):
        mi.age_decrypt(b"age-encryption.org/v1\n-> x " + b"a" * (1024 * 1024 + 10), [sk])


def test_payload_too_short(assert_mdq_error):
    sk, ct, lines, payload, fk = base_file()
    with assert_mdq_error(SYNTAX):
        mi.age_decrypt(forge(lines, payload[:31], fk), [sk])


# ---------------------------------------------------------------- arguments
@pytest.mark.parametrize("ids", [["AGE-SECRET-KEY-1xyz"], [PK1], ["age-secret-key-1" + SK1[16:].lower()[:-1] + "q"],
                                 [SK1[:20] + SK1[20:].lower()]])
def test_bad_identities(ids):
    with pytest.raises(ValueError):
        mi.age_decrypt(enc(b"x"), ids)


@pytest.mark.parametrize("recipients", [["age1xyz"], [SK1], [PK1.upper()[:10] + PK1[10:]]])
def test_bad_recipients(recipients):
    with pytest.raises(ValueError):
        mi.age_encrypt(b"x", bytes(64), recipients)


def bech32(hrp, data):
    """Reference BIP 173 encoder (test-only), to build keys pyrage refuses to make."""
    cs = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
    acc = bits = 0
    d5 = []
    for byte in data:
        acc, bits = acc << 8 | byte, bits + 8
        while bits >= 5:
            bits -= 5
            d5.append(acc >> bits & 31)
    if bits:
        d5.append(acc << (5 - bits) & 31)

    def polymod(values):
        chk = 1
        for v in values:
            top = chk >> 25
            chk = (chk & 0x1FFFFFF) << 5 ^ v
            for i, g in enumerate([0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3]):
                chk ^= g if top >> i & 1 else 0
        return chk

    exp = [ord(c) >> 5 for c in hrp] + [0] + [ord(c) & 31 for c in hrp]
    pm = polymod(exp + d5 + [0] * 6) ^ 1
    return hrp + "1" + "".join(cs[d] for d in d5) + "".join(cs[pm >> 5 * (5 - i) & 31] for i in range(6))


def test_bech32_helper_matches():
    assert bech32("age", x25519.Identity.from_str(SK1).to_public().__str__().encode()) != PK1  # sanity: different input
    sk = mi.age_identity(FIXED_SECRET)
    assert bech32("age-secret-key-", FIXED_SECRET).upper() == sk


def test_low_order_recipient():
    with pytest.raises(ValueError):
        mi.age_encrypt(b"x", bytes(64), [bech32("age", bytes(32))])
    with pytest.raises(ValueError):  # 31 bytes: right checksum, wrong length
        mi.age_encrypt(b"x", bytes(64), [bech32("age", bytes(31))])


def test_encrypt_argument_rules():
    with pytest.raises(ValueError):
        mi.age_encrypt(b"x", bytes(16))
    with pytest.raises(ValueError):
        mi.age_encrypt(b"x", bytes(80), [PK1], passphrase="pw")
    for wf in (0, 21):
        with pytest.raises(ValueError):
            mi.age_encrypt(b"x", bytes(48), passphrase="pw", work_factor=wf)


@pytest.mark.parametrize("call,exc", [
    (lambda: mi.age_decrypt("text", [SK1]), TypeError),
    (lambda: mi.age_decrypt(bytearray(b"x"), [SK1]), TypeError),
    (lambda: mi.age_decrypt(b"x", SK1), TypeError),
    (lambda: mi.age_decrypt(b"x", [1]), TypeError),
    (lambda: mi.age_decrypt(b"x", [SK1], max_work_factor=True), TypeError),
    (lambda: mi.age_decrypt(b"x", [SK1], max_work_factor=10.0), TypeError),
    (lambda: mi.age_decrypt(b"x", [SK1], max_work_factor=0), ValueError),
    (lambda: mi.age_decrypt(b"x", [SK1], max_work_factor=21), ValueError),
    (lambda: mi.age_encrypt(b"x", "random", [PK1]), TypeError),
    (lambda: mi.age_encrypt(b"x", bytes(64), PK1), TypeError),
    (lambda: mi.age_identity(bytes(31)), ValueError),
])
def test_argument_types(call, exc):
    with pytest.raises(exc):
        call()


# ---------------------------------------------------------------- packages
BASE = "format: mdq\nversion: '0.1'\nentrypoint: document.md\nfeatures: [core, package]\n"


def zpkg(manifest=BASE, more=()):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.yaml", manifest)
        z.writestr("document.md", "# Secret report\n")
        for name, data in more:
            z.writestr(name, data)
    return buf.getvalue()


def test_open_encrypted_package_from_pyrage():
    sealed = pyrage.encrypt(zpkg(), [ID1.to_public()])
    p = mi.package_open_age(sealed, [SK1])
    assert p["entrypoint"] == "document.md" and p["files"]["document.md"] == b"# Secret report\n"


def test_open_encrypted_package_with_passphrase():
    sealed = mi.age_encrypt(zpkg(), os.urandom(48), passphrase="pw", work_factor=4)
    assert mi.package_open_age(sealed, passphrase="pw")["format"] == "mdq"


def test_no_implicit_decryption(assert_mdq_error):
    for sealed in (pyrage.encrypt(zpkg(), [ID1.to_public()]), b"-----BEGIN AGE ENCRYPTED FILE-----\nxx\n"):
        with assert_mdq_error(DENIED):
            mi.package_open(sealed)
    with assert_mdq_error(DENIED):  # credentials missing: nothing is decrypted
        mi.package_open_age(pyrage.encrypt(zpkg(), [ID1.to_public()]))


def test_encrypted_package_wrong_key_and_bad_contents(assert_mdq_error):
    with assert_mdq_error(DENIED):
        mi.package_open_age(pyrage.encrypt(zpkg(), [ID1.to_public()]), [SK2])
    with assert_mdq_error(SYNTAX):  # plaintext is not a zip
        mi.package_open_age(enc(b"not a zip"), [SK1])
    with assert_mdq_error(DENIED):  # age inside age
        mi.package_open_age(enc(enc(zpkg())), [SK1])
    with assert_mdq_error(DENIED):  # zip errors still apply after decryption
        mi.package_open_age(enc(zpkg(more=[("../evil", "x")])), [SK1])


def test_encrypted_package_limits(assert_mdq_error):
    sealed = enc(zpkg())
    with assert_mdq_error(LIMIT):
        mi.package_open_age(sealed, [SK1], limits={"max_entries": 1})
    big = enc(os.urandom(200_000))
    with assert_mdq_error(LIMIT):  # refused before decrypting: ciphertext far over the archive limit
        mi.package_open_age(big, [SK1], limits={"max_archive_size": 1000})


def test_manifest_cannot_claim_encryption():
    # a plain zip whose manifest says "encrypted" is just a plain package with a metadata value
    p = mi.package_open(zpkg(BASE + "encrypted: true\nsecurity:\n  network: none\n"))
    assert p["metadata"] == {"encrypted": True}
    with pytest.raises(mdq.MdqError) as e:
        mi.package_open_age(zpkg(), [SK1])
    assert e.value.code == SYNTAX  # not age: never treated as encrypted


@pytest.mark.xfail(strict=True, reason="GAP-4: per-member encryption layout is not specified (package.md)")
def test_per_member_encryption_layout():
    member = enc(b"a,b\n1,2\n")
    p = mi.package_open_age(zpkg(more=[("data/table.csv.age", member)]), [SK1])
    assert p["files"]["data/table.csv"] == b"a,b\n1,2\n"
