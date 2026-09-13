//! A consumer-side parser for the VACB batch protocol, written from
//! docs/WIRE.md.
//!
//! Deliberately written without reading how the producer emits the bytes. A
//! parser derived from the producer's own code agrees with the producer by
//! construction, which is a fact about the two of them and not about the
//! document either is supposed to implement.
//!
//! The constants below are carried here separately from the shim and from the
//! C consumer. A test compares all three as text, because a record type that
//! is right in two of the three is the kind of bug that shows up as one shape
//! quietly missing from a large drawing, months later.

use crate::abi::{
    VIPRS_ACAD_ABI_MISMATCH, VIPRS_ACAD_CORRUPT_INPUT, VIPRS_ACAD_INTERNAL_ERROR,
};

pub const MAGIC: [u8; 4] = [0x56, 0x41, 0x43, 0x42];
pub const WIRE_VERSION: u16 = 2;

/// magic(4) + wire_version(2) + flags(2) + payload_length(4)
pub const BATCH_HEADER_BYTES: usize = 12;

/// type(2) + reserved(2) + length(4)
pub const RECORD_HEADER_BYTES: usize = 8;

pub const RECORD_ALIGNMENT: u32 = 4;
pub const FLAG_LAST: u16 = 1;
pub const TARGET_BATCH_BYTES: usize = 65536;
pub const MAX_BATCH_BYTES: usize = 1048576;

/// Types from here up carry no meaning in wire version 1 and are skipped by
/// length. The producer emits one into every stream so this path runs on real
/// bytes rather than only on a buffer a test assembled by hand.
pub const FORWARD_PROBE_FIRST: u16 = 0x7F00;

pub const TYPE_DOCUMENT_BEGIN: u16 = 1;
pub const TYPE_VIEW_BEGIN: u16 = 2;
pub const TYPE_LINE: u16 = 3;
pub const TYPE_POLYLINE: u16 = 4;
pub const TYPE_ARC: u16 = 5;
pub const TYPE_CIRCLE: u16 = 6;
pub const TYPE_ELLIPSE: u16 = 7;
pub const TYPE_SPLINE: u16 = 8;
pub const TYPE_POLYGON: u16 = 9;
pub const TYPE_TEXT: u16 = 10;
pub const TYPE_WARNING: u16 = 11;
pub const TYPE_VIEW_END: u16 = 12;
pub const TYPE_DOCUMENT_END: u16 = 13;

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Record<'a> {
    pub kind: u16,
    pub payload: &'a [u8],
}

#[derive(Debug)]
pub struct Reader<'a> {
    payload: &'a [u8],
    offset: usize,
    /// One iteration per smallest possible record, plus one. A parser that
    /// fails to advance trips this rather than spinning, so a mistake in the
    /// short-record rule is a failed test and not a job somebody has to kill.
    budget: i64,
    pub flags: u16,
    pub ran_away: bool,
}

/// Nothing inside a record is naturally aligned, because the batch header is
/// twelve bytes. Every scalar is assembled from bytes rather than loaded
/// through a cast, which is also the only way to read one safely here.
pub fn u16_at(b: &[u8], at: usize) -> u16 {
    u16::from_le_bytes([b[at], b[at + 1]])
}

pub fn u32_at(b: &[u8], at: usize) -> u32 {
    u32::from_le_bytes([b[at], b[at + 1], b[at + 2], b[at + 3]])
}

pub fn u64_at(b: &[u8], at: usize) -> u64 {
    let mut out = [0u8; 8];
    out.copy_from_slice(&b[at..at + 8]);
    u64::from_le_bytes(out)
}

pub fn f64_at(b: &[u8], at: usize) -> f64 {
    f64::from_bits(u64_at(b, at))
}

impl<'a> Reader<'a> {
    pub fn open(buf: &'a [u8]) -> Result<Reader<'a>, u32> {
        if buf.len() < BATCH_HEADER_BYTES {
            return Err(VIPRS_ACAD_CORRUPT_INPUT);
        }
        if buf[..4] != MAGIC {
            return Err(VIPRS_ACAD_CORRUPT_INPUT);
        }
        if u16_at(buf, 4) != WIRE_VERSION {
            // Refused rather than guessed. A consumer that parses a version it
            // does not know is reading a layout it is only assuming, and it
            // produces numbers instead of an error.
            //
            // ABI_MISMATCH rather than UNSUPPORTED_FORMAT, which is what this
            // said until wire version 2. UNSUPPORTED_FORMAT is about the
            // drawing; a foreign wire version is the two ends of this boundary
            // disagreeing, and the remedy is to rebuild one of them.
            return Err(VIPRS_ACAD_ABI_MISMATCH);
        }

        let flags = u16_at(buf, 6);
        let payload_length = u32_at(buf, 8) as usize;

        // A payload_length past the end of the buffer reads whatever the
        // caller allocated next, so it is refused before anything is indexed.
        if BATCH_HEADER_BYTES + payload_length > buf.len() {
            return Err(VIPRS_ACAD_CORRUPT_INPUT);
        }

        Ok(Reader {
            payload: &buf[BATCH_HEADER_BYTES..BATCH_HEADER_BYTES + payload_length],
            offset: 0,
            budget: (payload_length / RECORD_HEADER_BYTES) as i64 + 2,
            flags,
            ran_away: false,
        })
    }

    pub fn last_batch(&self) -> bool {
        self.flags & FLAG_LAST != 0
    }

    #[allow(clippy::should_implement_trait)]
    pub fn next(&mut self) -> Result<Option<Record<'a>>, u32> {
        if self.offset >= self.payload.len() {
            return Ok(None);
        }

        self.budget -= 1;
        if self.budget < 0 {
            self.ran_away = true;
            return Err(VIPRS_ACAD_INTERNAL_ERROR);
        }

        if self.payload.len() - self.offset < RECORD_HEADER_BYTES {
            return Err(VIPRS_ACAD_CORRUPT_INPUT);
        }

        let kind = u16_at(self.payload, self.offset);
        let reserved = u16_at(self.payload, self.offset + 2);
        let length = u32_at(self.payload, self.offset + 4) as usize;

        if reserved != 0 {
            return Err(VIPRS_ACAD_CORRUPT_INPUT);
        }

        // A short record, smaller than its own header, cannot advance the
        // cursor past itself. Accepting one is how a malformed batch becomes a
        // hang rather than a refusal.
        if length < RECORD_HEADER_BYTES {
            return Err(VIPRS_ACAD_CORRUPT_INPUT);
        }

        if length % RECORD_ALIGNMENT as usize != 0 {
            return Err(VIPRS_ACAD_CORRUPT_INPUT);
        }

        if length > self.payload.len() - self.offset {
            return Err(VIPRS_ACAD_CORRUPT_INPUT);
        }

        let payload = &self.payload[self.offset + RECORD_HEADER_BYTES..self.offset + length];

        // Unknown types are skipped by this same length, which is the entire
        // reason the length is in the record header.
        self.offset += length;
        Ok(Some(Record { kind, payload }))
    }
}

/// `(point_count, bulge_count)` for a wire version 2 `Polyline` or `Polygon`,
/// or the code docs/WIRE.md refuses it with.
///
/// Beside the framing parser rather than inside it. `Reader` walks record
/// headers and knows nothing about what a payload means, which is exactly what
/// lets it skip a type it has never heard of; a layout rule pushed into that
/// loop would make it wrong for every record type the day one of them changes.
///
/// Three rules, and the second is not implied by the first: a producer that
/// wrote the bulge array and left the count at zero produces a record whose
/// framing is perfect and whose trailing numbers nobody reads.
pub fn polyline_shape(payload: &[u8], length: usize) -> Result<(usize, usize), u32> {
    if payload.len() < 56 {
        return Err(VIPRS_ACAD_CORRUPT_INPUT);
    }

    let n = u32_at(payload, 16) as usize;
    let closed = u32_at(payload, 20);
    let bulges = u32_at(payload, 24) as usize;
    let reserved1 = u32_at(payload, 28);

    if closed > 1 || reserved1 != 0 {
        return Err(VIPRS_ACAD_CORRUPT_INPUT);
    }
    if bulges != 0 && bulges != n {
        return Err(VIPRS_ACAD_CORRUPT_INPUT);
    }
    if length != 64 + (24 * n) + (8 * bulges) {
        return Err(VIPRS_ACAD_CORRUPT_INPUT);
    }

    // The producer promises every f64 in a geometry record is finite. A
    // consumer checks anyway: the bytes may not have come from that producer,
    // and one NaN coordinate becomes a bounding box that is NaN in every
    // direction and a renderer that draws nothing at all.
    for k in 0..(3 + (3 * n) + bulges) {
        if !f64_at(payload, 32 + (k * 8)).is_finite() {
            return Err(VIPRS_ACAD_CORRUPT_INPUT);
        }
    }

    Ok((n, bulges))
}

/// One Polyline or Polygon built field by field, so a test can lie about any
/// one of them.
pub fn build_vertex_record(
    kind: u16,
    n: usize,
    bulges: usize,
    closed: u32,
    reserved1: u32,
    values: &[f64],
    length: Option<u32>,
) -> Vec<u8> {
    let mut body = Vec::new();
    body.extend_from_slice(&0x4Du64.to_le_bytes());
    body.extend_from_slice(&0u32.to_le_bytes());
    body.extend_from_slice(&0u32.to_le_bytes());
    body.extend_from_slice(&(n as u32).to_le_bytes());
    body.extend_from_slice(&closed.to_le_bytes());
    body.extend_from_slice(&(bulges as u32).to_le_bytes());
    body.extend_from_slice(&reserved1.to_le_bytes());
    for v in values {
        body.extend_from_slice(&v.to_le_bytes());
    }

    let len = length.unwrap_or((RECORD_HEADER_BYTES + body.len()) as u32);
    let mut out = Vec::new();
    out.extend_from_slice(&kind.to_le_bytes());
    out.extend_from_slice(&0u16.to_le_bytes());
    out.extend_from_slice(&len.to_le_bytes());
    out.extend_from_slice(&body);
    out
}

/// Builds a batch by hand, for the malformed cases. `claimed_payload` is
/// written into the header whatever the body actually is, which is how a
/// consumer gets to see a stream no honest producer would send.
pub fn build_batch(version: u16, claimed_payload: u32, body: &[u8]) -> Vec<u8> {
    let mut out = Vec::with_capacity(BATCH_HEADER_BYTES + body.len());
    out.extend_from_slice(&MAGIC);
    out.extend_from_slice(&version.to_le_bytes());
    out.extend_from_slice(&0u16.to_le_bytes());
    out.extend_from_slice(&claimed_payload.to_le_bytes());
    out.extend_from_slice(body);
    out
}

pub fn build_record(kind: u16, length: u32, payload_bytes: usize) -> Vec<u8> {
    let mut out = Vec::new();
    out.extend_from_slice(&kind.to_le_bytes());
    out.extend_from_slice(&0u16.to_le_bytes());
    out.extend_from_slice(&length.to_le_bytes());
    out.extend(std::iter::repeat(0u8).take(payload_bytes));
    out
}
