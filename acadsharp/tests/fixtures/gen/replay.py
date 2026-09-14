#!/usr/bin/env python3
"""Replay every committed expectation through the shim in the tree.

    python3 acadsharp/tests/fixtures/gen/replay.py --image <builder image>

``test_shim_digest.py`` says the sources that recorded the corpus are still
byte for byte what is in the tree. That is a staleness alarm and it is worth
having, but on its own it is forgeable: the manifest's shim block is computed
from the source tree by the same helpers the test verifies it with, so four
lines of Python rewrite it and every expectation goes back to green while
recording behaviour the shim no longer has. Nothing in CI closed that, because
neither conformance consumer opens a fixture or reads an expectation: both
drive ``SyntheticSource`` through ``open_memory``. Nothing on any runner
replayed the corpus.

This is what replays it. It builds the fixture generator in the builder image
the archive build already made, decodes every fixture the manifest names, and
compares the record stream against the committed dump beside it. A dump that
is a recording of code nobody runs any more stops being green here, whatever
the manifest says about it, and ``decode --check`` names the first differing
record rather than printing a wall of diff.

It is deliberately not ``regenerate.py --only expectations``. Regeneration
rewrites the dumps and the manifest, which turns a stale expectation into a
diff somebody has to notice; this rewrites nothing and fails.
"""

import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ACAD_ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
TESTS = os.path.join(ACAD_ROOT, "tests")
MANIFEST = os.path.join(TESTS, "expectations", "MANIFEST.json")

PROJECT = "tests/fixtures/gen/Viprs.ACadSharp.FixtureGen.csproj"
DLL = "/tmp/art/bin/Viprs.ACadSharp.FixtureGen/debug/fixturegen.dll"
PLATFORM = "linux/arm64"

# The fixture name and the exit code come back on marker lines of their own,
# so the JSON between them is parsed rather than grepped. The builder image's
# python3 is python3-minimal and has no json module, so the loop below runs in
# sh and the reading happens here.
FIXTURE_MARKER = "@@@fixture "
EXIT_MARKER = "@@@exit "

REPLAY_SH = f"""
set -eu
cp -r /acadsharp /tmp/acadsharp
cd /tmp/acadsharp
project=$(ls -d /build/ACadSharp-*/src/ACadSharp/ACadSharp.csproj)
dotnet build {PROJECT} -c Debug \\
    -p:AcadSharpProject="$project" --artifacts-path /tmp/art \\
    > /tmp/build.log 2>&1 \\
  || {{ tail -60 /tmp/build.log; exit 1; }}
[ -f {DLL} ] || {{ echo "the build produced no {DLL}" >&2; exit 1; }}
for fixture in "$@"; do
  stem=${{fixture%.dwg}}
  printf '{FIXTURE_MARKER}%s\\n' "$fixture"
  set +e
  dotnet {DLL} decode "tests/fixtures/$fixture" \\
      --check "tests/expectations/$stem.txt" --batch "$VIPRS_REPLAY_BATCH"
  code=$?
  set -e
  printf '{EXIT_MARKER}%s\\n' "$code"
done
"""


def manifest(path=MANIFEST):
    with open(path) as f:
        return json.load(f)


def fixtures(m):
    """Every fixture the manifest carries a dump for, in a fixed order.

    Sorted rather than in manifest order so two runs print the same thing and
    a diff of two logs is about the records rather than about dict ordering.
    """
    return sorted(m["fixtures"])


def docker_cmd(image, names, batch, acad=ACAD_ROOT, platform=PLATFORM):
    return [
        "docker",
        "run",
        "--rm",
        "--platform",
        platform,
        "-v",
        f"{acad}:/acadsharp:ro",
        "-e",
        f"VIPRS_REPLAY_BATCH={batch}",
        "--entrypoint",
        "sh",
        image,
        "-c",
        REPLAY_SH,
        "replay",
    ] + list(names)


def parse(output):
    """The container's stdout as a list of (fixture, result, exit code).

    A fixture whose block holds no readable JSON comes back with a result of
    None rather than being skipped, because a decode that printed nothing is
    the most interesting failure there is and dropping it would read as a
    pass.
    """
    out = []
    fixture = None
    body = []
    for line in output.splitlines():
        if line.startswith(FIXTURE_MARKER):
            fixture = line[len(FIXTURE_MARKER) :].strip()
            body = []
        elif line.startswith(EXIT_MARKER):
            if fixture is None:
                continue
            try:
                result = json.loads("\n".join(body))
            except ValueError:
                result = None
            out.append((fixture, result, int(line[len(EXIT_MARKER) :].strip())))
            fixture = None
        elif fixture is not None:
            body.append(line)
    return out


def verdict(fixture, result, code):
    """What went wrong with one replayed fixture, or None when nothing did.

    Three separate refusals rather than one, because they fail for different
    reasons and a single "not identical" message would hide two of them: a
    decode that never produced a stream at all, a walk that threw part way
    through, and a stream that is simply not what was recorded.
    """
    if result is None:
        return f"{fixture}: the decode printed nothing a reader could parse (exit {code})"
    if code != 0:
        return f"{fixture}: decode exited {code}, open_code={result.get('open_code')!r}"
    if result.get("dump_error"):
        return f"{fixture}: the walk threw: {result['dump_error']}"
    check = result.get("check")
    if check is None:
        return f"{fixture}: the decode reported no check at all, so nothing was compared"
    if check != "identical":
        return f"{fixture}: the committed dump is not what the shim produces now\n  {check}"
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--image",
        required=True,
        help="the builder image the archive build left behind, which already holds "
        "the SDK and the patched upstream checkout",
    )
    parser.add_argument("--platform", default=PLATFORM)
    parser.add_argument("--acad", default=ACAD_ROOT, help="the acadsharp directory to replay")
    parser.add_argument("--plan", action="store_true", help="print the command and stop")
    args = parser.parse_args(argv)

    m = manifest(os.path.join(args.acad, "tests", "expectations", "MANIFEST.json"))
    names = fixtures(m)
    if not names:
        print("the manifest names no fixture, so this replays nothing", file=sys.stderr)
        return 1

    cmd = docker_cmd(
        args.image,
        names,
        m.get("batch_bytes", 65536),
        acad=os.path.abspath(args.acad),
        platform=args.platform,
    )
    if args.plan:
        print(" ".join(cmd))
        return 0

    proc = subprocess.run(cmd, capture_output=True, text=True)
    sys.stderr.write(proc.stderr or "")
    results = parse(proc.stdout)

    seen = [name for name, _, _ in results]
    problems = [v for v in (verdict(*r) for r in results) if v]
    missing = [name for name in names if name not in seen]
    if missing:
        problems.append(f"{len(missing)} fixture(s) never ran at all: {missing}")

    for name, result, code in results:
        state = "identical" if verdict(name, result, code) is None else "DIFFERS"
        print(f"{state:>9}  {name}")

    if problems:
        print()
        for p in problems:
            print(p)
        print(
            f"\n{len(problems)} of {len(names)} replayed fixtures disagree with what is "
            "committed. Either the expectation is stale, in which case rerun "
            "tests/fixtures/gen/regenerate.py --only expectations and read the diff, "
            "or the change under review is not the one that was intended."
        )
        return 1

    if proc.returncode != 0:
        print(f"the replay container exited {proc.returncode}", file=sys.stderr)
        return 1

    print(f"\n{len(results)} of {len(names)} fixtures replay identical to what is committed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
