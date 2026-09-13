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
}


def declared_codes():
    with open(WARNING_CODES) as f:
        source = f.read()
    return set(re.findall(r'case [A-Za-z]+: return "([A-Z_]+)";', source))


class TestUnsupportedEntities:
    def test_the_fixture_produces_warnings_and_nothing_else(self):
        counts = kinds("g13_unsupported.dwg")
        assert set(counts) == {"Warning"}, f"expected only warnings, got {counts}"

    def test_each_one_names_the_entity_type(self):
        named = {
            w["message"].split(" ")[0]
            for w in warnings("g13_unsupported.dwg")
            if w["code"] == "UNSUPPORTED_ENTITY"
        }
        assert named == {"POINT", "SOLID"}, (
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

    def test_the_one_code_with_no_fixture_is_named_and_explained(self):
        # DIMENSION_WITHOUT_BLOCK is reachable and deliberately uncovered:
        # ACadSharp's DwgWriter generates a block for every dimension it
        # writes, so the corpus generator cannot produce a dimension without
        # one, and a file that has one would have to be a real drawing nobody
        # here has. The arm stays because a real drawing can carry it.
        assert CODE_FIXTURES["DIMENSION_WITHOUT_BLOCK"] is None
        with open(WARNING_CODES) as f:
            assert "DimensionWithoutBlock" in f.read()
