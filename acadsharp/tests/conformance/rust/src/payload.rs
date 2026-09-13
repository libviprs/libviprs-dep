//! Reads every field of every record at the offset docs/WIRE.md gives it.
//!
//! Until this existed, neither conformance consumer read a single scalar out
//! of a payload. The parsers walked record headers and never looked inside,
//! so swapping an arc's `radius` with its `start_angle` in the encoder changed
//! nothing any test could see, in any of the four branches. A field-layout
//! document nobody reads a field out of is a field-layout document that
//! drifts, and the whole premise here is that a consumer written from WIRE.md
//! alone reads every stream correctly.
//!
//! The synthetic document makes it checkable by carrying placement probes
//! instead of geometry: the k-th `f64` in a payload, counting from zero after
//! the geometry prologue, is `100 * type + k + 0.25`. Every field of a record
//! holds a different number, none holds zero, and none holds a value that
//! would look right one slot over. docs/ABI.md states the rule; the offsets
//! below are read off WIRE.md by hand, on this side of the boundary, because
//! offsets generated from the same place as the struct cannot disagree with
//! it.

use crate::wire::{self, f64_at, u32_at, u64_at, Record};

const PROBE_TEXT: &[u8] = b"VIPRS-TEXT-PROBE-";
const PROBE_WARNING: &[u8] = b"VIPRS-WARNING-PROBE";
const PROBE_WARNING_CODE: u32 = 1100;
const PROBE_SPLINE_DEGREE: u32 = 3;

fn probe(kind: u16, k: usize) -> f64 {
    (100.0 * kind as f64) + k as f64 + 0.25
}

fn probe_handle(kind: u16) -> u64 {
    1_000_000 + kind as u64
}

fn pad4(n: usize) -> usize {
    (4 - (n % 4)) % 4
}

#[derive(Default)]
pub struct Verifier {
    pub failures: u32,
    pub first: Option<String>,
    records: u64,
    view_records: u64,
    warnings: u64,
}

impl Verifier {
    pub fn new() -> Self {
        Self::default()
    }

    fn fail(&mut self, what: &str, got: String, want: String) {
        self.failures += 1;
        if self.first.is_none() {
            self.first = Some(format!("{what}: got {got}, wanted {want}"));
        }
    }

    fn f64_at(&mut self, p: &[u8], off: usize, want: f64, what: &str) {
        let got = f64_at(p, off);
        if got != want {
            self.fail(what, format!("{got}"), format!("{want}"));
        }
    }

    fn u32_at(&mut self, p: &[u8], off: usize, want: u32, what: &str) {
        let got = u32_at(p, off);
        if got != want {
            self.fail(what, format!("{got}"), format!("{want}"));
        }
    }

    fn u64_at(&mut self, p: &[u8], off: usize, want: u64, what: &str) {
        let got = u64_at(p, off);
        if got != want {
            self.fail(what, format!("{got}"), format!("{want}"));
        }
    }

    fn len_is(&mut self, r: &Record, want: usize, what: &str) {
        let got = r.payload.len() + wire::RECORD_HEADER_BYTES;
        if got != want {
            self.fail(what, format!("{got}"), format!("{want}"));
        }
    }

    fn probes(&mut self, p: &[u8], off: usize, kind: u16, count: usize, what: &str) {
        for k in 0..count {
            self.f64_at(p, off + (k * 8), probe(kind, k), what);
        }
    }

    fn bytes_at(&mut self, p: &[u8], off: usize, want: &[u8], what: &str) {
        if p.len() < off + want.len() || &p[off..off + want.len()] != want {
            self.fail(what, "different bytes".into(), String::from_utf8_lossy(want).into());
        }
    }

    fn prologue(&mut self, r: &Record) {
        self.u64_at(r.payload, 0, probe_handle(r.kind), "prologue item_handle");
        self.u32_at(r.payload, 8, 0, "prologue flags");
        self.u32_at(r.payload, 12, 0, "prologue reserved0");
    }

    /// Counts every record, the forward probe included, because ViewEnd and
    /// DocumentEnd count themselves and count it. Skipping a record for being
    /// unknown is a decision about what to do with it, not a licence to
    /// pretend it was not there.
    pub fn tally(&mut self, r: &Record) {
        self.records += 1;
        if r.kind == wire::TYPE_VIEW_BEGIN {
            self.view_records = 1;
        } else if self.view_records > 0 {
            self.view_records += 1;
        }
        if r.kind == wire::TYPE_WARNING {
            self.warnings += 1;
        }
    }

    /// Records 4 and 9, which share a payload in wire version 2.
    ///
    /// The counts and the length go through `wire::polyline_shape`, which is
    /// the same function the malformed cases drive, so what runs against a
    /// real stream and what runs against a hand-built one are the same code.
    fn vertices(&mut self, r: &Record) {
        self.prologue(r);
        let length = r.payload.len() + wire::RECORD_HEADER_BYTES;
        let (n, bulges) = match wire::polyline_shape(r.payload, length) {
            Ok(shape) => shape,
            Err(_) => {
                self.fail("vertex record shape", "refused".into(), "accepted".into());
                return;
            }
        };

        if r.kind == wire::TYPE_POLYGON {
            self.u32_at(r.payload, 20, 1, "Polygon closed");
        }

        // The synthetic document carries one bulge per vertex on purpose. The
        // record also allows none, and a probe set that only ever saw that
        // case would leave the trailing array unread by both consumers, which
        // is the state this whole file exists to rule out.
        if bulges != n {
            self.fail("vertex record bulge_count", format!("{bulges}"), format!("{n}"));
        }

        self.probes(r.payload, 32, r.kind, 3 + (3 * n) + bulges, "vertex record");
    }

    pub fn verify(&mut self, r: &Record) {
        let p = r.payload;
        match r.kind {
            wire::TYPE_DOCUMENT_BEGIN => {
                self.u32_at(p, 4, 1032, "DocumentBegin drawing_version");
                self.u64_at(p, 8, 0, "DocumentBegin reserved0");
                self.len_is(r, 24, "DocumentBegin length");
            }
            wire::TYPE_VIEW_BEGIN => {
                self.u32_at(p, 0, 0, "ViewBegin view_index");
                self.u32_at(p, 4, 0, "ViewBegin kind");
                self.f64_at(p, 8, -100.25, "ViewBegin min_x");
                self.f64_at(p, 16, -50.5, "ViewBegin min_y");
                self.f64_at(p, 24, 100.75, "ViewBegin max_x");
                self.f64_at(p, 32, 50.125, "ViewBegin max_y");
                self.u32_at(p, 52, 0, "ViewBegin reserved0");
                let name_len = u32_at(p, 48) as usize;
                self.len_is(r, 8 + 56 + name_len + pad4(name_len), "ViewBegin length");
            }
            wire::TYPE_LINE => {
                self.prologue(r);
                self.probes(p, 16, wire::TYPE_LINE, 6, "Line");
                self.len_is(r, 72, "Line length");
            }
            wire::TYPE_POLYLINE | wire::TYPE_POLYGON => self.vertices(r),
            wire::TYPE_ARC => {
                self.prologue(r);
                self.probes(p, 16, wire::TYPE_ARC, 9, "Arc");
                self.len_is(r, 96, "Arc length");
            }
            wire::TYPE_CIRCLE => {
                self.prologue(r);
                self.probes(p, 16, wire::TYPE_CIRCLE, 7, "Circle");
                self.len_is(r, 80, "Circle length");
            }
            wire::TYPE_ELLIPSE => {
                self.prologue(r);
                self.probes(p, 16, wire::TYPE_ELLIPSE, 12, "Ellipse");
                self.len_is(r, 120, "Ellipse length");
            }
            wire::TYPE_SPLINE => {
                self.prologue(r);
                self.u32_at(p, 16, PROBE_SPLINE_DEGREE, "Spline degree");
                self.u32_at(p, 20, 0, "Spline flags");
                let knots = u32_at(p, 24) as usize;
                let controls = u32_at(p, 28) as usize;
                let weights = u32_at(p, 32) as usize;
                self.u32_at(p, 36, 0, "Spline reserved1");
                if weights != 0 && weights != controls {
                    self.fail("Spline weight_count", format!("{weights}"), format!("{controls}"));
                }
                let total = knots + (controls * 3) + weights;
                self.probes(p, 40, wire::TYPE_SPLINE, total, "Spline");
                self.len_is(r, 8 + 16 + 24 + (8 * total), "Spline length");
            }
            wire::TYPE_TEXT => {
                self.prologue(r);
                self.probes(p, 16, wire::TYPE_TEXT, 5, "Text");
                let bytes = u32_at(p, 56) as usize;
                self.u32_at(p, 60, 0, "Text reserved1");
                self.bytes_at(p, 64, PROBE_TEXT, "Text bytes");
                self.len_is(r, 8 + 64 + bytes + pad4(bytes), "Text length");
            }
            wire::TYPE_WARNING => {
                self.u32_at(p, 0, PROBE_WARNING_CODE, "Warning code");
                self.u32_at(p, 4, 0, "Warning reserved0");
                self.u64_at(p, 8, probe_handle(wire::TYPE_WARNING), "Warning item_handle");
                let bytes = u32_at(p, 16) as usize;
                self.u32_at(p, 20, 0, "Warning reserved1");
                self.bytes_at(p, 24, PROBE_WARNING, "Warning bytes");
                self.len_is(r, 8 + 24 + bytes + pad4(bytes), "Warning length");
            }
            wire::TYPE_VIEW_END => {
                self.u32_at(p, 0, 0, "ViewEnd view_index");
                self.u32_at(p, 4, 0, "ViewEnd reserved0");
                let want = self.view_records;
                self.u64_at(p, 8, want, "ViewEnd record_count");
                self.len_is(r, 24, "ViewEnd length");
            }
            wire::TYPE_DOCUMENT_END => {
                let records = self.records;
                let warnings = self.warnings;
                self.u64_at(p, 0, records, "DocumentEnd total_records");
                self.u64_at(p, 8, warnings, "DocumentEnd warning_count");
                self.len_is(r, 24, "DocumentEnd length");
            }
            _ => {}
        }
    }
}
