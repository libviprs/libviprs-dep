#include "vacb.h"

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
		 * produces numbers instead of an error. */
		r->error = VIPRS_ACAD_UNSUPPORTED_FORMAT;
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
