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
    VIPRS_ACAD_CORRUPT_INPUT, VIPRS_ACAD_INTERNAL_ERROR, VIPRS_ACAD_UNSUPPORTED_FORMAT,
};

pub const MAGIC: [u8; 4] = [0x56, 0x41, 0x43, 0x42];
pub const WIRE_VERSION: u16 = 1;

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
            return Err(VIPRS_ACAD_UNSUPPORTED_FORMAT);
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
