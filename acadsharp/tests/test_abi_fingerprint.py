"""The fingerprint is a definition, not a number somebody chose.

`viprs_acad_abi_fingerprint()` returns the first eight bytes of the sha256 of
the published header, big-endian. That is only useful if it cannot drift from
the header it describes, which rules out a hand-assigned constant: the moment
the header changes and the constant does not, the value stops meaning what
every consumer believes it means, and it fails in the one direction that
matters, by reporting agreement.

So the shim generates `AbiFingerprint.g.cs` from `include/viprs_acadsharp.h`
at build time and the generated file is not checked in. This module is the
other half: the recorded digest of the header as it stands, checked on every
CI run with no toolchain at all. Editing one byte of the header turns this
red immediately, which is the gate the epic asks for, and it turns red in the
existing `pytest -v` job rather than in a new one.

The recorded numbers below are updated deliberately, in the same commit that
changes the header, never as a fix for a red run.
"""

import hashlib
import os
import re

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ACADSHARP = os.path.dirname(HERE)
HEADER = os.path.join(ACADSHARP, "include", "viprs_acadsharp.h")
NATIVE = os.path.join(ACADSHARP, "native")
CSPROJ = os.path.join(NATIVE, "Viprs.ACadSharp.Native.csproj")
GENERATED = "AbiFingerprint.g.cs"

# sha256 of acadsharp/include/viprs_acadsharp.h, and the first eight bytes of
# it read big-endian, which is what the export returns.
HEADER_SHA256 = "03bb8e542f149ec03b1686b215177a6b60f6b7429655a3cf8fdd7403500d65ad"
ABI_FINGERPRINT = 0x03BB8E542F149EC0


@pytest.fixture(scope="module")
def header_bytes():
    with open(HEADER, "rb") as f:
        return f.read()


@pytest.fixture(scope="module")
def csproj():
    with open(CSPROJ) as f:
        return f.read()


class TestTheRecordedDigest:
    def test_the_header_still_hashes_to_the_recorded_value(self, header_bytes):
        actual = hashlib.sha256(header_bytes).hexdigest()
        assert actual == HEADER_SHA256, (
            f"the header now hashes to {actual}, recorded as {HEADER_SHA256}. Every "
            "consumer compiled against the old file will read a fingerprint that no "
            "longer matches the library. If the change is deliberate, bump "
            "VIPRS_ACAD_ABI_VERSION, update both constants here in the same commit, and "
            "say in the pull request what moved."
        )

    def test_the_fingerprint_is_the_first_eight_bytes_big_endian(self, header_bytes):
        digest = hashlib.sha256(header_bytes).digest()
        assert int.from_bytes(digest[:8], "big") == ABI_FINGERPRINT

    def test_the_two_recorded_constants_agree_with_each_other(self):
        # They are written separately so a careless update of one is caught by
        # the other rather than by a consumer at run time.
        assert int(HEADER_SHA256[:16], 16) == ABI_FINGERPRINT

    def test_the_digest_is_of_the_file_as_it_ships(self, header_bytes):
        # sha256sum of the same path has to produce the same number, so the
        # recorded value must be of the raw bytes and not of some normalised
        # form of them.
        assert b"\r\n" not in header_bytes, (
            "the header has CRLF line endings, so sha256sum on a checkout that "
            "normalises them would disagree with this recorded value."
        )


class TestTheShimGeneratesItRatherThanCarryingIt:
    def test_the_project_hashes_the_header_at_build_time(self, csproj):
        assert "GetFileHash" in csproj, (
            "the fingerprint has to come from the header file during the build. "
            "MSBuild's GetFileHash is what makes that a two-line target instead of a "
            "script nobody runs."
        )
        assert "SHA256" in csproj
        assert "viprs_acadsharp.h" in csproj, (
            "the target has to name the published header, not a copy of it"
        )

    def test_the_generated_file_is_written_and_compiled(self, csproj):
        assert GENERATED in csproj
        assert "WriteLinesToFile" in csproj
        assert re.search(r"<Compile Include=.*AbiFingerprint\.g\.cs", csproj), (
            f"{GENERATED} is written but never added to the compilation, so the build "
            "would fall back to whatever stale copy is lying around"
        )

    def test_the_generated_file_is_not_checked_in(self):
        stray = [
            os.path.join(root, GENERATED)
            for root, _dirs, files in os.walk(ACADSHARP)
            if GENERATED in files and f"{os.sep}obj{os.sep}" not in root + os.sep
        ]
        assert not stray, (
            f"{stray} is in the tree. A generated fingerprint that is also a checked-in "
            "file is a hand-assigned fingerprint with extra steps: it will be the stale "
            "one on the first build that skips the target."
        )

    def test_no_managed_source_hand_assigns_a_fingerprint(self):
        offenders = []
        for root, _dirs, files in os.walk(NATIVE):
            if f"{os.sep}obj" in root or f"{os.sep}bin" in root:
                continue
            for name in files:
                if not name.endswith(".cs") or name.endswith(".g.cs"):
                    continue
                path = os.path.join(root, name)
                with open(path) as f:
                    code = f.read()
                for m in re.finditer(r"0x[0-9a-fA-F]{16}\s*(?:UL|ul)?", code):
                    offenders.append(f"{name}: {m.group(0)}")
        assert not offenders, (
            f"a 64-bit literal is sitting in the managed sources: {offenders}. If that is "
            "the fingerprint it is exactly the drift this design removes."
        )

    def test_the_export_returns_the_generated_constant(self):
        with open(os.path.join(NATIVE, "Exports.cs")) as f:
            code = f.read()
        m = re.search(
            r'EntryPoint = "viprs_acad_abi_fingerprint"\)\]\s*\n\s*[^\n]*\n?([^\n]*)', code
        )
        assert m, "viprs_acad_abi_fingerprint is not exported"
        assert "AbiFingerprint" in m.group(0), (
            "the fingerprint export does not read the generated constant"
        )


class TestTheDriftGateCanActuallyFail:
    """A gate that has only ever seen a matching header is a gate nobody tested."""

    def test_one_changed_byte_changes_the_recorded_digest(self, header_bytes, tmp_path):
        edited = bytearray(header_bytes)
        # The whitespace after the include guard: a byte no compiler notices
        # and no test other than this one would see move.
        edited[-1:] = b"\n\n"
        path = tmp_path / "viprs_acadsharp.h"
        path.write_bytes(bytes(edited))
        assert hashlib.sha256(path.read_bytes()).hexdigest() != HEADER_SHA256

    def test_one_changed_byte_changes_the_fingerprint(self, header_bytes):
        edited = bytes(header_bytes) + b"\n"
        fingerprint = int.from_bytes(hashlib.sha256(edited).digest()[:8], "big")
        assert fingerprint != ABI_FINGERPRINT, (
            "a one-byte edit produced the same fingerprint, which would mean the "
            "handshake cannot see the drift it exists to see"
        )
