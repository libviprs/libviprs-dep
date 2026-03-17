"""Tests for the Docker buildkit step marker regex."""

import build_pdfium as bp


class TestStepRegex:
    def test_standard_step(self):
        m = bp.STEP_RE.search("#8 [3/14] RUN apt-get update")
        assert m is not None
        assert m.group(1) == "3"
        assert m.group(2) == "14"

    def test_padded_step(self):
        m = bp.STEP_RE.search("#5 [ 1/20] FROM debian:bookworm")
        assert m is not None
        assert m.group(1) == "1"
        assert m.group(2) == "20"

    def test_final_step(self):
        m = bp.STEP_RE.search("#21 [20/20] RUN ninja -C out/Release pdfium")
        assert m is not None
        assert m.group(1) == "20"
        assert m.group(2) == "20"

    def test_ninja_step(self):
        m = bp.STEP_RE.search("#21 1065.7 [2223/2223] SOLINK ./libpdfium.so")
        assert m is not None
        assert m.group(1) == "2223"
        assert m.group(2) == "2223"

    def test_no_match_plain_text(self):
        m = bp.STEP_RE.search("Building PDFium for linux/x64")
        assert m is None

    def test_no_match_empty(self):
        m = bp.STEP_RE.search("")
        assert m is None

    def test_cached_line(self):
        m = bp.STEP_RE.search("#10 [5/15] WORKDIR /build")
        assert m is not None
        assert m.group(1) == "5"
        assert m.group(2) == "15"
