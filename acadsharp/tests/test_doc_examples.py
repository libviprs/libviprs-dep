"""Every `build_acadsharp.py` command printed in the documentation has to run.

`acadsharp/README.md` documented `--target linux-x64 --static`, which the
driver rejects outright: its one worked example failed on paste. Nobody caught
it because the example is prose, and prose is the one part of this repository
nothing executes.

This is `test_the_build_step_passes_flags_the_driver_accepts` pointed at the
documents instead of the workflow: pull every invocation out of the markdown,
compare its flags against the driver's own `--help`, and compare the values it
passes against the choices the driver declares. No build runs; the flags and
the choices are what the reader copies.
"""

import os
import re
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ACADSHARP = os.path.dirname(HERE)
REPO_ROOT = os.path.dirname(ACADSHARP)
DRIVER = os.path.join(ACADSHARP, "build_acadsharp.py")

DOCS = {
    "acadsharp/README.md": os.path.join(ACADSHARP, "README.md"),
    "MANUAL.md": os.path.join(REPO_ROOT, "MANUAL.md"),
}

# A flag that takes a value, mapped to the driver's declared choices. Anything
# not here is checked for its name only, which is all --help can prove.
CHOICES = {
    "--target": None,  # filled from the driver
    "--platform": None,
    "--arch": None,
}


def driver_help():
    """``build_acadsharp.py --help``, as a reader pasting the command sees it."""
    done = subprocess.run(
        [sys.executable, DRIVER, "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    return done.stdout + done.stderr


def invocations():
    """``(document, command)`` for every build_acadsharp.py line in the docs."""
    found = []
    for label, path in DOCS.items():
        with open(path) as f:
            for line in f.read().splitlines():
                stripped = line.strip()
                if "build_acadsharp.py" not in stripped:
                    continue
                # A command line, not a sentence that names the file.
                if not re.match(r"^(python3?|\$ python3?)\s", stripped):
                    continue
                found.append((label, stripped))
    return found


ALL = invocations()


def declared_choices():
    import build_acadsharp as ba

    return {
        "--target": sorted(ba.TARGETS),
        "--platform": list(ba.PLATFORMS),
        "--arch": ["amd64", "x86_64", "x64", "arm64", "aarch64"],
    }


class TestThereAreExamplesToCheck:
    def test_the_documents_carry_invocations(self):
        # The positive control. Every check below iterates this list, and an
        # empty one passes all of them while proving nothing.
        assert len(ALL) >= 5, (
            f"only {len(ALL)} build_acadsharp.py invocations were found in "
            f"{sorted(DOCS)}: {ALL}. Either the documents stopped showing the reader "
            "how to build, or this extraction stopped seeing them."
        )

    def test_both_documents_are_represented(self):
        seen = {label for label, _ in ALL}
        assert seen == set(DOCS), f"no invocation was found in {sorted(set(DOCS) - seen)}"


@pytest.mark.parametrize("label,command", ALL, ids=[f"{a}: {b}" for a, b in ALL])
class TestEveryInvocationParses:
    def test_every_flag_is_one_the_driver_accepts(self, label, command):
        help_text = driver_help()
        for flag in sorted(set(re.findall(r"(?<!\S)--[a-z][a-z0-9-]*", command))):
            assert flag in help_text, (
                f"{label} shows `{command}`, and build_acadsharp.py does not accept "
                f"{flag}. The example fails on paste."
            )

    def test_every_value_is_one_the_driver_offers(self, label, command):
        choices = declared_choices()
        tokens = command.split()
        for i, token in enumerate(tokens):
            if token not in choices or i + 1 >= len(tokens):
                continue
            value = tokens[i + 1]
            if value.startswith("-"):
                continue
            assert value in choices[token], (
                f"{label} shows `{command}`, and {value!r} is not a {token} the driver "
                f"offers. It accepts {choices[token]}."
            )
