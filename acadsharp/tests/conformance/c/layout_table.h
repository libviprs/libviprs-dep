/*
 * layout_table.h - what the header's structs must measure, written out by hand.
 *
 * The numbers below are not generated. If they were computed from the same
 * header the structs come from, reordering two fields would move both and
 * nothing would notice, which is exactly the failure this is here to catch. A
 * field in the wrong place does not fail to compile and does not throw: it
 * reads its neighbour's bytes, and a 64-bit integer read where a double lives
 * comes back as a perfectly plausible number.
 *
 * A test in acadsharp/tests/test_abi_layout.py computes the same table from
 * the header by the C rules and compares, so the same reorder fails three
 * times: here, in the other conformance consumer, and with no compiler at all.
 */

#ifndef VIPRS_CONFORMANCE_LAYOUT_TABLE_H
#define VIPRS_CONFORMANCE_LAYOUT_TABLE_H

#include <stddef.h>

#include "../../../include/viprs_acadsharp.h"

/* The tag keyword is inside the macro rather than at every call site, so the
 * table below reads as a table and a test with no compiler can parse it. */
#define VIPRS_LAYOUT_STRUCT(s, size, align)                                            \
	_Static_assert(sizeof(struct s) == (size),                                     \
		       "sizeof(struct " #s ") is not " #size                           \
		       ", so the header moved and this table did not");                \
	_Static_assert(_Alignof(struct s) == (align),                                  \
		       "_Alignof(struct " #s ") is not " #align                        \
		       ", so the header moved and this table did not")

#define VIPRS_LAYOUT_FIELD(s, f, off, size)                                            \
	_Static_assert(offsetof(struct s, f) == (off),                                 \
		       "offsetof(struct " #s ", " #f ") is not " #off                  \
		       ", so " #s " was reordered");                                   \
	_Static_assert(sizeof(((struct s *)0)->f) == (size),                           \
		       "sizeof(struct " #s "." #f ") is not " #size)

VIPRS_LAYOUT_STRUCT(viprs_acad_limits_v1, 56, 8);
VIPRS_LAYOUT_FIELD(viprs_acad_limits_v1, struct_size, 0, 4);
VIPRS_LAYOUT_FIELD(viprs_acad_limits_v1, struct_version, 4, 4);
VIPRS_LAYOUT_FIELD(viprs_acad_limits_v1, max_input_bytes, 8, 8);
VIPRS_LAYOUT_FIELD(viprs_acad_limits_v1, max_entities, 16, 8);
VIPRS_LAYOUT_FIELD(viprs_acad_limits_v1, max_string_bytes, 24, 8);
VIPRS_LAYOUT_FIELD(viprs_acad_limits_v1, max_polyline_points, 32, 8);
VIPRS_LAYOUT_FIELD(viprs_acad_limits_v1, max_block_depth, 40, 4);
VIPRS_LAYOUT_FIELD(viprs_acad_limits_v1, reserved0, 44, 4);
VIPRS_LAYOUT_FIELD(viprs_acad_limits_v1, max_output_bytes, 48, 8);

VIPRS_LAYOUT_STRUCT(viprs_acad_capabilities_v1, 32, 4);
VIPRS_LAYOUT_FIELD(viprs_acad_capabilities_v1, struct_size, 0, 4);
VIPRS_LAYOUT_FIELD(viprs_acad_capabilities_v1, struct_version, 4, 4);
VIPRS_LAYOUT_FIELD(viprs_acad_capabilities_v1, abi_version, 8, 4);
VIPRS_LAYOUT_FIELD(viprs_acad_capabilities_v1, wire_version, 12, 4);
VIPRS_LAYOUT_FIELD(viprs_acad_capabilities_v1, dwg_version_min, 16, 4);
VIPRS_LAYOUT_FIELD(viprs_acad_capabilities_v1, dwg_version_max, 20, 4);
VIPRS_LAYOUT_FIELD(viprs_acad_capabilities_v1, supports_block_expansion, 24, 1);
VIPRS_LAYOUT_FIELD(viprs_acad_capabilities_v1, supports_warnings, 25, 1);
VIPRS_LAYOUT_FIELD(viprs_acad_capabilities_v1, reserved0, 26, 1);
VIPRS_LAYOUT_FIELD(viprs_acad_capabilities_v1, reserved1, 27, 1);
VIPRS_LAYOUT_FIELD(viprs_acad_capabilities_v1, reserved2, 28, 4);

VIPRS_LAYOUT_STRUCT(viprs_view_info_v1, 56, 8);
VIPRS_LAYOUT_FIELD(viprs_view_info_v1, struct_size, 0, 4);
VIPRS_LAYOUT_FIELD(viprs_view_info_v1, struct_version, 4, 4);
VIPRS_LAYOUT_FIELD(viprs_view_info_v1, index, 8, 4);
VIPRS_LAYOUT_FIELD(viprs_view_info_v1, kind, 12, 4);
VIPRS_LAYOUT_FIELD(viprs_view_info_v1, min_x, 16, 8);
VIPRS_LAYOUT_FIELD(viprs_view_info_v1, min_y, 24, 8);
VIPRS_LAYOUT_FIELD(viprs_view_info_v1, max_x, 32, 8);
VIPRS_LAYOUT_FIELD(viprs_view_info_v1, max_y, 40, 8);
VIPRS_LAYOUT_FIELD(viprs_view_info_v1, entity_count, 48, 8);

#endif /* VIPRS_CONFORMANCE_LAYOUT_TABLE_H */
