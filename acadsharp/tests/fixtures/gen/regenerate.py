#!/usr/bin/env python3
"""Regenerate the G1.3 corpus, expectations, scenario captures and benchmarks.

    python3 acadsharp/tests/fixtures/gen/regenerate.py --scratch DIR --acad-source DIR --plan
    python3 acadsharp/tests/fixtures/gen/regenerate.py --scratch DIR --acad-source DIR --all

``--only corpus --write-only g13_slot`` writes just the fixtures whose name
carries that substring, which is how a round that adds one DWG keeps the other
twenty-five out of the diff: a DWG header carries timestamps, so rewriting an
unchanged fixture still changes its bytes and its digest.

Everything below runs inside the pinned .NET SDK container. The only host tools
it needs are docker and python3, which is the rule the rest of this repository
builds under.

What it produces, and what reads it back:

* ``tests/fixtures/g13_*.dwg`` - the corpus, committed, because a DWG header
  carries creation and update timestamps and a rerun produces different bytes.
* ``tests/expectations/*.txt`` - the canonical record dump per fixture, which
  is the expectation ``test_adapter_stream.py`` compares against.
  ``fixturegen decode --check`` is what names the first differing record.
* ``tests/fixtures/captures/g13_scenarios.json`` - every limit, refusal and
  malformed-input run, with the code each produced.
* ``tests/benchmarks/amplification.json`` - the block-expansion, path-versus-
  memory and streaming numbers.

pytest has no .NET and is not getting one (ADR 0001), so the committed files
are what the tests read. That only means something if a capture is provably a
run of the file in the tree, so every capture carries the sha256 of its fixture
and the tests recompute it, and ``MANIFEST.json`` carries the sha256 of every
source this generator compiles so a capture of a flattener that has since been
edited is refused the same way.
"""

import argparse
import hashlib
import importlib.util
import json
import os
import random
import struct
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ACAD_ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
REPO_ROOT = os.path.dirname(ACAD_ROOT)
FIXTURES = os.path.join(ACAD_ROOT, "tests", "fixtures")
EXPECTATIONS = os.path.join(ACAD_ROOT, "tests", "expectations")
# Not tests/fixtures/captures: that directory is the G1.1 JIT-versus-AOT
# parity set and test_acadsharp_recorded_parity.py asserts every JSON in it
# carries a system-variable count. A scenario capture is a different kind of
# artefact and sits with the expectations it belongs to.
CAPTURES = EXPECTATIONS
BENCHMARKS = os.path.join(ACAD_ROOT, "tests", "benchmarks")

SDK_IMAGE = "mcr.microsoft.com/dotnet/sdk:10.0.401-noble"
PLATFORM = "linux/arm64"
PROJECT = "tests/fixtures/gen/Viprs.ACadSharp.FixtureGen.csproj"
DLL = "/out/artifacts/bin/Viprs.ACadSharp.FixtureGen/debug/fixturegen.dll"

BATCH_BYTES = 65536

# The fixtures whose whole record stream is committed as an expectation.
#
# The hostile ones are left out on purpose. g13_many_inserts.dwg decodes to
# tens of thousands of records and a committed dump of those is a file nobody
# can read in a diff; its shape is pinned by the benchmark instead.
# g13_dimension_deep.dwg and g13_wide_spline.dwg are refused rather than
# decoded, so there is no stream to dump, and the scenario capture is where
# they are checked. g13_dimension_shallow.dwg is the readable one of that pair
# and is dumped.
DUMPED = (
    "g13_line.dwg",
    "g13_polyline.dwg",
    "g13_arc.dwg",
    "g13_circle.dwg",
    "g13_ellipse.dwg",
    "g13_spline.dwg",
    "g13_text.dwg",
    "g13_insert.dwg",
    "g13_dimension.dwg",
    "g13_hatch.dwg",
    "g13_unsupported.dwg",
    "g13_xref.dwg",
    "g13_nonuniform.dwg",
    "g13_two_entities.dwg",
    "g13_deep_blocks.dwg",
    "g13_slot.dwg",
    "g13_slot_block.dwg",
    "g13_mirrored_bulge.dwg",
    "g13_ocs_plane.dwg",
    "g13_ocs_mirror.dwg",
    "g13_ocs_rotated.dwg",
    "g13_ocs_skew.dwg",
    "g13_nan_bulge.dwg",
    "g13_bad_extents.dwg",
    "g13_empty_view.dwg",
    "g13_wide_polyline.dwg",
    "g13_long_text.dwg",
    "g13_scale_1x.dwg",
    "g13_dimension_shallow.dwg",
    # RAY and XLINE, one of each, so the deliberate refusal
    # (ENTITY_REFUSED_BY_DESIGN, docs/adr/0002) is evidenced on a fixture
    # carrying nothing else as well as on the two real drawings. It arrived
    # with #94 and was never added here, and expectations() iterates this
    # tuple and nothing else, so until now it was a DWG the suite could not
    # read: no dump, no MANIFEST entry, green and inert.
    "g13_ray_xline.dwg",
    "real_AC1032.dwg",
    "real_AC1018.dwg",
    "g11_shapes.dwg",
    "g11_codepage.dwg",
)

MALFORMED_SOURCE = "g13_insert.dwg"
TRUNCATIONS = (25, 50, 90)
FLIP_COUNT = 64
FLIP_SEED = 4713


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def truncate(data, percent):
    return data[: max(1, (len(data) * percent) // 100)]


def bitflip(data, count=FLIP_COUNT, seed=FLIP_SEED):
    """`count` bytes flipped where a fixed seed puts them.

    Fixed seed so the derivative is the same file on every run and a failure is
    reproducible. The positions come back too, because a flip inside the
    six-byte version signature is a different refusal from one in the body and
    a test that cannot tell them apart is asserting on luck.
    """
    rng = random.Random(seed)
    out = bytearray(data)
    positions = []
    for _ in range(count):
        p = rng.randrange(len(out))
        out[p] ^= 1 << rng.randrange(8)
        positions.append(p)
    return bytes(out), positions


def docker_cmd(scratch, args, network=None, repo_ro=False, read_only=False):
    repo_mount = f"{REPO_ROOT}:/work:ro" if repo_ro else f"{REPO_ROOT}:/work"
    cmd = [
        "docker",
        "run",
        "--rm",
        "--platform",
        PLATFORM,
        "-v",
        repo_mount,
        "-v",
        f"{scratch}:/scratch",
        "-v",
        f"{os.path.join(scratch, 'home')}:/home/build",
        "-v",
        f"{os.path.join(scratch, 'out')}:/out",
        "-e",
        "HOME=/home/build",
        "-e",
        "DOTNET_CLI_HOME=/home/build",
        "-e",
        "DOTNET_CLI_TELEMETRY_OPTOUT=1",
        "-e",
        "DOTNET_NOLOGO=1",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "-w",
        "/work/acadsharp",
    ]
    if network:
        cmd += ["--network", network]
    if read_only:
        cmd += ["--read-only", "--tmpfs", "/tmp:exec"]
    return cmd + [SDK_IMAGE] + list(args)


def run(cmd, check=True):
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if check and proc.returncode != 0:
        sys.stderr.write(proc.stdout or "")
        sys.stderr.write(proc.stderr or "")
        raise RuntimeError(f"command failed ({proc.returncode}): {' '.join(cmd)}")
    return proc


def driver():
    """`build_acadsharp.py` as a module, for the one thing this needs from it.

    The list of patches lives in one place, and that place is the build
    driver. A second copy of "which scripts in patches/ are applied" here is
    how the captures end up recording a reader the archive does not ship.
    """
    spec = importlib.util.spec_from_file_location(
        "build_acadsharp", os.path.join(ACAD_ROOT, "build_acadsharp.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def apply_patches(acad_source, plan=False):
    """Apply every patch the archive is built with to this checkout.

    Without this the generator compiles the pristine tarball while the
    published library is built from a patched one, so a capture would be a
    recording of a reader nobody ships. The scripts are idempotent, which is
    what makes a reused checkout safe to run this against.
    """
    patches = os.path.join(ACAD_ROOT, "patches")
    for name in driver().patch_scripts():
        cmd = [sys.executable, os.path.join(patches, name), acad_source]
        print(" ".join(cmd))
        if not plan:
            proc = run(cmd, check=False)
            sys.stdout.write(proc.stdout or "")
            if proc.returncode != 0:
                sys.stderr.write(proc.stderr or "")
                raise RuntimeError(
                    f"{name} refused {acad_source}. The captures cannot be recorded "
                    "against a tree the published library is not built from."
                )


def build(scratch, acad_source, plan=False):
    cmd = docker_cmd(
        scratch,
        [
            "dotnet",
            "build",
            PROJECT,
            "-c",
            "Debug",
            "-p:AcadSharpProject=/acs/src/ACadSharp/ACadSharp.csproj",
            "--artifacts-path",
            "/out/artifacts",
        ],
    )
    cmd.insert(cmd.index(SDK_IMAGE), "-v")
    cmd.insert(cmd.index(SDK_IMAGE), f"{acad_source}:/acs")
    print(" ".join(cmd))
    if not plan:
        run(cmd)


def decode(scratch, path, *args, **kwargs):
    cmd = docker_cmd(scratch, ["dotnet", DLL, "decode", path] + [str(a) for a in args], **kwargs)
    proc = run(cmd, check=False)
    try:
        result = json.loads(proc.stdout)
    except ValueError:
        result = {
            "open_code": "PROCESS_FAILED",
            "open_detail": (proc.stderr or proc.stdout or "")[-2000:],
        }
    result["exit_code"] = proc.returncode
    return result


def decode_unreadable(scratch, source_fixture, extra):
    """Decode a mode-000 copy of a fixture, made inside the container.

    The copy is made in the container's own tmpfs rather than in a bind mount,
    because a macOS bind mount does not carry Linux permissions and a file that
    is supposed to be unreadable comes back merely missing. Inside the
    container it is a real Linux file with a real mode, the process runs as a
    non-root user, and "cannot be opened" means what it says. The shell checks
    that before the decode runs, so a control that quietly stopped controlling
    anything shows up in the capture.
    """
    script = (
        "set -e\n"
        f"cp {source_fixture} /tmp/sealed.dwg\n"
        "chmod 000 /tmp/sealed.dwg\n"
        "if head -c 1 /tmp/sealed.dwg >/dev/null 2>&1; then\n"
        "  echo 'CONTROL FAILED: the process can still read a mode 000 file' >&2\n"
        "  exit 3\n"
        "fi\n"
        f"exec dotnet {DLL} decode /tmp/sealed.dwg {extra}\n"
    )
    cmd = docker_cmd(scratch, ["sh", "-c", script])
    cmd.insert(cmd.index(SDK_IMAGE), "--tmpfs")
    cmd.insert(cmd.index(SDK_IMAGE), "/tmp:exec")
    proc = run(cmd, check=False)
    try:
        result = json.loads(proc.stdout)
    except ValueError:
        result = {
            "open_code": "PROCESS_FAILED",
            "open_detail": (proc.stderr or proc.stdout or "")[-2000:],
        }
    result["exit_code"] = proc.returncode
    result["control"] = "mode 000 copy in the container tmpfs, non-root, read proven to fail"
    return result


def write_corpus(scratch, only=None):
    """Rewrite the corpus, or only the fixtures whose name contains `only`.

    Every DWG carries creation and update timestamps, so rewriting one is a new
    file even when nothing about its content changed, and every expectation is
    pinned to the digest. A round that adds one fixture and rewrites the other
    twenty-five produces a diff in which the change and the churn look the
    same, so the default for a round like that is to name what it is writing.
    """
    args = ["dotnet", DLL, "corpus", "/work/acadsharp/tests/fixtures"]
    if only:
        args.append(only)
    run(docker_cmd(scratch, args))


def stem(fixture):
    return os.path.splitext(fixture)[0]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--scratch", required=True, help="a writable directory outside the repo")
    parser.add_argument("--acad-source", required=True, help="the unpacked ACadSharp checkout")
    parser.add_argument("--plan", action="store_true", help="print the build command and stop")
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--skip-corpus", action="store_true")
    parser.add_argument(
        "--write-only",
        metavar="SUBSTR",
        help="write only the corpus fixtures whose file name contains SUBSTR",
    )
    parser.add_argument("--only", help="one stage: corpus, expectations, scenarios, benchmarks")
    args = parser.parse_args(argv)

    scratch = os.path.abspath(args.scratch)
    for d in ("home", "out", "derived"):
        os.makedirs(os.path.join(scratch, d), exist_ok=True)
    for d in (FIXTURES, EXPECTATIONS, CAPTURES, BENCHMARKS):
        os.makedirs(d, exist_ok=True)

    if not args.skip_build:
        apply_patches(os.path.abspath(args.acad_source), plan=args.plan)
        build(scratch, os.path.abspath(args.acad_source), plan=args.plan)
    if args.plan:
        return 0

    stage = args.only
    if not args.skip_corpus and stage in (None, "corpus"):
        write_corpus(scratch, args.write_only)

    if stage in (None, "expectations"):
        expectations(scratch)
    if stage in (None, "scenarios"):
        scenarios(scratch)
    if stage in (None, "benchmarks"):
        benchmarks(scratch)
    return 0


def support():
    """`tests/g13_support.py`, which owns the derivations a capture records.

    Imported rather than restated, for the reason shim_block gives below: a
    derivation is a contract between the file this writes and the test that
    reads it, and two implementations of a contract is how the two ends drift.
    """
    sys.path.insert(0, os.path.join(ACAD_ROOT, "tests"))
    import g13_support

    return g13_support


def ac18_forge():
    """`tests/ac18_forge.py`, which writes the AC1018 inputs from nothing.

    Same contract as `ac21_forge` below, one reader path over: the bytes this
    records a run of and the bytes the test rebuilds come from one place.
    """
    sys.path.insert(0, os.path.join(ACAD_ROOT, "tests"))
    import ac18_forge

    return ac18_forge


def ac21_forge():
    """`tests/ac21_forge.py`, which writes the AC1021 inputs from nothing.

    Imported for the same reason `support()` imports the derivation rather
    than restating it: the bytes this records a run of and the bytes the test
    rebuilds have to come from one place, or the two ends drift and the
    capture stops being evidence.
    """
    sys.path.insert(0, os.path.join(ACAD_ROOT, "tests"))
    import ac21_forge

    return ac21_forge


def shim_block():
    """The shim these captures are a run of, as digests.

    Every fixture entry already carries the sha256 of the DWG it was
    produced from, which is what lets pytest refuse a capture of a file that
    has since changed. This is the other half: the sha256 of every source
    this generator compiles, so a capture of a flattener that has since been
    edited is refused the same way.

    The functions come from the test support module rather than being
    written a second time here. The digest is a contract between the file
    this writes and the test that reads it, and two implementations of a
    contract is how the two ends drift.
    """
    g13 = support()
    digest_file = g13.sha256_file
    sources = g13.shim_sources()
    shim_digest = g13.shim_digest
    return {
        "sha256": shim_digest(sources),
        "sources": {rel: digest_file(os.path.join(ACAD_ROOT, rel)) for rel in sources},
    }


def expectations(scratch):
    shim = shim_block()

    manifest = {
        "recorded": time.strftime("%Y-%m-%d"),
        "runner": f"fixturegen decode --dump, {SDK_IMAGE}, {PLATFORM}",
        "batch_bytes": BATCH_BYTES,
        "shim": shim,
        "fixtures": {},
    }

    for fixture in DUMPED:
        path = os.path.join(FIXTURES, fixture)
        if not os.path.isfile(path):
            raise RuntimeError(f"{fixture} is not in tests/fixtures")

        out = f"/work/acadsharp/tests/expectations/{stem(fixture)}.txt"
        result = decode(
            scratch,
            f"/work/acadsharp/tests/fixtures/{fixture}",
            "--dump",
            out,
            "--batch",
            BATCH_BYTES,
        )
        if result.get("dump_error"):
            raise RuntimeError(f"{fixture}: {result['dump_error']}")

        lines = open(os.path.join(EXPECTATIONS, f"{stem(fixture)}.txt")).read().splitlines()
        counts = {}
        for line in lines:
            if not line:
                continue
            kind = line.split(" ")[1]
            counts[kind] = counts.get(kind, 0) + 1

        manifest["fixtures"][fixture] = {
            "sha256": sha256_file(path),
            "bytes": os.path.getsize(path),
            "records": len([x for x in lines if x]),
            "kinds": counts,
            "view_count": result.get("view_count"),
            "view_extents": result.get("view_extents"),
            "views_without_extents": result.get("views_without_extents"),
            "notification_count": result.get("notification_count"),
            "batches": result.get("batches"),
            "output_bytes": result.get("output_bytes"),
            "live_handles": result.get("live_handles"),
            "decode_code": result.get("decode_code"),
        }
        print(f"{fixture}: {manifest['fixtures'][fixture]['records']} records {counts}")

    with open(os.path.join(EXPECTATIONS, "MANIFEST.json"), "w") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)
        f.write("\n")


def non_finite_exponents(data):
    """Places in a raw batch stream that look like a non-finite f64's top two bytes.

    An IEEE-754 binary64 is NaN or infinite exactly when its exponent field is
    all ones, which in little-endian means the last two bytes satisfy
    `b6 & 0xF0 == 0xF0` and `b7 & 0x7F == 0x7F`: the f87f and f07f patterns and
    their negatives.

    Deliberately crude, and deliberately not a parser. Nothing inside a record
    is naturally aligned, so a scanner that walked the stream by record and
    field would be a fourth implementation of docs/WIRE.md, and a bug in it
    would look exactly like the thing it is checking for. This one reads every
    offset and cannot miss: a zero means no non-finite double crossed, whatever
    it sat on. A non-zero can be a false positive, which is a number somebody
    looks at rather than a claim anybody acts on, and the control beside it is
    what says the scan finds one when there is one.
    """
    hits = 0
    for i in range(len(data) - 1):
        if (data[i] & 0xF0) == 0xF0 and (data[i + 1] & 0x7F) == 0x7F:
            hits += 1
    return hits


# The two bytes of the character g13_xref_long.dwg's reference path is padded
# with, U+00E9 in UTF-8, and what the encoder appends to a message it cut.
FILLER_UTF8 = b"\xc3\xa9"
TRUNCATION_MARKER = b" [truncated]"


def longest_filler_run(data):
    """The longest run of the filler character in a raw batch stream.

    Deliberately crude, and deliberately not a parser, for the same reason
    non_finite_exponents above is not one: a scanner that walked the stream by
    record and field would be a fourth implementation of docs/WIRE.md and a bug
    in it would look exactly like the thing it is checking for. This counts a
    two-byte pattern and cannot miss.
    """
    best = 0
    run = 0
    i = 0
    while i + 1 < len(data):
        if data[i] == FILLER_UTF8[0] and data[i + 1] == FILLER_UTF8[1]:
            run += 1
            best = max(best, run)
            i += 2
        else:
            run = 0
            i += 1
    return best


def after_longest_filler_run(data, count=16):
    """The bytes that follow the longest run, hex-encoded.

    Where the marker sits is the whole claim: at the end of the message, after
    the last character that survived the cut. A count of markers somewhere in
    the stream would not say that.
    """
    best = 0
    end = 0
    run = 0
    i = 0
    while i + 1 < len(data):
        if data[i] == FILLER_UTF8[0] and data[i + 1] == FILLER_UTF8[1]:
            run += 1
            i += 2
            if run > best:
                best = run
                end = i
        else:
            run = 0
            i += 1
    return data[end : end + count].hex()


def dangling_lead_bytes(data):
    """Lead bytes of the filler character with no continuation byte after them.

    A cut that counted bytes rather than characters leaves one of these where
    the message ends, and the record then declares a message_len over bytes
    that are not UTF-8. Crude the same way: it reads every offset.
    """
    return sum(
        1
        for i in range(len(data))
        if data[i] == FILLER_UTF8[0] and (i + 1 >= len(data) or data[i + 1] != FILLER_UTF8[1])
    )


def occurrences(data, needle):
    count = 0
    at = data.find(needle)
    while at >= 0:
        count += 1
        at = data.find(needle, at + 1)
    return count


def fixture_arg(name):
    return f"/work/acadsharp/tests/fixtures/{name}"


def scenarios(scratch):
    derived = os.path.join(scratch, "derived")
    out = {
        "recorded": time.strftime("%Y-%m-%d"),
        "runner": f"fixturegen decode, {SDK_IMAGE}, {PLATFORM}",
        "batch_bytes": BATCH_BYTES,
        "malformed": {
            "source": MALFORMED_SOURCE,
            "truncations": list(TRUNCATIONS),
            "flip_count": FLIP_COUNT,
            "flip_seed": FLIP_SEED,
        },
        "scenarios": [],
    }

    def fan(name, depth, width, args, note):
        cmd = docker_cmd(
            scratch,
            ["dotnet", DLL, "fanout", str(depth), str(width)] + [str(a) for a in args],
        )
        proc = run(cmd, check=False)
        try:
            result = json.loads(proc.stdout)
        except ValueError:
            result = {
                "code": "PROCESS_FAILED",
                "detail": (proc.stderr or proc.stdout or "")[-2000:],
            }
        result["exit_code"] = proc.returncode
        out["scenarios"].append(
            {
                "name": name,
                "input": f"an in-memory fan-out, depth {depth} width {width}",
                "args": [str(a) for a in args],
                "note": note,
                "result": result,
            }
        )
        print(f"{name}: code={result.get('code')} walked={result.get('entities_walked')}")
        return result

    def record(name, fixture, args, note, **kw):
        result = decode(scratch, fixture, *args, **kw)
        entry = {
            "name": name,
            "input": fixture,
            "args": [str(a) for a in args],
            "note": note,
            "result": result,
        }
        if fixture.startswith("/work/acadsharp/tests/fixtures/"):
            local = os.path.join(FIXTURES, os.path.basename(fixture))
            entry["fixture_sha256"] = sha256_file(local)
        out["scenarios"].append(entry)
        print(f"{name}: open={result.get('open_code')} decode={result.get('decode_code')}")
        return entry

    # The version gate, and the control that shows it runs before parsing. Both
    # files are six good bytes and then nothing: one signature this build reads
    # and one it does not. A refusal that told them apart only by failing later
    # would give the same answer to both.
    record(
        "gate/signature_out_of_range",
        fixture_arg("g13_ac1009.dwg"),
        [],
        "AC1009 is a DWG this build does not read, refused on the signature",
    )
    control = os.path.join(derived, "g13_ac1032_zeros.dwg")
    with open(control, "wb") as f:
        f.write(b"AC1032" + bytes(506))
    record(
        "gate/signature_in_range_but_garbage",
        "/scratch/derived/g13_ac1032_zeros.dwg",
        [],
        "the same file with a signature this build does read, so the refusal has "
        "to come from the parse and be CORRUPT_INPUT",
    )

    # Limits, each with the loosened control beside it. A bound that has only
    # ever been seen refusing is a bound that might refuse everything.
    record(
        "limits/max_entities_1",
        fixture_arg("g13_two_entities.dwg"),
        ["--max-entities", 1],
        "two entities, one allowed",
    )
    record(
        "limits/max_entities_default",
        fixture_arg("g13_two_entities.dwg"),
        [],
        "the same fixture with the default bound",
    )
    record(
        "limits/max_polyline_2048",
        fixture_arg("g13_wide_polyline.dwg"),
        ["--max-polyline", 2048],
        "a 4096-vertex polyline, 2048 allowed",
    )
    # max_polyline_points bounds vertices, and wire version 2 gave the record a
    # second array of the same length. The slot is four vertices and four
    # bulges: at a bound of four it decodes, and a bound that counted the
    # bulges would see eight and refuse.
    record(
        "limits/bulged_polyline_at_the_bound",
        fixture_arg("g13_slot.dwg"),
        ["--max-polyline", 4],
        "four vertices and four bulges, four allowed",
    )
    record(
        "limits/bulged_polyline_past_the_bound",
        fixture_arg("g13_slot.dwg"),
        ["--max-polyline", 3],
        "the control: the same file one vertex over the bound",
    )
    record(
        "limits/max_polyline_8192",
        fixture_arg("g13_wide_polyline.dwg"),
        ["--max-polyline", 8192],
        "the same fixture with the bound above its width",
    )
    record(
        "limits/max_string_4096",
        fixture_arg("g13_long_text.dwg"),
        ["--max-string", 4096],
        "an 8192-byte string, 4096 allowed",
    )
    record(
        "limits/max_string_16384",
        fixture_arg("g13_long_text.dwg"),
        ["--max-string", 16384],
        "the same fixture with the bound above its length",
    )
    record(
        "limits/max_output_2048",
        fixture_arg("g13_many_inserts.dwg"),
        ["--max-output", 2048],
        "ten thousand insertions, 2 KiB of output allowed",
    )
    record(
        "limits/max_block_depth_5",
        fixture_arg("g13_deep_blocks.dwg"),
        ["--max-depth", 5],
        "blocks nested six deep, five allowed",
    )
    record(
        "limits/max_block_depth_7",
        fixture_arg("g13_deep_blocks.dwg"),
        ["--max-depth", 7],
        "the same fixture with the bound past its depth, which is what proves "
        "the refusal is the bound and not a crash",
    )

    # max_input_bytes, and the control that shows the file was never read. The
    # unreadable copy is mode 000 and the container runs as a non-root user, so
    # anything that opened it would fail; refusing on the length cannot.
    record(
        "limits/max_input_below_size",
        fixture_arg("g13_line.dwg"),
        ["--max-input", 1024],
        "the bound is below the file size",
    )
    record(
        "limits/max_input_default",
        fixture_arg("g13_line.dwg"),
        [],
        "the same file with the default bound",
    )
    sealed_source = "tests/fixtures/g13_line.dwg"
    out["scenarios"].append(
        {
            "name": "limits/max_input_below_size_unreadable",
            "input": "a mode 000 copy of g13_line.dwg",
            "args": ["--max-input", "1024"],
            "note": "the bound is below the size and the file cannot be opened, so a "
            "refusal that arrives at all is a refusal that never read it",
            "fixture_sha256": sha256_file(os.path.join(FIXTURES, "g13_line.dwg")),
            "result": decode_unreadable(scratch, sealed_source, "--max-input 1024"),
        }
    )
    out["scenarios"].append(
        {
            "name": "limits/unreadable_control",
            "input": "a mode 000 copy of g13_line.dwg",
            "args": [],
            "note": "the same unreadable copy with the default bound, which has to "
            "fail on the open and is what makes the scenario above mean "
            "something",
            "fixture_sha256": sha256_file(os.path.join(FIXTURES, "g13_line.dwg")),
            "result": decode_unreadable(scratch, sealed_source, ""),
        }
    )
    for name in ("limits/max_input_below_size_unreadable", "limits/unreadable_control"):
        r = [s for s in out["scenarios"] if s["name"] == name][0]["result"]
        print(f"{name}: open={r.get('open_code')} decode={r.get('decode_code')}")

    # The two Criticals the review found, each with the control that proves
    # the refusal is a bound and not a crash.
    #
    # A DIMENSION carries a block of its own and that block can hold another
    # dimension. Walking that by recursion put a file-controlled depth on the
    # CLR stack, and a StackOverflowException cannot be caught, so the export's
    # catch-all never saw it: the runtime calls FailFast and the caller's
    # process dies. The deep fixture is three thousand levels, comfortably past
    # the roughly 2686 frames the recursive walk died at, so a build that still
    # recurses aborts here rather than passing because the fixture was small.
    record(
        "criticals/dimension_chain_deep",
        fixture_arg("g13_dimension_deep.dwg"),
        [],
        "three thousand dimensions nested one inside the next, at default limits",
    )
    record(
        "criticals/dimension_chain_shallow",
        fixture_arg("g13_dimension_shallow.dwg"),
        [],
        "the same shape four deep, which is inside every bound and decodes",
    )

    # An INSERT emits no record, it pushes a frame, so a bound that counted
    # records never saw this one coming: a chain of block records each holding
    # two insertions of the next expands exponentially, emits nothing, and
    # keeps its nesting inside max_block_depth the whole way.
    # The fan-out is built in memory rather than read from a file, because
    # ACadSharp cannot store it: `new Insert(record)` deep-clones a record
    # that belongs to a document, so assembling the chain and then pointing at
    # it clones the whole expansion and never returns. That costs nothing
    # here. The hole was never about a file format; it is about a walk that
    # can do unbounded work while producing nothing.
    fan(
        "criticals/fanout_bounded",
        24,
        2,
        ["--max-entities", 100000],
        "twenty-five block records and forty-nine entities, 2^25 expansions, "
        "stopped by the entity counter",
    )
    fan(
        "criticals/fanout_shallow",
        8,
        2,
        [],
        "the same shape eight deep, which fits inside every default bound and decodes",
    )
    fan(
        "criticals/fanout_cancelled",
        24,
        2,
        ["--cancel"],
        "the flag is already up when the walk starts, and this document never "
        "yields a record, so only a poll inside the walk can notice it",
    )

    # docs/WIRE.md's producer guarantee, measured on the bytes rather than on
    # the primitives one layer above them. The dump is what the walk produced;
    # the encoder is the layer in between, and this is the only artefact that
    # can say what actually left it.
    raw_path = os.path.join(derived, "non_finite_stream.bin")
    non_finite = decode(
        scratch,
        fixture_arg("g13_nan_bulge.dwg"),
        "--batch",
        BATCH_BYTES,
        "--raw",
        "/scratch/derived/non_finite_stream.bin",
    )
    with open(raw_path, "rb") as f:
        stream = f.read()
    non_finite["non_finite_exponents_in_output"] = non_finite_exponents(stream)
    non_finite["non_finite_exponents_in_control"] = non_finite_exponents(
        struct.pack("<dd", float("nan"), float("inf"))
    )
    non_finite["scanned_bytes"] = len(stream)
    out["scenarios"].append(
        {
            "name": "finiteness/nothing_non_finite_reaches_the_wire",
            "input": fixture_arg("g13_nan_bulge.dwg"),
            "args": ["--raw"],
            "note": (
                "every batch the decode produced, scanned for the exponent pattern of a "
                "non-finite double, with a two-double control that carries one of each"
            ),
            "fixture_sha256": sha256_file(os.path.join(FIXTURES, "g13_nan_bulge.dwg")),
            "result": non_finite,
        }
    )
    print(
        "finiteness/nothing_non_finite_reaches_the_wire: "
        f"stream={non_finite['non_finite_exponents_in_output']} "
        f"control={non_finite['non_finite_exponents_in_control']}"
    )

    # A spline's points were never counted. The encoder guarded Polyline and
    # Polygon only, and it guarded after the list existed.
    record(
        "limits/spline_points_4096",
        fixture_arg("g13_wide_spline.dwg"),
        ["--max-polyline", 4096],
        "twenty thousand control points, four thousand allowed",
    )
    record(
        "limits/spline_points_65536",
        fixture_arg("g13_wide_spline.dwg"),
        ["--max-polyline", 65536],
        "the same fixture with the bound above its width",
    )

    # Cancellation, set between two batches and nowhere else.
    record(
        "cancel/after_one_batch",
        fixture_arg("g13_many_inserts.dwg"),
        ["--batch", 4096, "--cancel-after", 1],
        "the flag goes up after the first batch returns",
    )
    record(
        "cancel/never",
        fixture_arg("g13_many_inserts.dwg"),
        ["--batch", 4096],
        "the same decode with the flag left alone",
    )

    # Warnings, each with an input that triggers it.
    record(
        "warnings/unsupported_entity",
        fixture_arg("g13_unsupported.dwg"),
        [],
        "a POINT and a SOLID, neither of which this version flattens",
    )
    record(
        "warnings/reader_notification",
        fixture_arg("real_AC1032.dwg"),
        [],
        "a drawing AutoCAD produced, carrying objects ACadSharp's reader cannot name and reports",
    )
    record(
        "warnings/unresolved_xref",
        fixture_arg("g13_xref.dwg"),
        [],
        "an INSERT of an external reference",
    )
    # A warning this library writes itself, carrying a string the drawing chose
    # the length of. Under a bound below that length the message is cut and the
    # decode finishes; limits/max_string_4096 beside it is the control that a
    # Text record is still refused, because that string is the drawing's own.
    #
    # Measured on the bytes the caller would have received rather than on the
    # canonical dump, for the same reason the finiteness scan is: the cut
    # happens inside the encoder and the dump is the layer above it.
    for label, bound in (("long_warning_truncated", 4096), ("long_warning_fits", 16384)):
        raw_name = f"{label}.bin"
        entry = record(
            f"warnings/{label}",
            fixture_arg("g13_xref_long.dwg"),
            ["--max-string", bound, "--raw", f"/scratch/derived/{raw_name}"],
            f"an 8192-byte reference path in a warning, {bound} bytes of string allowed",
        )
        with open(os.path.join(derived, raw_name), "rb") as f:
            stream = f.read()
        # A control per scan, so a number that is zero because nothing was
        # found is told apart from a number that is zero because the scan
        # stopped working.
        control = ("x" + "\u00e9" * 4).encode()
        entry["result"]["scanned_bytes"] = len(stream)
        entry["result"]["longest_filler_run"] = longest_filler_run(stream)
        entry["result"]["longest_filler_run_in_control"] = longest_filler_run(control)
        entry["result"]["after_longest_filler_run"] = after_longest_filler_run(stream)
        entry["result"]["dangling_lead_bytes"] = dangling_lead_bytes(stream)
        entry["result"]["dangling_lead_bytes_in_control"] = dangling_lead_bytes(control[:-1])
        entry["result"]["truncation_markers"] = occurrences(stream, TRUNCATION_MARKER)
        entry["result"]["truncation_markers_in_control"] = occurrences(
            b"a" + TRUNCATION_MARKER + b"b" + TRUNCATION_MARKER, TRUNCATION_MARKER
        )
        print(
            f"warnings/{label}: run={entry['result']['longest_filler_run']} "
            f"markers={entry['result']['truncation_markers']} "
            f"dangling={entry['result']['dangling_lead_bytes']}"
        )

    # An empty view, and the control that says the code is about geometry and
    # not about the stream being short. Both files carry the same four reader
    # notifications, so a producer that counted records rather than geometry
    # would fire on neither.
    record(
        "warnings/empty_view",
        fixture_arg("g13_empty_view.dwg"),
        [],
        "a drawing with nothing in model space, so the view's stream is the "
        "reader's notifications and no geometry at all",
    )
    record(
        "warnings/empty_view_control",
        fixture_arg("g13_line.dwg"),
        [],
        "the same document with one line in it",
    )
    # The same code on the layout nobody has ever decoded. Every fixture in the
    # corpus has two views and every capture so far is of view 0, so the paper
    # space beside a drawing with geometry in it has never been in a recording.
    record(
        "warnings/empty_view_layout",
        fixture_arg("g13_line.dwg"),
        ["--view", 1],
        "view 1 of a drawing whose geometry is all in model space",
    )

    record(
        "warnings/nonuniform_block_scale",
        fixture_arg("g13_nonuniform.dwg"),
        [],
        "a circle under a block transform that squashes one axis",
    )
    record(
        "warnings/dimension_block",
        fixture_arg("g13_dimension.dwg"),
        [],
        "a dimension whose block carries the lines and the text",
    )

    # The same XREF fixture with no network and a read-only repository. It must
    # complete with the warning rather than reach for the file it names.
    record(
        "warnings/unresolved_xref_sealed",
        fixture_arg("g13_xref.dwg"),
        [],
        "no network, and the repository mounted read-only",
        network="none",
        repo_ro=True,
        read_only=True,
    )

    # The sizes a drawing declares, and what the reader allocates from them.
    #
    # max_input_bytes is applied to the input before the read begins and
    # nothing after it looks at a length again, so every buffer the DWG reader
    # allocates is a number the file chose. Each of these is the smallest
    # fixture in the corpus with one four-byte page-header field rewritten, so
    # the column that matters is alloc_open_bytes beside file_bytes: before the
    # ceiling patch a 10,539-byte file could ask for a gigabyte and get it.
    #
    # Two headers because the reader reaches them in order and refuses at the
    # first, so a single fixture can only ever prove the guard it hits.
    g13 = support()
    with open(os.path.join(FIXTURES, g13.DECLARED_SIZE_SOURCE), "rb") as f:
        pristine = f.read()

    for header, magic in g13.DECLARED_SIZE_HEADERS:
        for label, value in g13.DECLARED_SIZES:
            name = f"{header}_{label}"
            blob = g13.with_declared_size(pristine, magic, value)
            path = os.path.join(derived, f"{name}.dwg")
            with open(path, "wb") as f:
                f.write(blob)
            entry = record(
                f"declared/{name}",
                f"/scratch/derived/{name}.dwg",
                [],
                f"{g13.DECLARED_SIZE_SOURCE} with the {header} page header declaring "
                f"{value} bytes of decompressed data",
            )
            entry["derived"] = {
                "kind": "declared_size",
                "header": header,
                "declared": value,
                "source": g13.DECLARED_SIZE_SOURCE,
                "sha256": sha256_bytes(blob),
                "bytes": len(blob),
            }
            result = entry["result"]
            print(
                f"declared/{name}: open={result.get('open_code')} "
                f"alloc_open={result.get('alloc_open_bytes')}"
            )

    # The control, and it is the whole reason the numbers above mean anything:
    # the same file with nothing rewritten opens, decodes, and allocates what a
    # 10 KB drawing allocates. A refusal that applied to the pristine fixture
    # too would be a ceiling set below the corpus rather than a bound on a lie.
    control = record(
        "declared/unmodified_control",
        fixture_arg(g13.DECLARED_SIZE_SOURCE),
        [],
        "the same fixture with no page header rewritten",
    )
    print(
        f"declared/unmodified_control: open={control['result'].get('open_code')} "
        f"alloc_open={control['result'].get('alloc_open_bytes')}"
    )

    # The two guards in the AC18 descriptor loop, and the reason these inputs
    # are written rather than derived. Both fields live inside the LZ77 stream
    # the data-section map is stored as, so the four-byte rewrite above cannot
    # reach either: the page header in front of that stream is the only part of
    # the page in the clear.
    #
    # tests/ac18_forge.py writes a whole AC1018 file instead, which is possible
    # because nothing between those bytes and the descriptor loop authenticates
    # anything. The 0x6C block the reader calls encrypted is XORed with a
    # keystream a generator seeded with 1 produces, the file ID mismatch inside
    # it is a notification rather than an exception, every CRC is read into a
    # local and dropped, and the AC18 LZ77 has a literal run that copies with no
    # permutation.
    #
    # One of these is recorded patched and has never been run without the
    # guards: a zero page size does not over-allocate, it hangs, because
    # decompressSizeCounter advances by DecompressedSize and the gap fill at
    # DwgReader.cs:929 has nothing else to reach the offset with. `run` below
    # calls subprocess.run with no timeout on purpose, so a hang here is a
    # recorder that never comes back rather than a measurement.
    ac18 = ac18_forge()
    for case in ac18.inputs():
        name = case["name"]
        path = os.path.join(derived, f"{name}.dwg")
        with open(path, "wb") as f:
            f.write(case["blob"])
        entry = record(
            f"declared/{name}",
            f"/scratch/derived/{name}.dwg",
            [],
            f"a forged AC1018 file declaring {case['declared_text']} for its "
            f"{case['site']}, from {case['field']}",
        )
        entry["derived"] = {
            "kind": "ac18_declared_size",
            "guard": case["guard"],
            "site": case["site"],
            "field": case["field"],
            "declared": case["declared"],
            "declared_text": case["declared_text"],
            "source": "tests/ac18_forge.py",
            "sha256": case["sha256"],
            "bytes": case["bytes"],
        }
        result = entry["result"]
        print(
            f"declared/{name}: open={result.get('open_code')} "
            f"alloc_open={result.get('alloc_open_bytes')}"
        )

    # The AC18 control, and the seven above need it: the same forge with every
    # number inside the ceiling reaches both guards, passes both, has its
    # section buffer assembled out of a real page and one zero-filled gap, and
    # is then refused by a header variable the buffer does not contain. A
    # ceiling that refused every forged AC18 map would look identical without
    # this beside it.
    ac18_control = ac18.control_case()
    path = os.path.join(derived, "ac18_control.dwg")
    with open(path, "wb") as f:
        f.write(ac18_control["blob"])
    entry = record(
        "declared/ac18_control",
        "/scratch/derived/ac18_control.dwg",
        [],
        "the same forge with every declared size and offset inside the ceiling",
    )
    entry["derived"] = {
        "kind": "ac18_declared_size",
        "guard": None,
        "site": None,
        "field": "descriptor DecompressedSize",
        "declared": ac18_control["declared"],
        "declared_text": str(ac18_control["declared"]),
        "source": "tests/ac18_forge.py",
        "sha256": ac18_control["sha256"],
        "bytes": ac18_control["bytes"],
    }
    print(
        f"declared/ac18_control: open={entry['result'].get('open_code')} "
        f"alloc_open={entry['result'].get('alloc_open_bytes')}"
    )

    # The four AC21 sites, and the reason these inputs are written rather than
    # derived. `readFileHeader` sends AC1024, AC1027 and AC1032 to the AC18
    # reader, so the one real 2018 drawing in the corpus never touches
    # getPageBuffer or getSectionBuffer21, and upstream's DwgWriter refuses to
    # write AC1021, so the generator that produced every g13_*.dwg cannot make
    # a file that does. The fields that drive these allocations are not in
    # plaintext either: they sit inside an LZ77 stream inside the interleave at
    # offset 0x80, so the four-byte rewrite above reaches none of them.
    #
    # tests/ac21_forge.py builds them instead, which works because upstream's
    # "Reed-Solomon" decode is a stride gather that verifies nothing and the
    # AC21 LZ77 has a literal run. Both transforms are invertible, so the file
    # is a function of the numbers it is supposed to declare.
    forge = ac21_forge()
    for case in forge.inputs():
        name = case["name"]
        path = os.path.join(derived, f"{name}.dwg")
        with open(path, "wb") as f:
            f.write(case["blob"])
        entry = record(
            f"declared/{name}",
            f"/scratch/derived/{name}.dwg",
            [],
            f"a forged AC1021 file declaring {case['declared']} bytes for its "
            f"{case['site']}, from {case['field']}",
        )
        entry["derived"] = {
            "kind": "ac21_declared_size",
            "site": case["site"],
            "field": case["field"],
            "declared": case["declared"],
            "source": "tests/ac21_forge.py",
            "sha256": case["sha256"],
            "bytes": case["bytes"],
        }
        result = entry["result"]
        print(
            f"declared/{name}: open={result.get('open_code')} "
            f"alloc_open={result.get('alloc_open_bytes')}"
        )

    # The AC21 control, and it is what the four above need: the same forge with
    # nothing inflated gets past all four ceilings, the section buffer is built
    # out of a real page, and the refusal comes from the header parser instead.
    # A ceiling that simply refused every AC1021 file would look identical
    # without it.
    ac21_control = forge.control_case()
    path = os.path.join(derived, "ac21_control.dwg")
    with open(path, "wb") as f:
        f.write(ac21_control["blob"])
    entry = record(
        "declared/ac21_control",
        "/scratch/derived/ac21_control.dwg",
        [],
        "the same forge with every declared size inside the ceiling",
    )
    entry["derived"] = {
        "kind": "ac21_declared_size",
        "site": None,
        "field": "page DecompressedSize",
        "declared": ac21_control["declared"],
        "source": "tests/ac21_forge.py",
        "sha256": ac21_control["sha256"],
        "bytes": ac21_control["bytes"],
    }
    print(
        f"declared/ac21_control: open={entry['result'].get('open_code')} "
        f"alloc_open={entry['result'].get('alloc_open_bytes')}"
    )

    # The real 2018 drawing, unmodified. It was taken for this corpus's AC21
    # fixture and it is not one: it goes through readFileHeaderAC18 like every
    # other AC1024 and later file. Recorded here so that is a capture rather
    # than a reading of a switch statement.
    real = record(
        "declared/real_ac1032_control",
        fixture_arg("real_AC1032.dwg"),
        [],
        "a drawing AutoCAD wrote, on the AC18 path, with the ceiling in place",
    )
    print(
        f"declared/real_ac1032_control: open={real['result'].get('open_code')} "
        f"alloc_open={real['result'].get('alloc_open_bytes')}"
    )

    # Malformed derivatives, derived here and written to the scratch directory
    # so the fixture in the tree is never the thing that was mutilated.
    with open(os.path.join(FIXTURES, MALFORMED_SOURCE), "rb") as f:
        source = f.read()

    for pct in TRUNCATIONS:
        name = f"truncated_{pct}"
        blob = truncate(source, pct)
        path = os.path.join(derived, f"{name}.dwg")
        with open(path, "wb") as f:
            f.write(blob)
        entry = record(
            f"malformed/{name}",
            f"/scratch/derived/{name}.dwg",
            [],
            f"{MALFORMED_SOURCE} cut at {pct} percent",
        )
        entry["derived"] = {
            "kind": "truncation",
            "percent": pct,
            "sha256": sha256_bytes(blob),
            "bytes": len(blob),
        }

    flipped, positions = bitflip(source)
    path = os.path.join(derived, "bitflip_64.dwg")
    with open(path, "wb") as f:
        f.write(flipped)
    entry = record(
        "malformed/bitflip_64",
        "/scratch/derived/bitflip_64.dwg",
        [],
        f"{MALFORMED_SOURCE} with {FLIP_COUNT} bytes flipped under seed {FLIP_SEED}",
    )
    entry["derived"] = {
        "kind": "bitflip",
        "count": FLIP_COUNT,
        "seed": FLIP_SEED,
        "positions": positions,
        "touches_signature": any(p < 6 for p in positions),
        "sha256": sha256_bytes(flipped),
        "bytes": len(flipped),
    }

    with open(os.path.join(CAPTURES, "g13_scenarios.json"), "w") as f:
        json.dump(out, f, indent=2, sort_keys=True)
        f.write("\n")


def ratio(numerator, denominator):
    """Recorded to three decimals so the file diffs readably.

    Both halves are recorded beside it, and the test recomputes the division
    rather than trusting this: a ratio that stopped matching its own numerator
    would otherwise be the one number nothing checks.
    """
    if not numerator or not denominator:
        return None
    return round(numerator / denominator, 3)


def benchmarks(scratch):
    out = {
        "recorded": time.strftime("%Y-%m-%d"),
        "runner": f"fixturegen decode, {SDK_IMAGE}, {PLATFORM}",
        "batch_bytes": BATCH_BYTES,
        "host": "Apple Silicon, arm64 containers, one scenario per process",
    }

    amp = decode(scratch, fixture_arg("g13_many_inserts.dwg"), "--batch", BATCH_BYTES)
    one = decode(scratch, fixture_arg("g13_insert.dwg"), "--batch", BATCH_BYTES)
    # alloc_decode_bytes is the churn number, and the ratio beside it is what
    # test_adapter_benchmarks.py puts a ceiling on. The open has been measured
    # this way since the corpus was first recorded and the decode never was,
    # which left "the decoder allocates three bytes for every byte it emits" as
    # a claim with retention and peak RSS beside it, neither of which is about
    # allocation at all.
    out["amplification"] = {
        "fixture": "g13_many_inserts.dwg",
        "fixture_sha256": sha256_file(os.path.join(FIXTURES, "g13_many_inserts.dwg")),
        "file_bytes": amp.get("file_bytes"),
        "output_bytes": amp.get("output_bytes"),
        "batches": amp.get("batches"),
        "peak_rss_kb": amp.get("peak_rss_kb"),
        "rss_after_begin_kb": amp.get("rss_after_begin_kb"),
        "rss_peak_during_decode_kb": amp.get("rss_peak_during_decode_kb"),
        "managed_retained_kb": amp.get("managed_retained_kb"),
        "alloc_decode_bytes": amp.get("alloc_decode_bytes"),
        "alloc_decode_per_output_byte": ratio(
            amp.get("alloc_decode_bytes"), amp.get("output_bytes")
        ),
        "decode_micros": amp.get("decode_micros"),
        "single_instance": {
            "fixture": "g13_insert.dwg",
            "output_bytes": one.get("output_bytes"),
        },
    }

    # Five pairs rather than one. Peak RSS on a managed runtime moves by a
    # couple of megabytes between identical runs, and the caller's copy of the
    # largest fixture in the corpus is 175 KB, so a single pair would be
    # reporting the collector's mood. The allocation numbers beside them are
    # the exact version of the same claim.
    runs = []
    for _ in range(5):
        p = decode(scratch, fixture_arg("g13_many_inserts.dwg"))
        m = decode(scratch, fixture_arg("g13_many_inserts.dwg"), "--memory")
        runs.append(
            {
                "path_peak_rss_kb": p.get("peak_rss_kb"),
                "memory_peak_rss_kb": m.get("peak_rss_kb"),
                "peak_delta_kb": (m.get("peak_rss_kb") or 0) - (p.get("peak_rss_kb") or 0),
                "path_alloc_open_bytes": p.get("alloc_open_bytes"),
                "memory_alloc_open_bytes": m.get("alloc_open_bytes"),
                "alloc_delta_bytes": (m.get("alloc_open_bytes") or 0)
                - (p.get("alloc_open_bytes") or 0),
                "path_caller_copy_bytes": p.get("caller_copy_bytes"),
                "memory_caller_copy_bytes": m.get("caller_copy_bytes"),
            }
        )

    def median(values):
        v = sorted(values)
        return v[len(v) // 2]

    # And the same pair on an input big enough for peak RSS to see it.
    #
    # The corpus has no 32 MB drawing because ACadSharp's writer costs minutes
    # to produce one, so this is the largest fixture with 32 MiB of
    # pseudorandom bytes appended, built here and never committed. The padding
    # is not read: the decode produces the same record bytes as the unpadded
    # fixture, which is asserted rather than assumed. What it changes is the
    # size of the caller's copy in the memory case, which is the whole thing
    # the claim is about.
    padded = os.path.join(scratch, "out", "derived", "g13_padded.dwg")
    os.makedirs(os.path.dirname(padded), exist_ok=True)
    with open(os.path.join(FIXTURES, "g13_many_inserts.dwg"), "rb") as f:
        base = f.read()
    rng = random.Random(FLIP_SEED)
    chunk = bytes(rng.getrandbits(8) for _ in range(1 << 20))
    with open(padded, "wb") as f:
        f.write(base)
        for _ in range(32):
            f.write(chunk)

    padded_runs = []
    for _ in range(3):
        p = decode(scratch, "/out/derived/g13_padded.dwg")
        m = decode(scratch, "/out/derived/g13_padded.dwg", "--memory")
        padded_runs.append(
            {
                "path_peak_rss_kb": p.get("peak_rss_kb"),
                "memory_peak_rss_kb": m.get("peak_rss_kb"),
                "peak_delta_kb": (m.get("peak_rss_kb") or 0) - (p.get("peak_rss_kb") or 0),
                "path_output_bytes": p.get("output_bytes"),
                "memory_output_bytes": m.get("output_bytes"),
                "path_alloc_open_bytes": p.get("alloc_open_bytes"),
                "memory_alloc_open_bytes": m.get("alloc_open_bytes"),
                "alloc_delta_bytes": (m.get("alloc_open_bytes") or 0)
                - (p.get("alloc_open_bytes") or 0),
            }
        )
    padded_bytes = os.path.getsize(padded)

    file_bytes = runs and decode(scratch, fixture_arg("g13_many_inserts.dwg"), "--no-decode").get(
        "file_bytes"
    )
    out["path_versus_memory"] = {
        "fixture": "g13_many_inserts.dwg",
        "fixture_sha256": sha256_file(os.path.join(FIXTURES, "g13_many_inserts.dwg")),
        "file_bytes": file_bytes,
        "runs": runs,
        "median_peak_delta_kb": median([r["peak_delta_kb"] for r in runs]),
        "min_peak_delta_kb": min(r["peak_delta_kb"] for r in runs),
        "median_alloc_delta_bytes": median([r["alloc_delta_bytes"] for r in runs]),
        "min_alloc_delta_bytes": min(r["alloc_delta_bytes"] for r in runs),
        "padded": {
            "input": "g13_many_inserts.dwg with 32 MiB of pseudorandom bytes appended, "
            "built at measurement time and not committed",
            "file_bytes": padded_bytes,
            "file_kb": padded_bytes // 1024,
            "runs": padded_runs,
            "median_peak_delta_kb": median([r["peak_delta_kb"] for r in padded_runs]),
            "min_peak_delta_kb": min(r["peak_delta_kb"] for r in padded_runs),
            "median_alloc_delta_bytes": median([r["alloc_delta_bytes"] for r in padded_runs]),
            "unpadded_output_bytes": None,
        },
    }
    out["path_versus_memory"]["padded"]["unpadded_output_bytes"] = out["amplification"][
        "output_bytes"
    ]

    streaming = []
    for name in ("g13_scale_1x.dwg", "g13_scale_4x.dwg", "g13_scale_16x.dwg"):
        r = decode(scratch, fixture_arg(name), "--batch", BATCH_BYTES)
        streaming.append(
            {
                "fixture": name,
                "batches": r.get("batches"),
                "output_bytes": r.get("output_bytes"),
                "rss_after_begin_kb": r.get("rss_after_begin_kb"),
                "rss_peak_during_decode_kb": r.get("rss_peak_during_decode_kb"),
                "rss_decode_growth_kb": r.get("rss_decode_growth_kb"),
                "managed_after_begin_kb": r.get("managed_after_begin_kb"),
                "managed_after_decode_kb": r.get("managed_after_decode_kb"),
                "managed_retained_kb": r.get("managed_retained_kb"),
                "alloc_decode_bytes": r.get("alloc_decode_bytes"),
                "alloc_decode_per_output_byte": ratio(
                    r.get("alloc_decode_bytes"), r.get("output_bytes")
                ),
                "decode_micros": r.get("decode_micros"),
            }
        )
    out["streaming"] = streaming

    with open(os.path.join(BENCHMARKS, "amplification.json"), "w") as f:
        json.dump(out, f, indent=2, sort_keys=True)
        f.write("\n")


if __name__ == "__main__":
    sys.exit(main())
