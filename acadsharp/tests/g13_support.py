"""Shared readers for the G1.3 artefacts, and the malformed derivation.

pytest here has no .NET and is not getting one (ADR 0001), so what these tests
read is what a container recorded: the canonical record dumps under
``tests/expectations``, the scenario capture beside them and the benchmark
under ``tests/benchmarks``. That only means something if a capture is provably
a run of the file in the tree, so every capture carries the sha256 of its
fixture and every test that uses one checks it.

Regenerating all of it is ``tests/fixtures/gen/regenerate.py``.
"""

import hashlib
import json
import os
import random
import re

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(TESTS_DIR, "fixtures")
EXPECTATIONS = os.path.join(TESTS_DIR, "expectations")
BENCHMARKS = os.path.join(TESTS_DIR, "benchmarks")

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
