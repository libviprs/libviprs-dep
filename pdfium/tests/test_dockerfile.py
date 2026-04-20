"""Tests for generated Dockerfile content.

The build runs in two phases per platform so the release archive ships
both ``libpdfium.a`` (from the base patch — ``component()`` stays as
``static_library``) and ``libpdfium.so`` (from the shared-library
rewrite applied on top). The assertions below lock in that two-phase
structure for each platform.
"""

import build_pdfium as bp


class TestMakeDockerfileLinuxAmd64:
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
        assert "COPY platform.py" in self.df

    def test_base_patch_applied_before_static_build(self):
        base_pos = self.df.index("--mode base")
        static_ninja_pos = self.df.index("ninja -C out/Static pdfium")
        assert base_pos < static_ninja_pos

    def test_shared_patch_applied_between_builds(self):
        static_ninja_pos = self.df.index("ninja -C out/Static pdfium")
        shared_patch_pos = self.df.index("--mode shared")
        shared_ninja_pos = self.df.index("ninja -C out/Shared pdfium")
        assert static_ninja_pos < shared_patch_pos < shared_ninja_pos

    def test_gn_args(self):
        assert "is_component_build = false" in self.df
        assert "pdf_use_partition_alloc = false" in self.df
        assert "clang_use_chrome_plugins = false" in self.df

    def test_two_ninja_invocations(self):
        assert "ninja -C out/Static pdfium" in self.df
        assert "ninja -C out/Shared pdfium" in self.df

    def test_two_gn_gen_invocations(self):
        assert "gn gen out/Static" in self.df
        assert "gn gen out/Shared" in self.df

    def test_staging_contains_both_artifacts(self):
        assert "/staging/lib" in self.df
        assert "/staging/include" in self.df
        assert "libpdfium.so /staging/lib/" in self.df
        assert "libpdfium.a /staging/lib/" in self.df
        assert "COPY LICENSE" in self.df

    def test_staging_copies_both_args_files(self):
        assert "args.gn /staging/args.gn" in self.df
        assert "args.gn /staging/args.static.gn" in self.df


class TestMakeDockerfileLinuxArm64:
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

    def test_two_ninja_invocations(self):
        assert "ninja -C out/Static pdfium" in self.df
        assert "ninja -C out/Shared pdfium" in self.df


class TestMakeDockerfileMuslAmd64:
    def setup_method(self):
        self.df = bp.make_dockerfile("7725", "amd64", "musl")

    def test_musl_toolchain_downloaded(self):
        assert "x86_64-linux-musl-cross.tgz" in self.df

    def test_target_cpu_x64(self):
        assert 'target_cpu = "x64"' in self.df

    def test_is_musl_gn_arg(self):
        assert "is_musl = true" in self.df

    def test_two_ninja_invocations(self):
        assert "ninja -C out/Static pdfium" in self.df
        assert "ninja -C out/Shared pdfium" in self.df

    def test_base_then_shared(self):
        base_pos = self.df.index("--mode base")
        shared_pos = self.df.index("--mode shared")
        assert base_pos < shared_pos

    def test_staging_contains_both_artifacts(self):
        assert "libpdfium.so /staging/lib/" in self.df
        assert "libpdfium.a /staging/lib/" in self.df


class TestMakeDockerfileMuslArm64:
    def setup_method(self):
        self.df = bp.make_dockerfile("7725", "arm64", "musl")

    def test_musl_toolchain_downloaded(self):
        assert "aarch64-linux-musl-cross.tgz" in self.df

    def test_target_cpu_arm64(self):
        assert 'target_cpu = "arm64"' in self.df


class TestMakeDockerfileDifferentVersions:
    def test_version_in_sync_command(self):
        df = bp.make_dockerfile("6999", "amd64", "linux")
        assert "origin/chromium/6999" in df

    def test_version_does_not_hardcode_7725(self):
        df = bp.make_dockerfile("8000", "amd64", "linux")
        assert "7725" not in df
        assert "origin/chromium/8000" in df
