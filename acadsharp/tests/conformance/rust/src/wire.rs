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

/// Typed reads of a record's payload, each of them bounds checked.
///
/// A consumer written from this file gets these rather than a raw slice and an
/// offset it has to police itself, which is the difference between a malformed
/// batch being a `None` it has to handle and a panic in the middle of somebody
/// else's process.
impl<'a> Record<'a> {
    pub fn u16(&self, at: usize) -> Option<u16> {
        u16_at(self.payload, at)
    }

    pub fn u32(&self, at: usize) -> Option<u32> {
        u32_at(self.payload, at)
    }

    pub fn u64(&self, at: usize) -> Option<u64> {
        u64_at(self.payload, at)
    }

    pub fn f64(&self, at: usize) -> Option<f64> {
        f64_at(self.payload, at)
    }

    pub fn bytes(&self, at: usize, len: usize) -> Option<&'a [u8]> {
        self.payload.get(at..at.checked_add(len)?)
    }
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
///
/// `None` rather than a panic when the read would run off the end. These are
/// `pub`, they take a caller's slice and a caller's offset, and this file is
/// what the next consumer of this wire gets written from: a reference parser
/// whose scalar readers index unchecked teaches every consumer derived from it
/// to abort on a malformed batch, which is the one thing a parser of untrusted
/// bytes must not do.
fn window(b: &[u8], at: usize, len: usize) -> Option<&[u8]> {
    b.get(at..at.checked_add(len)?)
}

pub fn u16_at(b: &[u8], at: usize) -> Option<u16> {
    Some(u16::from_le_bytes(window(b, at, 2)?.try_into().ok()?))
}

pub fn u32_at(b: &[u8], at: usize) -> Option<u32> {
    Some(u32::from_le_bytes(window(b, at, 4)?.try_into().ok()?))
}

pub fn u64_at(b: &[u8], at: usize) -> Option<u64> {
    Some(u64::from_le_bytes(window(b, at, 8)?.try_into().ok()?))
}

pub fn f64_at(b: &[u8], at: usize) -> Option<f64> {
    Some(f64::from_bits(u64_at(b, at)?))
}

impl<'a> Reader<'a> {
    pub fn open(buf: &'a [u8]) -> Result<Reader<'a>, u32> {
        if buf.len() < BATCH_HEADER_BYTES {
            return Err(VIPRS_ACAD_CORRUPT_INPUT);
        }
        if buf[..4] != MAGIC {
            return Err(VIPRS_ACAD_CORRUPT_INPUT);
        }
        if u16_at(buf, 4) != Some(WIRE_VERSION) {
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

        let flags = u16_at(buf, 6).ok_or(VIPRS_ACAD_CORRUPT_INPUT)?;
        let payload_length = u32_at(buf, 8).ok_or(VIPRS_ACAD_CORRUPT_INPUT)? as usize;

        // A payload_length past the end of the buffer reads whatever the
        // caller allocated next, so it is refused before anything is indexed.
        // Added rather than subtracted, and checked, because a payload_length
        // near usize::MAX would otherwise wrap and pass.
        let end = match BATCH_HEADER_BYTES.checked_add(payload_length) {
            Some(end) if end <= buf.len() => end,
            _ => return Err(VIPRS_ACAD_CORRUPT_INPUT),
        };

        Ok(Reader {
            payload: &buf[BATCH_HEADER_BYTES..end],
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

        let kind = u16_at(self.payload, self.offset).ok_or(VIPRS_ACAD_CORRUPT_INPUT)?;
        let reserved = u16_at(self.payload, self.offset + 2).ok_or(VIPRS_ACAD_CORRUPT_INPUT)?;
        let length =
            u32_at(self.payload, self.offset + 4).ok_or(VIPRS_ACAD_CORRUPT_INPUT)? as usize;

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

    let n = u32_at(payload, 16).ok_or(VIPRS_ACAD_CORRUPT_INPUT)? as usize;
    let closed = u32_at(payload, 20).ok_or(VIPRS_ACAD_CORRUPT_INPUT)?;
    let bulges = u32_at(payload, 24).ok_or(VIPRS_ACAD_CORRUPT_INPUT)? as usize;
    let reserved1 = u32_at(payload, 28).ok_or(VIPRS_ACAD_CORRUPT_INPUT)?;

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
    // Every read here was in bounds only by the arithmetic two lines above:
    // the length equation makes the last probe land exactly on the end of the
    // payload. That is true and it is not a bounds check, so the read does its
    // own and a payload that disagrees is refused rather than indexed.
    for k in 0..(3 + (3 * n) + bulges) {
        let v = f64_at(payload, 32 + (k * 8)).ok_or(VIPRS_ACAD_CORRUPT_INPUT)?;
        if !v.is_finite() {
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

#[cfg(test)]
mod tests {
    use super::*;

    /// The readers above are `pub`, they take a caller's slice and a caller's
    /// offset, and this file is what a second consumer of this wire gets
    /// written from. Every one of these cases used to be an index panic.
    #[test]
    fn a_read_past_the_end_is_none_and_not_a_panic() {
        let short = [1u8, 2, 3];
        assert_eq!(u16_at(&short, 0), Some(0x0201));
        assert_eq!(u16_at(&short, 2), None);
        assert_eq!(u32_at(&short, 0), None);
        assert_eq!(u64_at(&short, 0), None);
        assert_eq!(f64_at(&short, 0), None);
        assert_eq!(u16_at(&[], 0), None);
    }

    /// An offset near the top of the address space wraps when the length is
    /// added to it, so the check has to be an addition that can fail rather
    /// than a comparison that quietly succeeds.
    #[test]
    fn an_offset_that_would_overflow_is_none() {
        let some = [0u8; 16];
        assert_eq!(u64_at(&some, usize::MAX), None);
        assert_eq!(u32_at(&some, usize::MAX - 1), None);
    }

    #[test]
    fn a_record_reads_its_own_payload_and_refuses_to_read_past_it() {
        let payload = [0x11u8, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77, 0x88];
        let r = Record {
            kind: TYPE_LINE,
            payload: &payload,
        };
        assert_eq!(r.u32(0), Some(0x4433_2211));
        assert_eq!(r.u64(0), Some(0x8877_6655_4433_2211));
        assert_eq!(r.u64(1), None);
        assert_eq!(r.bytes(4, 4), Some(&payload[4..]));
        assert_eq!(r.bytes(4, 5), None);
    }

    /// A batch whose header claims a payload longer than the buffer, and one
    /// whose claim is large enough to wrap when the header size is added.
    #[test]
    fn a_claimed_payload_past_the_buffer_is_refused() {
        let over = build_batch(WIRE_VERSION, 64, &[0u8; 8]);
        assert_eq!(Reader::open(&over).err(), Some(VIPRS_ACAD_CORRUPT_INPUT));

        let wrap = build_batch(WIRE_VERSION, u32::MAX, &[0u8; 8]);
        assert_eq!(Reader::open(&wrap).err(), Some(VIPRS_ACAD_CORRUPT_INPUT));
    }
}
