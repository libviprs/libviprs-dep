#!/usr/bin/env python3
"""Regenerate the G1.3 corpus, expectations, scenario captures and benchmarks.

    python3 acadsharp/tests/fixtures/gen/regenerate.py --plan
    python3 acadsharp/tests/fixtures/gen/regenerate.py --all

Everything below runs inside the pinned .NET SDK container. Nothing here
builds or runs a toolchain on the host: the only host tools it needs are
``docker``, ``python3`` and ``git``, which is the same rule the rest of this
repository builds under.

What it produces, and what reads it back:

* ``tests/fixtures/g13_*.dwg`` - the corpus, committed, because a DWG header
  carries timestamps and a rerun produces different bytes.
* ``tests/expectations/*.txt`` - the canonical record dump per fixture. This is
  the expectation ``test_adapter_stream.py`` compares against, and
  ``fixturegen decode --check`` is what names the first differing record.
* ``tests/fixtures/captures/g13_scenarios.json`` - every limit, refusal and
  malformed-input run, with the result code each one produced.
* ``tests/benchmarks/amplification.json`` - the block-expansion numbers.

pytest has no .NET and is not getting one (ADR 0001), so the committed files
are what the tests read. They are not taken on trust: every capture carries the
sha256 of the fixture it was produced from, and the tests recompute it.
"""

import argparse
import hashlib
import json
import os
import random
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ACAD_ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
REPO_ROOT = os.path.dirname(ACAD_ROOT)
FIXTURES = os.path.join(ACAD_ROOT, "tests", "fixtures")
EXPECTATIONS = os.path.join(ACAD_ROOT, "tests", "expectations")
CAPTURES = os.path.join(FIXTURES, "captures")
BENCHMARKS = os.path.join(ACAD_ROOT, "tests", "benchmarks")

SDK_IMAGE = "mcr.microsoft.com/dotnet/sdk:10.0.401-noble"
PLATFORM = "linux/arm64"
PROJECT = "tests/fixtures/gen/Viprs.ACadSharp.FixtureGen.csproj"
DLL = "/out/artifacts/bin/Viprs.ACadSharp.FixtureGen/debug/fixturegen.dll"

# The fixtures whose whole record stream is committed as an expectation. The
# hostile ones are left out on purpose: g13_many_inserts.dwg decodes to 30004
# records, and a committed dump of those is a file nobody can read in a diff.
# Its shape is pinned by the benchmark instead.
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
    "g13_wide_polyline.dwg",
    "g13_long_text.dwg",
    "g13_scale_1x.dwg",
    "real_AC1032.dwg",
    "real_AC1018.dwg",
    "g13_shapes_from_g11.dwg",
)

# The fixture the malformed derivatives come from, and how they are derived.
# Both numbers are the issue's: truncation at three fractions, and 64 flipped
# bytes under a fixed seed. Deriving at test time rather than committing the
# derivatives means the test cannot drift from the fixture it claims to
# mutilate.
MALFORMED_SOURCE = "g13_insert.dwg"
TRUNCATIONS = (25, 50, 90)
FLIP_COUNT = 64
FLIP_SEED = 4713

LARGE_POINTS = 600000


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def truncate(data, percent):
    return data[: max(1, (len(data) * percent) // 100)]


def bitflip(data, count=FLIP_COUNT, seed=FLIP_SEED):
    """`count` bytes flipped at positions a fixed seed chooses.

    Fixed seed so the derivative is the same file on every run and a failure
    is reproducible. The positions are reported alongside the result, because
    a flip inside the six-byte version signature is a different refusal from a
    flip in the body and the test has to be able to tell which it got.
    """
    rng = random.Random(seed)
    out = bytearray(data)
    positions = sorted(rng.randrange(len(out)) for _ in range(count))
    for p in positions:
        out[p] ^= 1 << rng.randrange(8)
    return bytes(out), positions


def docker_run(scratch, args, network=None, read_only=False, extra_mounts=(), env=()):
    cmd = [
        "docker", "run", "--rm", "--platform", PLATFORM,
        "-v", f"{REPO_ROOT}:/work",
        "-v", f"{scratch}:/scratch",
        "-v", f"{os.path.join(scratch, 'home')}:/home/build",
        "-v", f"{os.path.join(scratch, 'out')}:/out",
        "-e", "HOME=/home/build",
        "-e", "DOTNET_CLI_HOME=/home/build",
        "-e", "DOTNET_CLI_TELEMETRY_OPTOUT=1",
        "-e", "DOTNET_NOLOGO=1",
        "--user", f"{os.getuid()}:{os.getgid()}",
        "-w", "/work/acadsharp",
    ]
    for k in env:
        cmd += ["-e", k]
    for m in extra_mounts:
        cmd += ["-v", m]
    if network:
        cmd += ["--network", network]
    if read_only:
        cmd += ["--read-only", "--tmpfs", "/tmp:exec"]
    cmd += [SDK_IMAGE] + list(args)
    return cmd


def run(cmd, capture=True, check=True):
    proc = subprocess.run(cmd, capture_output=capture, text=True)
    if check and proc.returncode != 0:
        sys.stderr.write(proc.stderr or "")
        raise RuntimeError(f"command failed ({proc.returncode}): {' '.join(cmd)}")
    return proc


def build(scratch, acad_source, plan=False):
    cmd = docker_run(
        scratch,
        [
            "dotnet", "build", PROJECT, "-c", "Debug",
            "-p:AcadSharpProject=/acs/src/ACadSharp/ACadSharp.csproj",
            "--artifacts-path", "/out/artifacts",
        ],
        extra_mounts=[f"{acad_source}:/acs"],
    )
    print(" ".join(cmd))
    if not plan:
        run(cmd, capture=True)


def decode(scratch, path, *args, **kwargs):
    cmd = docker_run(scratch, ["dotnet", DLL, "decode", path] + [str(a) for a in args], **kwargs)
    proc = run(cmd)
    return json.loads(proc.stdout)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--scratch", required=True, help="a writable directory outside the repo")
    parser.add_argument("--acad-source", required=True, help="the unpacked ACadSharp checkout")
    parser.add_argument("--plan", action="store_true", help="print what it would run and stop")
    parser.add_argument("--all", action="store_true", help="run every stage")
    args = parser.parse_args(argv)

    os.makedirs(os.path.join(args.scratch, "home"), exist_ok=True)
    os.makedirs(os.path.join(args.scratch, "out"), exist_ok=True)
    for d in (FIXTURES, EXPECTATIONS, CAPTURES, BENCHMARKS):
        os.makedirs(d, exist_ok=True)

    build(args.scratch, args.acad_source, plan=args.plan)
    if args.plan or not args.all:
        print("nothing else run: pass --all")
        return 0

    print("the stages beyond the build are driven by run_corpus.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
