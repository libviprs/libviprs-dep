"""Tests for tools/publish_latest_tag.sh.

Every release workflow in this repo (release-acadsharp.yml,
release-zstd.yml, release.yml) calls this once, after its own build jobs
have all succeeded, to move `<dep>-latest` onto whatever it just
published. One implementation rather than three copies, so these are the
only tests any of the three needs for the moving logic itself; each
workflow's own tests cover only that it calls this script with the right
arguments and gates on the right condition.

`gh` is stubbed rather than real: a stub on PATH records every
invocation to a file this test reads back, which is what lets these run
with no network and no GitHub token, and what lets the "downloaded
nothing" and "gh itself fails" cases be produced on demand rather than
waited for.
"""

import os
import stat
import subprocess

SCRIPT_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "publish_latest_tag.sh")
)

# gh's own reply to `gh release download --pattern <glob> --dir <dir>`:
# write one file per name into $dir, ignoring the pattern, since the
# stub's caller decides what "the release contained these assets" means
# for each test rather than reimplementing glob matching in bash.
GH_STUB = """\
#!/usr/bin/env bash
set -euo pipefail
LOG="$GH_STUB_LOG"
printf '%s\\n' "$*" >> "$LOG"

case "$1 $2" in
  "release download")
    shift 2
    dir=""
    while [ $# -gt 0 ]; do
      case "$1" in
        --dir) dir=$2; shift 2 ;;
        *) shift ;;
      esac
    done
    mkdir -p "$dir"
    for name in ${GH_STUB_DOWNLOAD_NAMES:-}; do
      : > "$dir/$name"
    done
    exit "${GH_STUB_DOWNLOAD_EXIT:-0}"
    ;;
  "release delete")
    exit "${GH_STUB_DELETE_EXIT:-0}"
    ;;
  "release create")
    exit "${GH_STUB_CREATE_EXIT:-0}"
    ;;
  *)
    echo "gh-stub: unhandled invocation: $*" >&2
    exit 1
    ;;
esac
"""


def make_gh_stub(tmp_path):
    """A `gh` on its own PATH entry, logging every call to gh_calls.txt."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "gh"
    stub.write_text(GH_STUB)
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    return bin_dir, tmp_path / "gh_calls.txt"


def run_script(tmp_path, args, *, download_names=(), env_overrides=None, github_sha="deadbeef"):
    bin_dir, log = make_gh_stub(tmp_path)
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["GH_STUB_LOG"] = str(log)
    env["GH_STUB_DOWNLOAD_NAMES"] = " ".join(download_names)
    if github_sha is not None:
        env["GITHUB_SHA"] = github_sha
    else:
        env.pop("GITHUB_SHA", None)
    if env_overrides:
        env.update(env_overrides)

    result = subprocess.run(
        ["bash", SCRIPT_PATH, *args],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=env,
        check=False,
    )
    calls = log.read_text().splitlines() if log.exists() else []
    return result, calls


class TestScriptShape:
    def test_script_exists(self):
        assert os.path.exists(SCRIPT_PATH)

    def test_script_is_executable(self):
        mode = os.stat(SCRIPT_PATH).st_mode
        assert mode & stat.S_IXUSR

    def test_script_has_a_bash_shebang(self):
        with open(SCRIPT_PATH) as f:
            first = f.readline()
        assert first.startswith("#!") and "bash" in first

    def test_script_uses_strict_mode(self):
        with open(SCRIPT_PATH) as f:
            text = f.read()
        assert "set -euo pipefail" in text


class TestArguments:
    def test_refuses_with_no_arguments(self, tmp_path):
        result, _ = run_script(tmp_path, [])
        assert result.returncode == 2
        assert "Usage" in result.stderr

    def test_refuses_with_too_few_arguments(self, tmp_path):
        result, _ = run_script(tmp_path, ["acadsharp", "acadsharp-1.0"])
        assert result.returncode == 2

    def test_refuses_without_github_sha(self, tmp_path):
        result, _ = run_script(
            tmp_path, ["acadsharp", "acadsharp-1.0", "acadsharp-*.tgz"], github_sha=None
        )
        assert result.returncode != 0
        assert "GITHUB_SHA" in result.stderr


class TestTheHappyPath:
    def test_it_downloads_the_named_tag(self, tmp_path):
        result, calls = run_script(
            tmp_path,
            ["acadsharp", "acadsharp-3.7.1-viprs.1", "acadsharp-*.tgz"],
            download_names=["acadsharp-linux-x64.tgz"],
        )
        assert result.returncode == 0, result.stderr
        download = [c for c in calls if c.startswith("release download")]
        assert download, calls
        assert "acadsharp-3.7.1-viprs.1" in download[0]
        assert "acadsharp-*.tgz" in download[0]

    def test_it_deletes_before_it_creates(self, tmp_path):
        """A stale acadsharp-latest release left in place would sit
        beside the new assets instead of being replaced by them."""
        result, calls = run_script(
            tmp_path,
            ["acadsharp", "acadsharp-3.7.1-viprs.1", "acadsharp-*.tgz"],
            download_names=["acadsharp-linux-x64.tgz"],
        )
        assert result.returncode == 0, result.stderr
        kinds = [c.split()[1] for c in calls if c.startswith("release ")]
        assert kinds.index("delete") < kinds.index("create"), calls

    def test_it_moves_the_tag_named_after_the_dependency(self, tmp_path):
        result, calls = run_script(
            tmp_path,
            ["pdfium", "pdfium-8054", "pdfium-*.tgz"],
            download_names=["pdfium-linux-x64.tgz"],
        )
        assert result.returncode == 0, result.stderr
        create = next(c for c in calls if c.startswith("release create"))
        assert "pdfium-latest" in create
        assert "acadsharp-latest" not in create

    def test_it_targets_the_commit_this_run_built(self, tmp_path):
        result, calls = run_script(
            tmp_path,
            ["acadsharp", "acadsharp-3.7.1-viprs.1", "acadsharp-*.tgz"],
            download_names=["acadsharp-linux-x64.tgz"],
            github_sha="cafef00d",
        )
        assert result.returncode == 0, result.stderr
        create = next(c for c in calls if c.startswith("release create"))
        assert "cafef00d" in create

    def test_it_names_the_real_tag_in_the_notes(self, tmp_path):
        """The whole reason to open the latest release is to find out
        which real version it currently is."""
        result, calls = run_script(
            tmp_path,
            ["acadsharp", "acadsharp-3.7.1-viprs.1", "acadsharp-*.tgz"],
            download_names=["acadsharp-linux-x64.tgz"],
        )
        assert result.returncode == 0, result.stderr
        create = next(c for c in calls if c.startswith("release create"))
        assert "acadsharp-3.7.1-viprs.1" in create

    def test_it_uploads_every_downloaded_asset(self, tmp_path):
        result, calls = run_script(
            tmp_path,
            ["acadsharp", "acadsharp-3.7.1-viprs.1", "acadsharp-*.tgz"],
            download_names=[
                "acadsharp-linux-x64.tgz",
                "acadsharp-linux-arm64.tgz",
                "acadsharp-mac-arm64.tgz",
            ],
        )
        assert result.returncode == 0, result.stderr
        create = next(c for c in calls if c.startswith("release create"))
        for name in (
            "acadsharp-linux-x64.tgz",
            "acadsharp-linux-arm64.tgz",
            "acadsharp-mac-arm64.tgz",
        ):
            assert name in create, f"{name} was downloaded but never uploaded to latest"

    def test_a_second_dependencys_glob_does_not_catch_the_first(self, tmp_path):
        """The download step is told exactly what to look for, so a
        release that happens to carry another dependency's assets (it
        never should, but the pattern is the only thing standing between
        this and a mixed-up latest) does not pull them in."""
        result, calls = run_script(
            tmp_path,
            ["acadsharp", "acadsharp-3.7.1-viprs.1", "acadsharp-*.tgz"],
            download_names=["acadsharp-linux-x64.tgz", "pdfium-linux-x64.tgz"],
        )
        assert result.returncode == 0, result.stderr
        create = next(c for c in calls if c.startswith("release create"))
        assert "acadsharp-linux-x64.tgz" in create
        assert "pdfium-linux-x64.tgz" not in create, (
            "the stub ignores the glob when writing files, so this only passes if "
            "the script's own glob expansion, not the stub's, is what filtered it"
        )


class TestRefusingAnEmptyDownload:
    """Moving 'latest' to a release with nothing in it is worse than not
    moving it: a consumer resolving the tag would get an empty page
    instead of the newest working build."""

    def test_no_matching_assets_refuses_rather_than_publishing_an_empty_release(self, tmp_path):
        result, calls = run_script(
            tmp_path,
            ["acadsharp", "acadsharp-3.7.1-viprs.1", "acadsharp-*.tgz"],
            download_names=[],
        )
        assert result.returncode != 0
        assert "no assets" in result.stderr.lower()
        assert not any(c.startswith("release delete") for c in calls), (
            "the old latest release must not be torn down before a replacement exists"
        )
        assert not any(c.startswith("release create") for c in calls)

    def test_assets_present_but_none_matching_the_glob_also_refuses(self, tmp_path):
        # gh's real download would have written nothing for this
        # dependency's glob even though the release has other files, and
        # nullglob has to see that as zero matches rather than the
        # literal pattern string.
        result, _ = run_script(
            tmp_path,
            ["acadsharp", "acadsharp-3.7.1-viprs.1", "acadsharp-*.tgz"],
            download_names=["totally-unrelated-file.txt"],
        )
        assert result.returncode != 0
        assert "no assets" in result.stderr.lower()


class TestPropagatingGhFailures:
    def test_a_download_failure_stops_before_anything_is_deleted(self, tmp_path):
        result, calls = run_script(
            tmp_path,
            ["acadsharp", "acadsharp-3.7.1-viprs.1", "acadsharp-*.tgz"],
            download_names=["acadsharp-linux-x64.tgz"],
            env_overrides={"GH_STUB_DOWNLOAD_EXIT": "1"},
        )
        assert result.returncode != 0
        assert not any(c.startswith("release delete") for c in calls)

    def test_a_create_failure_is_not_swallowed(self, tmp_path):
        result, _ = run_script(
            tmp_path,
            ["acadsharp", "acadsharp-3.7.1-viprs.1", "acadsharp-*.tgz"],
            download_names=["acadsharp-linux-x64.tgz"],
            env_overrides={"GH_STUB_CREATE_EXIT": "1"},
        )
        assert result.returncode != 0

    def test_a_delete_failure_is_swallowed_because_no_prior_release_is_the_common_case(
        self, tmp_path
    ):
        # The very first time a dependency gets a "latest" tag, there is
        # nothing to delete, and gh reports that as a failure. Refusing
        # to publish the first-ever latest release over that would be
        # wrong, so this one failure mode is intentionally not fatal.
        result, calls = run_script(
            tmp_path,
            ["acadsharp", "acadsharp-3.7.1-viprs.1", "acadsharp-*.tgz"],
            download_names=["acadsharp-linux-x64.tgz"],
            env_overrides={"GH_STUB_DELETE_EXIT": "1"},
        )
        assert result.returncode == 0, result.stderr
        assert any(c.startswith("release create") for c in calls)
