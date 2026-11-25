"""The generated Dockerfile for the linux cells."""

import build_qpdf as b
import pytest


def df(arch="amd64"):
    return b.make_dockerfile(b.read_version(), arch, "linux")


def test_container_is_pinned_to_the_target_arch():
    assert "FROM --platform=linux/amd64 " in df("amd64")
    assert "FROM --platform=linux/arm64 " in df("arm64")


def test_source_is_checked_against_the_pin_before_it_is_unpacked():
    text = df()
    assert b.source_sha256(b.read_version()) in text
    assert text.index("sha256sum -c") < text.index("tar xzf")


def test_libjpeg_is_a_pinned_static_pic_build():
    text = df()
    assert b.JPEG_SOURCE_SHA256 in text
    assert text.index(b.JPEG_SOURCE_SHA256) < text.index("cmake -S . ")
    assert "-DENABLE_SHARED=OFF" in text
    assert "-DCMAKE_POSITION_INDEPENDENT_CODE=ON" in text
    assert f"-DCMAKE_PREFIX_PATH={b.JPEG_LINUX_PREFIX}" in text
    assert "libjpeg-dev" not in text


def test_mac_has_no_dockerfile():
    with pytest.raises(ValueError, match="mac"):
        b.make_dockerfile(b.read_version(), "arm64", "mac")


def test_licences_and_checksums_are_staged():
    text = df()
    assert "LICENSE.txt /staging/LICENSE" in text
    assert "/staging/NOTICE.md" in text
    assert "finalize.sh /staging" in text
    assert "metadata/CHECKSUMS.txt" in b.FINALIZE_SH
    assert "metadata/BUILDINFO.txt" in b.FINALIZE_SH


def test_the_staged_binary_is_run_before_the_image_is_done():
    text = df()
    assert "smoke.sh /staging" in text
    assert text.index("smoke.sh /staging") > text.index("cmake --install")


def test_smoke_script_checks_version_good_pdf_and_corrupt_pdf():
    s = b.SMOKE_TEST_SH
    assert "--version" in s
    assert "good.pdf" in s
    assert "corrupt.pdf" in s
    # the binary is run with the library path unset, so the rpath is what is tested
    assert "env -u LD_LIBRARY_PATH" in s
    assert "export LD_LIBRARY_PATH" not in s
