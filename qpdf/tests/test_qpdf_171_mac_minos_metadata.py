"""#171: the mac slice honours its macOS 11 promise, and BUILDINFO tells the truth.

The first qpdf-12.4.2 mac archive linked Homebrew's libjpeg.a, whose objects all
say minos 26.0, while the README promised macOS 11 or newer. BUILDINFO and the
release notes also named libstdc++ on mac (it is libc++) and recorded neither
the libjpeg-turbo build nor the driver commit.
"""

import inspect
import os
import re
import subprocess

import build_qpdf as b
import pytest
from test_qpdf_verify_archive import (
    ar,
    foreign_cpu,
    macho,
    make_archive,
    needs_other_host,
    run,
)

HERE = os.path.dirname(os.path.abspath(__file__))
QPDF = os.path.abspath(os.path.join(HERE, ".."))
ROOT = os.path.abspath(os.path.join(QPDF, ".."))
README = open(os.path.join(QPDF, "README.md")).read()
WF = open(os.path.join(ROOT, ".github", "workflows", "release-qpdf.yml")).read()


def mac(tmp_path, **files):
    return run(make_archive(tmp_path, cpu="arm64", plat="mac", mutate=lambda f: f.update(files)))


# ---- the verifier checks the deployment target of every Mach-O ----


@needs_other_host
def test_171_a_mac_static_library_built_for_a_newer_macos_is_refused(tmp_path):
    r = mac(tmp_path, **{"lib/libjpeg.a": ar(macho("arm64", (26, 0)))})
    assert r.returncode != 0
    out = r.stdout + r.stderr
    assert "libjpeg.a" in out and "26.0" in out and "11.0" in out


@needs_other_host
def test_171_a_mac_binary_built_for_a_newer_macos_is_refused(tmp_path):
    r = mac(tmp_path, **{"bin/qpdf": macho("arm64", (14, 2))})
    assert r.returncode != 0
    assert "bin/qpdf" in r.stdout + r.stderr


@needs_other_host
def test_171_a_mac_shared_library_built_for_a_newer_macos_is_refused(tmp_path):
    r = mac(tmp_path, **{"lib/libqpdf.30.dylib": macho("arm64", (12, 0))})
    assert r.returncode != 0
    assert "libqpdf.30.dylib" in r.stdout + r.stderr


@needs_other_host
def test_171_every_member_of_a_static_library_is_checked(tmp_path):
    members = ar(macho("arm64", (11, 0)), macho("arm64", (11, 0)), macho("arm64", (15, 0)))
    r = mac(tmp_path, **{"lib/libqpdf.a": members})
    assert r.returncode != 0
    assert "libqpdf.a" in r.stdout + r.stderr


@needs_other_host
def test_171_a_mac_archive_at_exactly_the_target_passes(tmp_path):
    r = mac(tmp_path, **{"lib/libjpeg.a": ar(macho("arm64", (11, 0)), macho("arm64", (10, 15)))})
    assert r.returncode == 0, r.stdout + r.stderr


@needs_other_host
def test_171_a_mac_binary_with_no_deployment_target_is_refused(tmp_path):
    bare = macho("arm64")[:32]
    r = mac(tmp_path, **{"bin/qpdf": bare})
    assert r.returncode != 0
    assert "bin/qpdf" in r.stdout + r.stderr


def test_171_a_linux_archive_is_not_asked_for_a_deployment_target(tmp_path):
    r = run(make_archive(tmp_path))
    assert r.returncode == 0, r.stdout + r.stderr


# ---- BUILDINFO is complete and truthful ----


def with_info(tmp_path, plat, transform):
    cpu = "arm64" if plat == "mac" else foreign_cpu()

    def mutate(files):
        text = files["metadata/BUILDINFO.txt"].decode()
        files["metadata/BUILDINFO.txt"] = transform(text).encode()

    return run(make_archive(tmp_path, cpu=cpu, plat=plat, mutate=mutate))


@pytest.mark.parametrize("key", ["jpeg_version", "jpeg_source_sha256", "driver_commit"])
def test_171_buildinfo_without_the_provenance_keys_is_refused(tmp_path, key):
    r = with_info(
        tmp_path, "linux", lambda t: "".join(ln for ln in t.splitlines(True) if key not in ln)
    )
    assert r.returncode != 0
    assert key in r.stdout + r.stderr


def test_171_a_driver_commit_that_is_not_a_commit_is_refused(tmp_path):
    r = with_info(tmp_path, "linux", lambda t: re.sub(r"driver_commit=.*", "driver_commit=main", t))
    assert r.returncode != 0
    assert "driver_commit" in r.stdout + r.stderr


@needs_other_host
def test_171_mac_buildinfo_naming_libstdcxx_is_refused(tmp_path):
    r = with_info(tmp_path, "mac", lambda t: t.replace("libc++", "libstdc++"))
    assert r.returncode != 0
    assert "libc++" in r.stdout + r.stderr


def test_171_linux_buildinfo_naming_libcxx_is_refused(tmp_path):
    r = with_info(tmp_path, "linux", lambda t: t.replace("libstdc++", "libc++"))
    assert r.returncode != 0
    assert "libstdc++" in r.stdout + r.stderr


# ---- the driver writes that metadata and builds libjpeg-turbo the same way everywhere ----


def finalize(tmp_path, plat):
    stage = tmp_path / "stage"
    (stage / "bin").mkdir(parents=True, exist_ok=True)
    (stage / "bin" / "qpdf").write_bytes(b"x")
    script = tmp_path / "finalize.sh"
    script.write_text(b.FINALIZE_SH)
    args = [plat, "arm64", "12.4.2", "a" * 64, "native", "static"]
    args += [b.JPEG_VERSION, b.JPEG_SOURCE_SHA256, "d" * 40]
    subprocess.run(["sh", str(script), str(stage), *args], check=True, capture_output=True)
    return (stage / "metadata" / "BUILDINFO.txt").read_text()


def test_171_buildinfo_records_the_libjpeg_turbo_build_and_the_driver_commit(tmp_path):
    text = finalize(tmp_path, "linux")
    assert f"jpeg_version={b.JPEG_VERSION}\n" in text
    assert f"jpeg_source_sha256={b.JPEG_SOURCE_SHA256}\n" in text
    assert "driver_commit=" + "d" * 40 + "\n" in text


def test_171_buildinfo_names_libcxx_on_mac_and_libstdcxx_on_linux(tmp_path):
    assert "runtime_libraries=libc libc++ libz\n" in finalize(tmp_path, "mac")
    assert "runtime_libraries=libc libstdc++ libz\n" in finalize(tmp_path, "linux")


def test_171_the_verifier_and_the_driver_agree_on_the_mac_target():
    script = open(os.path.join(QPDF, "scripts", "verify_archive.sh")).read()
    assert f"MAC_MINOS={b.MAC_DEPLOYMENT_TARGET}\n" in script


def test_171_driver_commit_is_a_full_commit_id():
    assert re.fullmatch(r"[0-9a-f]{40}(-dirty)?", b.driver_commit())


def test_171_the_linux_dockerfile_passes_the_provenance_to_finalize():
    text = b.make_dockerfile(b.read_version(), "amd64", "linux")
    line = text.split("finalize.sh /staging", 1)[1].split("\n\n", 1)[0]
    assert b.JPEG_VERSION in line and b.JPEG_SOURCE_SHA256 in line and b.driver_commit() in line


def test_171_release_notes_say_libcxx_for_mac():
    notes = b.release_notes(b.read_version())
    assert "libc++" in notes and "macOS 11" in notes


def test_171_mac_libjpeg_is_built_from_the_pinned_source_at_the_deployment_target():
    args = b.jpeg_cmake_args("mac", "arm64", "/opt/jpeg")
    assert f"-DCMAKE_OSX_DEPLOYMENT_TARGET={b.MAC_DEPLOYMENT_TARGET}" in args
    assert "-DCMAKE_OSX_ARCHITECTURES=arm64" in args
    assert "-DENABLE_SHARED=OFF" in args and "-DCMAKE_POSITION_INDEPENDENT_CODE=ON" in args
    assert "-DCMAKE_INSTALL_PREFIX=/opt/jpeg" in args


def test_171_linux_and_mac_share_one_libjpeg_configure():
    linux = b.jpeg_cmake_args("linux", "amd64", b.JPEG_LINUX_PREFIX)
    assert not any("OSX" in a for a in linux)
    text = b.make_dockerfile(b.read_version(), "amd64", "linux")
    assert all(a in text for a in linux if a.startswith("-DENABLE") or "WITH_" in a)


def test_171_the_mac_build_no_longer_links_homebrews_libjpeg():
    src = inspect.getsource(b._build_mac_native) + inspect.getsource(b.check_dependencies)
    assert "brew" not in src and "jpeg_prefix_mac" not in src
    assert b.JPEG_SOURCE_SHA256 in inspect.getsource(b) or "JPEG_SOURCE_SHA256" in src
    assert not hasattr(b, "jpeg_prefix_mac")


def test_171_the_mac_workflow_job_installs_cmake_only():
    mac_job = WF.split("build-mac:", 1)[1].split("\n  summary:", 1)[0]
    assert "jpeg-turbo" not in mac_job


# ---- revision tag: corrected bytes never reuse a published tag ----


def test_171_a_revision_gives_a_new_tag_and_zero_keeps_the_old_one():
    assert b.release_tag("12.4.2", 0) == "qpdf-12.4.2"
    assert b.release_tag("12.4.2", 1) == "qpdf-12.4.2-viprs.1"


def test_171_the_revision_is_pinned_in_a_file_next_to_version():
    assert b.read_revision() >= 1


def test_171_the_workflow_tag_carries_the_revision():
    assert "qpdf/REVISION" in WF


class Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        code = 1 if cmd[:3] == ["gh", "release", "view"] else 0
        out = "c" * 40 + "\n" if "rev-parse" in cmd else ""
        return subprocess.CompletedProcess(cmd, code, out, "")


def test_171_upload_creates_a_draft_on_the_revision_tag_and_never_clobbers(monkeypatch):
    rec = Recorder()
    monkeypatch.setattr(b.subprocess, "run", rec)
    b.upload_release("12.4.2", ["a.tgz", "a.tgz.sha256"])
    create = next(c for c in rec.calls if c[:3] == ["gh", "release", "create"])
    upload = next(c for c in rec.calls if c[:3] == ["gh", "release", "upload"])
    assert create[3] == b.release_tag("12.4.2", b.read_revision())
    assert "--draft" in create and "--latest=false" in create
    assert "--target" in create
    assert "--clobber" not in upload


# ---- README ----


def test_171_the_readme_no_longer_calls_the_release_a_draft():
    assert "still a draft" not in README


def test_171_the_readme_documents_the_mac_runtime_and_the_target_check():
    assert "libc++" in README
    assert "deployment target" in README


def test_171_the_readme_documents_the_revision_tag_and_the_latest_alias():
    assert "## The qpdf-latest alias" in README
    section = README.split("## The qpdf-latest alias", 1)[1].split("\n## ", 1)[0]
    assert "--latest=false" in section and ".sha256" in section
    assert "-viprs." in README


def test_171_the_latest_alias_copies_the_sidecars_too():
    assert "'qpdf-*.tgz*'" in WF.split("publish-latest:", 1)[1]
