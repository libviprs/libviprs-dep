"""acadsharp/VERSION carries two numbers and both have to stay honest.

Unlike zstd and pdfium, this dependency's version is the upstream
library version plus a shim revision, `3.7.1-viprs.1`. The driver
splits it, so there is one file to bump, and these hold the split
against a VERSION that stops parsing.
"""

import os
import re

import build_acadsharp as ba
import pytest

ACAD_DIR = os.path.join(os.path.dirname(__file__), "..")
VERSION_PATH = os.path.join(ACAD_DIR, "VERSION")


class TestVersionFile:
    def test_exists_and_parses(self):
        assert os.path.isfile(VERSION_PATH)
        assert re.fullmatch(r"\d+\.\d+\.\d+-viprs\.\d+", ba.read_version())

    def test_is_a_bare_version(self):
        # Consumers do `tr -d '[:space:]' < VERSION`, so the file holds
        # the number and nothing else.
        with open(VERSION_PATH) as f:
            assert len(f.read().strip().splitlines()) == 1

    def test_splits_into_upstream_and_shim_revision(self):
        upstream, shim = ba.split_version(ba.read_version())
        assert re.fullmatch(r"\d+\.\d+\.\d+", upstream)
        assert re.fullmatch(r"\d+", shim)

    def test_empty_file_is_an_error(self, tmp_path):
        empty = tmp_path / "VERSION"
        empty.write_text("\n")
        with pytest.raises(ValueError, match="single source of truth"):
            ba.read_version(str(empty))

    def test_a_bare_upstream_version_is_rejected(self):
        # `3.7.1` on its own would build the library but leave the shim
        # revision nowhere, and every artifact name would then collide
        # across shim rebuilds of the same upstream release.
        with pytest.raises(ValueError):
            ba.split_version("3.7.1")
