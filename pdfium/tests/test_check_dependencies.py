"""Tests for check_dependencies preflight logic.

The preflight runs before any build job and refuses to continue if the
host is missing Docker, gh, git, or a valid gh login. Mac-only
invocations are special: the mac build path is native (xcodebuild +
depot_tools, no Docker), and GitHub's macos-15 runner doesn't ship
Docker Desktop — so requiring Docker for a mac-only run would fail the
release workflow's mac job unconditionally. These tests pin the
skip-on-mac behavior and the "require Docker for any non-mac target"
invariant.
"""

import subprocess
from unittest import mock

import build_pdfium as bp
import pytest


@pytest.fixture
def stub_which():
    """Patch shutil.which so it returns False for every binary.

    That is enough to drive check_dependencies into the "missing X"
    branches without actually exec-ing anything on the host.
    """
    with mock.patch.object(bp.shutil, "which", return_value=None) as m:
        yield m


@pytest.fixture
def stub_run_ok():
    """Any subprocess.run call returns rc=0 with empty stdout/stderr.

    Used to satisfy the gh/docker/git sub-checks when we exercise paths
    that expect the binary to be present.
    """
    with mock.patch.object(
        bp.subprocess,
        "run",
        return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
    ) as m:
        yield m


class TestDockerSkipOnMacOnly:
    def test_mac_only_skips_docker(self, stub_which, capsys):
        # Even though `which("docker")` returns None, a mac-only platform
        # list must NOT raise or exit — the native mac build path has no
        # Docker dependency. upload=False so gh/git aren't checked either.
        bp.check_dependencies(upload=False, platforms=["mac"])
        out = capsys.readouterr().out
        assert "docker not found" not in out

    def test_mac_only_set_skips_docker(self, stub_which):
        # Callers may pass a set (resolved job platforms) rather than a
        # list; both must work.
        bp.check_dependencies(upload=False, platforms={"mac"})


class TestDockerRequiredForNonMac:
    def test_linux_requires_docker(self, stub_which, capsys):
        with pytest.raises(SystemExit):
            bp.check_dependencies(upload=False, platforms=["linux"])
        out = capsys.readouterr().out
        assert "docker not found" in out

    def test_musl_requires_docker(self, stub_which, capsys):
        with pytest.raises(SystemExit):
            bp.check_dependencies(upload=False, platforms=["musl"])
        out = capsys.readouterr().out
        assert "docker not found" in out

    def test_mixed_mac_and_linux_requires_docker(self, stub_which, capsys):
        # A mixed run still has a non-mac job that needs Docker, so the
        # docker check must fire even though "mac" appears in the list.
        with pytest.raises(SystemExit):
            bp.check_dependencies(upload=False, platforms=["mac", "linux"])
        out = capsys.readouterr().out
        assert "docker not found" in out

    def test_mixed_mac_and_musl_requires_docker(self, stub_which, capsys):
        with pytest.raises(SystemExit):
            bp.check_dependencies(upload=False, platforms=["mac", "musl"])
        out = capsys.readouterr().out
        assert "docker not found" in out


class TestDefaultPlatformsArgRequiresDocker:
    def test_platforms_none_requires_docker(self, stub_which, capsys):
        # Back-compat: callers that don't pass `platforms` (None) keep
        # the original behavior — Docker is required.
        with pytest.raises(SystemExit):
            bp.check_dependencies(upload=False)
        out = capsys.readouterr().out
        assert "docker not found" in out

    def test_empty_platforms_requires_docker(self, stub_which, capsys):
        # Defensive: an empty list shouldn't silently skip the check.
        with pytest.raises(SystemExit):
            bp.check_dependencies(upload=False, platforms=[])
        out = capsys.readouterr().out
        assert "docker not found" in out


class TestUploadFlagsStillChecked:
    def test_mac_only_with_upload_still_checks_gh(self, stub_which, capsys):
        # Skipping docker for mac-only must NOT skip the --upload
        # preflight: gh/git are needed regardless of build platform.
        with pytest.raises(SystemExit):
            bp.check_dependencies(upload=True, platforms=["mac"])
        out = capsys.readouterr().out
        assert "gh CLI not found" in out
