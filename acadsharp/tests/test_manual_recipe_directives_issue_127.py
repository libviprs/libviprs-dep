"""#127: the MANUAL.md build script and the one the suite runs emit the same directives.

Two copies of the recipe exist on purpose. The fenced block in MANUAL.md is
written for a reader: it uses `serde_json`, which a consumer has. The copy in
`acadsharp/tests/link_consumer/` hand-parses the manifest, because it has to
build offline inside a container with no dependencies. Generating the block
from the fixture would hand a reader the hand-parsing version, which is the
worse document, so the decision is to keep both and hold them to each other.

What they are held to is what matters, and it is not their source text. Five
defects were found in the hand-maintained lines of the block in one review
round and every one of them was green under a check that looked for a token
in the text. So this reads what each script *emits*: the template of every
`println!("cargo:...")` that reaches a dependent's link line, with the
placeholders normalised, as a set. Renaming a metadata key, dropping a
modifier or adding a directive on either side changes the set.

The rpath is not in the set: it belongs to the consumer's own build script and
the MANUAL documents it in prose, which `test_build_link_consumer.py` holds
by token.
"""

import os
import re

ACAD_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
REPO_ROOT = os.path.dirname(ACAD_DIR)
MANUAL = os.path.join(REPO_ROOT, "MANUAL.md")
BUILD_RS = os.path.join(ACAD_DIR, "tests", "link_consumer", "acadsharp-sys", "build.rs")

# The directives that travel to a dependent, plus the metadata that stands in
# for the one that does not. `rustc-env` and `rerun-if-*` are this script's own.
PROPAGATED = re.compile(r"cargo:(rustc-link-search|rustc-link-lib|lib_dir|rustc-link-arg)")


def read(path):
    with open(path) as f:
        return f.read()


def manual_build_script():
    blocks = [
        block
        for block in re.findall(r"```rust\n(.*?)```", read(MANUAL), re.S)
        if "cargo:rustc-link" in block
    ]
    assert len(blocks) == 1, f"MANUAL.md has {len(blocks)} rust blocks emitting link directives"
    return blocks[0]


def emitted_directives(source):
    """The set of normalised `println!("cargo:...")` templates that propagate."""
    found = set()
    for template in re.findall(r'println!\(\s*"(cargo:[^"]*)"', source):
        template = re.sub(r"\{[^}]*\}", "{}", template)
        if PROPAGATED.match(template):
            found.add(template)
    return found


def test_the_manual_and_the_fixture_emit_the_same_directives():
    manual = emitted_directives(manual_build_script())
    fixture = emitted_directives(read(BUILD_RS))
    assert manual, "no propagating directive was found in the MANUAL.md block"
    assert manual == fixture, (
        f"only in MANUAL.md: {sorted(manual - fixture)}; "
        f"only in the fixture: {sorted(fixture - manual)}. "
        "A consumer following the document gets a different link than the suite proves."
    )


def test_the_set_is_not_empty_in_the_places_that_matter():
    # Positive control: the extraction sees all five, so an equality over two
    # empty sets cannot pass for the wrong reason.
    fixture = emitted_directives(read(BUILD_RS))
    for want in (
        "cargo:lib_dir={}",
        "cargo:rustc-link-lib=static:-bundle,+whole-archive={}",
        "cargo:rustc-link-lib=static:-bundle={}",
        "cargo:rustc-link-lib={}",
        "cargo:rustc-link-search=native={}",
    ):
        assert want in fixture


def test_the_mirror_notices_the_defects_it_exists_for():
    # Each of these is a hand edit of the block that a token search lets
    # through, applied to a copy.
    block = manual_build_script()
    original = emitted_directives(block)
    for old, new in (
        ('"cargo:lib_dir={}"', '"cargo:rpath={}"'),
        ("static:-bundle,+whole-archive={}", "static:-bundle={}"),
        ('"cargo:rustc-link-search=native={}"', '"cargo:rustc-link-search={}"'),
    ):
        assert old in block, f"{old!r} is not in the block, so this control is hollow"
        assert emitted_directives(block.replace(old, new, 1)) != original, old
    extra = block.replace(
        'println!("cargo:lib_dir={}"',
        'println!("cargo:rustc-link-arg=-Wl,-rpath,{}", root.display());\n        '
        'println!("cargo:lib_dir={}"',
        1,
    )
    assert emitted_directives(extra) != original
