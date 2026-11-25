"""The release workflow publishes verified archives only."""

import os
import re

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
WF = open(os.path.join(ROOT, ".github", "workflows", "release-qpdf.yml")).read()


def test_cells_are_the_three_supported_targets():
    assert "qpdf-${{ matrix.platform }}-${{ matrix.cpu }}.tgz" in WF
    assert "{ platform: linux, arch: amd64, cpu: x64 }" in WF
    assert "{ platform: linux, arch: arm64, cpu: arm64 }" in WF
    assert "{ platform: mac, arch: arm64, cpu: arm64 }" in WF
    assert "musl" not in WF


def test_every_cell_verifies_before_it_uploads():
    for job in ("build-linux:", "build-mac:"):
        body = WF.split(job, 1)[1]
        body = re.split(r"\n  [a-z-]+:\n", body, 1)[0]
        assert body.index("qpdf/scripts/verify_archive.sh") < body.index("gh release upload")


def test_the_driver_is_never_given_upload():
    assert "--upload" not in WF


def test_version_input_goes_through_env_not_the_script():
    assert "REQUESTED: ${{ inputs.version }}" in WF
    assert "run: |" in WF and '${{ inputs.version }}"' not in WF


def test_latest_tag_only_moves_when_every_cell_published():
    assert "tools/publish_latest_tag.sh qpdf" in WF
    assert "needs.build-linux.result == 'success'" in WF
    assert "needs.build-mac.result == 'success'" in WF


def test_third_party_actions_are_pinned_to_a_commit():
    for line in WF.splitlines():
        m = re.search(r"uses: (docker/[^@\s]+)@(\S+)", line)
        if m:
            assert re.fullmatch(r"[0-9a-f]{40}", m.group(2)), line
