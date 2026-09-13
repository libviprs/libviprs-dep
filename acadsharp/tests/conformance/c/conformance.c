/*
 * conformance.c - the C consumer of the VIPRS CAD C ABI.
 *
 * It includes the published header, links the shim, and checks the things the
 * header promises that no compiler on either side of the boundary can see:
 * that a null argument comes back as a code instead of a fault, that a managed
 * exception on the other side arrives as INTERNAL_ERROR with this process
 * still running, that the fingerprint handshake catches a header that drifted
 * from its library, and that the batch protocol's malformed cases are refused
 * rather than parsed.
 *
 * The expected fingerprint is computed from the header by run.sh at compile
 * time, not written here. That is what makes the drift check real: edit one
 * byte of the header, rebuild only this program, and the handshake fails
 * against a library that was built from the old one.
 *
 * Everything prints, pass or fail. A conformance run whose output is a single
 * word is a conformance run nobody can audit.
 */

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "../../../include/viprs_acadsharp.h"
#include "layout_table.h"
#include "vacb.h"

#ifndef VIPRS_EXPECTED_FINGERPRINT
#error "run.sh must pass -DVIPRS_EXPECTED_FINGERPRINT, computed from the header"
#endif

/* The upstream release the shim was built over, read out of acadsharp/VERSION
 * by run.sh and never typed here. The header promises this call writes "the
 * pinned ACadSharp version", and it wrote the artifact version for a while,
 * so the consumer that reads the string is the right place to hold it to the
 * one file that decides it. */
#ifndef VIPRS_EXPECTED_ACADSHARP_VERSION
#error "run.sh must pass -DVIPRS_EXPECTED_ACADSHARP_VERSION, read from acadsharp/VERSION"
#endif

#ifdef VIPRS_WITH_TEST_EXPORTS
/* Not in the published header on purpose: it is compiled only into the test
 * configuration of the shim, so a consumer that wants it declares it. */
extern uint32_t viprs_acad__test_throw(uint32_t kind);
extern uint64_t viprs_acad__test_live_handles(void);
#endif

static int g_checks;
static int g_failures;

static void check(int ok, const char *what)
{
	g_checks++;
	if (!ok) {
		g_failures++;
		printf("FAIL  %s\n", what);
	} else {
		printf("ok    %s\n", what);
	}
}

static void put_u16(uint8_t *p, uint16_t v)
{
	p[0] = (uint8_t)(v & 0xFF);
	p[1] = (uint8_t)((v >> 8) & 0xFF);
}

static void put_u32(uint8_t *p, uint32_t v)
{
	p[0] = (uint8_t)(v & 0xFF);
	p[1] = (uint8_t)((v >> 8) & 0xFF);
	p[2] = (uint8_t)((v >> 16) & 0xFF);
	p[3] = (uint8_t)((v >> 24) & 0xFF);
}

/* magic, wire version, flags, payload_length, then whatever bytes. */
static uint64_t build_batch(uint8_t *out, uint16_t version, uint32_t claimed_payload,
			    const uint8_t *body, uint32_t body_len)
{
	out[0] = VACB_MAGIC_0;
	out[1] = VACB_MAGIC_1;
	out[2] = VACB_MAGIC_2;
	out[3] = VACB_MAGIC_3;
	put_u16(out + 4, version);
	put_u16(out + 6, 0);
	put_u32(out + 8, claimed_payload);
	if (body_len > 0) {
		memcpy(out + VACB_BATCH_HEADER_BYTES, body, body_len);
	}
	return (uint64_t)VACB_BATCH_HEADER_BYTES + (uint64_t)body_len;
}

static uint32_t put_record(uint8_t *p, uint16_t type, uint32_t length, uint32_t payload_bytes)
{
	put_u16(p, type);
	put_u16(p + 2, 0);
	put_u32(p + 4, length);
	memset(p + VACB_RECORD_HEADER_BYTES, 0, payload_bytes);
	return VACB_RECORD_HEADER_BYTES + payload_bytes;
}

/* Reads a batch to its end, or to its refusal.
 *
 * One call to vacb_next is not enough to test the short-record rule, which the
 * mutation run proved: with the rule removed, a single call returns a record
 * and nothing looks wrong. The cursor only fails to advance on the second
 * call, so the case has to drain. The counter here is the test's own, separate
 * from the parser's, so a parser with no bound of its own shows up as a failed
 * assertion rather than as a job somebody has to kill. */
#define VACB_DRAIN_CAP 100000

static int drain(vacb_reader *r)
{
	vacb_record record;
	int guard = VACB_DRAIN_CAP;
	int rc;

	while ((rc = vacb_next(r, &record)) == 1) {
		if (--guard <= 0) {
			return -2;
		}
	}
	return rc;
}

/* ------------------------------------------------------------------------- */

static void test_handshake(void)
{
	uint32_t version = viprs_acad_abi_version();
	uint64_t fingerprint = viprs_acad_abi_fingerprint();
	uint32_t result = VIPRS_ACAD_OK;

	printf("      abi_version=%u expected=%u\n", version, (unsigned)VIPRS_ACAD_ABI_VERSION);
	printf("      fingerprint=0x%016llX expected=0x%016llX\n",
	       (unsigned long long)fingerprint, (unsigned long long)VIPRS_EXPECTED_FINGERPRINT);

	if (version != VIPRS_ACAD_ABI_VERSION || fingerprint != VIPRS_EXPECTED_FINGERPRINT) {
		result = VIPRS_ACAD_ABI_MISMATCH;
	}

	if (result == VIPRS_ACAD_ABI_MISMATCH) {
		printf("FAIL  fingerprint handshake: VIPRS_ACAD_ABI_MISMATCH (%u)\n",
		       (unsigned)VIPRS_ACAD_ABI_MISMATCH);
		printf("      the header this consumer was built from is not the header the "
		       "library was built from\n");
		exit(VIPRS_ACAD_ABI_MISMATCH);
	}

	check(1, "fingerprint handshake agrees with the library");
}

static void test_capabilities(void)
{
	struct viprs_acad_capabilities_v1 caps;
	struct viprs_acad_capabilities_v1 wrong;
	uint64_t required = 0;
	uint32_t rc;
	char *text;

	memset(&caps, 0, sizeof caps);
	caps.struct_size = (uint32_t)sizeof caps;
	caps.struct_version = 1;

	rc = viprs_acad_capabilities_v1(&caps, NULL, 0, &required);
	check(rc == VIPRS_ACAD_OK, "a sizing call with a capacity of zero is not a failure");
	check(required > 0, "the sizing call reports a required length");

	text = (char *)malloc((size_t)required + 1);
	rc = viprs_acad_capabilities_v1(&caps, (uint8_t *)text, required, &required);
	check(rc == VIPRS_ACAD_OK, "capabilities fills the struct and the buffer");
	text[required] = '\0';
	printf("      backing version=\"%s\" abi=%u wire=%u dwg=%u..%u\n", text, caps.abi_version,
	       caps.wire_version, caps.dwg_version_min, caps.dwg_version_max);
	check(caps.abi_version == VIPRS_ACAD_ABI_VERSION, "capabilities reports this abi_version");
	check(caps.wire_version == VIPRS_ACAD_WIRE_VERSION, "capabilities reports the wire version");
	check(caps.dwg_version_min == 1014 && caps.dwg_version_max == 1032,
	      "capabilities reports the documented AC10xx range");
	check(caps.supports_warnings == 0 || caps.supports_warnings == 1,
	      "a flag is 0 or 1 and nothing else");
	check(strcmp(text, VIPRS_EXPECTED_ACADSHARP_VERSION) == 0,
	      "capabilities writes the pinned upstream version the header promises");
	check(strstr(text, "viprs") == NULL,
	      "and not the artifact version, which names the archive and not the reader");
	free(text);

	/* A buffer smaller than the string writes nothing and says so. */
	text = (char *)malloc(4);
	memset(text, 'Z', 4);
	rc = viprs_acad_capabilities_v1(&caps, (uint8_t *)text, 1, &required);
	check(rc == VIPRS_ACAD_LIMIT_EXCEEDED, "a buffer too small is LIMIT_EXCEEDED");
	check(text[0] == 'Z', "and nothing was written into it, not even a prefix");
	free(text);

	memset(&wrong, 0, sizeof wrong);
	wrong.struct_size = 8;
	wrong.struct_version = 1;
	rc = viprs_acad_capabilities_v1(&wrong, NULL, 0, &required);
	check(rc == VIPRS_ACAD_INVALID_ARGUMENT,
	      "a struct_size this build does not know is INVALID_ARGUMENT");
}

static void synthetic_input(uint8_t *out, uint32_t views, uint32_t items)
{
	memcpy(out, "VIPRSSYN", 8);
	put_u32(out + 8, views);
	put_u32(out + 12, items);
}

static void test_null_arguments(void)
{
	uint8_t synth[16];
	uint8_t buf[4096];
	viprs_cad_handle *doc = NULL;
	viprs_decode_handle *dec = NULL;
	struct viprs_view_info_v1 info;
	struct viprs_acad_limits_v1 limits;
	uint64_t required = 0;
	uint64_t written = 0;
	uint32_t count = 0;
	uint8_t done = 0;
	const uint8_t *path = (const uint8_t *)"/nonexistent/viprs.dwg";

	synthetic_input(synth, 2, 9);

	check(viprs_acad_capabilities_v1(NULL, NULL, 0, &required) == VIPRS_ACAD_INVALID_ARGUMENT,
	      "capabilities with a null out struct");
	memset(&info, 0, sizeof info);
	info.struct_size = (uint32_t)sizeof info;
	info.struct_version = 1;

	check(viprs_acad_open_path_utf8(NULL, 4, NULL, &doc) == VIPRS_ACAD_INVALID_ARGUMENT,
	      "open_path with a null path");
	check(viprs_acad_open_path_utf8(path, 0, NULL, &doc) == VIPRS_ACAD_INVALID_ARGUMENT,
	      "open_path with a zero length");
	check(viprs_acad_open_path_utf8(path, 22, NULL, NULL) == VIPRS_ACAD_INVALID_ARGUMENT,
	      "open_path with a null out handle");

	check(viprs_acad_open_memory(NULL, 16, NULL, &doc) == VIPRS_ACAD_INVALID_ARGUMENT,
	      "open_memory with null data");
	check(viprs_acad_open_memory(synth, 0, NULL, &doc) == VIPRS_ACAD_INVALID_ARGUMENT,
	      "open_memory with a zero length");
	check(viprs_acad_open_memory(synth, 16, NULL, NULL) == VIPRS_ACAD_INVALID_ARGUMENT,
	      "open_memory with a null out handle");

	memset(&limits, 0, sizeof limits);
	limits.struct_size = 12;
	limits.struct_version = 1;
	check(viprs_acad_open_memory(synth, 16, &limits, &doc) == VIPRS_ACAD_INVALID_ARGUMENT,
	      "open_memory with a limits struct_size this build does not know");

	memset(&limits, 0, sizeof limits);
	limits.struct_size = (uint32_t)sizeof limits;
	limits.struct_version = 99;
	check(viprs_acad_open_memory(synth, 16, &limits, &doc) == VIPRS_ACAD_INVALID_ARGUMENT,
	      "open_memory with a limits struct_version this build does not know");

	check(viprs_acad_view_count(NULL, &count) == VIPRS_ACAD_INVALID_ARGUMENT,
	      "view_count with a null handle");

	/* A number this library never issued. A shim that treated a handle as an
	 * address would dereference this and take the process with it. */
	check(viprs_acad_view_count((viprs_cad_handle *)(size_t)0xDEAD, &count) ==
		      VIPRS_ACAD_INVALID_ARGUMENT,
	      "view_count with a handle this library never issued");

	check(viprs_acad_view_info_v1(NULL, 0, &info, NULL, 0, &required) ==
		      VIPRS_ACAD_INVALID_ARGUMENT,
	      "view_info with a null handle");
	check(viprs_acad_decode_begin(NULL, 0, NULL, &dec) == VIPRS_ACAD_INVALID_ARGUMENT,
	      "decode_begin with a null handle");
	check(viprs_acad_decode_next_batch(NULL, buf, sizeof buf, &written, &done) ==
		      VIPRS_ACAD_INVALID_ARGUMENT,
	      "decode_next_batch with a null handle");

	/* Both are documented as never failing and take a null as a no-op. If
	 * either faulted, this program would not reach the next line. */
	viprs_acad_decode_close(NULL);
	viprs_acad_close(NULL);
	check(1, "close and decode_close accept a null handle without faulting");
}

static void test_null_arguments_on_a_live_handle(void)
{
	uint8_t synth[16];
	uint8_t buf[4096];
	viprs_cad_handle *doc = NULL;
	viprs_decode_handle *dec = NULL;
	struct viprs_view_info_v1 info;
	uint64_t required = 0;
	uint64_t written = 0;
	uint8_t done = 0;
	uint32_t rc;

	synthetic_input(synth, 2, 9);
	rc = viprs_acad_open_memory(synth, sizeof synth, NULL, &doc);
	check(rc == VIPRS_ACAD_OK, "open_memory recognises the synthetic magic");
	if (rc != VIPRS_ACAD_OK) {
		return;
	}

	check(viprs_acad_view_count(doc, NULL) == VIPRS_ACAD_INVALID_ARGUMENT,
	      "view_count with a null out pointer");

	memset(&info, 0, sizeof info);
	info.struct_size = (uint32_t)sizeof info;
	info.struct_version = 1;
	check(viprs_acad_view_info_v1(doc, 0, NULL, NULL, 0, &required) ==
		      VIPRS_ACAD_INVALID_ARGUMENT,
	      "view_info with a null out struct");
	check(viprs_acad_view_info_v1(doc, 0, &info, NULL, 0, NULL) == VIPRS_ACAD_INVALID_ARGUMENT,
	      "view_info with a null required pointer");
	check(viprs_acad_view_info_v1(doc, 4000000, &info, NULL, 0, &required) ==
		      VIPRS_ACAD_INVALID_ARGUMENT,
	      "view_info with an index past the end");
	check(viprs_acad_decode_begin(doc, 0, NULL, NULL) == VIPRS_ACAD_INVALID_ARGUMENT,
	      "decode_begin with a null out handle");
	check(viprs_acad_decode_begin(doc, 4000000, NULL, &dec) == VIPRS_ACAD_INVALID_ARGUMENT,
	      "decode_begin with an index past the end");

	check(viprs_acad_decode_begin(doc, 0, NULL, &dec) == VIPRS_ACAD_OK, "decode_begin");
	check(viprs_acad_decode_next_batch(dec, NULL, sizeof buf, &written, &done) ==
		      VIPRS_ACAD_INVALID_ARGUMENT,
	      "decode_next_batch with a null buffer");
	check(viprs_acad_decode_next_batch(dec, buf, 0, &written, &done) ==
		      VIPRS_ACAD_INVALID_ARGUMENT,
	      "decode_next_batch with a zero capacity");
	check(viprs_acad_decode_next_batch(dec, buf, sizeof buf, NULL, &done) ==
		      VIPRS_ACAD_INVALID_ARGUMENT,
	      "decode_next_batch with a null written pointer");
	check(viprs_acad_decode_next_batch(dec, buf, sizeof buf, &written, NULL) ==
		      VIPRS_ACAD_INVALID_ARGUMENT,
	      "decode_next_batch with a null done pointer");

	viprs_acad_decode_close(dec);
	viprs_acad_close(doc);
}

/* open_path_utf8 has a whole code path of its own (it sniffs the file rather
 * than a caller's buffer) and until this case existed nothing ran it except
 * the four ways it refuses. A route exercised only by its failures is a route
 * nobody has run. */
static void test_open_by_path(void)
{
	const char *path = "/tmp/viprs-synthetic.bin";
	uint8_t synth[16];
	viprs_cad_handle *doc = NULL;
	FILE *f;
	uint32_t count = 0;
	uint32_t rc;

	synthetic_input(synth, 2, 9);
	f = fopen(path, "wb");
	if (!f) {
		check(0, "could not write the synthetic document to a file");
		return;
	}
	fwrite(synth, 1, sizeof synth, f);
	fclose(f);

	rc = viprs_acad_open_path_utf8((const uint8_t *)path, strlen(path), NULL, &doc);
	check(rc == VIPRS_ACAD_OK, "open_path_utf8 opens the same document from a file");
	if (rc != VIPRS_ACAD_OK) {
		return;
	}

	check(viprs_acad_view_count(doc, &count) == VIPRS_ACAD_OK && count == 2,
	      "and the handle it returns behaves like the one open_memory returns");
	viprs_acad_close(doc);

	rc = viprs_acad_open_path_utf8((const uint8_t *)"/etc/hostname", 13, NULL, &doc);
	check(rc == VIPRS_ACAD_UNSUPPORTED_FORMAT,
	      "a file no source in this build recognises is UNSUPPORTED_FORMAT");

	rc = viprs_acad_open_path_utf8((const uint8_t *)"/nonexistent/viprs.dwg", 22, NULL, &doc);
	check(rc == VIPRS_ACAD_INVALID_ARGUMENT, "a path that names nothing is INVALID_ARGUMENT");

	/* A path is a pointer and a length on this boundary, so a caller can
	 * hand over one with a NUL inside it. That is the caller's argument
	 * being wrong rather than the input's problem, and it has to stay
	 * INVALID_ARGUMENT rather than becoming whatever the platform throws
	 * when it is asked to stat the thing. */
	rc = viprs_acad_open_path_utf8((const uint8_t *)"/tmp/vip\0rs.dwg", 15, NULL, &doc);
	check(rc == VIPRS_ACAD_INVALID_ARGUMENT,
	      "a path with a NUL inside it is INVALID_ARGUMENT, not a parse failure");
}

/* max_input_bytes, through both open calls, on the same bytes.
 *
 * The bound was enforced in three layers with three different mappings for a
 * failed stat, so which code a caller saw depended on which layer noticed
 * first, which depends on a race. The interesting assertion is not that either
 * call refuses: it is that they refuse identically, and that a caller can
 * therefore write one branch. The loosened pair below is the control, because
 * a bound that has only ever been seen refusing might be refusing everything.
 */
static void test_the_input_bound_agrees_across_both_opens(void)
{
	const char *path = "/tmp/viprs-synthetic-bound.bin";
	uint8_t synth[16];
	struct viprs_acad_limits_v1 tight;
	struct viprs_acad_limits_v1 loose;
	viprs_cad_handle *from_memory = NULL;
	viprs_cad_handle *from_path = NULL;
	uint32_t memory_rc;
	uint32_t path_rc;
	FILE *f;

	synthetic_input(synth, 2, 9);
	f = fopen(path, "wb");
	if (!f) {
		check(0, "could not write the synthetic document to a file");
		return;
	}
	fwrite(synth, 1, sizeof synth, f);
	fclose(f);

	memset(&tight, 0, sizeof tight);
	tight.struct_size = (uint32_t)sizeof tight;
	tight.struct_version = 1;
	tight.max_input_bytes = 8; /* the input is 16 */

	memory_rc = viprs_acad_open_memory(synth, sizeof synth, &tight, &from_memory);
	path_rc = viprs_acad_open_path_utf8((const uint8_t *)path, strlen(path), &tight, &from_path);

	printf("      too large: open_memory=%u open_path=%u\n", memory_rc, path_rc);
	check(memory_rc == VIPRS_ACAD_LIMIT_EXCEEDED,
	      "an input past max_input_bytes is LIMIT_EXCEEDED through open_memory");
	check(path_rc == VIPRS_ACAD_LIMIT_EXCEEDED,
	      "and through open_path_utf8");
	check(memory_rc == path_rc,
	      "the two open calls report the same code for the same too-large input");
	check(from_memory == NULL && from_path == NULL,
	      "and neither refusal handed back a handle");

	memset(&loose, 0, sizeof loose);
	loose.struct_size = (uint32_t)sizeof loose;
	loose.struct_version = 1;
	loose.max_input_bytes = 4096;

	memory_rc = viprs_acad_open_memory(synth, sizeof synth, &loose, &from_memory);
	path_rc = viprs_acad_open_path_utf8((const uint8_t *)path, strlen(path), &loose, &from_path);
	check(memory_rc == VIPRS_ACAD_OK && path_rc == VIPRS_ACAD_OK,
	      "the same input under a bound above its size opens through both calls");
	viprs_acad_close(from_memory);
	viprs_acad_close(from_path);
	remove(path);
}


/* The batch header is written whatever else happens, so a buffer that cannot
 * hold twelve bytes cannot be satisfied at all. This used to write those
 * twelve bytes anyway and report OK, which is the one shape a caller cannot
 * defend against: it has been told the call succeeded and that twelve bytes
 * are there to read. The arena is filled with a byte nothing else writes so
 * a single stray write shows up. */
static void test_a_short_buffer_is_never_written_past(void)
{
	static const uint64_t tiny_caps[] = { 1, 4, 11 };
	uint8_t synth[16];
	uint8_t arena[64];
	uint8_t *big;
	viprs_cad_handle *doc = NULL;
	viprs_decode_handle *dec = NULL;
	vacb_reader reader;
	vacb_record record;
	uint64_t written = 0;
	uint8_t done = 0;
	size_t i;
	size_t j;
	int clean;

	synthetic_input(synth, 1, 9);
	if (viprs_acad_open_memory(synth, sizeof synth, NULL, &doc) != VIPRS_ACAD_OK) {
		check(0, "open_memory for the short-buffer test");
		return;
	}
	viprs_acad_decode_begin(doc, 0, NULL, &dec);

	for (i = 0; i < sizeof tiny_caps / sizeof tiny_caps[0]; i++) {
		char label[96];
		memset(arena, 0xEE, sizeof arena);
		written = 999;
		done = 9;
		uint32_t rc = viprs_acad_decode_next_batch(dec, arena, tiny_caps[i], &written,
							   &done);
		snprintf(label, sizeof label, "a cap of %llu is LIMIT_EXCEEDED, not a batch",
			 (unsigned long long)tiny_caps[i]);
		check(rc == VIPRS_ACAD_LIMIT_EXCEEDED, label);
		check(written == (uint64_t)VACB_BATCH_HEADER_BYTES,
		      "and it asks for the twelve bytes a batch header needs");
		clean = 1;
		for (j = 0; j < sizeof arena; j++) {
			if (arena[j] != 0xEE) {
				clean = 0;
			}
		}
		check(clean, "and not one byte of the caller's buffer was touched");
	}

	/* Drain it, then keep asking. */
	big = (uint8_t *)malloc(VACB_MAX_BATCH_BYTES);
	done = 0;
	while (!done) {
		if (viprs_acad_decode_next_batch(dec, big, VACB_MAX_BATCH_BYTES, &written,
						 &done) != VIPRS_ACAD_OK) {
			break;
		}
	}
	free(big);

	for (i = 0; i < sizeof tiny_caps / sizeof tiny_caps[0]; i++) {
		memset(arena, 0xEE, sizeof arena);
		written = 999;
		check(viprs_acad_decode_next_batch(dec, arena, tiny_caps[i], &written, &done) ==
			      VIPRS_ACAD_LIMIT_EXCEEDED,
		      "a short cap after the stream finished is still LIMIT_EXCEEDED");
		clean = 1;
		for (j = 0; j < sizeof arena; j++) {
			if (arena[j] != 0xEE) {
				clean = 0;
			}
		}
		check(clean, "and still writes nothing");
	}

	/* A call after done is legal, and produces a batch a parser can read. */
	memset(arena, 0xEE, sizeof arena);
	written = 0;
	done = 9;
	check(viprs_acad_decode_next_batch(dec, arena, sizeof arena, &written, &done) ==
		      VIPRS_ACAD_OK,
	      "calling again after done is not an error");
	check(done == 1, "and it still reports done");
	check(written == (uint64_t)VACB_BATCH_HEADER_BYTES,
	      "and writes exactly one empty batch, never zero bytes");
	check(vacb_open(&reader, arena, written) == VIPRS_ACAD_OK,
	      "and what it wrote parses");
	check((reader.flags & VACB_FLAG_LAST) != 0, "carrying the last-batch flag");
	check(vacb_next(&reader, &record) == 0, "and holding no records");
	clean = 1;
	for (j = (size_t)written; j < sizeof arena; j++) {
		if (arena[j] != 0xEE) {
			clean = 0;
		}
	}
	check(clean, "and nothing past *written was touched");

	viprs_acad_decode_close(dec);
	viprs_acad_close(doc);
}

#ifdef VIPRS_WITH_TEST_EXPORTS
/* Closing a handle with the other close function used to evict it from the
 * table and then fail the cast, so the object was orphaned: nothing could
 * reach it to release it and nothing could reach it to say it was still
 * there. From out here that looked exactly like a close that worked. */
static void test_closing_with_the_wrong_handle_type(void)
{
	uint8_t synth[16];
	uint8_t buf[8192];
	viprs_cad_handle *doc = NULL;
	viprs_decode_handle *dec = NULL;
	uint64_t before;
	uint64_t written = 0;
	uint32_t count = 0;
	uint8_t done = 0;

	synthetic_input(synth, 1, 9);
	before = viprs_acad__test_live_handles();

	if (viprs_acad_open_memory(synth, sizeof synth, NULL, &doc) != VIPRS_ACAD_OK) {
		check(0, "open_memory for the wrong-close test");
		return;
	}
	viprs_acad_decode_begin(doc, 0, NULL, &dec);
	check(viprs_acad__test_live_handles() == before + 2,
	      "a document and a decode are two live handles");

	/* The live count on its own is not enough, which the mutation run
	 * proved: with the bug back, the wrong-typed close evicts the handle
	 * without disposing it, so the count still walks back down to the
	 * baseline and the last check below still passes while both objects are
	 * orphaned. What tells the two apart is whether the handle still works
	 * afterwards, because a close that did nothing must have left it
	 * alone. */
	viprs_acad_decode_close((viprs_decode_handle *)doc);
	check(viprs_acad__test_live_handles() == before + 2,
	      "decode_close on a document handle releases nothing");
	check(viprs_acad_view_count(doc, &count) == VIPRS_ACAD_OK,
	      "and leaves the document handle usable, rather than evicting it and dropping "
	      "the document on the floor");

	viprs_acad_close((viprs_cad_handle *)dec);
	check(viprs_acad__test_live_handles() == before + 2,
	      "and close on a decode handle releases nothing either");
	check(viprs_acad_decode_next_batch(dec, buf, sizeof buf, &written, &done) ==
		      VIPRS_ACAD_OK,
	      "and leaves the decode handle usable too");

	viprs_acad_decode_close(dec);
	viprs_acad_close(doc);
	check(viprs_acad__test_live_handles() == before,
	      "and the right calls still release both");
}
#endif


/* "Calling after done is legal" has to hold however tight max_output_bytes
 * is, or it is not a rule, it is a rule with an expiry date. A decode that
 * has nothing left to say is not producing output, so those empty batches are
 * not charged against the limit. Without that, a caller whose loop asks a few
 * hundred times too often gets LIMIT_EXCEEDED for bytes the decode did not
 * produce. */
static void test_calls_after_done_are_not_charged_to_the_output_limit(void)
{
	uint8_t synth[16];
	uint8_t buf[8192];
	struct viprs_acad_limits_v1 limits;
	viprs_cad_handle *doc = NULL;
	viprs_decode_handle *dec = NULL;
	uint64_t written = 0;
	uint8_t done = 0;
	int i;
	int all_ok = 1;

	synthetic_input(synth, 1, 9);
	memset(&limits, 0, sizeof limits);
	limits.struct_size = (uint32_t)sizeof limits;
	limits.struct_version = 1;
	limits.max_output_bytes = 8192;

	if (viprs_acad_open_memory(synth, sizeof synth, &limits, &doc) != VIPRS_ACAD_OK) {
		check(0, "open_memory with a tight output limit");
		return;
	}
	viprs_acad_decode_begin(doc, 0, NULL, &dec);

	while (!done) {
		if (viprs_acad_decode_next_batch(dec, buf, sizeof buf, &written, &done) !=
		    VIPRS_ACAD_OK) {
			break;
		}
	}
	check(done == 1, "the whole stream fits inside an 8 KiB output limit");

	for (i = 0; i < 1000; i++) {
		if (viprs_acad_decode_next_batch(dec, buf, sizeof buf, &written, &done) !=
		    VIPRS_ACAD_OK) {
			all_ok = 0;
			break;
		}
	}
	check(all_ok,
	      "and a thousand calls after done are all OK, because a decode with nothing "
	      "left to say is not producing output");

	viprs_acad_decode_close(dec);
	viprs_acad_close(doc);
}

static void test_views(void)
{
	uint8_t synth[16];
	viprs_cad_handle *doc = NULL;
	struct viprs_view_info_v1 info;
	uint64_t required = 0;
	uint32_t count = 0;
	char *name;

	synthetic_input(synth, 3, 9);
	if (viprs_acad_open_memory(synth, sizeof synth, NULL, &doc) != VIPRS_ACAD_OK) {
		check(0, "open_memory for the view tests");
		return;
	}

	check(viprs_acad_view_count(doc, &count) == VIPRS_ACAD_OK, "view_count");
	check(count == 3, "view_count reports what the input asked for");

	memset(&info, 0, sizeof info);
	info.struct_size = (uint32_t)sizeof info;
	info.struct_version = 1;
	check(viprs_acad_view_info_v1(doc, 0, &info, NULL, 0, &required) == VIPRS_ACAD_OK,
	      "view_info sizing call");
	name = (char *)malloc((size_t)required + 1);
	check(viprs_acad_view_info_v1(doc, 0, &info, (uint8_t *)name, required, &required) ==
		      VIPRS_ACAD_OK,
	      "view_info with a buffer");
	name[required] = '\0';
	printf("      view 0 name=\"%s\" kind=%u extents=[%g %g %g %g] entity_count=%llu\n", name,
	       info.kind, info.min_x, info.min_y, info.max_x, info.max_y,
	       (unsigned long long)info.entity_count);
	check(info.index == 0, "view_info echoes the index back");
	check(info.kind <= 2, "kind is one of the three documented values");
	free(name);

	viprs_acad_close(doc);
}

static void test_cancel_before_the_first_batch(void)
{
	uint8_t synth[16];
	uint8_t buf[65536];
	viprs_cad_handle *doc = NULL;
	viprs_decode_handle *dec = NULL;
	uint32_t cancel_flag = 1;
	uint64_t written = 123;
	uint8_t done = 9;
	uint32_t rc;

	synthetic_input(synth, 1, 9);
	if (viprs_acad_open_memory(synth, sizeof synth, NULL, &doc) != VIPRS_ACAD_OK) {
		check(0, "open_memory for the cancel test");
		return;
	}

	check(viprs_acad_decode_begin(doc, 0, &cancel_flag, &dec) == VIPRS_ACAD_OK,
	      "decode_begin with a cancel flag");
	rc = viprs_acad_decode_next_batch(dec, buf, sizeof buf, &written, &done);
	check(rc == VIPRS_ACAD_CANCELED,
	      "a cancel flag set before the first batch is CANCELED, not a batch");
	check(written == 0, "and nothing was written");

	viprs_acad_decode_close(dec);
	viprs_acad_close(doc);
}

static void test_small_buffer(void)
{
	uint8_t synth[16];
	uint8_t tiny[16];
	viprs_cad_handle *doc = NULL;
	viprs_decode_handle *dec = NULL;
	uint64_t written = 0;
	uint8_t done = 0;
	uint32_t rc;

	synthetic_input(synth, 1, 9);
	if (viprs_acad_open_memory(synth, sizeof synth, NULL, &doc) != VIPRS_ACAD_OK) {
		check(0, "open_memory for the small-buffer test");
		return;
	}

	viprs_acad_decode_begin(doc, 0, NULL, &dec);
	rc = viprs_acad_decode_next_batch(dec, tiny, sizeof tiny, &written, &done);
	check(rc == VIPRS_ACAD_LIMIT_EXCEEDED, "a buffer too small for one batch is LIMIT_EXCEEDED");
	check(written > sizeof tiny, "and the size it needs comes back through written");
	printf("      needed %llu bytes for the first batch\n", (unsigned long long)written);

	viprs_acad_decode_close(dec);
	viprs_acad_close(doc);
}


/* --- payload field placement ---------------------------------------------
 *
 * Nothing here reads a scalar out of a record until this section existed, and
 * that was the hole: swapping an arc's radius with its start angle in the
 * encoder changed nothing any test could see. docs/WIRE.md is a field-layout
 * document, and a field-layout document nobody reads a field out of drifts.
 *
 * The synthetic document makes this possible by carrying placement probes
 * rather than geometry: the k-th double in a payload, after the prologue, is
 * 100 * type + k + 0.25. So every field of a record holds a different number,
 * none holds zero, and none holds a value that would look right one slot
 * over. docs/ABI.md documents the rule; these are the offsets from WIRE.md,
 * written out by hand on this side of the boundary.
 * ------------------------------------------------------------------------- */

static int payload_failures;
static char payload_first[192];

static void field_fail(const char *what, double got, double want)
{
	payload_failures++;
	if (payload_first[0] == '\0') {
		snprintf(payload_first, sizeof payload_first, "%s: got %.6f, wanted %.6f", what,
			 got, want);
	}
}

static void count_fail(const char *what, unsigned long long got, unsigned long long want)
{
	payload_failures++;
	if (payload_first[0] == '\0') {
		snprintf(payload_first, sizeof payload_first, "%s: got %llu, wanted %llu", what,
			 got, want);
	}
}

static double probe(uint16_t type, uint32_t k)
{
	return (100.0 * (double)type) + (double)k + 0.25;
}

static uint64_t probe_handle(uint16_t type)
{
	return 1000000ULL + (uint64_t)type;
}

static uint32_t pad4(uint32_t n)
{
	return (4u - (n % 4u)) % 4u;
}

static void want_f64(const uint8_t *p, uint32_t off, double want, const char *what)
{
	double got = vacb_f64(p + off);
	if (got != want) {
		field_fail(what, got, want);
	}
}

static void want_u32(const uint8_t *p, uint32_t off, uint32_t want, const char *what)
{
	uint32_t got = vacb_u32(p + off);
	if (got != want) {
		count_fail(what, got, want);
	}
}

static void want_u64(const uint8_t *p, uint32_t off, uint64_t want, const char *what)
{
	uint64_t got = vacb_u64(p + off);
	if (got != want) {
		count_fail(what, (unsigned long long)got, (unsigned long long)want);
	}
}

static void want_len(uint32_t got, uint32_t want, const char *what)
{
	if (got != want) {
		count_fail(what, got, want);
	}
}

static void want_probes(const uint8_t *p, uint32_t off, uint16_t type, uint32_t first,
			uint32_t count, const char *what)
{
	uint32_t k;
	for (k = 0; k < count; k++) {
		want_f64(p, off + (k * 8u), probe(type, first + k), what);
	}
}

static void want_prologue(const vacb_record *r)
{
	want_u64(r->payload, 0, probe_handle(r->type), "prologue item_handle");
	want_u32(r->payload, 8, 0, "prologue flags");
	want_u32(r->payload, 12, 0, "prologue reserved0");
}

static void want_bytes(const uint8_t *p, uint32_t off, const char *want, const char *what)
{
	size_t n = strlen(want);
	if (memcmp(p + off, want, n) != 0) {
		payload_failures++;
		if (payload_first[0] == '\0') {
			snprintf(payload_first, sizeof payload_first, "%s: bytes differ", what);
		}
	}
}

/* `seen_records` and `seen_warnings` are the consumer's own tallies, compared
 * against what the stream says about itself at the end. */
static uint64_t seen_records;
static uint64_t seen_warnings;
static uint64_t view_records;

static void verify_payload(const vacb_record *r)
{
	uint32_t len = r->payload_len + (uint32_t)VACB_RECORD_HEADER_BYTES;
	uint32_t n;
	uint32_t bytes;
	uint32_t knots;
	uint32_t controls;
	uint32_t weights;

	switch (r->type) {
	case VACB_TYPE_DOCUMENT_BEGIN:
		want_u32(r->payload, 4, 1032, "DocumentBegin drawing_version");
		want_u64(r->payload, 8, 0, "DocumentBegin reserved0");
		want_len(len, 24, "DocumentBegin length");
		break;

	case VACB_TYPE_VIEW_BEGIN:
		want_u32(r->payload, 0, 0, "ViewBegin view_index");
		want_u32(r->payload, 4, 0, "ViewBegin kind");
		want_f64(r->payload, 8, -100.25, "ViewBegin min_x");
		want_f64(r->payload, 16, -50.5, "ViewBegin min_y");
		want_f64(r->payload, 24, 100.75, "ViewBegin max_x");
		want_f64(r->payload, 32, 50.125, "ViewBegin max_y");
		want_u32(r->payload, 52, 0, "ViewBegin reserved0");
		n = vacb_u32(r->payload + 48);
		want_len(len, 8u + 56u + n + pad4(n), "ViewBegin length");
		break;

	case VACB_TYPE_LINE:
		want_prologue(r);
		want_probes(r->payload, 16, VACB_TYPE_LINE, 0, 6, "Line");
		want_len(len, 72, "Line length");
		break;

	case VACB_TYPE_POLYLINE:
		want_prologue(r);
		n = vacb_u32(r->payload + 16);
		if (vacb_u32(r->payload + 20) > 1u) {
			count_fail("Polyline closed", vacb_u32(r->payload + 20), 1);
		}
		want_probes(r->payload, 24, VACB_TYPE_POLYLINE, 0, n * 3u, "Polyline");
		want_len(len, 8u + 16u + 8u + (24u * n), "Polyline length");
		break;

	case VACB_TYPE_ARC:
		want_prologue(r);
		want_probes(r->payload, 16, VACB_TYPE_ARC, 0, 9, "Arc");
		want_len(len, 96, "Arc length");
		break;

	case VACB_TYPE_CIRCLE:
		want_prologue(r);
		want_probes(r->payload, 16, VACB_TYPE_CIRCLE, 0, 7, "Circle");
		want_len(len, 80, "Circle length");
		break;

	case VACB_TYPE_ELLIPSE:
		want_prologue(r);
		want_probes(r->payload, 16, VACB_TYPE_ELLIPSE, 0, 12, "Ellipse");
		want_len(len, 120, "Ellipse length");
		break;

	case VACB_TYPE_SPLINE:
		want_prologue(r);
		want_u32(r->payload, 16, 3, "Spline degree");
		want_u32(r->payload, 20, 0, "Spline flags");
		knots = vacb_u32(r->payload + 24);
		controls = vacb_u32(r->payload + 28);
		weights = vacb_u32(r->payload + 32);
		want_u32(r->payload, 36, 0, "Spline reserved1");
		if (weights != 0u && weights != controls) {
			count_fail("Spline weight_count", weights, controls);
		}
		want_probes(r->payload, 40, VACB_TYPE_SPLINE, 0,
			    knots + (controls * 3u) + weights, "Spline");
		want_len(len, 8u + 16u + 24u + (8u * (knots + (controls * 3u) + weights)),
			 "Spline length");
		break;

	case VACB_TYPE_POLYGON:
		want_prologue(r);
		n = vacb_u32(r->payload + 16);
		want_u32(r->payload, 20, 0, "Polygon reserved1");
		want_probes(r->payload, 24, VACB_TYPE_POLYGON, 0, n * 3u, "Polygon");
		want_len(len, 8u + 16u + 8u + (24u * n), "Polygon length");
		break;

	case VACB_TYPE_TEXT:
		want_prologue(r);
		want_probes(r->payload, 16, VACB_TYPE_TEXT, 0, 5, "Text");
		bytes = vacb_u32(r->payload + 56);
		want_u32(r->payload, 60, 0, "Text reserved1");
		want_bytes(r->payload, 64, "VIPRS-TEXT-PROBE-", "Text bytes");
		want_len(len, 8u + 64u + bytes + pad4(bytes), "Text length");
		break;

	case VACB_TYPE_WARNING:
		want_u32(r->payload, 0, 1100, "Warning code");
		want_u32(r->payload, 4, 0, "Warning reserved0");
		want_u64(r->payload, 8, probe_handle(VACB_TYPE_WARNING), "Warning item_handle");
		bytes = vacb_u32(r->payload + 16);
		want_u32(r->payload, 20, 0, "Warning reserved1");
		want_bytes(r->payload, 24, "VIPRS-WARNING-PROBE", "Warning bytes");
		want_len(len, 8u + 24u + bytes + pad4(bytes), "Warning length");
		break;

	case VACB_TYPE_VIEW_END:
		want_u32(r->payload, 0, 0, "ViewEnd view_index");
		want_u32(r->payload, 4, 0, "ViewEnd reserved0");
		want_u64(r->payload, 8, view_records, "ViewEnd record_count");
		want_len(len, 24, "ViewEnd length");
		break;

	case VACB_TYPE_DOCUMENT_END:
		want_u64(r->payload, 0, seen_records, "DocumentEnd total_records");
		want_u64(r->payload, 8, seen_warnings, "DocumentEnd warning_count");
		want_len(len, 24, "DocumentEnd length");
		break;

	default:
		break;
	}
}

/* docs/WIRE.md's lengths for the records whose length never varies, as the
 * whole record including its eight-byte header. 0 means the record has a
 * variable-length payload and there is nothing to check. */
static uint32_t fixed_bytes(uint16_t type)
{
	switch (type) {
	case VACB_TYPE_DOCUMENT_BEGIN:
		return 24;
	case VACB_TYPE_LINE:
		return 72;
	case VACB_TYPE_ARC:
		return 96;
	case VACB_TYPE_CIRCLE:
		return 80;
	case VACB_TYPE_ELLIPSE:
		return 120;
	case VACB_TYPE_VIEW_END:
		return 24;
	case VACB_TYPE_DOCUMENT_END:
		return 24;
	default:
		return 0;
	}
}

static void test_decode_and_parse(void)
{
	uint8_t synth[16];
	uint8_t *buf;
	const size_t cap = VACB_MAX_BATCH_BYTES;
	viprs_cad_handle *doc = NULL;
	viprs_decode_handle *dec = NULL;
	uint64_t written = 0;
	uint8_t done = 0;
	int seen[16];
	int probes = 0;
	int after_probe_was_view_end = 0;
	int expect_view_end = 0;
	int last_flag_seen = 0;
	int batches = 0;
	int fixed_size_wrong = 0;
	int rc;
	int i;

	memset(seen, 0, sizeof seen);
	buf = (uint8_t *)malloc(cap);
	/* Enough primitives that the stream does not fit in one batch. A decode
	 * that always fits in the first call never exercises the loop, and the
	 * loop is where a producer gets the framing wrong. */
	synthetic_input(synth, 1, 2000);
	if (viprs_acad_open_memory(synth, sizeof synth, NULL, &doc) != VIPRS_ACAD_OK) {
		check(0, "open_memory for the decode test");
		free(buf);
		return;
	}

	check(viprs_acad_decode_begin(doc, 0, NULL, &dec) == VIPRS_ACAD_OK, "decode_begin");

	while (!done) {
		vacb_reader reader;
		vacb_record record;
		uint32_t open_rc;

		if (viprs_acad_decode_next_batch(dec, buf, cap, &written, &done) != VIPRS_ACAD_OK) {
			check(0, "decode_next_batch returned a failure mid-stream");
			break;
		}

		batches++;
		open_rc = vacb_open(&reader, buf, written);
		check(open_rc == VIPRS_ACAD_OK, "the batch the library produced parses");
		if (open_rc != VIPRS_ACAD_OK) {
			break;
		}

		if ((reader.flags & VACB_FLAG_LAST) != 0) {
			last_flag_seen = 1;
		}

		while ((rc = vacb_next(&reader, &record)) == 1) {
			/* The tallies go up first and count every record, the
			 * forward probe included, because ViewEnd and DocumentEnd
			 * count themselves and count it. Skipping a record for
			 * being unknown is a decision about what to do with it,
			 * not a licence to pretend it was not there. */
			seen_records++;
			if (record.type == VACB_TYPE_VIEW_BEGIN) {
				view_records = 1;
			} else if (view_records > 0) {
				view_records++;
			}
			if (record.type == VACB_TYPE_WARNING) {
				seen_warnings++;
			}

			if (record.type >= VACB_FORWARD_PROBE_FIRST) {
				probes++;
				expect_view_end = 1;
				continue;
			}

			if (expect_view_end) {
				expect_view_end = 0;
				after_probe_was_view_end = record.type == VACB_TYPE_VIEW_END;
			}

			if (record.type < 16) {
				seen[record.type] = 1;
			}

			verify_payload(&record);

			/* The documented length of every record whose length does
			 * not vary, checked against what the library actually
			 * emitted. A payload table nobody compares against the
			 * producer is a payload table that drifts. */
			if (fixed_bytes(record.type) != 0 &&
			    record.payload_len + VACB_RECORD_HEADER_BYTES !=
				    fixed_bytes(record.type)) {
				fixed_size_wrong = record.type;
			}
		}

		check(rc == 0, "the batch ends cleanly rather than on a refusal");
	}

	printf("      %d batches, %d forward probes skipped\n", batches, probes);
	for (i = 1; i <= 13; i++) {
		char label[64];
		snprintf(label, sizeof label, "record type %d appeared in the stream", i);
		check(seen[i] == 1, label);
	}
	check(batches > 1, "the stream spanned more than one batch, so the loop ran");
	check(probes >= 1, "the stream carried a record of an unknown type");
	check(after_probe_was_view_end == 1,
	      "the record after the unknown one was read, so the skip used its length");
	check(last_flag_seen == 1, "the final batch carries the last-batch flag");
	check(fixed_size_wrong == 0,
	      "every fixed-size record is the length docs/WIRE.md gives it");
	if (payload_failures != 0) {
		printf("      first mismatch: %s\n", payload_first);
	}
	check(payload_failures == 0,
	      "every field of every record is at the offset docs/WIRE.md gives it");

	viprs_acad_decode_close(dec);
	viprs_acad_close(doc);
	free(buf);
}

static void test_malformed_batches(void)
{
	uint8_t buf[256];
	uint8_t body[128];
	vacb_reader reader;
	vacb_record record;
	uint64_t len;
	uint32_t body_len;

	/* An unknown type between two known ones, assembled by hand rather than
	 * produced, so the skip is checked against a payload length no known
	 * record has. */
	body_len = 0;
	body_len += put_record(body + body_len, VACB_TYPE_LINE, 72, 64);
	body_len += put_record(body + body_len, 0x7F42, 24, 16);
	body_len += put_record(body + body_len, VACB_TYPE_CIRCLE, 16, 8);
	len = build_batch(buf, VACB_WIRE_VERSION, body_len, body, body_len);
	check(vacb_open(&reader, buf, len) == VIPRS_ACAD_OK, "a hand-built batch opens");
	check(vacb_next(&reader, &record) == 1 && record.type == VACB_TYPE_LINE, "first record");
	check(vacb_next(&reader, &record) == 1 && record.type == 0x7F42,
	      "the unknown record is reported rather than refused");
	check(vacb_next(&reader, &record) == 1 && record.type == VACB_TYPE_CIRCLE,
	      "the record after the unknown one is read, so the skip used its length");
	check(vacb_next(&reader, &record) == 0, "and the batch ends");

	/* payload_length past the end of the buffer. */
	body_len = put_record(body, VACB_TYPE_LINE, 72, 64);
	len = build_batch(buf, VACB_WIRE_VERSION, body_len + 4096, body, body_len);
	check(vacb_open(&reader, buf, len) == VIPRS_ACAD_CORRUPT_INPUT,
	      "a payload_length past the buffer end is CORRUPT_INPUT");

	/* A record length below its own header. The parser must refuse it rather
	 * than retry it forever, and the case drains rather than calling once,
	 * because a single call cannot tell a refusal from a cursor that has not
	 * failed to advance yet. */
	{
		const uint32_t shorts[] = { 0, 4, 7 };
		size_t i;
		for (i = 0; i < sizeof shorts / sizeof shorts[0]; i++) {
			char label[96];
			body_len = put_record(body, VACB_TYPE_LINE, shorts[i], 64);
			len = build_batch(buf, VACB_WIRE_VERSION, body_len, body, body_len);
			check(vacb_open(&reader, buf, len) == VIPRS_ACAD_OK,
			      "the short-record batch opens");
			snprintf(label, sizeof label,
				 "a record length of %u, below its own header, is CORRUPT_INPUT",
				 shorts[i]);
			check(drain(&reader) == -1 && reader.error == VIPRS_ACAD_CORRUPT_INPUT,
			      label);
			check(reader.ran_away == 0,
			      "and it was refused outright, not retried until the parser's own "
			      "bound caught it");
		}
	}

	/* A length that is not a multiple of four. */
	body_len = put_record(body, VACB_TYPE_LINE, 9, 64);
	len = build_batch(buf, VACB_WIRE_VERSION, body_len, body, body_len);
	vacb_open(&reader, buf, len);
	check(vacb_next(&reader, &record) == -1 && reader.error == VIPRS_ACAD_CORRUPT_INPUT,
	      "a record length that is not a multiple of four is CORRUPT_INPUT");

	/* A length that runs past the payload. */
	body_len = put_record(body, VACB_TYPE_LINE, 4096, 16);
	len = build_batch(buf, VACB_WIRE_VERSION, body_len, body, body_len);
	vacb_open(&reader, buf, len);
	check(vacb_next(&reader, &record) == -1 && reader.error == VIPRS_ACAD_CORRUPT_INPUT,
	      "a record length running past the payload is CORRUPT_INPUT");

	/* Wrong magic, and a wire version from the future. */
	body_len = put_record(body, VACB_TYPE_LINE, 72, 64);
	len = build_batch(buf, VACB_WIRE_VERSION, body_len, body, body_len);
	buf[2] = 'X';
	check(vacb_open(&reader, buf, len) == VIPRS_ACAD_CORRUPT_INPUT,
	      "a batch with the wrong magic is CORRUPT_INPUT");

	len = build_batch(buf, 2, body_len, body, body_len);
	check(vacb_open(&reader, buf, len) == VIPRS_ACAD_UNSUPPORTED_FORMAT,
	      "a wire version this consumer does not parse is refused, not guessed");

	len = build_batch(buf, VACB_WIRE_VERSION, 0, NULL, 0);
	check(vacb_open(&reader, buf, len) == VIPRS_ACAD_OK, "an empty batch is legal");
	check(vacb_next(&reader, &record) == 0, "and holds no records");

	check(vacb_open(&reader, buf, 4) == VIPRS_ACAD_CORRUPT_INPUT,
	      "a buffer too short to hold a batch header is CORRUPT_INPUT");
}

static void test_exceptions_cannot_escape(void)
{
#ifdef VIPRS_WITH_TEST_EXPORTS
	uint32_t kind;

	for (kind = 1; kind <= 3; kind++) {
		uint32_t rc = viprs_acad__test_throw(kind);
		char label[96];
		snprintf(label, sizeof label,
			 "a managed exception of kind %u arrives as INTERNAL_ERROR", kind);
		check(rc == VIPRS_ACAD_INTERNAL_ERROR, label);
	}

	check(viprs_acad__test_throw(4) == VIPRS_ACAD_CORRUPT_INPUT,
	      "a failure with a code of its own keeps that code rather than becoming a bug");
	check(viprs_acad__test_throw(99) == VIPRS_ACAD_INTERNAL_ERROR,
	      "and anything else is still INTERNAL_ERROR");

	/* The part that matters. Reaching this line at all means none of those
	 * five calls took the process with it. */
	check(viprs_acad_abi_version() == VIPRS_ACAD_ABI_VERSION,
	      "the library still works after five exceptions crossed the boundary");
#else
	printf("      skipped: built against a library without the test exports\n");
#endif
}

int main(void)
{
	printf("VIPRS CAD ABI conformance, C consumer\n");
	printf("--- handshake\n");
	test_handshake();
	printf("--- capabilities\n");
	test_capabilities();
	printf("--- arguments\n");
	test_null_arguments();
	test_null_arguments_on_a_live_handle();
	printf("--- opening by path\n");
	test_open_by_path();
	printf("--- max_input_bytes through both opens\n");
	test_the_input_bound_agrees_across_both_opens();
	printf("--- views\n");
	test_views();
	printf("--- cancellation\n");
	test_cancel_before_the_first_batch();
	printf("--- buffer sizing\n");
	test_small_buffer();
	test_a_short_buffer_is_never_written_past();
	test_calls_after_done_are_not_charged_to_the_output_limit();
	printf("--- handle lifetime\n");
#ifdef VIPRS_WITH_TEST_EXPORTS
	test_closing_with_the_wrong_handle_type();
#else
	printf("      skipped: built against a library without the test exports\n");
#endif
	printf("--- decode and parse\n");
	test_decode_and_parse();
	printf("--- malformed batches\n");
	test_malformed_batches();
	printf("--- exceptions\n");
	test_exceptions_cannot_escape();

	printf("\n%d checks, %d failures\n", g_checks, g_failures);
	return g_failures == 0 ? 0 : 1;
}
