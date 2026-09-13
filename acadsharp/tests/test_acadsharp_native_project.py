"""Two settings in the native project are load-bearing at run time.

Both were found by running the thing, and neither shows up as a build
failure if it is removed:

* Without `InvariantGlobalization`, `CadHeader..ctor` reaches
  `DateTime.Now` -> `TimeZoneInfo` -> `CultureInfo`, and a NativeAOT
  binary with no libicu present calls `FailFast`. That aborts with
  SIGABRT straight through a `try`/`catch` wrapping the whole export
  body, so the export's own error handling cannot report it.
* Without `TrimmerRootAssembly`, the publish succeeds and the binary
  then throws `MissingMethodException: No parameterless constructor
  defined for type 'ACadSharp.Tables.AppId'` on the first
  `new CadDocument()`.

So they are guarded here rather than trusted to a comment.
"""

import os
import re

ACAD_DIR = os.path.join(os.path.dirname(__file__), "..")
NATIVE_DIR = os.path.join(ACAD_DIR, "native")
CSPROJ = os.path.join(NATIVE_DIR, "Viprs.ACadSharp.Native.csproj")
EXPORTS = os.path.join(NATIVE_DIR, "Exports.cs")
GLOBAL_JSON = os.path.join(NATIVE_DIR, "global.json")
LOCK_FILE = os.path.join(NATIVE_DIR, "packages.lock.json")


def csproj():
    with open(CSPROJ) as f:
        return f.read()


class TestAotSettings:
    def test_publishes_as_an_aot_shared_library(self):
        code = csproj()
        assert "<PublishAot>true</PublishAot>" in code
        assert "<NativeLib>Shared</NativeLib>" in code

    def test_globalization_is_invariant(self):
        assert "<InvariantGlobalization>true</InvariantGlobalization>" in csproj(), (
            "without this the binary calls FailFast inside CadHeader's constructor on any "
            "host without libicu, and no try/catch in the export can turn that into a code"
        )

    def test_acadsharp_is_a_trimmer_root(self):
        assert re.search(r'<TrimmerRootAssembly\s+Include="ACadSharp"\s*/>', csproj()), (
            "without this the trimmer drops the constructors Table<T>.CreateDefaultEntries "
            "reaches through Activator.CreateInstance, and the first CadDocument throws"
        )

    def test_trim_warnings_are_reported_one_by_one(self):
        # TrimmerSingleWarn collapses every warning in an assembly into
        # one line, which is how a count of 2 hides a count of 16.
        assert "<TrimmerSingleWarn>false</TrimmerSingleWarn>" in csproj()


class TestExportsAreAbiSafe:
    def test_every_export_is_static(self):
        with open(EXPORTS) as f:
            code = f.read()
        exports = re.findall(
            r"\[UnmanagedCallersOnly\(EntryPoint = \"(\w+)\"\)\]\s*\n\s*public\s+(static\s+)?",
            code,
        )
        assert exports, "no [UnmanagedCallersOnly] exports found"
        for name, static in exports:
            assert static, f"{name} is not static, so it cannot be an unmanaged entry point"

    def test_every_export_is_namespaced(self):
        with open(EXPORTS) as f:
            code = f.read()
        names = re.findall(r'\[UnmanagedCallersOnly\(EntryPoint = "(\w+)"\)\]', code)
        assert names
        for name in names:
            assert name.startswith("viprs_acad_"), (
                f"{name} would collide with whatever else the consumer links in"
            )

    def test_no_export_lets_an_exception_escape(self):
        with open(EXPORTS) as f:
            code = f.read()
        bodies = code.split("[UnmanagedCallersOnly")[1:]
        for body in bodies:
            if "=> " in body.split("\n")[1]:
                continue  # an expression-bodied constant cannot throw
            assert "catch" in body, "an export can throw into unmanaged code"


class TestSdkPin:
    def test_global_json_pins_the_sdk_line(self):
        with open(GLOBAL_JSON) as f:
            code = f.read()
        assert re.search(r'"version"\s*:\s*"10\.0\.4\d\d"', code), (
            "global.json must pin the 10.0.4xx SDK line, the .NET 10 LTS the epic targets"
        )
        assert "rollForward" in code

    def test_the_lock_file_is_committed(self):
        assert os.path.isfile(LOCK_FILE), (
            "packages.lock.json is what pins the ILCompiler package the publish pulls"
        )
        assert os.path.getsize(LOCK_FILE) > 0
