//! The generated consumer of the VIPRS CAD C ABI.
//!
//! Every declaration it calls through came out of the published header at
//! build time. What it checks is what the header promises and no compiler on
//! either side can see: a null argument comes back as a code rather than a
//! fault, a managed exception on the other side arrives as INTERNAL_ERROR with
//! this process still running, the fingerprint handshake catches a header that
//! drifted from its library, and the batch protocol's malformed cases are
//! refused rather than parsed.
//!
//! Everything prints, pass or fail. A conformance run whose output is one word
//! is a conformance run nobody can audit.

use std::ffi::CStr;

use viprs_conformance::abi::*;
use viprs_conformance::wire::{self, Reader};

struct Tally {
    checks: u32,
    failures: u32,
}

impl Tally {
    fn check(&mut self, ok: bool, what: &str) {
        self.checks += 1;
        if ok {
            self.failures += 0;
            println!("ok    {what}");
        } else {
            self.failures += 1;
            println!("FAIL  {what}");
        }
    }
}

fn synthetic_input(views: u32, items: u32) -> Vec<u8> {
    let mut out = Vec::with_capacity(16);
    out.extend_from_slice(b"VIPRSSYN");
    out.extend_from_slice(&views.to_le_bytes());
    out.extend_from_slice(&items.to_le_bytes());
    out
}

fn handshake(t: &mut Tally) {
    let version = unsafe { viprs_acad_abi_version() };
    let fingerprint = unsafe { viprs_acad_abi_fingerprint() };

    println!("      header sha256 {VIPRS_ACAD_HEADER_SHA256}");
    println!("      abi_version={version} expected={VIPRS_ACAD_ABI_VERSION}");
    println!("      fingerprint=0x{fingerprint:016X} expected=0x{VIPRS_ACAD_EXPECTED_FINGERPRINT:016X}");

    if version != VIPRS_ACAD_ABI_VERSION || fingerprint != VIPRS_ACAD_EXPECTED_FINGERPRINT {
        println!("FAIL  fingerprint handshake: VIPRS_ACAD_ABI_MISMATCH ({VIPRS_ACAD_ABI_MISMATCH})");
        println!(
            "      the header this consumer was generated from is not the header the \
             library was built from"
        );
        std::process::exit(VIPRS_ACAD_ABI_MISMATCH as i32);
    }

    t.check(true, "fingerprint handshake agrees with the library");
}

fn capabilities(t: &mut Tally) {
    let mut caps = viprs_acad_capabilities_v1 {
        struct_size: std::mem::size_of::<viprs_acad_capabilities_v1>() as u32,
        struct_version: 1,
        ..Default::default()
    };
    let mut required: u64 = 0;

    let rc = unsafe {
        viprs_acad_capabilities_v1(&mut caps, std::ptr::null_mut(), 0, &mut required)
    };
    t.check(
        rc == VIPRS_ACAD_OK,
        "a sizing call with a capacity of zero is not a failure",
    );
    t.check(required > 0, "the sizing call reports a required length");

    let mut buffer = vec![0u8; required as usize + 1];
    let rc = unsafe {
        viprs_acad_capabilities_v1(&mut caps, buffer.as_mut_ptr(), required, &mut required)
    };
    t.check(rc == VIPRS_ACAD_OK, "capabilities fills the struct and the buffer");
    let text = CStr::from_bytes_until_nul(&buffer)
        .map(|s| s.to_string_lossy().into_owned())
        .unwrap_or_default();
    println!(
        "      backing version=\"{}\" abi={} wire={} dwg={}..{}",
        text, caps.abi_version, caps.wire_version, caps.dwg_version_min, caps.dwg_version_max
    );
    t.check(
        caps.abi_version == VIPRS_ACAD_ABI_VERSION,
        "capabilities reports this abi_version",
    );
    t.check(
        caps.wire_version == VIPRS_ACAD_WIRE_VERSION,
        "capabilities reports the wire version",
    );
    t.check(
        caps.dwg_version_min == 1014 && caps.dwg_version_max == 1032,
        "capabilities reports the documented AC10xx range",
    );
    t.check(
        caps.supports_warnings <= 1 && caps.supports_block_expansion <= 1,
        "a flag is 0 or 1 and nothing else",
    );

    let mut small = [b'Z'; 4];
    let rc = unsafe {
        viprs_acad_capabilities_v1(&mut caps, small.as_mut_ptr(), 1, &mut required)
    };
    t.check(rc == VIPRS_ACAD_LIMIT_EXCEEDED, "a buffer too small is LIMIT_EXCEEDED");
    t.check(
        small[0] == b'Z',
        "and nothing was written into it, not even a prefix",
    );

    let mut wrong = viprs_acad_capabilities_v1 {
        struct_size: 8,
        struct_version: 1,
        ..Default::default()
    };
    let rc = unsafe {
        viprs_acad_capabilities_v1(&mut wrong, std::ptr::null_mut(), 0, &mut required)
    };
    t.check(
        rc == VIPRS_ACAD_INVALID_ARGUMENT,
        "a struct_size this build does not know is INVALID_ARGUMENT",
    );
}

fn arguments(t: &mut Tally) {
    let synth = synthetic_input(2, 9);
    let mut doc: *mut viprs_cad_handle = std::ptr::null_mut();
    let mut dec: *mut viprs_decode_handle = std::ptr::null_mut();
    let mut required: u64 = 0;
    let mut written: u64 = 0;
    let mut done: u8 = 0;
    let mut count: u32 = 0;
    let mut buf = vec![0u8; 4096];
    let path = b"/nonexistent/viprs.dwg";

    unsafe {
        t.check(
            viprs_acad_capabilities_v1(std::ptr::null_mut(), std::ptr::null_mut(), 0, &mut required)
                == VIPRS_ACAD_INVALID_ARGUMENT,
            "capabilities with a null out struct",
        );

        t.check(
            viprs_acad_open_path_utf8(std::ptr::null(), 4, std::ptr::null(), &mut doc)
                == VIPRS_ACAD_INVALID_ARGUMENT,
            "open_path with a null path",
        );
        t.check(
            viprs_acad_open_path_utf8(path.as_ptr(), 0, std::ptr::null(), &mut doc)
                == VIPRS_ACAD_INVALID_ARGUMENT,
            "open_path with a zero length",
        );
        t.check(
            viprs_acad_open_path_utf8(
                path.as_ptr(),
                path.len() as u64,
                std::ptr::null(),
                std::ptr::null_mut(),
            ) == VIPRS_ACAD_INVALID_ARGUMENT,
            "open_path with a null out handle",
        );

        t.check(
            viprs_acad_open_memory(std::ptr::null(), 16, std::ptr::null(), &mut doc)
                == VIPRS_ACAD_INVALID_ARGUMENT,
            "open_memory with null data",
        );
        t.check(
            viprs_acad_open_memory(synth.as_ptr(), 0, std::ptr::null(), &mut doc)
                == VIPRS_ACAD_INVALID_ARGUMENT,
            "open_memory with a zero length",
        );
        t.check(
            viprs_acad_open_memory(
                synth.as_ptr(),
                synth.len() as u64,
                std::ptr::null(),
                std::ptr::null_mut(),
            ) == VIPRS_ACAD_INVALID_ARGUMENT,
            "open_memory with a null out handle",
        );

        let bad_size = viprs_acad_limits_v1 {
            struct_size: 12,
            struct_version: 1,
            ..Default::default()
        };
        t.check(
            viprs_acad_open_memory(synth.as_ptr(), synth.len() as u64, &bad_size, &mut doc)
                == VIPRS_ACAD_INVALID_ARGUMENT,
            "open_memory with a limits struct_size this build does not know",
        );

        let bad_version = viprs_acad_limits_v1 {
            struct_size: std::mem::size_of::<viprs_acad_limits_v1>() as u32,
            struct_version: 99,
            ..Default::default()
        };
        t.check(
            viprs_acad_open_memory(synth.as_ptr(), synth.len() as u64, &bad_version, &mut doc)
                == VIPRS_ACAD_INVALID_ARGUMENT,
            "open_memory with a limits struct_version this build does not know",
        );

        t.check(
            viprs_acad_view_count(std::ptr::null_mut(), &mut count) == VIPRS_ACAD_INVALID_ARGUMENT,
            "view_count with a null handle",
        );

        // A number this library never issued. A shim that treated a handle as
        // an address would dereference this and take the process with it.
        t.check(
            viprs_acad_view_count(0xDEAD as *mut viprs_cad_handle, &mut count)
                == VIPRS_ACAD_INVALID_ARGUMENT,
            "view_count with a handle this library never issued",
        );

        // Both are documented as never failing and take a null as a no-op. If
        // either faulted, this process would not reach the next line.
        viprs_acad_decode_close(std::ptr::null_mut());
        viprs_acad_close(std::ptr::null_mut());
        t.check(1 == 1, "close and decode_close accept a null without faulting");

        // Now against a live document.
        let rc = viprs_acad_open_memory(
            synth.as_ptr(),
            synth.len() as u64,
            std::ptr::null(),
            &mut doc,
        );
        t.check(rc == VIPRS_ACAD_OK, "open_memory recognises the synthetic magic");
        if rc != VIPRS_ACAD_OK {
            return;
        }

        t.check(
            viprs_acad_view_count(doc, std::ptr::null_mut()) == VIPRS_ACAD_INVALID_ARGUMENT,
            "view_count with a null out pointer",
        );

        let mut info = viprs_view_info_v1 {
            struct_size: std::mem::size_of::<viprs_view_info_v1>() as u32,
            struct_version: 1,
            ..Default::default()
        };
        t.check(
            viprs_acad_view_info_v1(doc, 0, std::ptr::null_mut(), std::ptr::null_mut(), 0, &mut required)
                == VIPRS_ACAD_INVALID_ARGUMENT,
            "view_info with a null out struct",
        );
        t.check(
            viprs_acad_view_info_v1(doc, 0, &mut info, std::ptr::null_mut(), 0, std::ptr::null_mut())
                == VIPRS_ACAD_INVALID_ARGUMENT,
            "view_info with a null required pointer",
        );
        t.check(
            viprs_acad_view_info_v1(doc, 4_000_000, &mut info, std::ptr::null_mut(), 0, &mut required)
                == VIPRS_ACAD_INVALID_ARGUMENT,
            "view_info with an index past the end",
        );
        t.check(
            viprs_acad_decode_begin(doc, 0, std::ptr::null(), std::ptr::null_mut())
                == VIPRS_ACAD_INVALID_ARGUMENT,
            "decode_begin with a null out handle",
        );
        t.check(
            viprs_acad_decode_begin(doc, 4_000_000, std::ptr::null(), &mut dec)
                == VIPRS_ACAD_INVALID_ARGUMENT,
            "decode_begin with an index past the end",
        );

        t.check(
            viprs_acad_decode_begin(doc, 0, std::ptr::null(), &mut dec) == VIPRS_ACAD_OK,
            "decode_begin",
        );
        t.check(
            viprs_acad_decode_next_batch(dec, std::ptr::null_mut(), 4096, &mut written, &mut done)
                == VIPRS_ACAD_INVALID_ARGUMENT,
            "decode_next_batch with a null buffer",
        );
        t.check(
            viprs_acad_decode_next_batch(dec, buf.as_mut_ptr(), 0, &mut written, &mut done)
                == VIPRS_ACAD_INVALID_ARGUMENT,
            "decode_next_batch with a zero capacity",
        );
        t.check(
            viprs_acad_decode_next_batch(
                dec,
                buf.as_mut_ptr(),
                4096,
                std::ptr::null_mut(),
                &mut done,
            ) == VIPRS_ACAD_INVALID_ARGUMENT,
            "decode_next_batch with a null written pointer",
        );
        t.check(
            viprs_acad_decode_next_batch(
                dec,
                buf.as_mut_ptr(),
                4096,
                &mut written,
                std::ptr::null_mut(),
            ) == VIPRS_ACAD_INVALID_ARGUMENT,
            "decode_next_batch with a null done pointer",
        );

        viprs_acad_decode_close(dec);
        viprs_acad_close(doc);
    }
}

fn views(t: &mut Tally) {
    let synth = synthetic_input(3, 9);
    let mut doc: *mut viprs_cad_handle = std::ptr::null_mut();
    let mut count: u32 = 0;
    let mut required: u64 = 0;

    unsafe {
        if viprs_acad_open_memory(synth.as_ptr(), synth.len() as u64, std::ptr::null(), &mut doc)
            != VIPRS_ACAD_OK
        {
            t.check(false, "open_memory for the view tests");
            return;
        }

        t.check(viprs_acad_view_count(doc, &mut count) == VIPRS_ACAD_OK, "view_count");
        t.check(count == 3, "view_count reports what the input asked for");

        let mut info = viprs_view_info_v1 {
            struct_size: std::mem::size_of::<viprs_view_info_v1>() as u32,
            struct_version: 1,
            ..Default::default()
        };
        t.check(
            viprs_acad_view_info_v1(doc, 0, &mut info, std::ptr::null_mut(), 0, &mut required)
                == VIPRS_ACAD_OK,
            "view_info sizing call",
        );
        let mut name = vec![0u8; required as usize];
        t.check(
            viprs_acad_view_info_v1(doc, 0, &mut info, name.as_mut_ptr(), required, &mut required)
                == VIPRS_ACAD_OK,
            "view_info with a buffer",
        );
        println!(
            "      view 0 name=\"{}\" kind={} extents=[{} {} {} {}] entity_count={}",
            String::from_utf8_lossy(&name),
            info.kind,
            info.min_x,
            info.min_y,
            info.max_x,
            info.max_y,
            info.entity_count
        );
        t.check(info.index == 0, "view_info echoes the index back");
        t.check(info.kind <= 2, "kind is one of the three documented values");

        viprs_acad_close(doc);
    }
}

fn cancellation(t: &mut Tally) {
    let synth = synthetic_input(1, 9);
    let mut doc: *mut viprs_cad_handle = std::ptr::null_mut();
    let mut dec: *mut viprs_decode_handle = std::ptr::null_mut();
    let cancel_flag: u32 = 1;
    let mut written: u64 = 123;
    let mut done: u8 = 9;
    let mut buf = vec![0u8; wire::TARGET_BATCH_BYTES];

    unsafe {
        if viprs_acad_open_memory(synth.as_ptr(), synth.len() as u64, std::ptr::null(), &mut doc)
            != VIPRS_ACAD_OK
        {
            t.check(false, "open_memory for the cancel test");
            return;
        }
        t.check(
            viprs_acad_decode_begin(doc, 0, &cancel_flag, &mut dec) == VIPRS_ACAD_OK,
            "decode_begin with a cancel flag",
        );
        let rc = viprs_acad_decode_next_batch(
            dec,
            buf.as_mut_ptr(),
            buf.len() as u64,
            &mut written,
            &mut done,
        );
        t.check(
            rc == VIPRS_ACAD_CANCELED,
            "a cancel flag set before the first batch is CANCELED, not a batch",
        );
        t.check(written == 0, "and nothing was written");

        viprs_acad_decode_close(dec);
        viprs_acad_close(doc);
    }
}

fn small_buffer(t: &mut Tally) {
    let synth = synthetic_input(1, 9);
    let mut doc: *mut viprs_cad_handle = std::ptr::null_mut();
    let mut dec: *mut viprs_decode_handle = std::ptr::null_mut();
    let mut written: u64 = 0;
    let mut done: u8 = 0;
    let mut tiny = [0u8; 16];

    unsafe {
        if viprs_acad_open_memory(synth.as_ptr(), synth.len() as u64, std::ptr::null(), &mut doc)
            != VIPRS_ACAD_OK
        {
            t.check(false, "open_memory for the small-buffer test");
            return;
        }
        viprs_acad_decode_begin(doc, 0, std::ptr::null(), &mut dec);
        let rc = viprs_acad_decode_next_batch(
            dec,
            tiny.as_mut_ptr(),
            tiny.len() as u64,
            &mut written,
            &mut done,
        );
        t.check(
            rc == VIPRS_ACAD_LIMIT_EXCEEDED,
            "a buffer too small for one batch is LIMIT_EXCEEDED",
        );
        t.check(
            written > tiny.len() as u64,
            "and the size it needs comes back through written",
        );
        println!("      needed {written} bytes for the first batch");

        viprs_acad_decode_close(dec);
        viprs_acad_close(doc);
    }
}

/// docs/WIRE.md's lengths for the records whose length never varies, as the
/// whole record including its eight-byte header. `None` means the payload is
/// variable-length and there is nothing to check.
fn fixed_bytes(kind: u16) -> Option<usize> {
    match kind {
        wire::TYPE_DOCUMENT_BEGIN => Some(24),
        wire::TYPE_LINE => Some(72),
        wire::TYPE_ARC => Some(96),
        wire::TYPE_CIRCLE => Some(80),
        wire::TYPE_ELLIPSE => Some(120),
        wire::TYPE_VIEW_END => Some(24),
        wire::TYPE_DOCUMENT_END => Some(24),
        _ => None,
    }
}

fn decode_and_parse(t: &mut Tally) {
    // Enough primitives that the stream does not fit in one batch. A decode
    // that always fits in the first call never exercises the loop, and the
    // loop is where a producer gets the framing wrong.
    let synth = synthetic_input(1, 2000);
    let mut doc: *mut viprs_cad_handle = std::ptr::null_mut();
    let mut dec: *mut viprs_decode_handle = std::ptr::null_mut();
    let mut written: u64 = 0;
    let mut done: u8 = 0;
    let mut buf = vec![0u8; wire::MAX_BATCH_BYTES];

    let mut seen = [false; 16];
    let mut probes = 0;
    let mut batches = 0;
    let mut expect_after_probe = false;
    let mut after_probe_was_view_end = false;
    let mut last_flag_seen = false;
    let mut fixed_size_wrong = None;

    unsafe {
        if viprs_acad_open_memory(synth.as_ptr(), synth.len() as u64, std::ptr::null(), &mut doc)
            != VIPRS_ACAD_OK
        {
            t.check(false, "open_memory for the decode test");
            return;
        }
        t.check(
            viprs_acad_decode_begin(doc, 0, std::ptr::null(), &mut dec) == VIPRS_ACAD_OK,
            "decode_begin",
        );

        while done == 0 {
            let rc = viprs_acad_decode_next_batch(
                dec,
                buf.as_mut_ptr(),
                buf.len() as u64,
                &mut written,
                &mut done,
            );
            if rc != VIPRS_ACAD_OK {
                t.check(false, "decode_next_batch returned a failure mid-stream");
                break;
            }

            batches += 1;
            let mut reader = match Reader::open(&buf[..written as usize]) {
                Ok(r) => r,
                Err(_) => {
                    t.check(false, "the batch the library produced parses");
                    break;
                }
            };
            if reader.last_batch() {
                last_flag_seen = true;
            }

            loop {
                match reader.next() {
                    Ok(Some(record)) => {
                        if record.kind >= wire::FORWARD_PROBE_FIRST {
                            probes += 1;
                            expect_after_probe = true;
                            continue;
                        }
                        if expect_after_probe {
                            expect_after_probe = false;
                            after_probe_was_view_end = record.kind == wire::TYPE_VIEW_END;
                        }
                        if (record.kind as usize) < seen.len() {
                            seen[record.kind as usize] = true;
                        }
                        // The documented length of every record whose length
                        // does not vary, against what the library emitted. A
                        // payload table nobody compares against the producer
                        // is a payload table that drifts.
                        if let Some(expected) = fixed_bytes(record.kind) {
                            if record.payload.len() + wire::RECORD_HEADER_BYTES != expected {
                                fixed_size_wrong = Some(record.kind);
                            }
                        }
                    }
                    Ok(None) => break,
                    Err(_) => {
                        t.check(false, "the batch ends cleanly rather than on a refusal");
                        break;
                    }
                }
            }
        }

        viprs_acad_decode_close(dec);
        viprs_acad_close(doc);
    }

    println!("      {batches} batches, {probes} forward probes skipped");
    for kind in 1..=13usize {
        t.check(seen[kind], &format!("record type {kind} appeared in the stream"));
    }
    t.check(batches > 1, "the stream spanned more than one batch, so the loop ran");
    t.check(probes >= 1, "the stream carried a record of an unknown type");
    t.check(
        after_probe_was_view_end,
        "the record after the unknown one was read, so the skip used its length",
    );
    t.check(last_flag_seen, "the final batch carries the last-batch flag");
    t.check(
        fixed_size_wrong.is_none(),
        "every fixed-size record is the length docs/WIRE.md gives it",
    );
}

const DRAIN_CAP: u32 = 100_000;

/// Reads a batch to its end, or to its refusal.
fn drain(reader: &mut Reader) -> Result<(), u32> {
    let mut guard = DRAIN_CAP;
    loop {
        match reader.next() {
            Ok(Some(_)) => {
                guard -= 1;
                if guard == 0 {
                    return Err(VIPRS_ACAD_INTERNAL_ERROR);
                }
            }
            Ok(None) => return Ok(()),
            Err(code) => return Err(code),
        }
    }
}

fn malformed(t: &mut Tally) {
    // An unknown type between two known ones, assembled by hand rather than
    // produced, so the skip is checked against a payload length no known
    // record has.
    let mut body = Vec::new();
    body.extend(wire::build_record(wire::TYPE_LINE, 72, 64));
    body.extend(wire::build_record(0x7F42, 24, 16));
    body.extend(wire::build_record(wire::TYPE_CIRCLE, 16, 8));
    let batch = wire::build_batch(wire::WIRE_VERSION, body.len() as u32, &body);

    let mut reader = Reader::open(&batch).expect("a hand-built batch opens");
    t.check(
        reader.next().unwrap().unwrap().kind == wire::TYPE_LINE,
        "first record",
    );
    t.check(
        reader.next().unwrap().unwrap().kind == 0x7F42,
        "the unknown record is reported rather than refused",
    );
    t.check(
        reader.next().unwrap().unwrap().kind == wire::TYPE_CIRCLE,
        "the record after the unknown one is read, so the skip used its length",
    );
    t.check(reader.next().unwrap().is_none(), "and the batch ends");

    let body = wire::build_record(wire::TYPE_LINE, 72, 64);
    let batch = wire::build_batch(wire::WIRE_VERSION, body.len() as u32 + 4096, &body);
    t.check(
        Reader::open(&batch).err() == Some(VIPRS_ACAD_CORRUPT_INPUT),
        "a payload_length past the buffer end is CORRUPT_INPUT",
    );

    // These drain rather than calling next() once. With the short-record rule
    // removed, one call returns a record and nothing looks wrong: the cursor
    // only fails to advance on the call after it. The counter is this test's
    // own, separate from the parser's, so a parser with no bound shows up as a
    // failed assertion rather than as a job somebody has to kill.
    for claimed in [0u32, 4, 7] {
        let body = wire::build_record(wire::TYPE_LINE, claimed, 64);
        let batch = wire::build_batch(wire::WIRE_VERSION, body.len() as u32, &body);
        let mut reader = Reader::open(&batch).expect("the short-record batch opens");
        let outcome = drain(&mut reader);
        t.check(
            outcome == Err(VIPRS_ACAD_CORRUPT_INPUT),
            &format!("a record length of {claimed}, below its own header, is CORRUPT_INPUT"),
        );
        t.check(
            !reader.ran_away,
            "and it was refused outright, not retried until the parser's own bound caught it",
        );
    }

    let body = wire::build_record(wire::TYPE_LINE, 9, 64);
    let batch = wire::build_batch(wire::WIRE_VERSION, body.len() as u32, &body);
    let mut reader = Reader::open(&batch).unwrap();
    t.check(
        drain(&mut reader) == Err(VIPRS_ACAD_CORRUPT_INPUT),
        "a record length that is not a multiple of four is CORRUPT_INPUT",
    );

    let body = wire::build_record(wire::TYPE_LINE, 4096, 16);
    let batch = wire::build_batch(wire::WIRE_VERSION, body.len() as u32, &body);
    let mut reader = Reader::open(&batch).unwrap();
    t.check(
        drain(&mut reader) == Err(VIPRS_ACAD_CORRUPT_INPUT),
        "a record length running past the payload is CORRUPT_INPUT",
    );

    let body = wire::build_record(wire::TYPE_LINE, 72, 64);
    let mut batch = wire::build_batch(wire::WIRE_VERSION, body.len() as u32, &body);
    batch[2] = b'X';
    t.check(
        Reader::open(&batch).err() == Some(VIPRS_ACAD_CORRUPT_INPUT),
        "a batch with the wrong magic is CORRUPT_INPUT",
    );

    let batch = wire::build_batch(2, body.len() as u32, &body);
    t.check(
        Reader::open(&batch).err() == Some(VIPRS_ACAD_UNSUPPORTED_FORMAT),
        "a wire version this consumer does not parse is refused, not guessed",
    );

    let batch = wire::build_batch(wire::WIRE_VERSION, 0, &[]);
    let mut reader = Reader::open(&batch).expect("an empty batch is legal");
    t.check(reader.next().unwrap().is_none(), "and it holds no records");

    t.check(
        Reader::open(&batch[..4]).err() == Some(VIPRS_ACAD_CORRUPT_INPUT),
        "a buffer too short to hold a batch header is CORRUPT_INPUT",
    );
}

fn exceptions(t: &mut Tally) {
    #[cfg(viprs_test_exports)]
    {
        for kind in 1..=3u32 {
            let rc = unsafe { viprs_acad__test_throw(kind) };
            t.check(
                rc == VIPRS_ACAD_INTERNAL_ERROR,
                &format!("a managed exception of kind {kind} arrives as INTERNAL_ERROR"),
            );
        }
        t.check(
            unsafe { viprs_acad__test_throw(4) } == VIPRS_ACAD_CORRUPT_INPUT,
            "a failure with a code of its own keeps that code rather than becoming a bug",
        );
        t.check(
            unsafe { viprs_acad__test_throw(99) } == VIPRS_ACAD_INTERNAL_ERROR,
            "and anything else is still INTERNAL_ERROR",
        );
        // The part that matters. Reaching this line at all means none of those
        // five calls took the process with it.
        t.check(
            unsafe { viprs_acad_abi_version() } == VIPRS_ACAD_ABI_VERSION,
            "the library still works after five exceptions crossed the boundary",
        );
    }

    #[cfg(not(viprs_test_exports))]
    {
        let _ = t;
        println!("      skipped: built against a library without the test exports");
    }
}

fn main() {
    let mut t = Tally {
        checks: 0,
        failures: 0,
    };

    println!("VIPRS CAD ABI conformance, generated consumer");
    println!("--- handshake");
    handshake(&mut t);
    println!("--- capabilities");
    capabilities(&mut t);
    println!("--- arguments");
    arguments(&mut t);
    println!("--- views");
    views(&mut t);
    println!("--- cancellation");
    cancellation(&mut t);
    println!("--- buffer sizing");
    small_buffer(&mut t);
    println!("--- decode and parse");
    decode_and_parse(&mut t);
    println!("--- malformed batches");
    malformed(&mut t);
    println!("--- exceptions");
    exceptions(&mut t);

    println!("\n{} checks, {} failures", t.checks, t.failures);
    if t.failures > 0 {
        std::process::exit(1);
    }
}
