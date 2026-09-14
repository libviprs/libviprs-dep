"""Rules about the shim's source that no compiler and no consumer can see.

Each one is a shape, not a behaviour. A bound enforced in three places with
three different error mappings compiles, runs, and returns whichever code the
layer that noticed first happened to use; a decode handle that is tracked and
never untracked leaks eight bytes per decode and every test stays green. So
these read `native/` as text, the way `test_abi_exports.py` does, and fail on
the arrangement that makes the bug possible rather than on the bug.

They run with no .NET present, which is the only way they run at all here
(ADR 0001).
"""

import os
import re

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ACADSHARP = os.path.dirname(HERE)
NATIVE = os.path.join(ACADSHARP, "native")

EXPORTS = os.path.join(NATIVE, "Exports.cs")
DECODE_SESSION = os.path.join(NATIVE, "Wire", "DecodeSession.cs")
SOURCE_FACTORY = os.path.join(NATIVE, "Sources", "SourceFactory.cs")
ACAD_SOURCE = os.path.join(NATIVE, "Sources", "AcadSharpSource.cs")


def read(path):
    with open(path) as f:
        return f.read()


def shim_sources():
    """Every C# file in the shim, by path relative to `native/`."""
    out = {}
    for root, _dirs, files in os.walk(NATIVE):
        for name in sorted(files):
            if name.endswith(".cs"):
                full = os.path.join(root, name)
                out[os.path.relpath(full, NATIVE)] = read(full)
    return out


def body_of(code, signature):
    """The brace-matched body of the first member whose declaration matches."""
    m = re.search(signature, code)
    if m is None:
        return None
    start = code.find("{", m.end())
    if start < 0:
        return None
    depth, i = 0, start
    while i < len(code):
        if code[i] == "{":
            depth += 1
        elif code[i] == "}":
            depth -= 1
            if depth == 0:
                return code[start : i + 1]
        i += 1
    return None


# ---------------------------------------------------------------------------
# The decode list a document keeps
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def decode_session():
    return read(DECODE_SESSION)


class TestEveryTrackedDecodeCanBeUntracked:
    """`_decodes` is how closing a document closes the decodes still open on
    it. `decode_begin` appends to it and `decode_close` used to remove the
    handle from the global table, dispose the session and leave the entry
    behind, so the list only ever emptied when the document did. A consumer
    re-decoding one view in a loop grows it without bound, and
    `viprs_acad__test_live_handles` cannot see it: that counts the handle
    table, which is the one thing `decode_close` did clean up."""

    def test_the_document_can_untrack_a_decode(self, decode_session):
        assert "public void Untrack(IntPtr" in decode_session, (
            "DocumentHandle has Track and no Untrack, so the list of open decodes only "
            "shrinks when the document closes"
        )

    def test_untrack_removes_under_the_same_lock_as_track(self, decode_session):
        track = body_of(decode_session, r"public void Track\(IntPtr \w+\)")
        untrack = body_of(decode_session, r"public void Untrack\(IntPtr \w+\)")
        assert track and untrack, "Track or Untrack is not a method with a body"
        for label, body in (("Track", track), ("Untrack", untrack)):
            assert "lock (_decodes)" in body, (
                f"{label} touches the decode list without holding the lock. Dispose "
                "walks the same list from whatever thread closed the document."
            )
        assert re.search(r"_decodes\.Remove\(", untrack), (
            "Untrack does not remove anything from _decodes, so it is a method that "
            "reads like a fix and is not one"
        )

    def test_decode_close_untracks_what_it_closes(self):
        body = body_of(read(EXPORTS), r'EntryPoint = "viprs_acad_decode_close"\)\]\s*\n\s*[^\n]+')
        assert body, "viprs_acad_decode_close is not an export with a body"
        assert "Untrack" in body, (
            "viprs_acad_decode_close removes the handle from the table and disposes the "
            "session without untracking it, so the document keeps a dead entry for every "
            "decode the caller opened and closed"
        )

    def test_the_session_still_knows_its_document(self, decode_session):
        # The control. Untracking from decode_close is only reachable because
        # DecodeSession exposes the document it belongs to; drop that and the
        # export has nothing to call.
        assert "public DocumentHandle Document" in decode_session


# ---------------------------------------------------------------------------
# max_input_bytes
# ---------------------------------------------------------------------------

# A comparison of anything against a resolved max_input_bytes. Not a mention:
# ResolvedLimits declares the field and fills it in, and neither is enforcement.
_ENFORCEMENT = re.compile(r"[<>]=?\s*\w+\.MaxInputBytes|\w+\.MaxInputBytes\s*[<>]=?")

# The one call that turns a caller's path into a size, which is also the one
# place that has to decide what a failed stat means.
_STAT = re.compile(r"new FileInfo\(")


class TestMaxInputBytesIsEnforcedInOnePlace:
    """It was three, with three mappings. `SourceFactory.FileLength` called a
    failed stat CORRUPT_INPUT, `AcadSharpSource.OpenPath` and
    `Exports.OpenPathUtf8` called it INVALID_ARGUMENT, and which one a caller
    saw depended on which layer noticed first. A caller cannot write a branch
    for a code that depends on a race."""

    def test_only_the_source_factory_compares_against_it(self):
        offenders = sorted(
            name for name, code in shim_sources().items() if _ENFORCEMENT.search(code)
        )
        assert offenders == [os.path.join("Sources", "SourceFactory.cs")], (
            f"max_input_bytes is compared against in {offenders}. One bound with two "
            "spellings is a bound whose refusal code depends on which layer noticed "
            "first, and the ordering comment in SourceFactory is the argument for "
            "where it belongs."
        )

    def test_the_source_factory_still_enforces_it(self):
        code = read(SOURCE_FACTORY)
        assert _ENFORCEMENT.search(code), (
            "nothing compares against max_input_bytes any more, so the bound the header "
            "promises is not applied at all. This is the positive control for the check "
            "above, which an empty shim would also pass."
        )
        assert "LimitExceeded" in code

    @pytest.mark.parametrize("route", ["OpenPath", "OpenMemory"])
    def test_both_open_routes_reach_it(self, route):
        body = body_of(read(SOURCE_FACTORY), rf"public static IDocumentSource {route}\(")
        assert body, f"SourceFactory has no {route}"
        assert "CheckInputBytes" in body or "FileLength" in body, (
            f"SourceFactory.{route} never applies max_input_bytes, so a too-large input "
            "is refused through one open call and not the other"
        )

    def test_only_one_place_stats_a_path(self):
        offenders = sorted(name for name, code in shim_sources().items() if _STAT.search(code))
        assert offenders == [os.path.join("Sources", "SourceFactory.cs")], (
            f"{offenders} each stat the caller's path. Two stats are two chances to map "
            "the same failure to two codes, which is exactly what happened."
        )
        assert len(_STAT.findall(read(SOURCE_FACTORY))) == 1

    def test_the_failed_stat_has_one_mapping(self):
        body = body_of(read(SOURCE_FACTORY), r"private static long FileLength\(")
        assert body, "SourceFactory no longer has FileLength"
        codes = set(re.findall(r"Result\.(\w+)", body))
        assert codes == {"InvalidArgument", "CorruptInput"}, (
            f"FileLength reports {sorted(codes)}. A missing path is the caller's "
            "argument and a path that exists and cannot be stat'ed is the input's "
            "problem, and those are the only two things this decides. The bound "
            "itself is CheckInputBytes."
        )

    def test_the_bound_reports_one_code(self):
        body = body_of(read(SOURCE_FACTORY), r"public static void CheckInputBytes\(")
        assert body, "SourceFactory no longer has CheckInputBytes"
        codes = set(re.findall(r"Result\.(\w+)", body))
        assert codes == {"LimitExceeded"}, (
            f"the bound reports {sorted(codes)}. The header says exceeding a limit is "
            "VIPRS_ACAD_LIMIT_EXCEEDED, and nothing else."
        )

    def test_the_memory_route_is_bounded_before_the_copy(self):
        # open_memory duplicates the caller's buffer, and a bound applied
        # after the copy has already paid for the thing it was refusing. This
        # is the one call outside SourceFactory that may know the number, and
        # it asks SourceFactory rather than deciding for itself.
        body = body_of(read(EXPORTS), r'EntryPoint = "viprs_acad_open_memory"\)\]\s*\n\s*[^\n]+')
        assert body, "viprs_acad_open_memory is not an export with a body"
        assert "CheckInputBytes" in body, (
            "open_memory copies the caller's bytes before anything applies "
            "max_input_bytes, so a buffer past the bound is duplicated and then refused"
        )
        assert body.index("CheckInputBytes") < body.index("Marshal.Copy"), (
            "the bound is applied after the copy, which is the allocation it exists to prevent"
        )


# ---------------------------------------------------------------------------
# The AC10xx range the build reads
# ---------------------------------------------------------------------------

ABI = os.path.join(NATIVE, "Abi.cs")
VERSION_GATE = os.path.join(NATIVE, "Adapter", "VersionGate.cs")

# A four-digit AC10xx code assigned to a constant. Deliberately the
# assignment and not the bare number: `SyntheticSource.DrawingVersion`
# returns 1032u, and that is the version the fake document claims to be
# rather than a copy of the boundary, so it is not an offender and must
# not be rewritten into one.
_DWG_CONSTANT = re.compile(r"=\s*10\d\du?\s*;")


def without_comments(code):
    return "\n".join(line for line in code.splitlines() if not line.lstrip().startswith("//"))


class TestTheDwgRangeHasOneCopyInTheShim:
    """`VersionGate` carried its own `MinVersion`/`MaxVersion` beside
    `AbiConstants.DwgVersionMin`/`Max`, both spelled as literals. Two copies of
    one fact drift in the direction nothing can see: `viprs_acad_get_capabilities_v1`
    answers out of `AbiConstants`, the gate that decides whether a file is
    opened at all reads the other pair, and a bump applied to one of them gives
    a library that advertises a range it does not enforce. Neither side fails to
    compile and no test of either half notices."""

    def test_only_abi_cs_assigns_the_range(self):
        offenders = sorted(
            name
            for name, code in shim_sources().items()
            if _DWG_CONSTANT.search(without_comments(code))
        )
        assert offenders == ["Abi.cs"], (
            f"the AC10xx range is assigned in {offenders}. It is one fact about the "
            "backing reader, and capabilities reports it from AbiConstants, so a "
            "second copy is a gate that can disagree with what the library says it "
            "reads."
        )

    def test_abi_cs_still_declares_both_ends(self):
        # The positive control. A shim that declared the range nowhere at
        # all would pass the check above.
        code = read(ABI)
        assert re.search(r"DwgVersionMin\s*=\s*10\d\du", code), "AbiConstants has no DwgVersionMin"
        assert re.search(r"DwgVersionMax\s*=\s*10\d\du", code), "AbiConstants has no DwgVersionMax"

    def test_the_gate_takes_its_bounds_from_there(self):
        code = read(VERSION_GATE)
        assert "AbiConstants.DwgVersionMin" in code and "AbiConstants.DwgVersionMax" in code, (
            "VersionGate no longer names AbiConstants, so whatever it compares against "
            "is a second copy again"
        )

    def test_the_gate_still_applies_them(self):
        # The other positive control: a gate that stopped comparing would
        # open an AC1009 file and let the reader fail somewhere obscure,
        # which is the case this class exists around.
        body = body_of(read(VERSION_GATE), r"public static bool IsSupported\(")
        assert body, "VersionGate has no IsSupported"
        assert "MinVersion" in body and "MaxVersion" in body, (
            "IsSupported no longer compares against both ends of the range"
        )


# ---------------------------------------------------------------------------
# The synthetic backing on the path route
# ---------------------------------------------------------------------------


class TestTheSyntheticDocumentIsReachableBothWays:
    """`docs/ABI.md` documented it for `open_memory` only, and
    `SourceFactory.OpenPath` has always sniffed it too. A second implementation
    built from the specification would have got that wrong in the direction
    that is hardest to notice: it would work on every real drawing."""

    def test_the_path_route_sniffs_it(self):
        body = body_of(read(SOURCE_FACTORY), r"public static IDocumentSource OpenPath\(")
        assert body, "SourceFactory has no OpenPath"
        assert "SyntheticSource.Matches" in body, (
            "the path route no longer recognises the synthetic magic. If that is "
            "deliberate, ABI.md and the header have to stop saying it does."
        )

    def test_the_memory_route_sniffs_it(self):
        body = body_of(read(SOURCE_FACTORY), r"public static IDocumentSource OpenMemory\(")
        assert body and "SyntheticSource.Matches" in body


# ---------------------------------------------------------------------------
# The test-only exports
# ---------------------------------------------------------------------------


class TestTheTestConfigurationCompiles:
    """`viprs_acad__test_live_handles` was declared twice inside the same
    `#if VIPRS_ACAD_TEST_EXPORTS` block. Two members with one signature is a
    build error, so the AbiTest configuration could not be published at all,
    and the AbiTest configuration is the one both conformance consumers run
    against. Nothing noticed because every check here keys exports by name
    into a dictionary, where the second declaration silently replaces the
    first."""

    def test_no_entry_point_is_declared_twice(self):
        code = read(EXPORTS)
        names = re.findall(r'\[UnmanagedCallersOnly\(EntryPoint = "(\w+)"\)\]', code)
        duplicates = sorted({n for n in names if names.count(n) > 1})
        assert not duplicates, (
            f"{duplicates} is declared more than once in Exports.cs. The C# compiler "
            "refuses a type with two members of the same signature, so whichever "
            "configuration compiles both of them does not build."
        )

    def test_no_method_name_is_declared_twice(self):
        code = read(EXPORTS)
        names = re.findall(r"public static (?:unsafe )?\S+ (\w+)\(", code)
        duplicates = sorted({n for n in names if names.count(n) > 1})
        assert not duplicates, f"Exports.cs declares {duplicates} more than once"


# ---------------------------------------------------------------------------
# The entity switch, and the arms that match more than they name
# ---------------------------------------------------------------------------

FLATTENER = os.path.join(NATIVE, "Adapter", "Flattener.cs")

# The ACadSharp release the table below was read off, which is the one
# build_acadsharp.py pins. The table is a copy of part of upstream's entity
# model, so a bump is a new model and has to come back through here. That is
# the whole of what this check can do about a kind upstream has not added yet.
MODEL_VERSION = "3.7.1"

# Every type an arm could name that matches more than itself, and what else it
# matches, read off the pinned tarball's src/ACadSharp (the interfaces live at
# the root of it, and one of them is implemented outside Entities).
#
# Leaves are not here and do not need to be: `case Line line:` matches a LINE
# and nothing else, so an arm on one raises no question and a lane adding one
# is not held up by this table. Two names are left out of IPolyline on
# purpose. `Polyline<T>` is the abstract base and cannot be a case without a
# type argument, and `PolyLinePlaceholder` is internal to upstream's reader
# and replaced before a document is handed out, so the shim cannot name it in
# a case at all. A generic base is left out for the same reason wherever it
# turns up, which is what took the `UnderlayEntity` row out below.
ENTITY_SUBTYPES = {
    "AttributeBase": ("AttributeDefinition", "AttributeEntity"),
    "CadWipeoutBase": ("RasterImage", "Wipeout"),
    "Circle": ("Arc",),
    "Dimension": (
        "DimensionAligned",
        "DimensionAngular2Line",
        "DimensionAngular3Pt",
        "DimensionArc",
        "DimensionDiameter",
        "DimensionLinear",
        "DimensionOrdinate",
        "DimensionPlaceholder",
        "DimensionRadius",
    ),
    "DimensionAligned": ("DimensionLinear",),
    "IPolyline": ("LwPolyline", "PolyfaceMesh", "PolygonMesh", "Polyline2D", "Polyline3D"),
    # ProxyObject is a NonGraphicalObject rather than an Entity, so it can
    # never reach the switch and an arm on IProxy would not in fact swallow it.
    # It is here because the table is a copy of part of upstream's model and a
    # row that is wrong about the model is a row nobody can check the next one
    # against.
    "IProxy": ("ProxyEntity", "ProxyObject"),
    "IText": ("AttributeBase", "AttributeDefinition", "AttributeEntity", "MText", "TextEntity"),
    "IVertex": (
        "PolygonMeshVertex",
        "Vertex",
        "Vertex2D",
        "Vertex3D",
        "VertexFaceMesh",
        "VertexFaceRecord",
        "VertexPlaceholder",
    ),
    "Insert": ("TableEntity",),
    "MechanicalEntity": ("AcmBalloon", "AcmPartList", "AcmPartRef"),
    "ModelerGeometry": ("CadBody", "Region", "Solid3D"),
    "TextEntity": ("AttributeBase", "AttributeDefinition", "AttributeEntity"),
    # UnderlayEntity was a row here and is gone. Upstream's type is
    # `UnderlayEntity<T>`, so `case UnderlayEntity x:` does not compile without
    # a type argument and no arm can ever name it: the row could not have
    # matched anything and was checking nothing. PDFUNDERLAY reaches the
    # default arm, which is where RefusedKinds decides about it.
    "Vertex": (
        "PolygonMeshVertex",
        "Vertex2D",
        "Vertex3D",
        "VertexFaceMesh",
        "VertexFaceRecord",
        "VertexPlaceholder",
    ),
}

# An arm that is deliberately written for a kind it also matches, and the
# reason. Everything else in the table above needs its own case ahead of the
# wider one, and the compiler agrees: a subtype after its base, or a class
# after an interface it implements, is CS8120 and does not build.
CARRIED = {
    ("IPolyline", "Polyline2D"): (
        "a POLYLINE whose vertices are in the plane its normal defines, which is the "
        "case the arm's placement split is written around"
    ),
    ("IPolyline", "Polyline3D"): (
        "a POLYLINE whose vertices are already world coordinates, which the arm branches on by name"
    ),
    ("TextEntity", "AttributeBase"): (
        "abstract, and the base the two below share, so nothing is ever one of these"
    ),
    ("TextEntity", "AttributeEntity"): (
        "the text a block instance actually shows, which is why the arm is reached "
        "from InsertBody at all"
    ),
    ("TextEntity", "AttributeDefinition"): (
        "an ATTDEF carries a text value and a position and the arm emits both. It is "
        "in a block's own entity list, so an insertion reaches the definition and the "
        "instance, which is the thing to look at first if a drawing ever comes back "
        "with its attribute text doubled"
    ),
}

# An arm on one of these is the default arm under another name: it matches
# whole families of entity, so whatever it does is done to kinds nobody chose
# it for. There is no reason to write one, and a lane that finds one it wants
# should split it rather than list every kind it swallows here.
MATCHES_WHOLE_FAMILIES = (
    "Entity",
    "IEntity",
    "IGeometricEntity",
    "IHandledCadObject",
    "IOrientable",
)

_ARM = re.compile(r"^\s*case\s+([A-Za-z_][\w.]*)\b[^\n:]*:", re.M)


def entity_switch():
    """The body of `switch (e)` in `Flattener.Map`, braces included."""
    return body_of(read(FLATTENER), r"switch \(e\)")


def switch_arms(body):
    """The type each `case` in that body names, in source order."""
    return _ARM.findall(body)


def swallowed_without_an_arm(arms):
    """Every (arm, kind) the arm also matches and nothing ahead of it names."""
    out = []
    for i, arm in enumerate(arms):
        for kind in ENTITY_SUBTYPES.get(arm, ()):
            if (arm, kind) in CARRIED or kind in arms[:i]:
                continue
            out.append((arm, kind))
    return out


class TestNoArmSwallowsAKindNobodyWroteItFor:
    """`case IPolyline poly:` matched POLYFACE_MESH and POLYGON_MESH, because
    both derive from `Polyline<T>` and `Polyline<T>` implements `IPolyline`.
    Each went out as one open polyline threaded through its own vertices, with
    no warning and byte-indistinguishable from the polylines beside it, which
    is the one outcome docs/WIRE.md exists to prevent (libviprs-dep#82).

    The compiler is no help here and it is worth being precise about why. It
    orders the arms that exist: a subtype after its base, or a class after an
    interface it implements, is CS8120 and a hard build error, so the arm this
    one needed could not have been put in the wrong place. What nothing checks
    is that the arm is there at all, and an absent arm is the defect. So this
    reads the switch as text and asks the other question."""

    def test_the_switch_is_the_one_this_reads(self):
        # The extraction control. A rule that silently found nothing to check
        # is the same colour as a rule that checked everything.
        body = entity_switch()
        assert body, "Flattener has no `switch (e)`, so nothing below read the entity switch"
        arms = switch_arms(body)
        assert "Line" in arms and "IPolyline" in arms, (
            f"the switch this parsed has arms {arms}, which is not the entity switch"
        )
        assert "switch (" not in body, (
            "the entity switch now holds a nested switch, so the arms above are two "
            "switches' arms in one list and the ordering this checks is not a real order"
        )
        assert body.rstrip().endswith("}") and "default:" in body

    def test_the_table_is_pinned_to_the_model_it_was_read_from(self):
        import build_acadsharp as ba

        upstream, _shim = ba.split_version(ba.read_version())
        assert upstream == MODEL_VERSION, (
            f"ENTITY_SUBTYPES was read off ACadSharp {MODEL_VERSION} and the pin is now "
            f"{upstream}. Re-read src/ACadSharp/Entities before moving this string: a new "
            "kind under an existing base is exactly what this check exists to catch, and "
            "it cannot see one that is not in the table."
        )

    def test_no_arm_matches_a_kind_with_no_arm_ahead_of_it(self):
        offenders = swallowed_without_an_arm(switch_arms(entity_switch()))
        assert not offenders, (
            "these arms match kinds nothing ahead of them names: "
            + ", ".join(f"`case {arm}` also matches {kind}" for arm, kind in offenders)
            + ". Each one crosses the wire as whatever that arm emits, with no warning "
            "and nothing in the record saying it is not the kind the arm was written "
            "for. Give it its own arm ahead of this one, or say in CARRIED why this "
            "arm is right for it."
        )

    def test_no_arm_matches_whole_families(self):
        arms = switch_arms(entity_switch())
        offenders = sorted(set(arms) & set(MATCHES_WHOLE_FAMILIES))
        assert not offenders, (
            f"{offenders} is an arm on a type most of the entity model derives from, so "
            "it is the default arm with a narrower name and every kind it swallows was "
            "swallowed by nobody's decision"
        )

    def test_the_rule_catches_the_defect_it_was_written_for(self):
        # The positive control, and the one that says this is not vacuous: the
        # switch as it stood before #82, with LwPolyline ahead of IPolyline and
        # no mesh arm anywhere.
        assert swallowed_without_an_arm(["Line", "LwPolyline", "IPolyline", "MText"]) == [
            ("IPolyline", "PolyfaceMesh"),
            ("IPolyline", "PolygonMesh"),
        ]

    def test_an_arm_ahead_of_the_wide_one_satisfies_it(self):
        # The other control. Ordering is the fix, so the rule has to accept it,
        # and `case Arc` ahead of `case Circle` is the version of it already in
        # the tree.
        assert swallowed_without_an_arm(["Arc", "Circle"]) == []
        assert swallowed_without_an_arm(["Circle", "Arc"]) == [("Circle", "Arc")]

    def test_the_table_agrees_with_itself_about_what_derives_from_what(self):
        # Nothing in this job can validate ENTITY_SUBTYPES against the model,
        # and it is worth saying so plainly: these tests run with no .NET (ADR
        # 0001), so the table is a hand copy of part of upstream's entity model
        # and a row that is wrong about upstream is wrong here too. Two of them
        # were, and both were found by reflecting over the pinned assembly
        # rather than by anything in this file: `IProxy` was missing
        # `ProxyObject`, and `UnderlayEntity` named a type that is generic and
        # that no `case` can spell.
        #
        # What the table can be held to is itself, and this is the half of the
        # error that shows up there. Derivation is transitive: if a `case X`
        # swallows Y and a `case Y` would swallow Z, then `case X` swallows Z,
        # so Z belongs in X's row. An omission in one row is visible from the
        # other, which is the shape of every miss in the table so far.
        missing = []
        for base, kinds in sorted(ENTITY_SUBTYPES.items()):
            assert base not in kinds, f"{base} lists itself as one of its own subtypes"
            assert len(set(kinds)) == len(kinds), f"{base} lists a kind twice"
            for kind in kinds:
                for deeper in ENTITY_SUBTYPES.get(kind, ()):
                    if deeper != base and deeper not in kinds:
                        missing.append((base, kind, deeper))
        assert not missing, (
            "these rows disagree: "
            + ", ".join(
                f"`case {base}` matches {kind}, {kind} matches {deeper}, and {base}'s "
                f"row does not list {deeper}"
                for base, kind, deeper in missing
            )
            + ". A kind an arm swallows through two steps is swallowed just as quietly "
            "as one it swallows directly."
        )

    def test_the_transitivity_check_catches_an_omission(self):
        # The control. Without it a table nobody could parse would pass the
        # rule above, and the rule is the only thing standing between a hand
        # copy of the model and the day somebody adds one name and not the
        # other.
        table = {"Base": ("Middle",), "Middle": ("Leaf",)}
        missing = [
            (base, kind, deeper)
            for base, kinds in table.items()
            for kind in kinds
            for deeper in table.get(kind, ())
            if deeper != base and deeper not in kinds
        ]
        assert missing == [("Base", "Middle", "Leaf")]

    def test_every_carried_pair_is_one_the_model_has(self):
        # A reason written for a pair that cannot happen is a reason nobody
        # will ever reread, and it hides the day the pair stops existing.
        stale = sorted(
            (arm, kind) for arm, kind in CARRIED if kind not in ENTITY_SUBTYPES.get(arm, ())
        )
        assert not stale, f"{stale} is carried by an arm that does not match it"
        assert all(CARRIED.values()), "a carried pair with no reason is not a decision"
