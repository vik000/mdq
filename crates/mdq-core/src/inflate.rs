//! Raw DEFLATE decompression (RFC 1951) and CRC-32 (E7.1, G47).
//!
//! Written from the RFC in the style of Mark Adler's `puff.c`: small and easy to check rather
//! than fast. The output size is bounded while decoding, so a "zip bomb" stops as soon as it
//! passes the limit instead of being expanded in memory first.
//!
//! What is accepted matches zlib's raw inflate (`zlib.decompressobj(-15)` in Python), which the
//! tests use as the oracle. There is one difference on purpose: the input must END with the
//! final block. Whole bytes left after it are an error (zlib would put them in `unused_data`).

use crate::diag::{Code, Diagnostic};

/// Why decoding stopped. `inflate_bounded` returns this small enum instead of a `Diagnostic`,
/// so the zip reader can turn "too big" into its own message ("larger than declared").
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum InflateError {
    /// The output would exceed `max_out` bytes.
    Limit,
    /// The stream is malformed or truncated. The text is fixed (never contains data).
    Malformed(&'static str),
}

// ---------------------------------------------------------------- CRC-32

/// The CRC-32 lookup table, computed at COMPILE time.
///
/// A `const fn` can run at compile time; `TABLE` below is filled by calling it in a `const`,
/// so the program contains the finished 256 entries and does no work at start-up.
/// The polynomial 0xEDB88320 is the reflected IEEE 802.3 polynomial (the one zip and zlib use).
const fn make_crc_table() -> [u32; 256] {
    let mut table = [0u32; 256];
    let mut n = 0;
    // `for` loops are not allowed in a const fn (yet), so we use `while`.
    while n < 256 {
        let mut c = n as u32;
        let mut k = 0;
        while k < 8 {
            c = if c & 1 != 0 { 0xEDB8_8320 ^ (c >> 1) } else { c >> 1 };
            k += 1;
        }
        table[n] = c;
        n += 1;
    }
    table
}

const CRC_TABLE: [u32; 256] = make_crc_table();

/// CRC-32 of `data`, the same value as Python's `zlib.crc32(data)`.
pub fn crc32(data: &[u8]) -> u32 {
    let mut c = 0xFFFF_FFFFu32;
    for &b in data {
        // Index with the low byte of (crc XOR byte); `as usize` converts for indexing.
        c = CRC_TABLE[((c ^ b as u32) & 0xFF) as usize] ^ (c >> 8);
    }
    c ^ 0xFFFF_FFFF
}

// ---------------------------------------------------------------- bit reader

/// Reads bits least-significant first, as DEFLATE stores them.
struct Bits<'a> {
    data: &'a [u8],
    pos: usize,     // next byte to load
    bitbuf: u32,    // bits loaded but not used yet (low bits first)
    bitcnt: u32,    // how many bits are in `bitbuf`
}

impl<'a> Bits<'a> {
    fn new(data: &'a [u8]) -> Self {
        Bits { data, pos: 0, bitbuf: 0, bitcnt: 0 }
    }

    /// Take `need` bits (0..=16) as a number. Running out of input is a truncated stream.
    fn bits(&mut self, need: u32) -> Result<u32, InflateError> {
        while self.bitcnt < need {
            // `get` returns an Option: None past the end instead of panicking.
            let b = *self.data.get(self.pos).ok_or(InflateError::Malformed("the compressed data is truncated"))?;
            self.pos += 1;
            self.bitbuf |= (b as u32) << self.bitcnt;
            self.bitcnt += 8;
        }
        let v = self.bitbuf & ((1u32 << need) - 1);
        self.bitbuf >>= need;
        self.bitcnt -= need;
        Ok(v)
    }

    /// Drop the unused bits of the current byte (stored blocks start on a byte boundary).
    fn align(&mut self) {
        self.bitbuf = 0;
        self.bitcnt = 0;
    }

    fn byte(&mut self) -> Result<u8, InflateError> {
        let b = *self.data.get(self.pos).ok_or(InflateError::Malformed("the compressed data is truncated"))?;
        self.pos += 1;
        Ok(b)
    }
}

// ---------------------------------------------------------------- Huffman codes

const MAXBITS: usize = 15;

/// A canonical Huffman code: how many codes have each length, and the symbols in code order.
struct Huffman {
    count: [u16; MAXBITS + 1],
    symbol: Vec<u16>,
}

/// Result of building a code: complete, incomplete (some bit patterns unused) or
/// over-subscribed (more codes than bit patterns: never valid).
#[derive(PartialEq, Eq)]
enum Fill {
    Complete,
    Incomplete,
    Over,
}

impl Huffman {
    /// Build the code from a list of code lengths (0 = symbol not used).
    fn new(lengths: &[u8]) -> (Huffman, Fill) {
        let mut count = [0u16; MAXBITS + 1];
        for &l in lengths {
            count[l as usize] += 1;
        }
        let mut h = Huffman { count, symbol: vec![0; lengths.len()] };
        if h.count[0] as usize == lengths.len() {
            // No codes at all: "complete" in the sense that nothing is missing to decode.
            return (h, Fill::Complete);
        }
        // `left` = bit patterns still free at the current length (i64 so it can go negative).
        let mut left: i64 = 1;
        for len in 1..=MAXBITS {
            left <<= 1;
            left -= h.count[len] as i64;
            if left < 0 {
                return (h, Fill::Over);
            }
        }
        // offs[len] = index in `symbol` of the first symbol with that code length.
        let mut offs = [0u16; MAXBITS + 1];
        for len in 1..MAXBITS {
            offs[len + 1] = offs[len] + h.count[len];
        }
        for (sym, &l) in lengths.iter().enumerate() {
            if l != 0 {
                h.symbol[offs[l as usize] as usize] = sym as u16;
                offs[l as usize] += 1;
            }
        }
        (h, if left > 0 { Fill::Incomplete } else { Fill::Complete })
    }

    /// The longest code length in use (0 if none).
    fn max_len(&self) -> usize {
        (1..=MAXBITS).rev().find(|&l| self.count[l] != 0).unwrap_or(0)
    }

    /// Decode one symbol, reading one bit at a time (canonical codes are read MSB first).
    fn decode(&self, s: &mut Bits<'_>, invalid: &'static str) -> Result<u16, InflateError> {
        let mut code: i32 = 0; // bits read so far
        let mut first: i32 = 0; // first code of the current length
        let mut index: i32 = 0; // index of the first symbol of the current length
        for len in 1..=MAXBITS {
            code |= s.bits(1)? as i32;
            let count = self.count[len] as i32;
            if code - count < first {
                return Ok(self.symbol[(index + (code - first)) as usize]);
            }
            index += count;
            first += count;
            first <<= 1;
            code <<= 1;
        }
        // Only reachable for an incomplete code: the bit pattern names no symbol.
        Err(InflateError::Malformed(invalid))
    }
}

// ---------------------------------------------------------------- block decoding

// Base lengths and extra bits for length symbols 257..285 (RFC 1951 section 3.2.5).
const LBASE: [u16; 29] = [3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 15, 17, 19, 23, 27, 31, 35, 43, 51, 59, 67, 83, 99, 115, 131, 163, 195, 227, 258];
const LEXT: [u8; 29] = [0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2, 3, 3, 3, 3, 4, 4, 4, 4, 5, 5, 5, 5, 0];
// Base distances and extra bits for distance symbols 0..29.
const DBASE: [u16; 30] = [1, 2, 3, 4, 5, 7, 9, 13, 17, 25, 33, 49, 65, 97, 129, 193, 257, 385, 513, 769, 1025, 1537, 2049, 3073, 4097, 6145, 8193, 12289, 16385, 24577];
const DEXT: [u8; 30] = [0, 0, 0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7, 7, 8, 8, 9, 9, 10, 10, 11, 11, 12, 12, 13, 13];

/// Append one byte, refusing to grow past `max_out`.
fn push(out: &mut Vec<u8>, b: u8, max_out: usize) -> Result<(), InflateError> {
    if out.len() >= max_out {
        return Err(InflateError::Limit);
    }
    out.push(b);
    Ok(())
}

/// A stored (uncompressed) block: LEN, its one's complement NLEN, then LEN bytes.
fn stored(s: &mut Bits<'_>, out: &mut Vec<u8>, max_out: usize) -> Result<(), InflateError> {
    s.align();
    let len = s.byte()? as u16 | (s.byte()? as u16) << 8;
    let nlen = s.byte()? as u16 | (s.byte()? as u16) << 8;
    if len != !nlen {
        return Err(InflateError::Malformed("a stored block has a bad length"));
    }
    for _ in 0..len {
        let b = s.byte()?;
        push(out, b, max_out)?;
    }
    Ok(())
}

/// Decode literal/length and distance symbols until the end-of-block symbol (256).
fn codes(s: &mut Bits<'_>, out: &mut Vec<u8>, max_out: usize, lencode: &Huffman, distcode: &Huffman) -> Result<(), InflateError> {
    loop {
        let sym = lencode.decode(s, "invalid literal/length code")?;
        if sym < 256 {
            push(out, sym as u8, max_out)?;
        } else if sym == 256 {
            return Ok(());
        } else {
            let i = (sym - 257) as usize;
            if i >= 29 {
                // Symbols 286 and 287 exist in the fixed code but are never valid.
                return Err(InflateError::Malformed("invalid literal/length code"));
            }
            let len = LBASE[i] as usize + s.bits(LEXT[i] as u32)? as usize;
            let d = distcode.decode(s, "invalid distance code")? as usize;
            if d >= 30 {
                return Err(InflateError::Malformed("invalid distance code"));
            }
            let dist = DBASE[d] as usize + s.bits(DEXT[d] as u32)? as usize;
            if dist > out.len() {
                return Err(InflateError::Malformed("a distance points before the start of the data"));
            }
            // Copy byte by byte: the source may overlap what we are writing (a run).
            for _ in 0..len {
                let b = out[out.len() - dist];
                push(out, b, max_out)?;
            }
        }
    }
}

/// The fixed codes of RFC 1951 section 3.2.6.
fn fixed(s: &mut Bits<'_>, out: &mut Vec<u8>, max_out: usize) -> Result<(), InflateError> {
    let mut lengths = [0u8; 288];
    for (i, l) in lengths.iter_mut().enumerate() {
        *l = match i {
            0..=143 => 8,
            144..=255 => 9,
            256..=279 => 7,
            _ => 8,
        };
    }
    let (lencode, _) = Huffman::new(&lengths);
    // 32 distance codes of 5 bits (complete); 30 and 31 are rejected in `codes`.
    let (distcode, _) = Huffman::new(&[5u8; 32]);
    codes(s, out, max_out, &lencode, &distcode)
}

/// Order in which the code-length code lengths are stored.
const ORDER: [usize; 19] = [16, 17, 18, 0, 8, 7, 9, 6, 10, 5, 11, 4, 12, 3, 13, 2, 14, 1, 15];

/// A block with its own codes, described at the start of the block.
fn dynamic(s: &mut Bits<'_>, out: &mut Vec<u8>, max_out: usize) -> Result<(), InflateError> {
    let nlen = s.bits(5)? as usize + 257;
    let ndist = s.bits(5)? as usize + 1;
    let ncode = s.bits(4)? as usize + 4;
    if nlen > 286 || ndist > 30 {
        return Err(InflateError::Malformed("too many length or distance codes"));
    }
    let mut lengths = [0u8; 19];
    for &i in ORDER.iter().take(ncode) {
        lengths[i] = s.bits(3)? as u8;
    }
    let (lencode, fill) = Huffman::new(&lengths);
    if fill != Fill::Complete || lencode.max_len() == 0 {
        // zlib requires the code-length code to be complete.
        return Err(InflateError::Malformed("invalid code lengths set"));
    }
    // Read the literal/length and distance code lengths as one run (runs may cross over).
    let mut lens = vec![0u8; nlen + ndist];
    let mut i = 0;
    while i < nlen + ndist {
        let sym = lencode.decode(s, "invalid code lengths set")?;
        if sym < 16 {
            lens[i] = sym as u8;
            i += 1;
            continue;
        }
        let (value, repeat) = match sym {
            16 => {
                if i == 0 {
                    return Err(InflateError::Malformed("a length repeat has nothing to repeat"));
                }
                (lens[i - 1], 3 + s.bits(2)? as usize)
            }
            17 => (0, 3 + s.bits(3)? as usize),
            _ => (0, 11 + s.bits(7)? as usize),
        };
        if i + repeat > nlen + ndist {
            return Err(InflateError::Malformed("too many code lengths"));
        }
        for l in lens.iter_mut().skip(i).take(repeat) {
            *l = value;
        }
        i += repeat;
    }
    if lens[256] == 0 {
        return Err(InflateError::Malformed("the block has no end-of-block code"));
    }
    let (lc, lfill) = Huffman::new(&lens[..nlen]);
    // zlib: over-subscribed is an error; incomplete only allowed for a single 1-bit code.
    if lfill == Fill::Over || (lfill == Fill::Incomplete && lc.max_len() != 1) {
        return Err(InflateError::Malformed("invalid literal/lengths set"));
    }
    let (dc, dfill) = Huffman::new(&lens[nlen..]);
    if dfill == Fill::Over || (dfill == Fill::Incomplete && dc.max_len() != 1) {
        return Err(InflateError::Malformed("invalid distances set"));
    }
    codes(s, out, max_out, &lc, &dc)
}

/// Decompress a raw DEFLATE stream, producing at most `max_out` bytes.
///
/// The stream must end exactly at the end of `data` (unused bits in the last byte are allowed).
pub fn inflate_bounded(data: &[u8], max_out: usize) -> Result<Vec<u8>, InflateError> {
    let mut s = Bits::new(data);
    let mut out = Vec::new();
    loop {
        let last = s.bits(1)?;
        match s.bits(2)? {
            0 => stored(&mut s, &mut out, max_out)?,
            1 => fixed(&mut s, &mut out, max_out)?,
            2 => dynamic(&mut s, &mut out, max_out)?,
            _ => return Err(InflateError::Malformed("invalid block type")),
        }
        if last == 1 {
            break;
        }
    }
    if s.pos != data.len() {
        return Err(InflateError::Malformed("data follows the end of the compressed stream"));
    }
    Ok(out)
}

/// `inflate_bounded` with diagnostics: a malformed stream is MDQ_INVALID_SYNTAX, output over
/// `max_out` bytes is MDQ_RESOURCE_LIMIT.
pub fn inflate(data: &[u8], max_out: usize) -> Result<Vec<u8>, Diagnostic> {
    inflate_bounded(data, max_out).map_err(|e| match e {
        InflateError::Limit => Diagnostic::new(Code::ResourceLimit, format!("the decompressed data is larger than {} bytes", max_out)),
        InflateError::Malformed(m) => Diagnostic::new(Code::InvalidSyntax, format!("invalid compressed data: {}", m)),
    })
}
