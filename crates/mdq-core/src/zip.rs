//! Hostile-input ZIP reader for `.mdq` packages (package.md; E7.1, G47).
//!
//! `zip_read` takes the WHOLE archive as bytes (the host reads the file; the core does no I/O)
//! and either returns every member, fully checked, or fails with one diagnostic. Nothing is
//! "best effort": an archive that two zip tools could read differently is rejected.
//!
//! Checks, in order (all central-directory checks happen before anything is decompressed):
//! 1. archive size limit;
//! 2. exactly one end-of-central-directory record (EOCD), ending the file;
//! 3. no zip64, no multi-disk, entry-count limit;
//! 4. each central-directory entry: flags, method, sizes, name, file type, extra fields;
//! 5. names: duplicates, case/NFC collisions, a name used as both file and directory;
//! 6. size limits (member, total, compression ratio) from the declared sizes;
//! 7. local headers agree with the central directory and the records tile the file exactly
//!    (no gaps, no overlaps, nothing before the first record);
//! 8. decompress each member (bounded by its declared size) and check size and CRC-32.
//!
//! Messages identify a member by its position ("member 3"), never by its name: names come from
//! the archive and may be hostile.

use crate::diag::{Code, Diagnostic};
use crate::inflate::{crc32, inflate_bounded, InflateError};
use std::collections::{HashMap, HashSet};
use unicode_normalization::UnicodeNormalization;

type Res<T> = Result<T, Diagnostic>;

fn err(code: Code, msg: impl Into<String>) -> Diagnostic {
    Diagnostic::new(code, msg)
}

fn bad(msg: impl Into<String>) -> Diagnostic {
    err(Code::InvalidSyntax, msg)
}

const MIB: u64 = 1024 * 1024;

/// Members at or below this size are never refused for their compression ratio
/// (tiny text files compress extremely well and are harmless).
pub const RATIO_MIN_SIZE: u64 = MIB;

/// Effective limits. Every value is >= 1.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ZipLimits {
    pub max_entries: u64,
    pub max_member_size: u64,
    pub max_total_size: u64,
    pub max_archive_size: u64,
    pub max_ratio: u64,
}

/// The limit names, their defaults and their hard caps (a host value above the cap is clamped).
pub const LIMIT_KEYS: [(&str, u64, u64); 5] = [
    ("max_entries", 1_000, 10_000),
    ("max_member_size", 16 * MIB, 64 * MIB),
    ("max_total_size", 64 * MIB, 256 * MIB),
    ("max_archive_size", 64 * MIB, 256 * MIB),
    ("max_ratio", 100, 100),
];

impl ZipLimits {
    /// Build limits from `(name, value)` pairs given by the host. Missing names get the default.
    /// Unknown names and values below 1 are `Err(message)`; the wrapper raises ValueError.
    pub fn from_pairs(pairs: &[(String, u64)]) -> Result<ZipLimits, String> {
        let mut vals: Vec<u64> = LIMIT_KEYS.iter().map(|k| k.1).collect();
        let mut seen = HashSet::new();
        for (name, v) in pairs {
            let i = LIMIT_KEYS
                .iter()
                .position(|k| k.0 == name)
                .ok_or_else(|| "unknown zip limit".to_string())?;
            if !seen.insert(i) {
                return Err("a zip limit is given twice".to_string());
            }
            if *v < 1 {
                return Err(format!("{} must be at least 1", LIMIT_KEYS[i].0));
            }
            // `min` clamps to the hard cap.
            vals[i] = (*v).min(LIMIT_KEYS[i].2);
        }
        Ok(ZipLimits {
            max_entries: vals[0],
            max_member_size: vals[1],
            max_total_size: vals[2],
            max_archive_size: vals[3],
            max_ratio: vals[4],
        })
    }

    pub fn defaults() -> ZipLimits {
        ZipLimits {
            max_entries: LIMIT_KEYS[0].1,
            max_member_size: LIMIT_KEYS[1].1,
            max_total_size: LIMIT_KEYS[2].1,
            max_archive_size: LIMIT_KEYS[3].1,
            max_ratio: LIMIT_KEYS[4].1,
        }
    }

    pub fn as_pairs(&self) -> [(&'static str, u64); 5] {
        [
            ("max_entries", self.max_entries),
            ("max_member_size", self.max_member_size),
            ("max_total_size", self.max_total_size),
            ("max_archive_size", self.max_archive_size),
            ("max_ratio", self.max_ratio),
        ]
    }
}

/// Compression method of a member.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Method {
    Stored,
    Deflate,
}

impl Method {
    pub fn as_str(self) -> &'static str {
        match self {
            Method::Stored => "stored",
            Method::Deflate => "deflate",
        }
    }
}

/// One file member, decompressed and verified.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Member {
    pub name: String,
    pub method: Method,
    pub size: u64,
    pub compressed_size: u64,
    pub crc32: u32,
    pub data: Vec<u8>,
}

/// The archive contents: files in central-directory order, and directory entries (names
/// ending in `/`), which carry no data and are listed only for completeness.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Archive {
    pub members: Vec<Member>,
    pub directories: Vec<String>,
}

// ---------------------------------------------------------------- little-endian helpers

/// Read a little-endian u16 at `at`, or None if it does not fit.
fn u16_at(d: &[u8], at: usize) -> Option<u16> {
    // `get(a..b)` is a checked slice; `try_into` turns a 2-byte slice into a [u8; 2].
    let b: [u8; 2] = d.get(at..at.checked_add(2)?)?.try_into().ok()?;
    Some(u16::from_le_bytes(b))
}

fn u32_at(d: &[u8], at: usize) -> Option<u32> {
    let b: [u8; 4] = d.get(at..at.checked_add(4)?)?.try_into().ok()?;
    Some(u32::from_le_bytes(b))
}

const SIG_LOCAL: u32 = 0x0403_4b50; // "PK\x03\x04"
const SIG_CENTRAL: u32 = 0x0201_4b50; // "PK\x01\x02"
const SIG_EOCD: u32 = 0x0605_4b50; // "PK\x05\x06"
const SIG_ZIP64_LOCATOR: u32 = 0x0706_4b50; // "PK\x06\x07"
const SIG_DESCRIPTOR: u32 = 0x0807_4b50; // "PK\x07\x08"
const EOCD_LEN: usize = 22;
const CENTRAL_LEN: usize = 46;
const LOCAL_LEN: usize = 30;

// General-purpose flag bits.
const FLAG_ENCRYPTED: u16 = 1 << 0;
const FLAG_DESCRIPTOR: u16 = 1 << 3;
const FLAG_STRONG_ENCRYPTION: u16 = 1 << 6;
const FLAG_UTF8: u16 = 1 << 11;
const FLAG_MASKED_HEADERS: u16 = 1 << 13;
/// Bits we understand: 1-2 (deflate speed hint, no effect on decoding), 3, 11.
const FLAGS_ALLOWED: u16 = (1 << 1) | (1 << 2) | FLAG_DESCRIPTOR | FLAG_UTF8;

fn zip64() -> Diagnostic {
    err(Code::UnsupportedFeature, "zip64 archives are not supported")
}

// ---------------------------------------------------------------- EOCD

struct Eocd {
    offset: usize,
    entries: usize,
    cd_size: usize,
    cd_offset: usize,
}

fn find_eocd(d: &[u8]) -> Res<Eocd> {
    if d.len() < EOCD_LEN {
        return Err(bad("not a zip archive (no end of central directory record)"));
    }
    // The EOCD is 22 bytes plus a comment of at most 65535 bytes, so search the tail only.
    let lowest = d.len().saturating_sub(EOCD_LEN + 0xFFFF);
    let mut any_sig = false;
    let mut found: Option<usize> = None;
    let mut at = d.len() - EOCD_LEN;
    loop {
        if u32_at(d, at) == Some(SIG_EOCD) {
            any_sig = true;
            let clen = u16_at(d, at + 20).unwrap_or(0) as usize;
            if at + EOCD_LEN + clen == d.len() {
                found = Some(at);
                break;
            }
        }
        if at == lowest {
            break;
        }
        at -= 1;
    }
    let off = match found {
        Some(o) => o,
        None if any_sig => return Err(bad("data follows the end of the zip archive")),
        None => return Err(bad("not a zip archive (no end of central directory record)")),
    };
    // A second EOCD signature in the comment means two readers could pick different records.
    let comment = &d[off + EOCD_LEN..];
    if comment.windows(4).any(|w| w == SIG_EOCD.to_le_bytes()) {
        return Err(bad("the archive has more than one end of central directory record"));
    }
    let disk = u16_at(d, off + 4).unwrap_or(0);
    let cd_disk = u16_at(d, off + 6).unwrap_or(0);
    let n_disk = u16_at(d, off + 8).unwrap_or(0);
    let n_total = u16_at(d, off + 10).unwrap_or(0);
    let cd_size = u32_at(d, off + 12).unwrap_or(0);
    let cd_offset = u32_at(d, off + 16).unwrap_or(0);
    if n_disk == 0xFFFF || n_total == 0xFFFF || cd_size == 0xFFFF_FFFF || cd_offset == 0xFFFF_FFFF {
        return Err(zip64());
    }
    if disk == 0xFFFF || cd_disk == 0xFFFF {
        return Err(zip64());
    }
    if disk != 0 || cd_disk != 0 {
        return Err(err(Code::UnsupportedFeature, "multi-disk zip archives are not supported"));
    }
    if n_disk != n_total {
        return Err(bad("the entry counts of the end of central directory record disagree"));
    }
    Ok(Eocd { offset: off, entries: n_total as usize, cd_size: cd_size as usize, cd_offset: cd_offset as usize })
}

// ---------------------------------------------------------------- names

/// Check a member name (already decoded, trailing `/` of a directory removed).
fn check_name(name: &str, idx: usize) -> Res<()> {
    let m = |s: &str| format!("member {}: {}", idx, s);
    if name.is_empty() {
        return Err(bad(m("the name is empty")));
    }
    if name.len() > 4096 {
        return Err(err(Code::ResourceLimit, m("the name is longer than 4096 bytes")));
    }
    if name.chars().any(|c| c < ' ' || c == '\u{7f}') {
        return Err(bad(m("the name contains a control character")));
    }
    if name.starts_with('/') {
        return Err(err(Code::SecurityDenied, m("the name is an absolute path")));
    }
    if name.contains('\\') {
        return Err(err(Code::SecurityDenied, m("the name contains a backslash")));
    }
    if name.contains(':') {
        return Err(err(Code::SecurityDenied, m("the name contains a colon (drive letter or stream)")));
    }
    for seg in name.split('/') {
        if seg == ".." {
            return Err(err(Code::SecurityDenied, m("the name leaves the archive root (..)")));
        }
        if seg.is_empty() || seg == "." {
            return Err(bad(m("the name has an empty or '.' segment")));
        }
        if seg.len() > 255 {
            return Err(err(Code::ResourceLimit, m("a name segment is longer than 255 bytes")));
        }
    }
    Ok(())
}

/// The key used to detect names that would collide when extracted: NFC, then lower case.
fn collision_key(name: &str) -> String {
    name.nfc().collect::<String>().to_lowercase()
}

// ---------------------------------------------------------------- extra fields

/// Walk the (id, length, data) records of an extra field. zip64 records are refused.
fn check_extra(extra: &[u8], idx: usize) -> Res<()> {
    let mut at = 0;
    while at < extra.len() {
        let (id, len) = match (u16_at(extra, at), u16_at(extra, at + 2)) {
            (Some(i), Some(l)) => (i, l as usize),
            _ => return Err(bad(format!("member {}: malformed extra field", idx))),
        };
        if at + 4 + len > extra.len() {
            return Err(bad(format!("member {}: malformed extra field", idx)));
        }
        if id == 0x0001 {
            return Err(zip64());
        }
        at += 4 + len;
    }
    Ok(())
}

// ---------------------------------------------------------------- central directory

/// What the central directory says about one entry.
struct Entry {
    idx: usize, // 1-based position, for messages
    name: String,
    name_bytes: Vec<u8>,
    is_dir: bool,
    flags: u16,
    method: Method,
    crc: u32,
    csize: u64,
    usize_: u64, // `usize` is a Rust type name, hence the underscore
    local_offset: usize,
}

fn parse_entry(d: &[u8], at: usize, end: usize, idx: usize) -> Res<(Entry, usize)> {
    let m = |s: &str| format!("member {}: {}", idx, s);
    if at + CENTRAL_LEN > end || u32_at(d, at) != Some(SIG_CENTRAL) {
        return Err(bad(m("bad central directory entry")));
    }
    // `unwrap_or(0)` is never used: the 46 bytes were bounds-checked just above.
    let flags = u16_at(d, at + 8).unwrap_or(0);
    let method = u16_at(d, at + 10).unwrap_or(0);
    let crc = u32_at(d, at + 16).unwrap_or(0);
    let csize = u32_at(d, at + 20).unwrap_or(0);
    let usize_ = u32_at(d, at + 24).unwrap_or(0);
    let nlen = u16_at(d, at + 28).unwrap_or(0) as usize;
    let elen = u16_at(d, at + 30).unwrap_or(0) as usize;
    let clen = u16_at(d, at + 32).unwrap_or(0) as usize;
    let disk = u16_at(d, at + 34).unwrap_or(0);
    let ext_attr = u32_at(d, at + 38).unwrap_or(0);
    let local = u32_at(d, at + 42).unwrap_or(0);
    let next = at + CENTRAL_LEN + nlen + elen + clen;
    if next > end {
        return Err(bad(m("bad central directory entry")));
    }
    let name_bytes = d[at + CENTRAL_LEN..at + CENTRAL_LEN + nlen].to_vec();
    let extra = &d[at + CENTRAL_LEN + nlen..at + CENTRAL_LEN + nlen + elen];

    // Encryption first: it is the most important thing to report clearly.
    if flags & (FLAG_ENCRYPTED | FLAG_STRONG_ENCRYPTION | FLAG_MASKED_HEADERS) != 0 {
        return Err(err(Code::UnsupportedFeature, m("zip encryption is not supported (use age encryption)")));
    }
    if flags & !FLAGS_ALLOWED != 0 {
        return Err(err(Code::UnsupportedFeature, m("unsupported zip flag")));
    }
    if csize == 0xFFFF_FFFF || usize_ == 0xFFFF_FFFF || local == 0xFFFF_FFFF || disk == 0xFFFF {
        return Err(zip64());
    }
    check_extra(extra, idx)?;
    if disk != 0 {
        return Err(err(Code::UnsupportedFeature, "multi-disk zip archives are not supported"));
    }
    let method = match method {
        0 => Method::Stored,
        8 => Method::Deflate,
        _ => return Err(err(Code::UnsupportedFeature, m("unsupported compression method (only stored and deflate)"))),
    };
    if method == Method::Stored && csize != usize_ {
        return Err(bad(m("a stored member has different compressed and uncompressed sizes")));
    }

    // Name: UTF-8 if flag 11 is set, otherwise it must be plain ASCII (we never guess cp437).
    let name = if flags & FLAG_UTF8 != 0 {
        String::from_utf8(name_bytes.clone()).map_err(|_| bad(m("the name is not valid UTF-8")))?
    } else if name_bytes.is_ascii() {
        String::from_utf8(name_bytes.clone()).map_err(|_| bad(m("the name is not valid UTF-8")))?
    } else {
        return Err(bad(m("the name is not marked as UTF-8 (legacy code page)")));
    };
    let is_dir = name.ends_with('/');
    let bare = if is_dir { &name[..name.len() - 1] } else { &name[..] };
    check_name(bare, idx)?;

    // File type from the Unix mode in the high 16 bits. The "made by" host byte is not
    // trusted: any non-zero mode is checked.
    let mode = ext_attr >> 16;
    let ftype = mode & 0o170000;
    match ftype {
        0 => {}
        0o100000 if !is_dir => {}
        0o040000 if is_dir => {}
        0o100000 | 0o040000 => return Err(bad(m("the file type does not match the name"))),
        0o120000 => return Err(err(Code::SecurityDenied, m("symbolic links are not allowed"))),
        _ => return Err(err(Code::SecurityDenied, m("special files are not allowed"))),
    }
    if is_dir && usize_ != 0 {
        return Err(bad(m("a directory entry has data")));
    }
    Ok((
        Entry {
            idx,
            name,
            name_bytes,
            is_dir,
            flags,
            method,
            crc,
            csize: csize as u64,
            usize_: usize_ as u64,
            local_offset: local as usize,
        },
        next,
    ))
}

// ---------------------------------------------------------------- local records

/// Check the local header (and data descriptor) of `e`; return (data start, record end).
fn check_local(d: &[u8], e: &Entry) -> Res<(usize, usize)> {
    let m = |s: &str| format!("member {}: {}", e.idx, s);
    let at = e.local_offset;
    if u32_at(d, at) != Some(SIG_LOCAL) || at + LOCAL_LEN > d.len() {
        return Err(bad(m("bad local header")));
    }
    let flags = u16_at(d, at + 6).unwrap_or(0);
    let method = u16_at(d, at + 8).unwrap_or(0);
    let crc = u32_at(d, at + 14).unwrap_or(0);
    let csize = u32_at(d, at + 18).unwrap_or(0) as u64;
    let usize_ = u32_at(d, at + 22).unwrap_or(0) as u64;
    let nlen = u16_at(d, at + 26).unwrap_or(0) as usize;
    let elen = u16_at(d, at + 28).unwrap_or(0) as usize;
    let data_start = at + LOCAL_LEN + nlen + elen;
    if data_start > d.len() {
        return Err(bad(m("bad local header")));
    }
    let cd_method = match e.method {
        Method::Stored => 0,
        Method::Deflate => 8,
    };
    if flags != e.flags || method != cd_method {
        return Err(bad(m("the local header disagrees with the central directory")));
    }
    if d[at + LOCAL_LEN..at + LOCAL_LEN + nlen] != e.name_bytes[..] {
        return Err(bad(m("the local header disagrees with the central directory")));
    }
    if csize == 0xFFFF_FFFF || usize_ == 0xFFFF_FFFF {
        return Err(zip64());
    }
    check_extra(&d[at + LOCAL_LEN + nlen..data_start], e.idx)?;
    let same = crc == e.crc && csize == e.csize && usize_ == e.usize_;
    // `checked_add` returns None on overflow instead of wrapping around.
    let data_end = data_start
        .checked_add(e.csize as usize)
        .filter(|&x| x <= d.len())
        .ok_or_else(|| bad(m("the member data is truncated")))?;
    if flags & FLAG_DESCRIPTOR == 0 {
        if !same {
            return Err(bad(m("the local header disagrees with the central directory")));
        }
        return Ok((data_start, data_end));
    }
    // With a data descriptor the local fields are either zero or equal to the central ones.
    if !(same || (crc == 0 && csize == 0 && usize_ == 0)) {
        return Err(bad(m("the local header disagrees with the central directory")));
    }
    // Descriptor: optional signature, then crc, compressed size, uncompressed size (4 bytes each).
    let matches = |p: usize| u32_at(d, p) == Some(e.crc) && u32_at(d, p + 4).map(|x| x as u64) == Some(e.csize) && u32_at(d, p + 8).map(|x| x as u64) == Some(e.usize_);
    if u32_at(d, data_end) == Some(SIG_DESCRIPTOR) && matches(data_end + 4) {
        return Ok((data_start, data_end + 16));
    }
    if matches(data_end) {
        return Ok((data_start, data_end + 12));
    }
    Err(bad(m("the data descriptor is missing or disagrees with the central directory")))
}

// ---------------------------------------------------------------- the reader

/// Read and verify a whole archive. See the module comment for the order of checks.
pub fn zip_read(d: &[u8], lim: &ZipLimits) -> Res<Archive> {
    if d.len() as u64 > lim.max_archive_size {
        return Err(err(Code::ResourceLimit, format!("the archive is larger than {} bytes", lim.max_archive_size)));
    }
    let eocd = find_eocd(d)?;
    if eocd.entries as u64 > lim.max_entries {
        return Err(err(Code::ResourceLimit, format!("the archive has more than {} entries", lim.max_entries)));
    }
    if eocd.cd_offset.checked_add(eocd.cd_size) != Some(eocd.offset) {
        // A zip64 locator sits right before the EOCD in zip64 archives.
        if eocd.offset >= 20 && u32_at(d, eocd.offset - 20) == Some(SIG_ZIP64_LOCATOR) {
            return Err(zip64());
        }
        return Err(bad("the central directory is not where the end record says"));
    }

    // 4. central directory entries
    let mut entries = Vec::with_capacity(eocd.entries);
    let mut at = eocd.cd_offset;
    for idx in 1..=eocd.entries {
        let (e, next) = parse_entry(d, at, eocd.offset, idx)?;
        entries.push(e);
        at = next;
    }
    if at != eocd.offset {
        return Err(bad("the central directory has more data than its entry count"));
    }

    // 5. names: exact duplicates, then collisions, then file/directory conflicts
    let mut exact: HashSet<String> = HashSet::new();
    let mut keys: HashMap<String, bool> = HashMap::new(); // collision key -> is_dir
    for e in &entries {
        let bare = e.name.trim_end_matches('/');
        let nfc: String = bare.nfc().collect();
        if !exact.insert(nfc) {
            return Err(err(Code::SecurityDenied, format!("member {}: duplicate name", e.idx)));
        }
        if keys.insert(collision_key(bare), e.is_dir).is_some() {
            return Err(err(Code::SecurityDenied, format!("member {}: the name differs from another only in case or Unicode form", e.idx)));
        }
    }
    for e in &entries {
        // Every parent ("a" and "a/b" for "a/b/c") must not be a file.
        let bare = e.name.trim_end_matches('/');
        let segs: Vec<&str> = bare.split('/').collect();
        for k in 1..segs.len() {
            let parent = collision_key(&segs[..k].join("/"));
            if keys.get(&parent) == Some(&false) {
                return Err(err(Code::SecurityDenied, format!("member {}: a parent directory is also a file", e.idx)));
            }
        }
    }

    // 6. size limits from the declared sizes (before decompressing anything)
    let mut total: u64 = 0;
    for e in &entries {
        if e.usize_ > lim.max_member_size {
            return Err(err(Code::ResourceLimit, format!("member {}: larger than {} bytes", e.idx, lim.max_member_size)));
        }
        if e.usize_ > RATIO_MIN_SIZE && e.usize_ > lim.max_ratio.saturating_mul(e.csize) {
            return Err(err(Code::ResourceLimit, format!("member {}: compression ratio above {}:1", e.idx, lim.max_ratio)));
        }
        total += e.usize_;
        if total > lim.max_total_size {
            return Err(err(Code::ResourceLimit, format!("the members expand to more than {} bytes", lim.max_total_size)));
        }
    }

    // 7. local records: check each, then require that they tile [0, cd_offset) exactly
    let mut spans: Vec<(usize, usize, usize)> = Vec::with_capacity(entries.len()); // (start, end, entry index)
    let mut data_ranges = Vec::with_capacity(entries.len());
    for (i, e) in entries.iter().enumerate() {
        let (ds, end) = check_local(d, e)?;
        data_ranges.push((ds, ds + e.csize as usize));
        spans.push((e.local_offset, end, i));
    }
    spans.sort();
    let mut pos = 0;
    for (start, end, _) in &spans {
        if *start != pos {
            return Err(bad("the members overlap or leave gaps in the archive"));
        }
        pos = *end;
    }
    if pos != eocd.cd_offset {
        return Err(bad("the members overlap or leave gaps in the archive"));
    }

    // 8. decompress and verify
    let mut members = Vec::new();
    let mut directories = Vec::new();
    for (e, (ds, de)) in entries.iter().zip(data_ranges) {
        let raw = &d[ds..de];
        let data = match e.method {
            Method::Stored => raw.to_vec(),
            Method::Deflate => match inflate_bounded(raw, e.usize_ as usize) {
                Ok(v) => v,
                Err(InflateError::Limit) => return Err(bad(format!("member {}: larger than its declared size", e.idx))),
                Err(InflateError::Malformed(msg)) => return Err(bad(format!("member {}: invalid compressed data: {}", e.idx, msg))),
            },
        };
        if data.len() as u64 != e.usize_ {
            return Err(bad(format!("member {}: smaller than its declared size", e.idx)));
        }
        if crc32(&data) != e.crc {
            return Err(bad(format!("member {}: CRC-32 mismatch", e.idx)));
        }
        if e.is_dir {
            directories.push(e.name.clone());
        } else {
            members.push(Member {
                name: e.name.clone(),
                method: e.method,
                size: e.usize_,
                compressed_size: e.csize,
                crc32: e.crc,
                data,
            });
        }
    }
    Ok(Archive { members, directories })
}
