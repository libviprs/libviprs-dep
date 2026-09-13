/*
 * viprs_acadsharp.h - the VIPRS CAD C ABI, version 1.
 *
 * This header is the contract. Everything downstream is written against it,
 * including the `acadsharp-rs` crate, so it is frozen here rather than left to
 * evolve alongside the adapter that happens to back it today.
 *
 * It describes a VIPRS-owned boundary, never ACadSharp's object model. No
 * upstream document, entity, layer or block type crosses it. The managed
 * implementation behind these symbols is an implementation detail, and a
 * second implementation in any language could satisfy this file alone.
 *
 * Those four are written in lower case on purpose. The acceptance check for
 * this file greps it for the PascalCase type names, and this repository has
 * already learnt once that a check which greps a whole file for a forbidden
 * string fires on the prose explaining why the string is forbidden
 * (zstd/tests/test_ci_coverage.py grew `without_comments()` for exactly that).
 * The forbidden thing is the type in the ABI surface, so the rule is stated
 * here without spelling the types.
 *
 * The rules, and each one exists because its absence has broken a C ABI
 * somewhere before:
 *
 *   - No struct on this boundary is typedef'd to its own name. In C a
 *     typedef name and a function name are the same kind of identifier, and
 *     viprs_acad_capabilities_v1 is both a struct and a call, so a header
 *     that typedef'd it would not compile as C at all. It compiled as C++,
 *     which is how a header ships broken. The two opaque handles below are
 *     typedef'd, because they have no fields and no call shares their name.
 *   - Every struct opens with `uint32_t struct_size; uint32_t struct_version;`
 *     and otherwise holds only fixed-width scalars. The caller sets both; a
 *     callee that receives a struct_size it does not recognise returns
 *     VIPRS_ACAD_INVALID_ARGUMENT rather than reading past what was allocated.
 *   - Result codes are plain uint32_t constants, never a C enum. Enum layout
 *     is implementation defined, and the width a C compiler picks is not the
 *     width a Rust or C# one picks.
 *   - No bool. C `bool` and C# `bool` do not agree on width, and neither is
 *     fixed by either language's ABI. Flags are uint8_t, 0 or 1.
 *   - No pointer-sized integers except opaque handles. `size_t` and `nuint`
 *     differ across the targets this ships to. Lengths are uint64_t.
 *   - No implicit null termination. Every string is a byte pointer plus an
 *     explicit length, in both directions.
 *   - No callbacks into the caller in v1. Cancellation is a flag the callee
 *     polls, not a function it calls.
 *
 * Strings are UTF-8, written into caller-provided buffers. Every such call
 * takes a capacity and writes the required byte count through an out
 * parameter, so a caller can size a buffer by calling once with a capacity of
 * zero. The required length never includes a terminator, because there is
 * none.
 *
 * Threading: one decode handle is single threaded. Two handles may be used
 * from two threads. Nothing here is re-entrant.
 *
 * See docs/ABI.md for the ownership and error model in prose, and docs/WIRE.md
 * for the batch protocol the decode calls emit.
 */

#ifndef VIPRS_ACADSHARP_H
#define VIPRS_ACADSHARP_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* -------------------------------------------------------------------------
 * Versions
 * ------------------------------------------------------------------------- */

/* Bumped when anything in this header changes in a way a compiled consumer
 * would notice. The fingerprint below is the finer-grained check. */
#define VIPRS_ACAD_ABI_VERSION 1u

/* The batch protocol version, documented in docs/WIRE.md. Carried in every
 * batch header so a consumer can refuse a stream it cannot parse. */
#define VIPRS_ACAD_WIRE_VERSION 1u

/* -------------------------------------------------------------------------
 * Result codes
 *
 * Plain constants rather than an enum, deliberately. Downstream switches on
 * the numeric value and never parses an error string for control flow.
 * ------------------------------------------------------------------------- */

#define VIPRS_ACAD_OK                  0u
#define VIPRS_ACAD_INVALID_ARGUMENT    1u
#define VIPRS_ACAD_UNSUPPORTED_FORMAT  2u
#define VIPRS_ACAD_CORRUPT_INPUT       3u
#define VIPRS_ACAD_UNSUPPORTED_ENTITY  4u
#define VIPRS_ACAD_OUT_OF_MEMORY       5u
#define VIPRS_ACAD_CANCELED            6u
#define VIPRS_ACAD_INTERNAL_ERROR      7u
#define VIPRS_ACAD_ABI_MISMATCH        8u
#define VIPRS_ACAD_LIMIT_EXCEEDED      9u

/* -------------------------------------------------------------------------
 * Opaque handles
 *
 * Created and destroyed only by the calls below. The caller never dereferences
 * one and never frees one with free(). No managed pointer is ever exposed.
 * ------------------------------------------------------------------------- */

typedef struct viprs_cad_handle viprs_cad_handle;
typedef struct viprs_decode_handle viprs_decode_handle;

/* -------------------------------------------------------------------------
 * Limits
 *
 * DWG is untrusted input and this library is not a sandbox. Every bound the
 * decoder enforces is here, set by the caller, so a host can make the tradeoff
 * rather than inherit one. A null pointer to either open call means the
 * documented defaults; a zero field means the default for that field, so a
 * caller can set one bound without knowing the rest.
 *
 * Exceeding any of these is VIPRS_ACAD_LIMIT_EXCEEDED, never a crash and never
 * a silently truncated stream.
 * ------------------------------------------------------------------------- */

struct viprs_acad_limits_v1 {
	uint32_t struct_size;
	uint32_t struct_version;

	/* Refused by open_* before the input is read. */
	uint64_t max_input_bytes;
	/* Counted across the whole decode, block expansion included. */
	uint64_t max_entities;
	/* Longest single UTF-8 string a Text or Warning record may carry. */
	uint64_t max_string_bytes;
	/* Vertices in one Polyline record. */
	uint64_t max_polyline_points;
	/* INSERT nesting depth. Exceeding it is a bounded refusal, not a stack
	 * overflow, which is the only reason this field exists. */
	uint32_t max_block_depth;
	uint32_t reserved0;
	/* Total bytes the decode may emit across every batch. */
	uint64_t max_output_bytes;
};

/* -------------------------------------------------------------------------
 * Capabilities
 *
 * What this build can actually do, asked at run time rather than assumed from
 * the version it was compiled against. dwg_version_min and dwg_version_max are
 * the numeric AC10xx codes, so 1014 and 1032 for ACadSharp 3.7.1.
 * ------------------------------------------------------------------------- */

struct viprs_acad_capabilities_v1 {
	uint32_t struct_size;
	uint32_t struct_version;

	uint32_t abi_version;
	uint32_t wire_version;
	/* Inclusive AC10xx range this build reads. */
	uint32_t dwg_version_min;
	uint32_t dwg_version_max;
	/* 1 when the build can expand INSERT into transformed primitives. */
	uint8_t  supports_block_expansion;
	/* 1 when reader notifications reach the stream as Warning records. */
	uint8_t  supports_warnings;
	uint8_t  reserved0;
	uint8_t  reserved1;
	uint32_t reserved2;
};

/* -------------------------------------------------------------------------
 * View info
 *
 * A view is model space or a paper-space layout. The name is written into a
 * caller buffer rather than returned as a pointer, so nothing the callee owns
 * outlives the call.
 * ------------------------------------------------------------------------- */

struct viprs_view_info_v1 {
	uint32_t struct_size;
	uint32_t struct_version;

	uint32_t index;
	/* 0 model, 1 layout, 2 unknown. A uint32_t rather than an enum. */
	uint32_t kind;
	/* Drawing-unit extents. All zero when the view is empty. */
	double   min_x;
	double   min_y;
	double   max_x;
	double   max_y;
	/* Entities before block expansion, for progress reporting only. Never
	 * relied on as an exact count of the records a decode will emit. */
	uint64_t entity_count;
};

/* -------------------------------------------------------------------------
 * Entry points
 * ------------------------------------------------------------------------- */

/* VIPRS_ACAD_ABI_VERSION as this library was built. Never fails. */
uint32_t viprs_acad_abi_version(void);

/* The first eight bytes of the sha256 of this header, big-endian.
 *
 * Generated from the header at build time rather than assigned by hand, so it
 * cannot drift from the file it describes. A consumer compares it against the
 * value in its own generated bindings; a mismatch means the header and the
 * library came from different commits and is VIPRS_ACAD_ABI_MISMATCH. */
uint64_t viprs_acad_abi_fingerprint(void);

/* Fills *out and writes the pinned ACadSharp version into acadsharp_version_utf8.
 *
 * Call with cap 0 and a null buffer to learn the required length through
 * *required. out->struct_size and out->struct_version must be set by the caller
 * before the call. */
uint32_t viprs_acad_capabilities_v1(struct viprs_acad_capabilities_v1 *out,
                                    uint8_t *acadsharp_version_utf8,
                                    uint64_t cap,
                                    uint64_t *required);

/* Opens a document from a filesystem path. Preferred over open_memory: the
 * input is not duplicated across the boundary.
 *
 * path is UTF-8 and is not null terminated; path_len is its byte length.
 * limits may be null for the defaults. */
uint32_t viprs_acad_open_path_utf8(const uint8_t *path,
                                   uint64_t path_len,
                                   const struct viprs_acad_limits_v1 *limits,
                                   viprs_cad_handle **out);

/* Opens a document from caller-owned bytes. The caller owns data for the
 * duration of this call only. */
uint32_t viprs_acad_open_memory(const uint8_t *data,
                                uint64_t data_len,
                                const struct viprs_acad_limits_v1 *limits,
                                viprs_cad_handle **out);

/* Number of views in the document. */
uint32_t viprs_acad_view_count(viprs_cad_handle *h, uint32_t *out_count);

/* Fills *out for one view and writes its name into name_utf8.
 *
 * Same buffer convention as capabilities: cap 0 with a null buffer reports the
 * required length through *name_required. */
uint32_t viprs_acad_view_info_v1(viprs_cad_handle *h,
                                 uint32_t index,
                                 struct viprs_view_info_v1 *out,
                                 uint8_t *name_utf8,
                                 uint64_t name_cap,
                                 uint64_t *name_required);

/* Begins decoding one view into the batch stream described in docs/WIRE.md.
 *
 * cancel_flag is caller-owned and may be null. When non-null the decoder reads
 * it between batches; any non-zero value stops the decode with
 * VIPRS_ACAD_CANCELED. It is read, never written, and the caller may change it
 * from another thread. */
uint32_t viprs_acad_decode_begin(viprs_cad_handle *h,
                                 uint32_t view_index,
                                 const uint32_t *cancel_flag,
                                 viprs_decode_handle **out);

/* Writes the next batch into buf.
 *
 * *written is the byte count produced, *done is 1 when the stream is complete.
 * A batch never spans two calls: if cap is too small for the next batch the
 * call returns VIPRS_ACAD_LIMIT_EXCEEDED and writes the needed size into
 * *written, so the caller can grow its buffer and retry.
 *
 * cap must be at least 12, the size of a batch header. A successful call
 * always writes one complete batch, and the header is part of every batch, so
 * a cap below that cannot be satisfied at all: it returns
 * VIPRS_ACAD_LIMIT_EXCEEDED with 12 in *written, having written nothing.
 *
 * Calling again after *done came back 1 is legal and is not an error. It
 * writes an empty batch carrying the last-batch flag and reports *done 1
 * again, so a caller whose loop asks one more time than it needed to
 * terminates rather than failing. The alternative, succeeding with *written 0,
 * was rejected: it would make a successful call sometimes produce bytes a
 * parser can read and sometimes produce nothing, and a consumer that parses
 * every successful batch would have to learn the difference. *written is
 * therefore never 0 on success and never less than 12. */
uint32_t viprs_acad_decode_next_batch(viprs_decode_handle *d,
                                      uint8_t *buf,
                                      uint64_t cap,
                                      uint64_t *written,
                                      uint8_t *done);

/* Releases a decode handle. Null is a no-op. Never fails. */
void viprs_acad_decode_close(viprs_decode_handle *d);

/* Releases a document handle and every decode handle still open on it. Null is
 * a no-op. Never fails. */
void viprs_acad_close(viprs_cad_handle *h);

#ifdef __cplusplus
}
#endif

#endif /* VIPRS_ACADSHARP_H */
