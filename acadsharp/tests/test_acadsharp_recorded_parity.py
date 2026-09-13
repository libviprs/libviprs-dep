"""The recorded JIT and NativeAOT reads of the same DWG have to agree.

These compare captures, not a live build: pytest here has no .NET and is
not going to get one (see ADR 0001 on C# coverage). The captures are what
the spike measured, committed next to the fixtures that produced them, so
a later change that recompiles the shim and re-records has to face this
diff rather than describe it.

The reason it is not enough to compare entity counts: `DwgReader.Read`
builds `CadHeader`'s system-variable map by reflection, and if trimming
hollows that map nothing throws. The map comes back short, the defaults
stand, and ACadSharp's defaults are not zeroes. `Objects/Layout.cs`
ships MinExtents (25.7, 19.5, 0) and MaxExtents (231.3, 175.5, 0), which
is a plausible-looking sheet rectangle. So the fixture sets values that
are nothing like the defaults, and this asserts on those.
"""

import json
import os

import pytest

CAPTURES = os.path.join(os.path.dirname(__file__), "fixtures", "captures")

# field -> (what the fixture sets, what ACadSharp defaults to)
SENTINELS = {
    "text_height_default": (7.25, 2.5),
    "elevation": (3.5, 0),
    "angular_unit_precision": (6, 0),
    "dimension_scale_factor": (12.5, 1),
}
EXTENT_SENTINELS = {
    "min_extents": ([-11.5, -22.25, -3.75], [25.7, 19.5, 0]),
    "max_extents": ([101.5, 202.25, 33.75], [231.3, 175.5, 0]),
}

# The reflection-built map in CadHeader.GetHeaderMap(). A hollowed map is
# the failure this whole spike is looking for, so the number is pinned.
HEADER_MAP_COUNT = 249


def capture_pairs():
    """(stem, jit path, aot path) for every recorded AOT capture."""
    pairs = []
    for name in sorted(os.listdir(CAPTURES)):
        if ".aot-" not in name:
            continue
        stem = name.split(".aot-")[0]
        jit = os.path.join(CAPTURES, f"{stem}.jit.json")
        pairs.append((name, jit, os.path.join(CAPTURES, name)))
    return pairs


def load(path):
    with open(path) as f:
        return json.load(f)


class TestCapturesExist:
    def test_there_is_at_least_one_pair(self):
        assert capture_pairs(), "no AOT capture recorded, so nothing here checks anything"

    def test_every_aot_capture_has_a_jit_capture(self):
        for name, jit, _aot in capture_pairs():
            assert os.path.isfile(jit), f"{name} has no JIT oracle beside it"


class TestJitAndAotAgree:
    @pytest.mark.parametrize("name,jit,aot", capture_pairs(), ids=[p[0] for p in capture_pairs()])
    def test_the_captures_are_identical(self, name, jit, aot):
        with open(jit) as f:
            expected = f.read()
        with open(aot) as f:
            actual = f.read()
        assert actual == expected, f"{name} diverges from the JIT read of the same file"


class TestTheOracleIsNotItselfHollow:
    """A matching pair of hollowed reads would pass the test above.

    So the JIT side has to be shown to carry the values the fixture set,
    and to carry them *instead of* ACadSharp's defaults.
    """

    def test_the_fixture_capture_carries_the_header_sentinels(self):
        doc = load(os.path.join(CAPTURES, "g11_shapes.jit.json"))
        for field, (measured, default) in SENTINELS.items():
            assert measured != default
            assert doc[field] == measured, (
                f"{field} came back {doc[field]}, and ACadSharp's default is {default}"
            )

    def test_the_fixture_capture_carries_the_layout_extents(self):
        layout = load(os.path.join(CAPTURES, "g11_shapes.jit.json"))["model_layout"]
        for field, (measured, default) in EXTENT_SENTINELS.items():
            assert measured != default
            assert layout[field] == measured, (
                f"{field} came back {layout[field]}, which is ACadSharp's default "
                f"{default} if the system-variable map was hollowed"
            )

    def test_every_capture_has_the_whole_system_variable_map(self):
        for name in sorted(os.listdir(CAPTURES)):
            if not name.endswith(".json"):
                continue
            doc = load(os.path.join(CAPTURES, name))
            assert doc["header_map_count"] == HEADER_MAP_COUNT, (
                f"{name} built {doc['header_map_count']} system variables out of "
                f"{HEADER_MAP_COUNT}, so reflection lost members"
            )

    def test_the_real_world_capture_is_not_trivial(self):
        # A round-trip of our own writer proves less than a file AutoCAD
        # produced. This one carries 163 entities over 36 types.
        doc = load(os.path.join(CAPTURES, "real_AC1032.jit.json"))
        assert doc["entity_count"] > 100
        assert len(doc["entities_by_type"]) > 25
