"""#171: the release workflow never changes the bytes of a published qpdf tag.

The driver's --upload already makes a draft on an explicit commit and never
passes --clobber, but release-qpdf.yml still created a public release and
uploaded with --clobber, so a re-run (or a REVISION left unbumped) would have
replaced archives somebody may have pinned. qpdf-latest also has to wait until
the versioned release is public, the order the README describes.

The steps are run for real here, with a stand-in `gh` that logs its calls.
"""

import os
import re
import subprocess
import textwrap

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
WF = open(os.path.join(ROOT, ".github", "workflows", "release-qpdf.yml")).read()
TAG = "qpdf-12.4.2-viprs.1"
SHA = "c" * 40

FAKE_GH = """#!/bin/sh
echo "$*" >> "$GH_LOG"
if [ "$1 $2" = "release view" ]; then
  [ "$FAKE_RELEASE" = none ] && exit 1
  echo "$FAKE_RELEASE"
fi
exit 0
"""


def step_script(name):
    """The `run: |` body of the workflow step called `name`, dedented."""
    step = WF.split(f"- name: {name}\n", 1)[1]
    step = re.split(r"\n\s*- name: |\n  [a-z-]+:\n", step, maxsplit=1)[0]
    assert "run: |" in step, f"step '{name}' has no multi-line run block"
    lines = step.split("run: |\n", 1)[1].splitlines()
    return textwrap.dedent("\n".join(lines))


def run_step(tmp_path, name, release):
    """Run one step's script with a fake gh; `release` is none, true (draft) or false."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "gh").write_text(FAKE_GH)
    (bindir / "gh").chmod(0o755)
    for d in ("qpdf", "tools"):
        os.symlink(os.path.join(ROOT, d), tmp_path / d)
    log = tmp_path / "gh.log"
    log.write_text("")
    env = dict(os.environ)
    env.update(
        PATH=f"{bindir}{os.pathsep}{env['PATH']}",
        GH_LOG=str(log),
        FAKE_RELEASE=release,
        GH_TOKEN="x",
        GITHUB_SHA=SHA,
        TAG=TAG,
        VERSION="12.4.2",
        REVISION="1",
    )
    r = subprocess.run(
        ["bash", "-c", step_script(name)], cwd=tmp_path, env=env, capture_output=True, text=True
    )
    return r, log.read_text().splitlines()


def test_171_no_step_replaces_a_release_asset():
    code = [ln for ln in WF.splitlines() if not ln.lstrip().startswith("#")]
    assert not [ln for ln in code if "--clobber" in ln]


def test_171_resolve_version_exposes_the_revision():
    assert "revision: ${{ steps.v.outputs.revision }}" in WF
    assert 'echo "revision=$R" >> "$GITHUB_OUTPUT"' in WF


def test_171_create_release_makes_a_draft_on_the_run_commit(tmp_path):
    r, calls = run_step(tmp_path, "Create or confirm release", "none")
    assert r.returncode == 0, r.stdout + r.stderr
    create = [c for c in calls if c.startswith(f"release create {TAG} ")]
    assert len(create) == 1, calls
    assert "--draft" in create[0] and "--latest=false" in create[0]
    assert f"--target {SHA}" in create[0]
    assert "--title qpdf 12.4.2 (viprs.1)" in create[0]


def test_171_the_workflow_notes_carry_the_revision(tmp_path):
    r, _ = run_step(tmp_path, "Create or confirm release", "none")
    assert r.returncode == 0, r.stdout + r.stderr
    notes = (tmp_path / "release-notes.md").read_text()
    assert "Packaging revision 1 of qpdf 12.4.2" in notes


def test_171_create_release_refuses_a_published_tag(tmp_path):
    r, calls = run_step(tmp_path, "Create or confirm release", "false")
    assert r.returncode != 0
    assert "published" in r.stdout + r.stderr
    assert not [c for c in calls if c.startswith("release create")]


def test_171_create_release_reuses_its_own_draft(tmp_path):
    r, calls = run_step(tmp_path, "Create or confirm release", "true")
    assert r.returncode == 0, r.stdout + r.stderr
    assert not [c for c in calls if c.startswith("release create")]


def test_171_latest_stays_put_while_the_release_is_a_draft(tmp_path):
    r, calls = run_step(tmp_path, "Move qpdf-latest to this release", "true")
    assert r.returncode == 0, r.stdout + r.stderr
    assert not [c for c in calls if c.split()[:2] in (["release", "delete"], ["release", "create"])]
    assert "draft" in r.stdout + r.stderr


def test_171_latest_moves_once_the_release_is_published(tmp_path):
    _, calls = run_step(tmp_path, "Move qpdf-latest to this release", "false")
    assert any(c.startswith(f"release download {TAG} ") for c in calls), calls
