"""The mac archive's cmake-args.txt must not carry the build machine's paths (#43).

The mac build installs into a staging directory under the output dir, and
used to write that absolute path into cmake-args.txt, which then shipped
inside the archive (home directory, user name and all). The container
builds write the placeholder /staging, so the mac build has to as well.
"""

import os

import build_zstd as bz
import pytest


@pytest.mark.parametrize("arch", ["amd64", "arm64"])
def test_mac_cmake_args_txt_has_no_local_path(arch, tmp_path, monkeypatch):
    monkeypatch.setattr(bz.sys, "platform", "darwin")
    monkeypatch.setattr(bz, "download_source", lambda *a, **k: None)
    prefix = {}

    def fake_run(cmd, log_file, job, **kwargs):
        if cmd[0] == "tar" and cmd[1] == "xzf":
            src = os.path.join(cmd[4], "zstd-1.5.7")
            os.makedirs(src)
            with open(os.path.join(src, "LICENSE"), "w") as f:
                f.write("license")
        elif cmd[0] == "cmake" and "-S" in cmd:
            prefix["dir"] = next(
                a.split("=", 1)[1] for a in cmd if a.startswith("-DCMAKE_INSTALL_PREFIX=")
            )
        elif cmd[:2] == ["cmake", "--install"]:
            os.makedirs(prefix["dir"])
        elif cmd[0] == "tar" and cmd[1] == "czf":
            with open(cmd[2], "wb") as f:
                f.write(b"x")

    monkeypatch.setattr(bz, "run_checked", fake_run)

    with open(tmp_path / "log", "w") as log:
        bz._build_mac_native("1.5.7", arch, str(tmp_path), log, f"mac/{arch}")

    with open(os.path.join(prefix["dir"], "cmake-args.txt")) as f:
        text = f.read()
    assert str(tmp_path) not in text
    assert "-DCMAKE_INSTALL_PREFIX=/staging\n" in text
