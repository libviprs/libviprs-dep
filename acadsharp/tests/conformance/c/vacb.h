/*
 * vacb.h - a consumer-side parser for the VACB batch protocol.
 *
 * Written from docs/WIRE.md, deliberately without looking at how the producer
 * writes the bytes. A parser derived from the producer's own code agrees with
 * the producer by construction and proves nothing about the document.
 *
 * The numbers below are carried here separately from the shim and from the
 * other conformance consumer. All three are compared as text by a test, so a
 * type that is right in two of the three cannot quietly go missing from a
 * large drawing months later.
 */

#ifndef VIPRS_CONFORMANCE_VACB_H
#define VIPRS_CONFORMANCE_VACB_H

#include <stdint.h>

#define VACB_MAGIC_0 0x56 /* 'V' */
#define VACB_MAGIC_1 0x41 /* 'A' */
#define VACB_MAGIC_2 0x43 /* 'C' */
#define VACB_MAGIC_3 0x42 /* 'B' */

#define VACB_WIRE_VERSION 1

#define VACB_BATCH_HEADER_BYTES 12
#define VACB_RECORD_HEADER_BYTES 8
#define VACB_RECORD_ALIGNMENT 4

#define VACB_FLAG_LAST 1

#define VACB_TARGET_BATCH_BYTES 65536
#define VACB_MAX_BATCH_BYTES 1048576

/* Types from here up carry no meaning in wire version 1 and are skipped by
 * length. The producer emits one into every stream so this path runs on real
 * bytes rather than only on a buffer this file assembled by hand. */
#define VACB_FORWARD_PROBE_FIRST 0x7F00

#define VACB_TYPE_DOCUMENT_BEGIN 1
#define VACB_TYPE_VIEW_BEGIN 2
#define VACB_TYPE_LINE 3
#define VACB_TYPE_POLYLINE 4
#define VACB_TYPE_ARC 5
#define VACB_TYPE_CIRCLE 6
#define VACB_TYPE_ELLIPSE 7
#define VACB_TYPE_SPLINE 8
#define VACB_TYPE_POLYGON 9
#define VACB_TYPE_TEXT 10
#define VACB_TYPE_WARNING 11
#define VACB_TYPE_VIEW_END 12
#define VACB_TYPE_DOCUMENT_END 13

typedef struct vacb_record {
	uint16_t type;
	const uint8_t *payload;
	uint32_t payload_len;
} vacb_record;

typedef struct vacb_reader {
	const uint8_t *payload;
	uint32_t payload_len;
	uint32_t offset;
	uint16_t flags;
	/* One iteration per smallest possible record, plus one. A parser that
	 * fails to advance trips this rather than spinning, so a mistake in the
	 * short-record rule is a failed test and not a job somebody has to kill. */
	int32_t budget;
	int ran_away;
	uint32_t error;
} vacb_reader;

/* VIPRS_ACAD_OK, or the code docs/WIRE.md gives for the refusal. */
uint32_t vacb_open(vacb_reader *r, const uint8_t *buf, uint64_t len);

/* 1 when *out was filled, 0 at the end of the batch, -1 on a refusal with
 * r->error holding the code. */
int vacb_next(vacb_reader *r, vacb_record *out);

/* Little-endian readers. Nothing inside a record is naturally aligned, because
 * the batch header is twelve bytes, so every scalar is assembled from bytes
 * rather than loaded through a cast. */
uint16_t vacb_u16(const uint8_t *p);
uint32_t vacb_u32(const uint8_t *p);
uint64_t vacb_u64(const uint8_t *p);
double vacb_f64(const uint8_t *p);

#endif /* VIPRS_CONFORMANCE_VACB_H */
