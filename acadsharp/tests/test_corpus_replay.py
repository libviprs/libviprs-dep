"""The corpus replay, and the parts of it that have to be able to fail.

`test_shim_digest.py` binds the committed dumps to the sources that recorded
them, and on its own that binding is forgeable: the manifest's shim block is
computed from the source tree by the same `g13_support` helpers the test
verifies it with, so four lines rewrite it and the whole adapter suite returns
to exactly its old colour over expectations recording behaviour the shim no
longer has. Measured, with #82's refusal applied to `Flattener.cs`:
`test_shim_digest.py` and `test_adapter_stream.py` were 477 green while the
shim had stopped emitting two of the records they assert on.

Only running the decoder closes that, and `tests/fixtures/gen/replay.py` is
what runs it: every fixture the manifest names, decoded in the builder image,
compared against the dump beside it by `fixturegen decode --check`. That needs
.NET and a container, so it is a CI step rather than a test here.

What is here is the half of it pytest can hold: the driver replays everything
committed rather than a list somebody typed, and its comparison refuses each
of the ways a replay can go wrong. A guard whose own failure path has never
run is the shape this whole file exists to stop.
"""

import json
import os
import re
import types

import pytest
from g13_support import load_script, manifest

HERE = os.path.dirname(os.path.abspath(__file__))
DRIVER = os.path.join(HERE, "fixtures", "gen", "replay.py")
WORKFLOW = os.path.join(HERE, "..", "..", ".github", "workflows", "acadsharp-conformance.yml")


replay = load_script(DRIVER, "replay")
MANIFEST = manifest()


def transcript(blocks):
    """What the container prints, assembled the way the driver reads it."""
    out = []
    for name, body, code in blocks:
        out.append(replay.FIXTURE_MARKER + name)
        out.append(body)
        out.append(replay.EXIT_MARKER + str(code))
    return "\n".join(out) + "\n"


class TestItReplaysEverythingThatIsCommitted:
    """A replay of a subset is a subset of a guard.

    The list comes out of the manifest rather than out of the driver, for the
    same reason `shim_sources` reads the csproj: the set that matters is the
    set the repository actually carries, and a fixture added to it is covered
    the day it lands rather than the day somebody edits a second list.
    """

    def test_it_replays_every_fixture_the_manifest_names(self):
        assert replay.fixtures(MANIFEST) == sorted(MANIFEST["fixtures"])

    def test_it_names_no_fixture_of_its_own(self):
        # A literal fixture name, which the shell's `${fixture%.dwg}` is not.
        with open(DRIVER) as f:
            named = re.findall(r"\w+\.dwg", f.read())
        assert not named, f"the driver spells {named} out, so its list and the manifest's can drift"

    def test_the_batch_size_is_the_one_the_dumps_were_recorded_at(self):
        cmd = replay.docker_cmd("an-image", ["g13_line.dwg"], MANIFEST["batch_bytes"])
        assert f"VIPRS_REPLAY_BATCH={MANIFEST['batch_bytes']}" in cmd

    def test_every_fixture_reaches_the_container_as_an_argument(self):
        names = replay.fixtures(MANIFEST)
        cmd = replay.docker_cmd("an-image", names, 65536)
        assert cmd[-len(names) :] == names

    def test_it_compares_rather_than_rewrites(self):
        with open(DRIVER) as f:
            code = f.read()
        assert "--check" in code, "the driver does not run the comparison at all"
        assert "--dump" not in code, (
            "the driver writes the dumps it is supposed to be checking, which turns "
            "every stale expectation into a silent pass"
        )


class TestTheComparisonRefusesEachWayAReplayGoesWrong:
    """Five failure modes, five separate refusals.

    They are worth separating because they mean different things: a decode
    that printed nothing, a decode that exited non-zero, a walk that threw
    part way through, a run that compared nothing at all, and a stream that
    is simply not what was recorded. One shared "not identical" message would
    report the last one for all five.
    """

    def test_an_identical_stream_is_not_a_problem(self):
        assert replay.verdict("g13_line.dwg", {"check": "identical", "dump_error": None}, 0) is None

    def test_a_differing_stream_is_named_with_the_first_differing_record(self):
        out = replay.verdict(
            "real_AC1018.dwg",
            {"check": "record 63 differs\n  expected: x\n  actual: y", "dump_error": None},
            0,
        )
        assert out is not None
        assert "real_AC1018.dwg" in out and "record 63 differs" in out

    def test_a_decode_that_printed_nothing_is_a_problem(self):
        out = replay.verdict("g13_line.dwg", None, 134)
        assert out is not None and "134" in out

    def test_a_non_zero_exit_is_a_problem_even_when_the_check_says_identical(self):
        out = replay.verdict("g13_line.dwg", {"check": "identical", "open_code": "IO_ERROR"}, 3)
        assert out is not None and "IO_ERROR" in out

    def test_a_walk_that_threw_is_a_problem(self):
        out = replay.verdict("g13_line.dwg", {"check": "identical", "dump_error": "LIMIT"}, 0)
        assert out is not None and "LIMIT" in out

    def test_a_run_that_compared_nothing_is_a_problem(self):
        # The one that would otherwise read as a pass: --check dropped from
        # the command line leaves a decode that succeeds and says nothing.
        out = replay.verdict("g13_line.dwg", {"dump_error": None}, 0)
        assert out is not None and "nothing was compared" in out


class TestTheReaderSurvivesWhatTheContainerPrints:
    """The builder image's python3 is python3-minimal and has no json module,
    so the loop runs in sh and the reading happens on this side. That makes
    the marker protocol load-bearing."""

    def test_two_fixtures_come_back_as_two_results(self):
        text = transcript(
            [
                ("g13_line.dwg", json.dumps({"check": "identical"}), 0),
                ("g13_arc.dwg", json.dumps({"check": "identical"}), 0),
            ]
        )
        assert [name for name, _, _ in replay.parse(text)] == ["g13_line.dwg", "g13_arc.dwg"]

    def test_a_block_that_is_not_json_comes_back_rather_than_vanishing(self):
        text = transcript([("g13_line.dwg", "Segmentation fault", 139)])
        parsed = replay.parse(text)
        assert parsed == [("g13_line.dwg", None, 139)]
        assert replay.verdict(*parsed[0]) is not None

    def test_noise_before_the_first_fixture_is_not_a_result(self):
        text = "warning: something\n" + transcript(
            [("g13_line.dwg", json.dumps({"check": "identical"}), 0)]
        )
        assert len(replay.parse(text)) == 1

    def test_a_fixture_that_never_ran_is_not_quietly_absent(self):
        # The whole-run version of the same rule: the driver compares what
        # came back against what it asked for, so a container that died half
        # way through is a failure rather than a short green list.
        names = replay.fixtures(MANIFEST)
        text = transcript([(names[0], json.dumps({"check": "identical"}), 0)])
        seen = [name for name, _, _ in replay.parse(text)]
        assert [n for n in names if n not in seen], "this control needs more than one fixture"


class FakeSubprocess:
    """A stand-in for the driver's `subprocess`, holding one canned run.

    Patched onto the module rather than onto the real `subprocess`, which is
    shared with every other suite in this session, so what this replaces is
    one attribute of one module.
    """

    def __init__(self, stdout="", stderr="", returncode=0):
        self.result = types.SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)
        self.calls = []

    def run(self, cmd, **kwargs):
        self.calls.append(cmd)
        return self.result


def drive(monkeypatch, argv, stdout="", stderr="", returncode=0):
    """`main(argv)` over a canned container transcript, as (exit code, fake)."""
    fake = FakeSubprocess(stdout=stdout, stderr=stderr, returncode=returncode)
    monkeypatch.setattr(replay, "subprocess", fake)
    return replay.main(list(argv)), fake


def every_fixture_identical():
    """The transcript of a run where nothing has drifted."""
    names = replay.fixtures(MANIFEST)
    return names, transcript(
        [(name, json.dumps({"check": "identical", "dump_error": None}), 0) for name in names]
    )


class TestTheDriverTurnsATranscriptIntoAVerdict:
    """`verdict` and `parse` are covered above, one case at a time. This is
    the part that decides the exit code, and CI reads nothing else: a `main`
    that collected every problem correctly and returned 0 would be a replay
    step that cannot fail, with a suite of green unit tests under it.

    The container is the one thing stubbed out, because it is the one thing
    that needs .NET. Everything between its stdout and the exit code is real.
    """

    def test_a_clean_run_exits_zero_and_says_what_it_covered(self, monkeypatch, capsys):
        names, text = every_fixture_identical()
        code, fake = drive(monkeypatch, ["--image", "builder"], stdout=text)
        out = capsys.readouterr().out
        assert code == 0
        assert f"{len(names)} of {len(names)} fixtures replay identical" in out
        assert len(fake.calls) == 1 and fake.calls[0][0] == "docker"

    def test_one_differing_fixture_exits_one_and_names_it(self, monkeypatch, capsys):
        names, _ = every_fixture_identical()
        blocks = []
        for name in names:
            check = (
                "record 4 differs\n  expected: x\n  actual:   y"
                if name == names[0]
                else "identical"
            )
            blocks.append((name, json.dumps({"check": check, "dump_error": None}), 0))
        code, _ = drive(monkeypatch, ["--image", "builder"], stdout=transcript(blocks))
        out = capsys.readouterr().out
        assert code == 1
        assert f"DIFFERS  {names[0]}" in out
        assert "record 4 differs" in out
        assert f"1 of {len(names)} replayed fixtures disagree" in out

    def test_a_container_that_died_half_way_is_not_a_short_green_run(self, monkeypatch, capsys):
        # The failure the printed list cannot show on its own: everything that
        # came back was identical, and most of the corpus never ran. A driver
        # that reported on what it received would exit 0 here.
        names = replay.fixtures(MANIFEST)
        text = transcript([(names[0], json.dumps({"check": "identical"}), 0)])
        code, _ = drive(monkeypatch, ["--image", "builder"], stdout=text, returncode=137)
        out = capsys.readouterr().out
        assert code == 1
        assert "never ran at all" in out
        assert f"{len(names) - 1} fixture(s)" in out

    def test_a_container_that_failed_after_a_clean_replay_is_still_a_failure(
        self, monkeypatch, capsys
    ):
        # Every fixture came back identical and the container itself exited
        # non-zero, which is a build or a mount that went wrong after the
        # comparisons. Reported rather than swallowed.
        _names, text = every_fixture_identical()
        code, _ = drive(monkeypatch, ["--image", "builder"], stdout=text, returncode=1)
        assert code == 1
        assert "the replay container exited 1" in capsys.readouterr().err

    def test_the_container_stderr_reaches_the_log(self, monkeypatch, capsys):
        _names, text = every_fixture_identical()
        drive(monkeypatch, ["--image", "builder"], stdout=text, stderr="CS1002: ; expected\n")
        assert "CS1002" in capsys.readouterr().err

    def test_plan_prints_the_command_and_runs_nothing(self, monkeypatch, capsys):
        code, fake = drive(monkeypatch, ["--image", "builder", "--plan"])
        out = capsys.readouterr().out
        assert code == 0
        assert fake.calls == [], "--plan ran the container it was asked to describe"
        assert out.startswith("docker run --rm --platform")
        assert "builder" in out

    def test_a_manifest_naming_no_fixture_replays_nothing_and_says_so(
        self, monkeypatch, capsys, tmp_path
    ):
        # The case that would otherwise be the quietest green in the repo: a
        # driver pointed at an empty manifest runs a container over no
        # arguments, gets a clean transcript back, and prints "0 of 0
        # fixtures replay identical".
        expectations = tmp_path / "tests" / "expectations"
        expectations.mkdir(parents=True)
        (expectations / "MANIFEST.json").write_text(json.dumps({"fixtures": {}}))
        code, fake = drive(monkeypatch, ["--image", "builder", "--acad", str(tmp_path)])
        assert code == 1
        assert fake.calls == []
        assert "replays nothing" in capsys.readouterr().err


class TestCiRunsIt:
    """A driver nothing calls is a script, not a guard.

    The other end of this is in test_conformance_workflow.py, which holds the
    step to the builder image and to its place in the job. This is the cheap
    half: the workflow mentions the driver by the path it lives at.
    """

    def test_the_driver_is_where_the_workflow_looks_for_it(self):
        assert os.path.isfile(DRIVER)

    def test_the_workflow_runs_the_driver(self):
        with open(WORKFLOW) as f:
            text = f.read()
        assert "tests/fixtures/gen/replay.py" in text, (
            "nothing in CI replays the corpus, so every expectation under "
            "tests/expectations is a recording nobody has re-run"
        )


@pytest.mark.parametrize("name", sorted(MANIFEST["fixtures"]))
def test_every_replayed_fixture_has_both_halves_on_disk(name):
    """The replay reads two files per fixture inside the container.

    Checked here rather than discovered as a decode failure at the far end of
    a container that has to build the generator first, because "the dump is
    missing" and "the dump disagrees" are different problems and `--check`
    reports a missing file as an empty expectation, which is a difference at
    record 0.
    """
    stem = os.path.splitext(name)[0]
    assert os.path.isfile(os.path.join(HERE, "fixtures", name))
    assert os.path.isfile(os.path.join(HERE, "expectations", stem + ".txt"))
