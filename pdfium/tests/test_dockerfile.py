"""Tests for generated Dockerfile content."""

import build_pdfium as bp


class TestMakeDockerfileAmd64:
    def setup_method(self):
        self.df = bp.make_dockerfile("7725", "amd64", "linux")

    def test_base_image(self):
        assert "FROM debian:bookworm-slim" in self.df

    def test_depot_tools_bootstrap(self):
        assert "gclient --version" in self.df

    def test_depot_tools_update_disabled_after_bootstrap(self):
        bootstrap_pos = self.df.index("gclient --version")
        update_pos = self.df.index("DEPOT_TOOLS_UPDATE=0")
        assert update_pos > bootstrap_pos

    def test_checkout_configuration_small(self):
        assert "checkout_configuration=small" in self.df

    def test_branch_in_sync(self):
        assert "origin/chromium/7725" in self.df

    def test_target_cpu_x64(self):
        assert 'target_cpu = "x64"' in self.df

    def test_target_os_linux(self):
        assert 'target_os = "linux"' in self.df

    def test_no_cross_compiler(self):
        assert "g++-aarch64-linux-gnu" not in self.df

    def test_no_arm_bti_disable(self):
        assert "arm_control_flow_integrity" not in self.df

    def test_sysroot_install(self):
        assert "install-sysroot.py --arch=x64" in self.df

    def test_platform_patch_copied(self):
        assert "COPY platform.sh" in self.df

    def test_shared_library_gn_args(self):
        assert "is_component_build = false" in self.df
        assert "pdf_use_partition_alloc = false" in self.df
        assert "clang_use_chrome_plugins = false" in self.df

    def test_staging_step(self):
        assert "/staging/lib" in self.df
        assert "/staging/include" in self.df
        assert "COPY LICENSE" in self.df

    def test_ninja_build(self):
        assert "ninja -C out/Release pdfium" in self.df


class TestMakeDockerfileArm64:
    def setup_method(self):
        self.df = bp.make_dockerfile("7725", "arm64", "linux")

    def test_cross_compiler_installed(self):
        assert "g++-aarch64-linux-gnu" in self.df

    def test_target_cpu_arm64(self):
        assert 'target_cpu = "arm64"' in self.df

    def test_bti_disabled(self):
        assert 'arm_control_flow_integrity = "none"' in self.df

    def test_sysroot_arm64(self):
        assert "install-sysroot.py --arch=arm64" in self.df

    def test_same_branch(self):
        assert "origin/chromium/7725" in self.df


class TestMakeDockerfileDifferentVersions:
    def test_version_in_sync_command(self):
        df = bp.make_dockerfile("6999", "amd64", "linux")
        assert "origin/chromium/6999" in df

    def test_version_does_not_hardcode_7725(self):
        df = bp.make_dockerfile("8000", "amd64", "linux")
        assert "7725" not in df
        assert "origin/chromium/8000" in df
