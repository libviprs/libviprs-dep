"""The generated Dockerfile for the linux cells."""

import pytest

import build_qpdf as b


def df(arch="amd64"):
    return b.make_dockerfile(b.read_version(), arch, "linux")


def test_container_is_pinned_to_the_target_arch():
    assert "FROM --platform=linux/amd64 " in df("amd64")
    assert "FROM --platform=linux/arm64 " in df("arm64")


def test_source_is_checked_against_the_pin_before_it_is_unpacked():
    text = df()
    assert b.source_sha256(b.read_version()) in text
    assert text.index("sha256sum -c") < text.index("tar xzf")


def test_mac_has_no_dockerfile():
    with pytest.raises(ValueError, match="mac"):
        b.make_dockerfile(b.read_version(), "arm64", "mac")


def test_licences_and_checksums_are_staged():
    text = df()
    assert "LICENSE.txt /staging/LICENSE" in text
    assert "/staging/NOTICE.md" in text
    assert "/staging/metadata/CHECKSUMS.txt" in text
    assert "/staging/metadata/BUILDINFO.txt" in text


def test_the_staged_binary_is_run_before_the_image_is_done():
    text = df()
    assert "smoke.sh /staging" in text
    assert text.index("smoke.sh /staging") > text.index("cmake --install")


def test_smoke_script_checks_version_good_pdf_and_corrupt_pdf():
    s = b.SMOKE_TEST_SH
    assert "--version" in s
    assert "good.pdf" in s
    assert "corrupt.pdf" in s
    assert "LD_LIBRARY_PATH" not in s.split("bin/qpdf", 1)[1].split("\n", 1)[0]
