//! What the header's structs must measure, written out by hand.
//!
//! These numbers are not generated. If they came from the same header the
//! struct definitions come from, reordering two fields would move both and
//! this file would keep passing, which is exactly the failure it exists to
//! catch. A field in the wrong place does not fail to compile and does not
//! panic: it reads its neighbour's bytes, and a 64-bit integer read where a
//! double lives comes back as a perfectly plausible number.
//!
//! The same table is asserted by the C consumer and computed from the header
//! by acadsharp/tests/test_abi_layout.py, so a reorder fails three times, once
//! of them with no compiler at all.

macro_rules! layout_struct {
    ($t:ident, $size:expr, $align:expr) => {{
        assert_eq!(
            std::mem::size_of::<viprs_conformance::$t>(),
            $size,
            "size_of::<{}>() moved, so the header changed and this table did not",
            stringify!($t)
        );
        assert_eq!(
            std::mem::align_of::<viprs_conformance::$t>(),
            $align,
            "align_of::<{}>() moved, so the header changed and this table did not",
            stringify!($t)
        );
    }};
}

macro_rules! layout_field {
    ($t:ident, $f:ident, $off:expr, $size:expr) => {{
        assert_eq!(
            std::mem::offset_of!(viprs_conformance::$t, $f),
            $off,
            "offset_of!({}, {}) moved, so {} was reordered",
            stringify!($t),
            stringify!($f),
            stringify!($t)
        );
        let probe = viprs_conformance::$t::default();
        assert_eq!(
            std::mem::size_of_val(&probe.$f),
            $size,
            "size_of {}.{} moved",
            stringify!($t),
            stringify!($f)
        );
    }};
}

#[test]
fn limits_layout_is_what_the_header_says() {
    layout_struct!(viprs_acad_limits_v1, 56, 8);
    layout_field!(viprs_acad_limits_v1, struct_size, 0, 4);
    layout_field!(viprs_acad_limits_v1, struct_version, 4, 4);
    layout_field!(viprs_acad_limits_v1, max_input_bytes, 8, 8);
    layout_field!(viprs_acad_limits_v1, max_entities, 16, 8);
    layout_field!(viprs_acad_limits_v1, max_string_bytes, 24, 8);
    layout_field!(viprs_acad_limits_v1, max_polyline_points, 32, 8);
    layout_field!(viprs_acad_limits_v1, max_block_depth, 40, 4);
    layout_field!(viprs_acad_limits_v1, reserved0, 44, 4);
    layout_field!(viprs_acad_limits_v1, max_output_bytes, 48, 8);
}

#[test]
fn capabilities_layout_is_what_the_header_says() {
    layout_struct!(viprs_acad_capabilities_v1, 32, 4);
    layout_field!(viprs_acad_capabilities_v1, struct_size, 0, 4);
    layout_field!(viprs_acad_capabilities_v1, struct_version, 4, 4);
    layout_field!(viprs_acad_capabilities_v1, abi_version, 8, 4);
    layout_field!(viprs_acad_capabilities_v1, wire_version, 12, 4);
    layout_field!(viprs_acad_capabilities_v1, dwg_version_min, 16, 4);
    layout_field!(viprs_acad_capabilities_v1, dwg_version_max, 20, 4);
    layout_field!(viprs_acad_capabilities_v1, supports_block_expansion, 24, 1);
    layout_field!(viprs_acad_capabilities_v1, supports_warnings, 25, 1);
    layout_field!(viprs_acad_capabilities_v1, reserved0, 26, 1);
    layout_field!(viprs_acad_capabilities_v1, reserved1, 27, 1);
    layout_field!(viprs_acad_capabilities_v1, reserved2, 28, 4);
}

#[test]
fn view_info_layout_is_what_the_header_says() {
    layout_struct!(viprs_view_info_v1, 56, 8);
    layout_field!(viprs_view_info_v1, struct_size, 0, 4);
    layout_field!(viprs_view_info_v1, struct_version, 4, 4);
    layout_field!(viprs_view_info_v1, index, 8, 4);
    layout_field!(viprs_view_info_v1, kind, 12, 4);
    layout_field!(viprs_view_info_v1, min_x, 16, 8);
    layout_field!(viprs_view_info_v1, min_y, 24, 8);
    layout_field!(viprs_view_info_v1, max_x, 32, 8);
    layout_field!(viprs_view_info_v1, max_y, 40, 8);
    layout_field!(viprs_view_info_v1, entity_count, 48, 8);
}

/// The result codes are generated from the header's own #defines, so this
/// pins the numbers themselves rather than the fact that they parsed.
#[test]
fn the_result_codes_keep_their_frozen_numbers() {
    assert_eq!(viprs_conformance::VIPRS_ACAD_OK, 0);
    assert_eq!(viprs_conformance::VIPRS_ACAD_INVALID_ARGUMENT, 1);
    assert_eq!(viprs_conformance::VIPRS_ACAD_UNSUPPORTED_FORMAT, 2);
    assert_eq!(viprs_conformance::VIPRS_ACAD_CORRUPT_INPUT, 3);
    assert_eq!(viprs_conformance::VIPRS_ACAD_UNSUPPORTED_ENTITY, 4);
    assert_eq!(viprs_conformance::VIPRS_ACAD_OUT_OF_MEMORY, 5);
    assert_eq!(viprs_conformance::VIPRS_ACAD_CANCELED, 6);
    assert_eq!(viprs_conformance::VIPRS_ACAD_INTERNAL_ERROR, 7);
    assert_eq!(viprs_conformance::VIPRS_ACAD_ABI_MISMATCH, 8);
    assert_eq!(viprs_conformance::VIPRS_ACAD_LIMIT_EXCEEDED, 9);
    assert_eq!(viprs_conformance::VIPRS_ACAD_ABI_VERSION, 1);
    assert_eq!(viprs_conformance::VIPRS_ACAD_WIRE_VERSION, 2);
}
