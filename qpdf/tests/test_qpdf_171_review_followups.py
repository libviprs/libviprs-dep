"""#171 review follow-ups: a dirty driver tree is refused, and a version override
cannot silently ride on a REVISION that belongs to another version."""

import os
import re
import subprocess

from test_qpdf_171_mac_minos_metadata import with_info
from test_qpdf_171_release_workflow_guard import step_script


def test_171_a_driver_commit_from_a_dirty_tree_is_refused(tmp_path):
    r = with_info(
        tmp_path,
        "linux",
        lambda t: re.sub(r"driver_commit=.*", "driver_commit=" + "a" * 40 + "-dirty", t),
    )
    assert r.returncode != 0
    assert "driver_commit" in r.stdout + r.stderr


def test_171_a_clean_driver_commit_is_accepted(tmp_path):
    r = with_info(
        tmp_path, "linux", lambda t: re.sub(r"driver_commit=.*", "driver_commit=" + "a" * 40, t)
    )
    assert r.returncode == 0, r.stdout + r.stderr


def resolve(tmp_path, version, revision, requested):
    (tmp_path / "qpdf").mkdir()
    (tmp_path / "qpdf" / "VERSION").write_text(version + "\n")
    (tmp_path / "qpdf" / "REVISION").write_text(revision + "\n")
    out = tmp_path / "out"
    out.write_text("")
    env = dict(os.environ, REQUESTED=requested, GITHUB_OUTPUT=str(out))
    r = subprocess.run(
        ["bash", "-c", step_script("Resolve qpdf version")],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    return r, out.read_text()


def test_171_a_version_override_on_a_nonzero_revision_is_refused(tmp_path):
    r, _ = resolve(tmp_path, "12.4.2", "1", "12.5.0")
    assert r.returncode != 0
    assert "REVISION" in r.stderr


def test_171_a_version_override_on_revision_zero_is_fine(tmp_path):
    r, out = resolve(tmp_path, "12.4.2", "0", "12.5.0")
    assert r.returncode == 0, r.stderr
    assert "tag=qpdf-12.5.0\n" in out


def test_171_an_override_equal_to_the_version_keeps_its_revision(tmp_path):
    r, out = resolve(tmp_path, "12.4.2", "1", "12.4.2")
    assert r.returncode == 0, r.stderr
    assert "tag=qpdf-12.4.2-viprs.1\n" in out


def test_171_no_override_uses_version_and_revision(tmp_path):
    r, out = resolve(tmp_path, "12.4.2", "1", "")
    assert r.returncode == 0, r.stderr
    assert "tag=qpdf-12.4.2-viprs.1\n" in out
