"""An independent statement of what wire 3's three records should say.

The dumps under `tests/expectations/` are what the decoder emitted, so a test
that only compared them with themselves would be asserting that the decoder
agrees with its own last run. These helpers are the other implementation: plain
vector arithmetic written from the definitions in `docs/WIRE.md` and ADR 0003,
with no code shared with the C# and none of its names, used to say what a
fixture's values have to be.

The fixtures insert their blocks under two transforms, one non-uniform and one
mirrored (`Corpus.AddBlockInstances`), and these are the numbers for them.
"""

import math
import re

EPS = 1e-12
ARBITRARY_AXIS_BAND = 1.0 / 64.0


def sub(a, b):
    return tuple(x - y for x, y in zip(a, b))


def add(a, b):
    return tuple(x + y for x, y in zip(a, b))


def scale(a, k):
    return tuple(x * k for x in a)


def dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def cross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def length(a):
    return math.sqrt(dot(a, a))


def unit(a):
    n = length(a)
    return scale(a, 1.0 / n)


def arbitrary_axis_x(normal):
    """The x axis DXF's arbitrary axis algorithm gives an object coordinate system."""
    n = unit(normal)
    if abs(n[0]) < ARBITRARY_AXIS_BAND and abs(n[1]) < ARBITRARY_AXIS_BAND:
        return unit(cross((0.0, 1.0, 0.0), n))
    return unit(cross((0.0, 0.0, 1.0), n))


class Insertion:
    """An INSERT at a point with a per-axis scale and no rotation."""

    def __init__(self, at, xs, ys, zs):
        self.at = at
        self.s = (xs, ys, zs)

    def point(self, p):
        return add(tuple(a * b for a, b in zip(self.s, p)), self.at)

    def vector(self, v):
        return tuple(a * b for a, b in zip(self.s, v))


# Corpus.AddBlockInstances: one non-uniform insertion and one mirrored.
SHEARED = Insertion((40.0, 0.0, 0.0), 3.0, 1.5, 1.0)
MIRRORED = Insertion((80.0, 0.0, 0.0), -2.0, 2.0, 1.0)
IDENTITY = Insertion((0.0, 0.0, 0.0), 1.0, 1.0, 1.0)


def plane_after(insertion, normal):
    """(normal, turn, mirrored) of a plane after an insertion.

    The image of the plane's own two axes decides all three: the side of the
    plane the record faces, how far its x axis has turned from the arbitrary
    axis of that normal, and whether the transform reverses handedness.
    """
    ox = arbitrary_axis_x(normal)
    oy = cross(unit(normal), ox)
    a = insertion.vector(ox)
    b = insertion.vector(oy)
    c = insertion.vector(unit(normal))
    n = cross(a, b)
    mirrored = dot(n, c) < 0.0
    plane = unit(n) if length(n) >= EPS else unit(c)
    out = scale(plane, -1.0) if mirrored else plane
    u = arbitrary_axis_x(out)
    v = cross(out, u)
    return out, math.atan2(dot(a, v), dot(a, u)), mirrored


def point_record(insertion, location, normal, rotation):
    """Record 14's (x, y, z), normal and rotation for a POINT under an insertion."""
    n, turn, mirrored = plane_after(insertion, normal)
    rot = turn - rotation if mirrored else rotation + turn
    return insertion.point(location), n, rot


def unbounded_record(insertion, base, direction):
    """Record 15's base point and unit direction under an insertion."""
    return insertion.point(base), unit(insertion.vector(direction))


# ---------------------------------------------------------------- the dump


def numbers(text):
    return [float(x) for x in re.findall(r"-?\d+\.\d+", text)]


def triple(rest, name):
    m = re.search(name + r"=\[\(([-0-9.]+),([-0-9.]+),([-0-9.]+)\)\]", rest)
    assert m, f"no {name}=[(x,y,z)] in {rest!r}"
    return tuple(float(g) for g in m.groups())


def scalar(rest, name):
    m = re.search(r" " + name + r"=(-?[0-9.]+)", rest)
    assert m, f"no {name}= in {rest!r}"
    return float(m.group(1))


def plain_pair(rest, name):
    m = re.search(" " + name + r"=\[(-?[0-9.]+),(-?[0-9.]+)\]", rest)
    assert m, f"no {name}=[a,b] in {rest!r}"
    return float(m.group(1)), float(m.group(2))


def pairs(rest, name):
    m = re.search(" " + name + r"=\[([^\]]*)\]", rest)
    assert m, f"no {name}=[...] in {rest!r}"
    return [(float(a), float(b)) for a, b in re.findall(r"\(([-0-9.]+),([-0-9.]+)\)", m.group(1))]


def close(a, b, places=5):
    """Equal to the six decimals a dump prints, within a unit in the last one."""
    tol = 10.0**-places
    if isinstance(a, tuple) or isinstance(a, list):
        return len(a) == len(b) and all(close(x, y, places) for x, y in zip(a, b))
    return abs(a - b) <= tol


def dump_angle(a):
    """An angle as the dump would print it, so pi and -pi compare equal."""
    return math.atan2(math.sin(a), math.cos(a))
