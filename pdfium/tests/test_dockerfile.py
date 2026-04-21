"""Tests for generated Dockerfile content.

The build runs in two phases per platform so the release archive ships
both ``libpdfium.a`` (from the Static pass, which passes
``pdf_is_complete_lib = true`` into ``args.gn`` so PDFium's own BUILD.gn
branch emits a fat ``static_library``) and ``libpdfium.so`` (from the
shared-library rewrite applied on top). The assertions below lock in
that two-phase structure for each platform.
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

    def test_libcxx_standard_not_custom(self):
        # Downstream rustc consumers (via pdfium-render/static with the
        # libstdc++ feature) expect std::* symbols from the system
        # libstdc++, not Chromium's std::__Cr::* custom namespace. We
        # therefore disable Chromium's bundled libc++.
        assert "use_custom_libcxx = false" in self.df
        assert "use_custom_libcxx_for_host = false" in self.df

    def test_no_custom_libcxx_true(self):
        # Regression guard: ensure we never accidentally re-enable
        # Chromium's __Cr-namespaced libc++ on linux-glibc.
        assert "use_custom_libcxx = true" not in self.df

    def test_use_sysroot_false_linux_glibc(self):
        # Chromium's pinned debian-bullseye sysroot lacks libstdc++
        # headers; with use_custom_libcxx=false, compilation would fail
        # with "<string> not found" if we kept use_sysroot=true.
        # build-essential (already installed) supplies libstdc++-12-dev.
        assert "use_sysroot = false" in self.df

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

    def test_pdf_is_complete_lib_in_static_args(self):
        # The static pass writes out/Static/args.gn with pdf_is_complete_lib = true
        # (triggering PDFium's own complete-lib branch in BUILD.gn); the shared
        # pass writes out/Shared/args.gn without it. The flag must therefore
        # appear AFTER the Static args heredoc starts but BEFORE the Shared one.
        static_args_pos = self.df.index("out/Static/args.gn")
        shared_args_pos = self.df.index("out/Shared/args.gn")
        flag_pos = self.df.index("pdf_is_complete_lib = true")
        assert static_args_pos < flag_pos < shared_args_pos

    def test_pdf_is_complete_lib_not_in_shared_args(self):
        # pdf_is_complete_lib is specific to the static pass — the shared pass
        # emits shared_library("pdfium") where the flag doesn't apply. The
        # string must therefore appear exactly once in the whole Dockerfile
        # (inside the Static args.gn heredoc).
        assert self.df.count("pdf_is_complete_lib = true") == 1

    def test_verify_rejects_thin_archive(self):
        assert "'!<thin>'" in self.df
        # Verify step still names the regression so operators see why the
        # build failed. The exact wording in build_pdfium.py's _thin_err is
        # "libpdfium.a is a GNU thin archive — complete_static_lib patch regressed".
        assert "complete_static_lib patch regressed" in self.df

    def test_verify_runs_between_shared_build_and_staging(self):
        static_ninja = self.df.index("ninja -C out/Static pdfium")
        verify = self.df.index("libpdfium.a has fat-archive magic")
        stage = self.df.index("cp out/Static/obj/libpdfium.a /staging/lib/")
        assert static_ninja < verify < stage

    def test_verify_checks_member_count_and_size(self):
        assert "ar t" in self.df
        assert "MEMBERS" in self.df
        assert "SIZE" in self.df


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

    def test_libcxx_standard_not_custom(self):
        assert "use_custom_libcxx = false" in self.df
        assert "use_custom_libcxx_for_host = false" in self.df
        assert "use_custom_libcxx = true" not in self.df

    def test_use_sysroot_false_linux_glibc(self):
        assert "use_sysroot = false" in self.df


class TestMakeDockerfileMuslAmd64:
    def setup_method(self):
        self.df = bp.make_dockerfile("7725", "amd64", "musl")

    def test_musl_toolchain_downloaded(self):
        assert "x86_64-linux-musl-cross.tgz" in self.df

    def test_musl_toolchain_downloaded_to_file_not_piped(self):
        # musl.cc occasionally returns a truncated body (HTTP 200 but the
        # connection drops mid-stream). Piping curl into tar hides that
        # failure as a cryptic tar error, so we download to a file first,
        # assert the size, and extract separately. The anti-pattern below
        # must stay out of the Dockerfile.
        assert "-o /tmp/tc.tgz" in self.df
        assert "| tar xz" not in self.df
        assert "| tar -xz" not in self.df

    def test_musl_toolchain_size_check(self):
        # Real musl-cross-make toolchains are ~100 MB; a 50 MB floor is
        # conservative enough to accept the real archive while rejecting a
        # truncated body or a stray HTML error page.
        assert "stat -c%s /tmp/tc.tgz" in self.df
        assert "50000000" in self.df

    def test_musl_toolchain_extracted_separately(self):
        assert "tar xzf /tmp/tc.tgz -C /opt" in self.df

    def test_musl_toolchain_download_retries(self):
        # Keep retry/backoff flags on both the primary (GH mirror) and
        # fallback (musl.cc) curls so a single transient failure doesn't
        # kill the build.
        assert self.df.count("--retry-all-errors") >= 2
        assert self.df.count("--connect-timeout 30") >= 2

    def test_musl_toolchain_mirror_primary(self):
        # Our libviprs-dep releases mirror is the primary source; musl.cc
        # is only a fallback (GH Actions runners intermittently blackhole
        # TCP to musl.cc).
        mirror_pos = self.df.index("libviprs/libviprs-dep/releases/download/musl-cross-mirror/")
        muslcc_pos = self.df.index("https://musl.cc/")
        assert mirror_pos < muslcc_pos

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

    def test_verify_complete_static_lib_present(self):
        assert "libpdfium.a has fat-archive magic" in self.df
        assert "'!<thin>'" in self.df

    def test_pdf_is_complete_lib_in_static_args(self):
        # Same invariant as the linux dockerfile: pdf_is_complete_lib = true
        # lives only in the Static args.gn heredoc, not the Shared one.
        static_args_pos = self.df.index("out/Static/args.gn")
        shared_args_pos = self.df.index("out/Shared/args.gn")
        flag_pos = self.df.index("pdf_is_complete_lib = true")
        assert static_args_pos < flag_pos < shared_args_pos

    def test_pdf_is_complete_lib_not_in_shared_args(self):
        assert self.df.count("pdf_is_complete_lib = true") == 1

    def test_libcxx_standard_not_custom(self):
        # musl already uses libstdc++ (gcc + musl-cross-make ships its own
        # libstdc++.a); this regression-guards both the libcxx and the
        # for_host flag remaining disabled.
        assert "use_custom_libcxx = false" in self.df
        assert "use_custom_libcxx_for_host = false" in self.df
        assert "use_custom_libcxx = true" not in self.df


class TestMakeDockerfileMuslArm64:
    def setup_method(self):
        self.df = bp.make_dockerfile("7725", "arm64", "musl")

    def test_musl_toolchain_downloaded(self):
        assert "aarch64-linux-musl-cross.tgz" in self.df

    def test_target_cpu_arm64(self):
        assert 'target_cpu = "arm64"' in self.df

    def test_libcxx_standard_not_custom(self):
        assert "use_custom_libcxx = false" in self.df
        assert "use_custom_libcxx_for_host = false" in self.df
        assert "use_custom_libcxx = true" not in self.df


class TestMakeDockerfileDifferentVersions:
    def test_version_in_sync_command(self):
        df = bp.make_dockerfile("6999", "amd64", "linux")
        assert "origin/chromium/6999" in df

    def test_version_does_not_hardcode_7725(self):
        df = bp.make_dockerfile("8000", "amd64", "linux")
        assert "7725" not in df
        assert "origin/chromium/8000" in df
