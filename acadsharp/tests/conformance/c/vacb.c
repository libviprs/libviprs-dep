#include "vacb.h"

#include <math.h>
#include <string.h>

#include "../../../include/viprs_acadsharp.h"

uint16_t vacb_u16(const uint8_t *p)
{
	return (uint16_t)((uint16_t)p[0] | (uint16_t)((uint16_t)p[1] << 8));
}

uint32_t vacb_u32(const uint8_t *p)
{
	return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) |
	       ((uint32_t)p[3] << 24);
}

uint64_t vacb_u64(const uint8_t *p)
{
	uint64_t v = 0;
	int i;
	for (i = 7; i >= 0; i--) {
		v = (v << 8) | (uint64_t)p[i];
	}
	return v;
}

double vacb_f64(const uint8_t *p)
{
	uint64_t bits = vacb_u64(p);
	double out;
	memcpy(&out, &bits, sizeof(out));
	return out;
}

uint32_t vacb_open(vacb_reader *r, const uint8_t *buf, uint64_t len)
{
	uint32_t payload_length;

	memset(r, 0, sizeof(*r));

	if (len < (uint64_t)VACB_BATCH_HEADER_BYTES) {
		r->error = VIPRS_ACAD_CORRUPT_INPUT;
		return r->error;
	}

	if (buf[0] != VACB_MAGIC_0 || buf[1] != VACB_MAGIC_1 || buf[2] != VACB_MAGIC_2 ||
	    buf[3] != VACB_MAGIC_3) {
		r->error = VIPRS_ACAD_CORRUPT_INPUT;
		return r->error;
	}

	if (vacb_u16(buf + 4) != VACB_WIRE_VERSION) {
		/* Refused rather than guessed. A consumer that parses a version it
		 * does not know is reading a layout it is only assuming, and it
		 * produces numbers instead of an error.
		 *
		 * ABI_MISMATCH rather than UNSUPPORTED_FORMAT, which is what this
		 * said until wire version 2. UNSUPPORTED_FORMAT is about the
		 * drawing: it means check dwg_version_min and dwg_version_max and
		 * hand the file to something else. A foreign wire version is about
		 * the two ends of this boundary disagreeing, and the remedy is to
		 * rebuild one of them. docs/ABI.md always said ABI_MISMATCH. */
		r->error = VIPRS_ACAD_ABI_MISMATCH;
		return r->error;
	}

	r->flags = vacb_u16(buf + 6);
	payload_length = vacb_u32(buf + 8);

	/* payload_length past the end of the buffer reads whatever the caller
	 * allocated next, so it is refused before anything is dereferenced. */
	if ((uint64_t)VACB_BATCH_HEADER_BYTES + (uint64_t)payload_length > len) {
		r->error = VIPRS_ACAD_CORRUPT_INPUT;
		return r->error;
	}

	r->payload = buf + VACB_BATCH_HEADER_BYTES;
	r->payload_len = payload_length;
	r->offset = 0;
	r->budget = (int32_t)(payload_length / VACB_RECORD_HEADER_BYTES) + 2;
	return VIPRS_ACAD_OK;
}

int vacb_next(vacb_reader *r, vacb_record *out)
{
	uint32_t length;
	uint16_t reserved;

	if (r->error != VIPRS_ACAD_OK) {
		return -1;
	}

	if (r->offset >= r->payload_len) {
		return 0;
	}

	r->budget--;
	if (r->budget < 0) {
		r->ran_away = 1;
		r->error = VIPRS_ACAD_INTERNAL_ERROR;
		return -1;
	}

	if (r->payload_len - r->offset < (uint32_t)VACB_RECORD_HEADER_BYTES) {
		r->error = VIPRS_ACAD_CORRUPT_INPUT;
		return -1;
	}

	reserved = vacb_u16(r->payload + r->offset + 2);
	length = vacb_u32(r->payload + r->offset + 4);

	if (reserved != 0) {
		r->error = VIPRS_ACAD_CORRUPT_INPUT;
		return -1;
	}

	/* A short record, smaller than its own header, cannot advance the cursor
	 * past itself. Accepting one is how a malformed batch becomes a hang. */
	if (length < (uint32_t)VACB_RECORD_HEADER_BYTES) {
		r->error = VIPRS_ACAD_CORRUPT_INPUT;
		return -1;
	}

	if (length % (uint32_t)VACB_RECORD_ALIGNMENT != 0) {
		r->error = VIPRS_ACAD_CORRUPT_INPUT;
		return -1;
	}

	if (length > r->payload_len - r->offset) {
		r->error = VIPRS_ACAD_CORRUPT_INPUT;
		return -1;
	}

	out->type = vacb_u16(r->payload + r->offset);
	out->payload = r->payload + r->offset + VACB_RECORD_HEADER_BYTES;
	out->payload_len = length - (uint32_t)VACB_RECORD_HEADER_BYTES;

	/* Unknown types are skipped by this same length, which is the entire
	 * reason the length is in the record header. */
	r->offset += length;
	return 1;
}

uint32_t vacb_polyline_shape(const uint8_t *payload, uint32_t payload_len, uint32_t length,
			     uint32_t *point_count, uint32_t *bulge_count)
{
	uint32_t n;
	uint32_t closed;
	uint32_t bulges;
	uint32_t reserved1;
	uint32_t k;
	uint32_t values;

	if (payload_len < 56u) {
		return VIPRS_ACAD_CORRUPT_INPUT;
	}

	n = vacb_u32(payload + 16);
	closed = vacb_u32(payload + 20);
	bulges = vacb_u32(payload + 24);
	reserved1 = vacb_u32(payload + 28);

	if (closed > 1u || reserved1 != 0u) {
		return VIPRS_ACAD_CORRUPT_INPUT;
	}

	/* Either one bulge per vertex or none at all. Anything between leaves a
	 * consumer working out which spans the array covers, which is a length
	 * it inferred rather than one the record gave it. */
	if (bulges != 0u && bulges != n) {
		return VIPRS_ACAD_CORRUPT_INPUT;
	}

	if (length != 64u + (24u * n) + (8u * bulges)) {
		return VIPRS_ACAD_CORRUPT_INPUT;
	}

	/* The producer promises every f64 in a geometry record is finite. A
	 * consumer checks anyway: the bytes may not have come from that
	 * producer, and one NaN coordinate becomes a bounding box that is NaN in
	 * every direction and a renderer that draws nothing at all. */
	values = 3u + (3u * n) + bulges;
	for (k = 0; k < values; k++) {
		if (!isfinite(vacb_f64(payload + 32u + (k * 8u)))) {
			return VIPRS_ACAD_CORRUPT_INPUT;
		}
	}

	if (point_count != NULL) {
		*point_count = n;
	}
	if (bulge_count != NULL) {
		*bulge_count = bulges;
	}
	return VIPRS_ACAD_OK;
}

static void put_le32(uint8_t *p, uint32_t v)
{
	p[0] = (uint8_t)(v & 0xFFu);
	p[1] = (uint8_t)((v >> 8) & 0xFFu);
	p[2] = (uint8_t)((v >> 16) & 0xFFu);
	p[3] = (uint8_t)((v >> 24) & 0xFFu);
}

uint32_t vacb_build_vertex_record(uint8_t *out, uint16_t kind, uint32_t n, uint32_t bulges,
				  uint32_t closed, uint32_t reserved1, const double *values,
				  uint32_t value_count, uint32_t claimed_length)
{
	uint32_t at = 8u;
	uint32_t k;
	uint32_t length;
	uint64_t bits;

	memset(out + at, 0, 8);
	out[at] = 0x4D;
	at += 8u;
	put_le32(out + at, 0u);
	at += 4u;
	put_le32(out + at, 0u);
	at += 4u;
	put_le32(out + at, n);
	at += 4u;
	put_le32(out + at, closed);
	at += 4u;
	put_le32(out + at, bulges);
	at += 4u;
	put_le32(out + at, reserved1);
	at += 4u;

	for (k = 0; k < value_count; k++) {
		memcpy(&bits, &values[k], sizeof bits);
		put_le32(out + at, (uint32_t)(bits & 0xFFFFFFFFull));
		put_le32(out + at + 4u, (uint32_t)(bits >> 32));
		at += 8u;
	}

	length = claimed_length != 0u ? claimed_length : at;
	out[0] = (uint8_t)(kind & 0xFFu);
	out[1] = (uint8_t)((kind >> 8) & 0xFFu);
	out[2] = 0;
	out[3] = 0;
	put_le32(out + 4, length);
	return at;
}
