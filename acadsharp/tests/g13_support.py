"""Shared readers for the G1.3 artefacts, and the malformed derivation.

pytest here has no .NET and is not getting one (ADR 0001), so what these tests
read is what a container recorded: the canonical record dumps under
``tests/expectations``, the scenario capture beside them and the benchmark
under ``tests/benchmarks``. That only means something if a capture is provably
a run of the file in the tree, so every capture carries the sha256 of its
fixture and every test that uses one checks it. The other half of "provably a
run of" is the code: ``shim_sources`` and ``shim_digest`` below are what
``tests/expectations/MANIFEST.json`` records the shim with, and
``test_shim_digest.py`` is what refuses a stale one.

Regenerating all of it is ``tests/fixtures/gen/regenerate.py``.
"""

import hashlib
import json
import os
import random
import re

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ACAD_ROOT = os.path.dirname(TESTS_DIR)
FIXTURES = os.path.join(TESTS_DIR, "fixtures")
EXPECTATIONS = os.path.join(TESTS_DIR, "expectations")
BENCHMARKS = os.path.join(TESTS_DIR, "benchmarks")
GEN_DIR = os.path.join(FIXTURES, "gen")
GEN_PROJECT = os.path.join(GEN_DIR, "Viprs.ACadSharp.FixtureGen.csproj")

MANIFEST = os.path.join(EXPECTATIONS, "MANIFEST.json")
SCENARIOS = os.path.join(EXPECTATIONS, "g13_scenarios.json")
AMPLIFICATION = os.path.join(BENCHMARKS, "amplification.json")
DECISION = os.path.join(BENCHMARKS, "DECISION.md")

# The record types docs/WIRE.md names, as the dump spells them. Four of them
# are curves and stay curves.
RECORD_KINDS = (
    "Line",
    "Polyline",
    "Arc",
    "Circle",
    "Ellipse",
    "Spline",
    "Text",
    "Polygon",
    "Warning",
)
CURVE_KINDS = ("Arc", "Circle", "Ellipse", "Spline")

# The derivation the malformed tests apply, kept here so the test and the
# runner that recorded the results cannot drift apart.
MALFORMED_SOURCE = "g13_insert.dwg"
TRUNCATIONS = (25, 50, 90)
FLIP_COUNT = 64
FLIP_SEED = 4713

LINE_RE = re.compile(r"^(\d{5}) ([A-Za-z0-9]+) handle=([0-9A-F]+) flags=(\d+)(.*)$")


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


# One <Compile Include="..."/> line of the fixture generator's csproj.
COMPILE_INCLUDE = re.compile(r'<Compile\s+Include="([^"]+)"\s*/>')


def shim_sources():
    """Every source the fixture generator compiles, relative to acadsharp/.

    Read out of the generator's csproj rather than listed here. The set that
    matters is the set that actually compiled into the run that recorded the
    captures, and a file added to the shim is picked up by the same glob the
    generator uses, so it lands in the digest the day it lands in the tree.

    An include this reader does not understand is an error rather than a
    skip: a pattern silently dropped here is a source file silently outside
    the digest, which is the failure this whole file exists to stop.
    """
    with open(GEN_PROJECT) as f:
        includes = COMPILE_INCLUDE.findall(f.read())
    if not includes:
        raise AssertionError(f"{GEN_PROJECT} compiles nothing this reader can see")

    found = []
    for include in includes:
        pattern = include.replace("\\", "/")
        if pattern.endswith("/**/*.cs"):
            root = os.path.normpath(os.path.join(GEN_DIR, pattern[: -len("/**/*.cs")]))
            for dirpath, dirs, files in os.walk(root):
                dirs[:] = sorted(dirs)
                found += [os.path.join(dirpath, f) for f in files if f.endswith(".cs")]
        elif pattern.endswith(".cs") and "*" not in pattern:
            found.append(os.path.normpath(os.path.join(GEN_DIR, pattern)))
        else:
            raise AssertionError(
                f"{pattern!r} is a <Compile Include> this reader does not understand, "
                "so the shim digest would quietly stop covering it. Teach "
                "g13_support.shim_sources() the shape before shipping it."
            )

    return sorted(os.path.relpath(p, ACAD_ROOT).replace(os.sep, "/") for p in found)


def shim_digest(sources=None):
    """One sha256 over the sorted source paths and the bytes behind them.

    The path goes into the hash as well as the contents, so moving a file
    without changing a byte of it still moves the number.
    """
    h = hashlib.sha256()
    for rel in shim_sources() if sources is None else sources:
        h.update(rel.encode())
        h.update(b"\0")
        h.update(sha256_file(os.path.join(ACAD_ROOT, rel)).encode())
        h.update(b"\0")
    return h.hexdigest()


def load_json(path):
    with open(path) as f:
        return json.load(f)


def manifest():
    return load_json(MANIFEST)


def scenarios():
    return load_json(SCENARIOS)


def scenario(name):
    for s in scenarios()["scenarios"]:
        if s["name"] == name:
            return s
    raise AssertionError(f"no scenario named {name!r} in {SCENARIOS}")


def amplification():
    return load_json(AMPLIFICATION)


def expectation_path(fixture):
    return os.path.join(EXPECTATIONS, os.path.splitext(fixture)[0] + ".txt")


def read_expectation(fixture):
    with open(expectation_path(fixture)) as f:
        return [line for line in f.read().splitlines() if line]


def parse(line):
    """One dumped record as a dict, or None when the line is not one."""
    m = LINE_RE.match(line)
    if not m:
        return None
    return {
        "index": int(m.group(1)),
        "kind": m.group(2),
        "handle": m.group(3),
        "flags": int(m.group(4)),
        "rest": m.group(5),
    }


def records(fixture):
    out = []
    for line in read_expectation(fixture):
        r = parse(line)
        assert r is not None, f"{fixture}: unparseable record line {line!r}"
        out.append(r)
    return out


def kinds(fixture):
    counts = {}
    for r in records(fixture):
        counts[r["kind"]] = counts.get(r["kind"], 0) + 1
    return counts


def warnings(fixture):
    out = []
    for r in records(fixture):
        if r["kind"] != "Warning":
            continue
        m = re.search(r' code=(\S+) message="((?:[^"\\]|\\.)*)"', r["rest"])
        assert m, f"{fixture}: a Warning record with no code and message: {r['rest']!r}"
        out.append({"code": m.group(1), "message": m.group(2), "handle": r["handle"]})
    return out


TRIPLE_RE = re.compile(r"\(([-0-9.]+),([-0-9.]+),([-0-9.]+)\)")


def vertex_record(rest):
    """A dumped Polyline or Polygon as (points, bulges, normal).

    Records 4 and 9 share a payload in wire version 2, so they share a line and
    they share this reader. `bulges` comes back as an empty list when the
    record carries no array at all, which the dump prints as `bulges=[]`: that
    is a different statement from "every bulge is zero" and a test that could
    not tell them apart would not notice the array being dropped.
    """
    fields = {}
    for name in ("pts", "normal"):
        m = re.search(rf" {name}=\[([^\]]*)\]", rest)
        assert m, f"no {name}= in {rest!r}"
        found = TRIPLE_RE.findall(m.group(1))
        fields[name] = [(float(a), float(b), float(c)) for a, b, c in found]

    m = re.search(r" bulges=\[([^\]]*)\]", rest)
    assert m, f"no bulges= in {rest!r}"
    body = m.group(1).strip()
    bulges = [float(x) for x in body.split(",")] if body else []

    assert len(fields["normal"]) == 1, "a record carries one normal"
    return fields["pts"], bulges, fields["normal"][0]


def closed_flag(rest):
    m = re.search(r" closed=(\d+)", rest)
    assert m, f"no closed= in {rest!r}"
    return int(m.group(1))


def arc_midpoint(start, end, bulge, normal):
    """docs/WIRE.md's midpoint formula, written out here rather than imported.

    `mid + (b * c / 2) * (d_hat x normal)`. The cross product is the whole of
    the sign convention, so it is spelled out: a test that called a helper
    which shared code with the producer would agree with the producer by
    construction and say nothing about the document.
    """
    dx, dy, dz = (end[0] - start[0], end[1] - start[1], end[2] - start[2])
    chord = (dx * dx + dy * dy + dz * dz) ** 0.5
    d = (dx / chord, dy / chord, dz / chord)
    cross = (
        d[1] * normal[2] - d[2] * normal[1],
        d[2] * normal[0] - d[0] * normal[2],
        d[0] * normal[1] - d[1] * normal[0],
    )
    k = bulge * chord / 2.0
    mid = ((start[0] + end[0]) / 2.0, (start[1] + end[1]) / 2.0, (start[2] + end[2]) / 2.0)
    return (mid[0] + k * cross[0], mid[1] + k * cross[1], mid[2] + k * cross[2])


def distance(a, b):
    return sum((a[i] - b[i]) ** 2 for i in range(3)) ** 0.5


def first_difference(expected, actual):
    """The first record two dumps disagree about, or None.

    The same rule the C# side applies, written twice on purpose: this is what
    turns "the expectation is stale" into a line number rather than into a wall
    of diff.
    """
    n = min(len(expected), len(actual))
    for i in range(n):
        if expected[i] != actual[i]:
            return f"record {i} differs\n  expected: {expected[i]}\n  actual:   {actual[i]}"
    if len(expected) != len(actual):
        i = n
        if len(expected) > len(actual):
            tail = f"expected: {expected[i]}\n  actual:   <end of stream>"
        else:
            tail = f"expected: <end of stream>\n  actual:   {actual[i]}"
        return (
            f"record {i} differs, the streams are {len(expected)} and {len(actual)} "
            f"records long\n  {tail}"
        )
    return None


def truncate(data, percent):
    return data[: max(1, (len(data) * percent) // 100)]


def bitflip(data, count=FLIP_COUNT, seed=FLIP_SEED):
    """`count` bytes flipped where a fixed seed puts them.

    The positions come back too, because a flip inside the six-byte version
    signature is a different refusal from one in the body, and a test that
    cannot tell them apart is asserting on luck.
    """
    rng = random.Random(seed)
    out = bytearray(data)
    positions = []
    for _ in range(count):
        p = rng.randrange(len(out))
        out[p] ^= 1 << rng.randrange(8)
        positions.append(p)
    return bytes(out), positions


def derived_inputs():
    """Every malformed derivative, derived here from the committed fixture."""
    with open(os.path.join(FIXTURES, MALFORMED_SOURCE), "rb") as f:
        data = f.read()

    out = []
    for pct in TRUNCATIONS:
        d = truncate(data, pct)
        out.append(
            {
                "name": f"truncated_{pct}",
                "scenario": f"malformed/truncated_{pct}",
                "bytes": len(d),
                "sha256": sha256_bytes(d),
            }
        )

    flipped, positions = bitflip(data)
    out.append(
        {
            "name": "bitflip_64",
            "scenario": "malformed/bitflip_64",
            "bytes": len(flipped),
            "sha256": sha256_bytes(flipped),
            "positions": positions,
            "touches_signature": any(p < 6 for p in positions),
        }
    )
    return out
