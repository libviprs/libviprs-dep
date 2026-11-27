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
    code = "\n".join(ln for ln in WF.splitlines() if not ln.lstrip().startswith("#"))
    assert "--upload" not in code


def test_version_input_goes_through_env_not_the_script():
    assert "REQUESTED: ${{ inputs.version }}" in WF
    assert "run: |" in WF and '${{ inputs.version }}"' not in WF


def test_latest_tag_only_moves_when_every_cell_published():
    assert "tools/publish_latest_tag.sh qpdf" in WF
    assert "needs.build-linux.result == 'success'" in WF
    assert "needs.build-mac.result" not in WF.split("publish-latest:", 1)[1]


def test_third_party_actions_are_pinned_to_a_commit():
    for line in WF.splitlines():
        m = re.search(r"uses: (docker/[^@\s]+)@(\S+)", line)
        if m:
            assert re.fullmatch(r"[0-9a-f]{40}", m.group(2)), line


def test_every_job_declares_its_permissions():
    jobs = WF.split("\njobs:\n", 1)[1]
    for block in re.split(r"\n  (?=[a-z-]+:\n)", jobs):
        if block.strip():
            assert "permissions:" in block, block.splitlines()[0]


def test_the_mac_job_is_optional_and_off_by_default():
    top, jobs = WF.split("\njobs:\n", 1)
    mac = jobs.split("build-mac:", 1)[1].split("\n  summary:", 1)[0]
    assert "continue-on-error: true" in mac
    assert "inputs.build_mac" in mac
    assert "build_mac:" in top
    assert "default: false" in top


def _code_lines():
    return [ln for ln in WF.splitlines() if not ln.lstrip().startswith("#")]


def test_no_upload_overwrites_an_existing_asset():
    # A re-run must fail on a clashing asset, not silently replace a
    # published archive; build_qpdf.py's own upload gets the same treatment.
    code = "\n".join(_code_lines())
    assert "--clobber" not in code


def test_the_release_is_created_as_a_draft():
    create = [ln for ln in _code_lines() if "gh release create" in ln]
    assert create, "no gh release create call found"
    assert all("--draft" in ln for ln in create)
