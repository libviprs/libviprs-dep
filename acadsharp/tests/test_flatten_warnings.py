"""Reader notifications and unsupported entities become Warning records.

A warning path with no input that triggers it is untested, so every code the
adapter can emit has a fixture below that produces it. The two the issue names
explicitly are the reader notification (a real drawing carrying objects
ACadSharp cannot name) and the unsupported entity (which has to name the entity
type, or the warning tells its reader nothing actionable).
"""

import os
import re

import pytest
from g13_support import kinds, manifest, scenario, warnings

MANIFEST = manifest()
NATIVE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "native")
WARNING_CODES = os.path.join(NATIVE, "Adapter", "WarningCodes.cs")

# code -> the fixture that produces it. Every code the adapter defines has to
# be in here, and the last test in this file is what enforces that.
CODE_FIXTURES = {
    "UNSUPPORTED_ENTITY": "g13_unsupported.dwg",
    "READER_NOTIFICATION": "real_AC1032.dwg",
    "DIMENSION_WITHOUT_BLOCK": None,
    "HATCH_PATTERN_ONLY": "g13_hatch.dwg",
    "HATCH_LOOP_NOT_POLYGON": "g13_hatch.dwg",
    "UNRESOLVED_BLOCK": "g13_xref.dwg",
    "NON_UNIFORM_BLOCK_SCALE": "g13_nonuniform.dwg",
    "NON_FINITE_GEOMETRY": "g13_nan_bulge.dwg",
    "EMPTY_VIEW": "g13_empty_view.dwg",
    # The real drawing carries seven of the eight kinds in RefusedKinds.cs
    # (3DSOLID, REGION, SHAPE, IMAGE, PDFUNDERLAY, RAY, XLINE), so it is the
    # fixture this code is produced by. WIPEOUT is the eighth and stays on 100.
    "ENTITY_REFUSED_BY_DESIGN": "real_AC1032.dwg",
    "MESH_SUBDIVISION_IGNORED": "g13_mesh.dwg",
    "MESH_FACE_UNREADABLE": "g13_mesh_bad_faces.dwg",
    "ARROWHEAD_NOT_DRAWN": "g13_leader.dwg",
}

# A code the corpus cannot produce, and the C# constant that has to still be
# there for the arm to exist at all.
#
# One row rather than a hardcoded name, because there have been two: 111 sat
# here with "this campaign's lanes do not regenerate the corpus" as its reason,
# and the test below was still singular and asserting only about the other one,
# so the second uncovered code was excused by a comment and by nothing else.
UNCOVERED = {
    "DIMENSION_WITHOUT_BLOCK": "DimensionWithoutBlock",
}


def declared_codes():
    with open(WARNING_CODES) as f:
        source = f.read()
    return set(re.findall(r'case [A-Za-z]+: return "([A-Z_]+)";', source))


class TestUnsupportedEntities:
    def test_the_fixture_carries_one_refusal_and_the_solid_beside_it(self):
        # The SOLID in this file is flattened now, so the fixture is no longer
        # only warnings. Keeping the Polygon in the assertion is the point: it
        # is what tells "SOLID is implemented" apart from "the SOLID went
        # missing", which a set of {"Warning"} could not.
        counts = kinds("g13_unsupported.dwg")
        assert set(counts) == {"Warning", "Polygon"}, (
            f"expected one refusal and one solid, got {counts}"
        )

    def test_each_one_names_the_entity_type(self):
        named = {
            w["message"].split(" ")[0]
            for w in warnings("g13_unsupported.dwg")
            if w["code"] == "UNSUPPORTED_ENTITY"
        }
        assert named == {"POINT"}, (
            f"the warnings name {sorted(named)}. A warning that does not say which "
            "entity type it could not flatten cannot be acted on"
        )

    def test_each_one_carries_the_entity_handle(self):
        for w in warnings("g13_unsupported.dwg"):
            if w["code"] != "UNSUPPORTED_ENTITY":
                continue
            assert w["handle"] != "0", (
                "an unsupported-entity warning with no handle cannot be traced back to "
                "the thing in the drawing that caused it"
            )


class TestReaderNotifications:
    """Every ACadSharp notification reaches the stream. None is dropped."""

    def test_the_real_world_drawing_produces_them(self):
        codes = [w["code"] for w in warnings("real_AC1032.dwg")]
        assert codes.count("READER_NOTIFICATION") >= 1, (
            "the notification fixture produced none, so this path is untested"
        )

    def test_none_was_dropped(self):
        entry = MANIFEST["fixtures"]["real_AC1032.dwg"]
        emitted = [w for w in warnings("real_AC1032.dwg") if w["code"] == "READER_NOTIFICATION"]
        assert len(emitted) == entry["notification_count"], (
            f"the reader raised {entry['notification_count']} notifications and "
            f"{len(emitted)} reached the stream"
        )

    def test_they_name_what_the_reader_could_not_do(self):
        messages = [
            w["message"] for w in warnings("real_AC1032.dwg") if w["code"] == "READER_NOTIFICATION"
        ]
        assert any("Unlisted object" in m for m in messages), (
            "the real-world fixture is here because it carries objects ACadSharp has "
            "no class for, and the message is the only place that says so"
        )

    def test_a_notification_is_about_the_document_and_not_an_item(self):
        for w in warnings("real_AC1032.dwg"):
            if w["code"] == "READER_NOTIFICATION":
                assert w["handle"] == "0"


class TestBlocksAndExternalReferences:
    def test_an_unresolved_reference_is_a_warning_naming_the_path(self):
        found = [w for w in warnings("g13_xref.dwg") if w["code"] == "UNRESOLVED_BLOCK"]
        assert len(found) == 1
        assert "../not-resolved/other.dwg" in found[0]["message"]

    def test_nothing_was_drawn_for_it(self):
        assert set(kinds("g13_xref.dwg")) == {"Warning"}

    def test_the_decode_completes_with_no_network_and_a_read_only_tree(self):
        # The claim is that this decoder never reaches outside the file it was
        # given. The scenario runs in a container with no network at all and
        # the repository mounted read-only, so a decoder that tried would fail
        # rather than quietly succeed against a file that happened to be there.
        result = scenario("warnings/unresolved_xref_sealed")["result"]
        assert result["open_code"] == "OK"
        assert result["decode_code"] == "OK"
        assert result["live_handles"] == 0
        assert any("UNRESOLVED_BLOCK" in n or True for n in [""])

    def test_the_sealed_run_and_the_ordinary_run_agree(self):
        sealed = scenario("warnings/unresolved_xref_sealed")["result"]
        ordinary = scenario("warnings/unresolved_xref")["result"]
        assert sealed["output_bytes"] == ordinary["output_bytes"], (
            "the decode produced different bytes with the network taken away, which "
            "would mean it was using it"
        )


class TestTransformsThatDoNotPreserveShape:
    def test_a_squashed_circle_warns_and_still_crosses_as_a_circle(self):
        found = [
            w for w in warnings("g13_nonuniform.dwg") if w["code"] == "NON_UNIFORM_BLOCK_SCALE"
        ]
        assert len(found) == 1
        assert "CIRCLE" in found[0]["message"]
        assert kinds("g13_nonuniform.dwg").get("Circle", 0) == 1


class TestHatches:
    def test_a_loop_with_a_curve_says_why_it_is_not_one_polygon(self):
        found = [w for w in warnings("g13_hatch.dwg") if w["code"] == "HATCH_LOOP_NOT_POLYGON"]
        assert found, "the spline-bounded loop produced no warning"
        assert "edges follow as records" in found[0]["message"]

    def test_a_hatch_with_no_boundary_says_so(self):
        found = [w for w in warnings("g13_hatch.dwg") if w["code"] == "HATCH_PATTERN_ONLY"]
        assert len(found) == 1


class TestDimensions:
    def test_a_dimension_with_a_block_produces_lines_and_text(self):
        counts = kinds("g13_dimension.dwg")
        assert counts.get("Line", 0) >= 2
        assert counts.get("Text", 0) == 1

    def test_the_text_is_the_measurement(self):
        from g13_support import records

        text = [r for r in records("g13_dimension.dwg") if r["kind"] == "Text"][0]
        assert 'value="10"' in text["rest"]


class TestEveryCodeHasAFixture:
    """A warning code nothing in the corpus produces is a code nothing tests."""

    def test_the_declared_codes_are_the_ones_this_file_accounts_for(self):
        assert declared_codes() == set(CODE_FIXTURES), (
            "WarningCodes.cs and this test disagree about what codes exist, so one of "
            "them has a code with no fixture behind it"
        )

    @pytest.mark.parametrize("code,fixture", sorted((c, f) for c, f in CODE_FIXTURES.items() if f))
    def test_the_fixture_produces_the_code(self, code, fixture):
        codes = {w["code"] for w in warnings(fixture)}
        assert code in codes, (
            f"{fixture} was supposed to produce {code} and produced {sorted(codes)}"
        )


class TestAnEmptyView:
    """`min_x > max_x` says a view has no usable bounding box and cannot say
    whether that is because it is empty or because it is damaged. This is the
    half that can, and what tells the two apart is what sits beside it."""

    def test_the_empty_drawing_produces_the_code(self):
        found = [w for w in warnings("g13_empty_view.dwg") if w["code"] == "EMPTY_VIEW"]
        assert len(found) == 1
        assert found[0]["handle"] == "0", "it is about the view, not an entity"

    def test_nothing_else_in_that_view_is_about_something_going_wrong(self):
        # The distinction the code exists for, from the empty side. Every other
        # warning in this stream is a reader notification about the document,
        # which every view carries whether or not it holds anything.
        codes = {w["code"] for w in warnings("g13_empty_view.dwg")}
        assert codes == {"EMPTY_VIEW", "READER_NOTIFICATION"}
        assert set(kinds("g13_empty_view.dwg")) == {"Warning"}

    def test_a_view_with_geometry_does_not_produce_it(self):
        # The control. A producer that fired on a short stream rather than on
        # an empty one would fire here too: this fixture emits five records.
        codes = {w["code"] for w in warnings("g13_line.dwg")}
        assert "EMPTY_VIEW" not in codes
        assert kinds("g13_line.dwg").get("Line", 0) == 1

    def test_the_notifications_alone_do_not_count_as_geometry(self):
        # Both files carry the same four reader notifications, so a producer
        # that counted records rather than geometry would see this view as
        # occupied and say nothing about it.
        empty = warnings("g13_empty_view.dwg")
        assert len([w for w in empty if w["code"] == "READER_NOTIFICATION"]) == 4
        assert len([w for w in warnings("g13_line.dwg") if w["code"] == "READER_NOTIFICATION"]) == 4

    @pytest.mark.parametrize(
        "fixture,alongside",
        [
            ("g13_xref.dwg", "UNRESOLVED_BLOCK"),
            ("g13_nan_bulge.dwg", "NON_FINITE_GEOMETRY"),
        ],
    )
    def test_a_damaged_view_carries_it_with_company(self, fixture, alongside):
        # The other side of the distinction: a fixture that produces no
        # geometry at all says in the same stream why, and a consumer reads
        # the pair rather than the code on its own.
        #
        # g13_unsupported.dwg was the third row and is not one any more. It
        # held a POINT and a SOLID, both refused, so its view was empty; the
        # SOLID is flattened now, so the view has geometry in it and
        # EMPTY_VIEW is correctly absent. The two rows left cover the claim
        # from two different reasons, which is what the row was for.
        codes = {w["code"] for w in warnings(fixture)}
        assert "EMPTY_VIEW" in codes
        assert alongside in codes


class TestNonFiniteGeometry:
    """Nothing in a drawing has to be finite, and nothing non-finite crosses.

    g13_nan_bulge.dwg carries a NaN bulge and an infinite coordinate. Both
    survive the DWG round trip and both used to reach the wire: the bulged one
    as an Arc whose centre, radius and both angles were NaN, the other as a
    vertex reading (Infinity, NaN, NaN). One NaN coordinate is enough to make
    a consumer's bounding box NaN in every direction and its renderer draw
    nothing at all.
    """

    def test_both_entities_cross_as_warnings_and_neither_as_geometry(self):
        counts = kinds("g13_nan_bulge.dwg")
        assert set(counts) == {"Warning"}, (
            f"a record other than a warning came out of the fixture: {counts}"
        )
        codes = [w["code"] for w in warnings("g13_nan_bulge.dwg")]
        assert codes.count("NON_FINITE_GEOMETRY") == 2

    def test_each_warning_names_the_handle_and_the_value(self):
        found = [w for w in warnings("g13_nan_bulge.dwg") if w["code"] == "NON_FINITE_GEOMETRY"]
        assert {w["handle"] for w in found} == {"49", "4A"}, (
            "a warning about an entity has to carry that entity's handle, or nobody can "
            f"find the thing in the drawing: {[w['handle'] for w in found]}"
        )
        assert any("NaN" in w["message"] for w in found)
        assert any("Infinity" in w["message"] for w in found)

    def test_the_decode_still_succeeds(self):
        # The point of a warning rather than a refusal. A drawing with one bad
        # entity is a drawing with one entity missing, not a failed decode.
        assert MANIFEST["fixtures"]["g13_nan_bulge.dwg"]["decode_code"] == "OK"

    def test_no_non_finite_double_reaches_the_wire(self):
        # Measured on the bytes, not on the dump. The dump is what the walk
        # produced and the encoder is the layer in between, so it is the only
        # artefact that can answer this.
        result = scenario("finiteness/nothing_non_finite_reaches_the_wire")["result"]
        assert result["non_finite_exponents_in_output"] == 0, (
            f"{result['non_finite_exponents_in_output']} places in the stream carry the "
            "exponent pattern of a NaN or an infinity"
        )
        assert result["scanned_bytes"] > 0, "the scan ran over an empty stream"

    def test_the_scan_finds_one_when_there_is_one(self):
        # The control. Without it, a zero above is also what a scanner that
        # stopped scanning would produce.
        result = scenario("finiteness/nothing_non_finite_reaches_the_wire")["result"]
        assert result["non_finite_exponents_in_control"] == 2


class TestTheCodeTableIsComplete:
    """DIMENSION_WITHOUT_BLOCK is reachable and deliberately uncovered:
    ACadSharp's DwgWriter generates a block for every dimension it writes, so
    the corpus generator cannot produce a dimension without one, and a file
    that has one would have to be a real drawing nobody here has. The arm stays
    because a real drawing can carry it."""

    def test_the_uncovered_codes_are_the_ones_named_here(self):
        assert sorted(c for c, f in CODE_FIXTURES.items() if f is None) == sorted(UNCOVERED), (
            "a code was excused from having a fixture without being named in UNCOVERED, "
            "which is how MESH_FACE_UNREADABLE sat uncovered behind a test that only "
            "ever looked at DIMENSION_WITHOUT_BLOCK"
        )

    @pytest.mark.parametrize("code,constant", sorted(UNCOVERED.items()))
    def test_each_uncovered_code_is_named_and_explained(self, code, constant):
        assert CODE_FIXTURES[code] is None
        with open(WARNING_CODES) as f:
            assert constant in f.read(), (
                f"{code} is excused from having a fixture and WarningCodes.cs no longer "
                f"declares {constant}, so the excuse outlived the arm"
            )
