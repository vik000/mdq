//! age v1 file encryption (https://age-encryption.org/v1) for `.mdq.age` packages (E7.3, G49).
//!
//! The cryptographic PRIMITIVES come from audited RustCrypto crates (X25519, ChaCha20-Poly1305,
//! HKDF-SHA256, HMAC-SHA256, scrypt). This file only implements the age FORMAT around them:
//!
//! ```text
//! age-encryption.org/v1
//! -> X25519 <ephemeral share, base64>        one "stanza" per recipient: the 16-byte file key,
//! <wrapped file key, base64, 64 columns>     wrapped for that recipient
//! --- <HMAC of the header, base64>
//! <16-byte nonce><payload in 64 KiB chunks, each ChaCha20-Poly1305 sealed (STREAM)>
//! ```
//!
//! The core never reads the OS random source: `age_encrypt` takes the random bytes it needs from
//! the caller, so the same input and the same random bytes always give the same output.

use crate::diag::{Code, Diagnostic};
use chacha20poly1305::aead::{Aead, KeyInit};
use chacha20poly1305::{ChaCha20Poly1305, Key, Nonce};
use hkdf::Hkdf;
use hmac::{Hmac, Mac};
use sha2::Sha256;
use x25519_dalek::{PublicKey, StaticSecret};

type Res<T> = Result<T, Diagnostic>;

const MAGIC: &str = "age-encryption.org/v1";
const ARMOR: &str = "-----BEGIN AGE ENCRYPTED FILE-----";
const CHUNK: usize = 64 * 1024;
const TAG: usize = 16;
/// Hard cap for the scrypt work factor accepted when decrypting (log2 N). At 20, scrypt needs
/// 1 GiB of memory; the file chooses the value, so it must be bounded.
pub const MAX_WORK_FACTOR: u8 = 20;
/// Bounds on the header, which is attacker-controlled: total size and number of stanzas.
const MAX_HEADER: usize = 1024 * 1024;
const MAX_STANZAS: usize = 256;

fn syntax(msg: &str) -> Diagnostic {
    Diagnostic::new(Code::InvalidSyntax, format!("invalid age file: {}", msg))
}

fn denied(msg: &str) -> Diagnostic {
    Diagnostic::new(Code::SecurityDenied, msg.to_string())
}

// ---------------------------------------------------------------- base64 (standard, no padding)

const B64: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

fn b64_encode(data: &[u8]) -> String {
    let mut out = String::new();
    for chunk in data.chunks(3) {
        // Pack up to 3 bytes into a 24-bit number, then emit 4 (or fewer) 6-bit digits.
        let n = chunk.len();
        let v = (chunk[0] as u32) << 16 | (*chunk.get(1).unwrap_or(&0) as u32) << 8 | *chunk.get(2).unwrap_or(&0) as u32;
        for k in 0..=n {
            out.push(B64[((v >> (18 - 6 * k)) & 63) as usize] as char);
        }
    }
    out
}

/// Decode unpadded base64, refusing non-canonical text (unused bits must be zero), as age requires.
fn b64_decode(s: &str) -> Option<Vec<u8>> {
    if s.len() % 4 == 1 {
        return None;
    }
    let mut out = Vec::new();
    let mut acc: u32 = 0;
    let mut bits = 0;
    for ch in s.bytes() {
        let v = B64.iter().position(|&c| c == ch)? as u32;
        acc = acc << 6 | v;
        bits += 6;
        if bits >= 8 {
            bits -= 8;
            out.push((acc >> bits) as u8);
            acc &= (1 << bits) - 1;
        }
    }
    // Leftover bits (2 or 4) must all be zero, otherwise two spellings would decode the same.
    if acc != 0 {
        return None;
    }
    Some(out)
}

// ---------------------------------------------------------------- Bech32 keys (BIP 173)

const BECH32: &[u8; 32] = b"qpzry9x8gf2tvdw0s3jn54khce6mua7l";

fn polymod(values: &[u8]) -> u32 {
    const GEN: [u32; 5] = [0x3b6a57b2, 0x26508e6d, 0x1ea119fa, 0x3d4233dd, 0x2a1462b3];
    let mut chk: u32 = 1;
    for &v in values {
        let top = chk >> 25;
        chk = (chk & 0x1ff_ffff) << 5 ^ v as u32;
        for (i, g) in GEN.iter().enumerate() {
            if (top >> i) & 1 == 1 {
                chk ^= g;
            }
        }
    }
    chk
}

fn hrp_expand(hrp: &str) -> Vec<u8> {
    let mut v: Vec<u8> = hrp.bytes().map(|c| c >> 5).collect();
    v.push(0);
    v.extend(hrp.bytes().map(|c| c & 31));
    v
}

/// Regroup bits: 8-bit bytes to 5-bit digits (pad = true) or back (pad = false, strict).
fn convert_bits(data: &[u8], from: u32, to: u32, pad: bool) -> Option<Vec<u8>> {
    let mut acc: u32 = 0;
    let mut bits = 0;
    let mut out = Vec::new();
    let maxv = (1 << to) - 1;
    for &d in data {
        acc = acc << from | d as u32;
        bits += from;
        while bits >= to {
            bits -= to;
            out.push(((acc >> bits) & maxv) as u8);
        }
    }
    if pad {
        if bits > 0 {
            out.push(((acc << (to - bits)) & maxv) as u8);
        }
    } else if bits >= from || (acc << (to - bits)) & maxv != 0 {
        return None;
    }
    Some(out)
}

fn bech32_encode(hrp: &str, data: &[u8]) -> String {
    let d5 = convert_bits(data, 8, 5, true).unwrap_or_default();
    let mut values = hrp_expand(hrp);
    values.extend(&d5);
    values.extend([0u8; 6]);
    let pm = polymod(&values) ^ 1;
    let mut out = format!("{}1", hrp);
    for &d in &d5 {
        out.push(BECH32[d as usize] as char);
    }
    for i in 0..6 {
        out.push(BECH32[((pm >> (5 * (5 - i))) & 31) as usize] as char);
    }
    out
}

/// Decode a Bech32 string with the expected human-readable part. Mixed case is invalid.
fn bech32_decode(s: &str, want_hrp: &str) -> Option<Vec<u8>> {
    if s.bytes().any(|c| c.is_ascii_lowercase()) && s.bytes().any(|c| c.is_ascii_uppercase()) {
        return None;
    }
    let s = s.to_ascii_lowercase();
    let sep = s.rfind('1')?;
    let (hrp, rest) = (&s[..sep], &s[sep + 1..]);
    if hrp != want_hrp || rest.len() < 6 {
        return None;
    }
    let mut d5 = Vec::new();
    for c in rest.bytes() {
        d5.push(BECH32.iter().position(|&x| x == c)? as u8);
    }
    let mut values = hrp_expand(hrp);
    values.extend(&d5);
    if polymod(&values) != 1 {
        return None;
    }
    convert_bits(&d5[..d5.len() - 6], 5, 8, false)
}

fn key32(v: Option<Vec<u8>>) -> Option<[u8; 32]> {
    v?.try_into().ok()
}

/// Parse an `AGE-SECRET-KEY-1...` identity. Errors are plain text (the wrapper raises ValueError):
/// identities come from the trusted host, so a bad one is a programming error, not hostile input.
pub fn parse_identity(s: &str) -> Result<[u8; 32], String> {
    key32(bech32_decode(s, "age-secret-key-")).ok_or_else(|| "invalid age identity (expected AGE-SECRET-KEY-1...)".to_string())
}

/// Parse an `age1...` X25519 recipient.
pub fn parse_recipient(s: &str) -> Result<[u8; 32], String> {
    key32(bech32_decode(s, "age")).ok_or_else(|| "invalid age recipient (expected age1...)".to_string())
}

/// The `age1...` recipient (public key) of an identity.
pub fn recipient_of(identity: &[u8; 32]) -> String {
    let pk = PublicKey::from(&StaticSecret::from(*identity));
    bech32_encode("age", pk.as_bytes())
}

/// The `AGE-SECRET-KEY-1...` text of 32 secret bytes (age writes it in upper case).
pub fn identity_text(identity: &[u8; 32]) -> String {
    bech32_encode("age-secret-key-", identity).to_ascii_uppercase()
}

// ---------------------------------------------------------------- key derivation helpers

fn hkdf(ikm: &[u8], salt: &[u8], info: &[u8]) -> [u8; 32] {
    let mut out = [0u8; 32];
    // `expand` fails only for outputs longer than 255 * 32 bytes; 32 is always fine.
    let _ = Hkdf::<Sha256>::new(Some(salt), ikm).expand(info, &mut out);
    out
}

/// Seal (or open) the 16-byte file key with a wrap key and an all-zero nonce, as age specifies.
fn wrap(key: &[u8; 32], file_key: &[u8; 16]) -> Vec<u8> {
    let c = ChaCha20Poly1305::new(Key::from_slice(key));
    // Encryption with a 16-byte plaintext cannot fail.
    c.encrypt(Nonce::from_slice(&[0u8; 12]), &file_key[..]).unwrap_or_default()
}

fn unwrap(key: &[u8; 32], body: &[u8]) -> Option<[u8; 16]> {
    let c = ChaCha20Poly1305::new(Key::from_slice(key));
    c.decrypt(Nonce::from_slice(&[0u8; 12]), body).ok()?.try_into().ok()
}

fn header_mac(file_key: &[u8; 16], header: &[u8]) -> Hmac<Sha256> {
    let k = hkdf(file_key, b"", b"header");
    // HMAC accepts keys of any length.
    let mut m = match <Hmac<Sha256> as Mac>::new_from_slice(&k) {
        Ok(m) => m,
        // Unreachable (HMAC accepts any key length); a fixed key keeps the MAC check failing safely.
        Err(_) => <Hmac<Sha256> as Mac>::new_from_slice(&[0u8; 32]).unwrap_or_else(|_| unreachable!()),
    };
    m.update(header);
    m
}

fn scrypt_key(passphrase: &str, salt: &[u8], log_n: u8) -> Option<[u8; 32]> {
    let mut s = b"age-encryption.org/v1/scrypt".to_vec();
    s.extend_from_slice(salt);
    let params = scrypt::Params::new(log_n, 8, 1, 32).ok()?;
    let mut out = [0u8; 32];
    scrypt::scrypt(passphrase.as_bytes(), &s, &params, &mut out).ok()?;
    Some(out)
}

// ---------------------------------------------------------------- header parsing

struct Stanza {
    args: Vec<String>, // args[0] is the stanza type
    body: Vec<u8>,
}

struct Header {
    stanzas: Vec<Stanza>,
    /// The bytes covered by the MAC: everything up to and including `---`.
    mac_input_end: usize,
    mac: Vec<u8>,
    /// Where the payload (nonce + chunks) starts.
    payload: usize,
}

/// A visible ASCII character (age "VCHAR"), allowed in stanza arguments.
fn vchar(b: u8) -> bool {
    (0x21..=0x7e).contains(&b)
}

fn parse_header(d: &[u8]) -> Res<Header> {
    let mut pos = 0;
    // Read one LF-terminated line as text; a missing LF means the header is truncated.
    let line = |pos: &mut usize| -> Res<(usize, String)> {
        let start = *pos;
        let rel = d[start..].iter().take(MAX_HEADER).position(|&b| b == b'\n').ok_or_else(|| {
            if d.len() - start >= MAX_HEADER {
                Diagnostic::new(Code::ResourceLimit, "the age header is larger than 1 MiB")
            } else {
                syntax("the header is truncated")
            }
        })?;
        *pos = start + rel + 1;
        if *pos > MAX_HEADER {
            return Err(Diagnostic::new(Code::ResourceLimit, "the age header is larger than 1 MiB"));
        }
        let text = std::str::from_utf8(&d[start..start + rel]).map_err(|_| syntax("the header is not text"))?;
        Ok((start, text.to_string()))
    };
    let (_, first) = line(&mut pos)?;
    if first != MAGIC {
        return Err(syntax("bad header line"));
    }
    let mut stanzas = Vec::new();
    let (mut start, mut l) = line(&mut pos)?;
    loop {
        if let Some(rest) = l.strip_prefix("-> ") {
            if stanzas.len() == MAX_STANZAS {
                return Err(Diagnostic::new(Code::ResourceLimit, "the age header has more than 256 stanzas"));
            }
            let args: Vec<String> = rest.split(' ').map(str::to_string).collect();
            if args.iter().any(|a| a.is_empty() || !a.bytes().all(vchar)) {
                return Err(syntax("malformed stanza"));
            }
            // Body: base64 lines of exactly 64 characters, ended by a shorter (possibly empty) one.
            let mut text = String::new();
            loop {
                let (_, b) = line(&mut pos)?;
                let full = b.len() == 64;
                if b.len() > 64 {
                    return Err(syntax("malformed stanza body"));
                }
                text.push_str(&b);
                if !full {
                    break;
                }
            }
            let body = b64_decode(&text).ok_or_else(|| syntax("malformed stanza body"))?;
            stanzas.push(Stanza { args, body });
            let next = line(&mut pos)?;
            start = next.0;
            l = next.1;
        } else if let Some(mac_text) = l.strip_prefix("--- ") {
            let mac = b64_decode(mac_text).filter(|m| m.len() == 32).ok_or_else(|| syntax("malformed header MAC"))?;
            if stanzas.is_empty() {
                return Err(syntax("the header has no recipients"));
            }
            return Ok(Header { stanzas, mac_input_end: start + 3, mac, payload: pos });
        } else {
            return Err(syntax("unexpected header line"));
        }
    }
}

// ---------------------------------------------------------------- STREAM payload

fn chunk_nonce(counter: u64, last: bool) -> [u8; 12] {
    // 11-byte big-endian counter, then a flag byte: 1 marks the final chunk.
    let mut n = [0u8; 12];
    n[3..11].copy_from_slice(&counter.to_be_bytes());
    n[11] = last as u8;
    n
}

fn payload_decrypt(file_key: &[u8; 16], data: &[u8]) -> Res<Vec<u8>> {
    if data.len() < 16 + TAG {
        return Err(syntax("the payload is truncated"));
    }
    let key = hkdf(file_key, &data[..16], b"payload");
    let c = ChaCha20Poly1305::new(Key::from_slice(&key));
    let body = &data[16..];
    let chunks: Vec<&[u8]> = body.chunks(CHUNK + TAG).collect();
    let mut out = Vec::with_capacity(body.len());
    for (i, ch) in chunks.iter().enumerate() {
        let last = i + 1 == chunks.len();
        if ch.len() < TAG {
            return Err(syntax("the payload is truncated"));
        }
        // Only an empty file may end with an empty chunk.
        if last && ch.len() == TAG && i > 0 {
            return Err(syntax("the payload ends with an empty chunk"));
        }
        let plain = c
            .decrypt(Nonce::from_slice(&chunk_nonce(i as u64, last)), *ch)
            .map_err(|_| denied("the encrypted data was modified or truncated"))?;
        out.extend_from_slice(&plain);
    }
    Ok(out)
}

fn payload_encrypt(file_key: &[u8; 16], nonce: &[u8], data: &[u8]) -> Vec<u8> {
    let key = hkdf(file_key, nonce, b"payload");
    let c = ChaCha20Poly1305::new(Key::from_slice(&key));
    let mut out = nonce.to_vec();
    let mut chunks: Vec<&[u8]> = data.chunks(CHUNK).collect();
    if chunks.is_empty() {
        chunks.push(&[]);
    }
    let n = chunks.len();
    for (i, ch) in chunks.into_iter().enumerate() {
        // Encryption with a valid key and nonce cannot fail.
        out.extend(c.encrypt(Nonce::from_slice(&chunk_nonce(i as u64, i + 1 == n)), ch).unwrap_or_default());
    }
    out
}

// ---------------------------------------------------------------- decrypt

/// Does `data` look like an age file (binary or armored)? Used to refuse implicit decryption.
pub fn is_age(data: &[u8]) -> bool {
    data.starts_with(b"age-encryption.org/") || data.starts_with(ARMOR.as_bytes())
}

/// Decrypt an age file with X25519 identities and/or a passphrase.
///
/// Errors: not age or malformed MDQ_INVALID_SYNTAX; armored or another age version
/// MDQ_UNSUPPORTED_FEATURE; no key, wrong key, modified or truncated data MDQ_SECURITY_DENIED;
/// scrypt work factor above `max_work_factor` or a huge header MDQ_RESOURCE_LIMIT.
pub fn age_decrypt(data: &[u8], identities: &[[u8; 32]], passphrase: Option<&str>, max_work_factor: u8) -> Res<Vec<u8>> {
    if data.starts_with(ARMOR.as_bytes()) {
        return Err(Diagnostic::new(Code::UnsupportedFeature, "armored (PEM) age files are not supported"));
    }
    if !data.starts_with(b"age-encryption.org/") {
        return Err(syntax("not an age file"));
    }
    if !data.starts_with(format!("{}\n", MAGIC).as_bytes()) {
        // Only a COMPLETE first line naming another version (`v` + digits) is a newer format;
        // a cut-off or otherwise mangled line (e.g. CRLF) is a malformed file.
        let first = data.split(|&b| b == b'\n').next().unwrap_or(&[]);
        let complete = data.len() > first.len();
        let ver = &first[b"age-encryption.org/".len()..];
        if complete && ver.len() > 1 && ver[0] == b'v' && ver[1..].iter().all(u8::is_ascii_digit) {
            return Err(Diagnostic::new(Code::UnsupportedFeature, "unsupported age format version"));
        }
        return Err(syntax("bad header line"));
    }
    let h = parse_header(data)?;

    // Check the known stanza types strictly; unknown types are ignored (age "grease").
    let mut scrypt_stanza = None;
    for s in &h.stanzas {
        match s.args[0].as_str() {
            "X25519" => {
                let share = s.args.get(1).and_then(|a| b64_decode(a)).filter(|v| v.len() == 32);
                if s.args.len() != 2 || share.is_none() || s.body.len() != 32 {
                    return Err(syntax("malformed X25519 stanza"));
                }
            }
            "scrypt" => {
                let salt_ok = s.args.get(1).and_then(|a| b64_decode(a)).map(|v| v.len() == 16).unwrap_or(false);
                let n = s.args.get(2).map(|x| x.as_str()).unwrap_or("");
                // Decimal without leading zeros, 1..=255.
                let log_n = n.parse::<u8>().ok().filter(|v| *v >= 1 && !n.starts_with('0') && n.bytes().all(|b| b.is_ascii_digit()));
                if s.args.len() != 3 || !salt_ok || log_n.is_none() || s.body.len() != 32 {
                    return Err(syntax("malformed scrypt stanza"));
                }
                if h.stanzas.len() != 1 {
                    return Err(syntax("an scrypt stanza must be the only one"));
                }
                scrypt_stanza = Some((s, log_n.unwrap_or(0)));
            }
            _ => {}
        }
    }
    if identities.is_empty() && passphrase.is_none() {
        return Err(denied("the package is encrypted and no identity or passphrase was given"));
    }

    let file_key = if let Some((s, log_n)) = scrypt_stanza {
        let Some(pw) = passphrase else {
            return Err(denied("the package is encrypted with a passphrase and none was given"));
        };
        if log_n > max_work_factor {
            return Err(Diagnostic::new(
                Code::ResourceLimit,
                format!("the scrypt work factor {} is above the limit {}", log_n, max_work_factor),
            ));
        }
        let salt = b64_decode(&s.args[1]).unwrap_or_default();
        let key = scrypt_key(pw, &salt, log_n).ok_or_else(|| syntax("bad scrypt parameters"))?;
        unwrap(&key, &s.body).ok_or_else(|| denied("the passphrase does not match"))?
    } else {
        let mut found = None;
        'outer: for s in h.stanzas.iter().filter(|s| s.args[0] == "X25519") {
            let share: [u8; 32] = b64_decode(&s.args[1]).and_then(|v| v.try_into().ok()).unwrap_or([0; 32]);
            for id in identities {
                let secret = StaticSecret::from(*id);
                let shared = secret.diffie_hellman(&PublicKey::from(share));
                // A low-order share gives an all-zero secret: age requires rejecting it.
                if !shared.was_contributory() {
                    return Err(syntax("invalid X25519 share"));
                }
                let mut salt = share.to_vec();
                salt.extend_from_slice(PublicKey::from(&secret).as_bytes());
                let key = hkdf(shared.as_bytes(), &salt, b"age-encryption.org/v1/X25519");
                if let Some(fk) = unwrap(&key, &s.body) {
                    found = Some(fk);
                    break 'outer;
                }
            }
        }
        found.ok_or_else(|| denied("no identity matches the encrypted package"))?
    };

    // Verify the header MAC (constant-time comparison inside `verify_slice`).
    header_mac(&file_key, &data[..h.mac_input_end])
        .verify_slice(&h.mac)
        .map_err(|_| denied("the age header was modified"))?;
    payload_decrypt(&file_key, &data[h.payload..])
}

// ---------------------------------------------------------------- encrypt

/// Number of random bytes `age_encrypt` needs: 16 (file key) + 32 per X25519 recipient, or
/// + 16 (salt) for a passphrase, + 16 (payload nonce).
pub fn random_len(recipients: usize, passphrase: bool) -> usize {
    16 + if passphrase { 16 } else { 32 * recipients } + 16
}

/// Encrypt `data` to X25519 recipients or (exclusively) a passphrase, using exactly
/// `random_len(...)` caller-supplied random bytes, consumed in this order: file key, then each
/// recipient's ephemeral secret (or the scrypt salt), then the payload nonce.
/// Errors are argument errors (the wrapper raises ValueError).
pub fn age_encrypt(data: &[u8], recipients: &[[u8; 32]], passphrase: Option<(&str, u8)>, random: &[u8]) -> Result<Vec<u8>, String> {
    if recipients.is_empty() == passphrase.is_none() {
        return Err("give either X25519 recipients or a passphrase (not both, not neither)".to_string());
    }
    if recipients.len() > MAX_STANZAS {
        return Err("too many recipients (at most 256)".to_string());
    }
    let need = random_len(recipients.len(), passphrase.is_some());
    if random.len() != need {
        return Err(format!("random must be exactly {} bytes", need));
    }
    let mut file_key = [0u8; 16];
    file_key.copy_from_slice(&random[..16]);
    let mut at = 16;
    let mut header = format!("{}\n", MAGIC);
    let stanza = |args: &str, body: &[u8], header: &mut String| {
        header.push_str(&format!("-> {}\n", args));
        let text = b64_encode(body);
        // Wrap at 64 columns; the final line is always shorter than 64 (possibly empty).
        let mut rest = text.as_str();
        while rest.len() >= 64 {
            header.push_str(&rest[..64]);
            header.push('\n');
            rest = &rest[64..];
        }
        header.push_str(rest);
        header.push('\n');
    };
    if let Some((pw, log_n)) = passphrase {
        if !(1..=MAX_WORK_FACTOR).contains(&log_n) {
            return Err(format!("work_factor must be between 1 and {}", MAX_WORK_FACTOR));
        }
        let salt = &random[at..at + 16];
        at += 16;
        let key = scrypt_key(pw, salt, log_n).ok_or("bad scrypt parameters")?;
        stanza(&format!("scrypt {} {}", b64_encode(salt), log_n), &wrap(&key, &file_key), &mut header);
    } else {
        for pk in recipients {
            let mut e = [0u8; 32];
            e.copy_from_slice(&random[at..at + 32]);
            at += 32;
            let eph = StaticSecret::from(e);
            let share = PublicKey::from(&eph);
            let shared = eph.diffie_hellman(&PublicKey::from(*pk));
            if !shared.was_contributory() {
                return Err("invalid age recipient (low-order point)".to_string());
            }
            let mut salt = share.as_bytes().to_vec();
            salt.extend_from_slice(pk);
            let key = hkdf(shared.as_bytes(), &salt, b"age-encryption.org/v1/X25519");
            stanza(&format!("X25519 {}", b64_encode(share.as_bytes())), &wrap(&key, &file_key), &mut header);
        }
    }
    header.push_str("---");
    let mac = header_mac(&file_key, header.as_bytes()).finalize().into_bytes();
    header.push_str(&format!(" {}\n", b64_encode(&mac)));
    let mut out = header.into_bytes();
    out.extend(payload_encrypt(&file_key, &random[at..at + 16], data));
    Ok(out)
}
