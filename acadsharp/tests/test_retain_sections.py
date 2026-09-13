"""The ELF flag that keeps NativeAOT's module table out of the collector.

`__modules` holds the runtime's module headers and the bootstrapper finds
them through `__start___modules` and `__stop___modules`, the symbols a
linker synthesises for a section whose name is a C identifier. Nothing
relocates against the section, so those two symbols are its only
references, and since lld 13 `-z start-stop-gc` is on by default, which
says a reference through an encapsulation symbol is not a reason to keep
a section alive. Under `--gc-sections` lld therefore collects `__modules`
and then has nothing for `__start___modules` to point at.

That is issue #67 exactly: every consumer that links with lld, which on
`x86_64-unknown-linux-gnu` is every Rust consumer, failed on a recipe
that worked everywhere GNU ld was in charge.

These are the unit tests for the flag-setter. The objects are synthesised
here rather than compiled, so they run on a host with no toolchain and so
a malformed file can be constructed deliberately. The end-to-end proof
that the flag fixes the link lives in test_build_link_consumer.py, which
links a fixture archive carrying the same shape through the real cargo
recipe.
"""

import os
import struct
import subprocess
import sys

import pytest

SCRIPTS = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts"))
SCRIPT = os.path.join(SCRIPTS, "retain_sections.py")

sys.path.insert(0, SCRIPTS)
import retain_sections  # noqa: E402

SHF_GNU_RETAIN = 0x200000
EI_OSABI = 7
ELFOSABI_GNU = 3
SHF_ALLOC = 0x2
SHF_WRITE = 0x1

EHDR_SIZE = 64
SHDR_SIZE = 64


def make_object(names, flags=None, *, elf_class=2, endianness=1, osabi=0):
    """A minimal ELF64 with one section header per name, plus the string table.

    Section 0 is the null entry a real object always has, so the indices
    here line up with what readelf would print.
    """
    flags = flags or {}
    names = list(names)

    table = b"\0"
    offsets = {}
    for name in names + [".shstrtab"]:
        offsets[name] = len(table)
        table += name.encode() + b"\0"

    count = len(names) + 2  # the null entry, the named sections, .shstrtab
    shoff = EHDR_SIZE
    table_offset = shoff + count * SHDR_SIZE

    header = bytearray(EHDR_SIZE)
    header[0:4] = b"\x7fELF"
    header[4] = elf_class
    header[5] = endianness
    header[6] = 1  # EV_CURRENT
    header[EI_OSABI] = osabi
    struct.pack_into("<HH", header, 16, 1, 62)  # ET_REL, EM_X86_64
    struct.pack_into("<Q", header, 0x28, shoff)
    struct.pack_into("<HHH", header, 0x3A, SHDR_SIZE, count, count - 1)

    sections = bytearray()
    sections += bytes(SHDR_SIZE)  # SHN_UNDEF
    # sh_offset, sh_size and sh_addralign are filled in with plausible
    # values rather than left zero. A section header of zeros makes the
    # "nothing else changed" test hollow: the setter can clobber any of
    # those fields and the bytes it destroyed were already zero, so the
    # comparison passes. Measured on the real ILC object, `__modules` has
    # sh_offset 8503624, sh_size 8 and sh_addralign 8.
    for index, name in enumerate(names, start=1):
        entry = bytearray(SHDR_SIZE)
        struct.pack_into("<I", entry, 0, offsets[name])
        struct.pack_into("<I", entry, 4, 1)  # SHT_PROGBITS
        struct.pack_into("<Q", entry, 8, flags.get(name, SHF_ALLOC | SHF_WRITE))
        struct.pack_into("<Q", entry, 16, 0x1000 * index)  # sh_addr
        struct.pack_into("<Q", entry, 24, 0x2000 * index)  # sh_offset
        struct.pack_into("<Q", entry, 32, 8 * index)  # sh_size
        struct.pack_into("<Q", entry, 48, 8)  # sh_addralign
        sections += entry

    strtab = bytearray(SHDR_SIZE)
    struct.pack_into("<I", strtab, 0, offsets[".shstrtab"])
    struct.pack_into("<I", strtab, 4, 3)  # SHT_STRTAB
    struct.pack_into("<Q", strtab, 24, table_offset)
    struct.pack_into("<Q", strtab, 32, len(table))
    sections += strtab

    return bytes(header) + bytes(sections) + table


def flags_of(blob, wanted):
    """Read one section's sh_flags back out, without using the code under test."""
    (shoff,) = struct.unpack_from("<Q", blob, 0x28)
    shentsize, shnum, shstrndx = struct.unpack_from("<HHH", blob, 0x3A)
    (table_offset,) = struct.unpack_from("<Q", blob, shoff + shstrndx * shentsize + 24)
    for index in range(shnum):
        at = shoff + index * shentsize
        (name_index,) = struct.unpack_from("<I", blob, at)
        start = table_offset + name_index
        name = blob[start : blob.index(b"\0", start)].decode()
        if name == wanted:
            return struct.unpack_from("<Q", blob, at + 8)[0]
    raise AssertionError(f"no section named {wanted}")


def write_object(tmp_path, names, flags=None, **kwargs):
    path = tmp_path / "object.o"
    path.write_bytes(make_object(names, flags, **kwargs))
    return str(path)


class TestSettingTheFlag:
    def test_the_retain_bit_goes_on_and_nothing_else_changes(self, tmp_path):
        """Two bytes move, and no others.

        Counting the differing bytes is what makes this a real check
        rather than a restatement: a setter that also clobbered
        sh_addralign, or wrote the whole header back in a different
        layout, passes a flags-only comparison and fails this.
        """
        path = write_object(tmp_path, ["__managedcode", "__modules", ".text"])
        before = open(path, "rb").read()

        retain_sections.retain(path, ["__modules"])

        after = open(path, "rb").read()
        assert len(after) == len(before)

        differing = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
        assert len(differing) == 2, f"expected two changed bytes, got {len(differing)}"
        # One is EI_OSABI, without which binutils reads the flag as an
        # unnamed OS-specific bit. The other is the third byte of the
        # little-endian sh_flags, where 0x200000 lives.
        assert differing[0] == EI_OSABI
        assert after[EI_OSABI] == ELFOSABI_GNU
        assert (before[differing[1]] ^ after[differing[1]]) == 0x20

        assert flags_of(after, "__modules") == flags_of(before, "__modules") | SHF_GNU_RETAIN
        # The measured value on a real ILC object, kept as a number so a
        # change of meaning shows up as a changed expectation.
        assert flags_of(after, "__modules") == 0x200003

    def test_a_section_whose_name_merely_contains_the_wanted_one_is_left_alone(self, tmp_path):
        """The real object carries `.rela__modules` beside `__modules`.

        Without a decoy, a setter that matched by prefix or by substring
        passes every test here, and it would retain sections nobody asked
        for. Both mutations are caught by this one.
        """
        path = write_object(tmp_path, ["__modules", ".rela__modules", "__modules_extra"])

        retain_sections.retain(path, ["__modules"])

        after = open(path, "rb").read()
        assert flags_of(after, "__modules") & SHF_GNU_RETAIN
        assert not flags_of(after, ".rela__modules") & SHF_GNU_RETAIN
        assert not flags_of(after, "__modules_extra") & SHF_GNU_RETAIN

    def test_the_sections_not_named_are_left_alone(self, tmp_path):
        path = write_object(tmp_path, ["__managedcode", "__modules", "__unbox"])
        before = open(path, "rb").read()

        retain_sections.retain(path, ["__modules"])

        after = open(path, "rb").read()
        for other in ("__managedcode", "__unbox"):
            assert flags_of(after, other) == flags_of(before, other)
            assert not flags_of(after, other) & SHF_GNU_RETAIN

    def test_several_sections_at_once(self, tmp_path):
        path = write_object(tmp_path, ["__modules", "__managedcode", ".text"])

        changed = retain_sections.retain(path, ["__modules", "__managedcode"])

        assert sorted(changed) == ["__managedcode", "__modules"]
        after = open(path, "rb").read()
        assert flags_of(after, "__modules") & SHF_GNU_RETAIN
        assert flags_of(after, "__managedcode") & SHF_GNU_RETAIN
        assert not flags_of(after, ".text") & SHF_GNU_RETAIN

    def test_running_it_twice_leaves_the_same_bytes(self, tmp_path):
        path = write_object(tmp_path, ["__modules"])
        retain_sections.retain(path, ["__modules"])
        once = open(path, "rb").read()

        retain_sections.retain(path, ["__modules"])

        assert open(path, "rb").read() == once

    def test_a_section_that_is_already_retained_stays_retained(self, tmp_path):
        path = write_object(tmp_path, ["__modules"], {"__modules": SHF_ALLOC | SHF_GNU_RETAIN})

        retain_sections.retain(path, ["__modules"])

        assert flags_of(open(path, "rb").read(), "__modules") == SHF_ALLOC | SHF_GNU_RETAIN


class TestRefusing:
    """Every refusal here is a build that should stop rather than ship.

    An archive that silently missed the flag links on GNU ld and fails on
    lld, which is the failure that reached a release once already. So a
    named section that is not there is an error, not a skip.
    """

    def test_a_section_that_is_absent_is_an_error(self, tmp_path):
        path = write_object(tmp_path, ["__managedcode", ".text"])

        with pytest.raises(ValueError) as problem:
            retain_sections.retain(path, ["__modules"])

        assert "__modules" in str(problem.value)

    def test_one_absent_section_fails_the_whole_call(self, tmp_path):
        path = write_object(tmp_path, ["__modules"])

        with pytest.raises(ValueError):
            retain_sections.retain(path, ["__modules", "__nosuchsection"])

    def test_a_file_that_is_not_elf_is_an_error(self, tmp_path):
        path = tmp_path / "not-elf.o"
        path.write_bytes(b"!<arch>\n" + b"\0" * 128)

        with pytest.raises(ValueError) as problem:
            retain_sections.retain(str(path), ["__modules"])

        assert "not an ELF file" in str(problem.value)

    def test_a_32_bit_object_is_an_error(self, tmp_path):
        path = write_object(tmp_path, ["__modules"], elf_class=1)

        with pytest.raises(ValueError) as problem:
            retain_sections.retain(path, ["__modules"])

        assert "ELF64" in str(problem.value)

    def test_a_big_endian_object_is_an_error(self, tmp_path):
        path = write_object(tmp_path, ["__modules"], endianness=2)

        with pytest.raises(ValueError) as problem:
            retain_sections.retain(path, ["__modules"])

        assert "little-endian" in str(problem.value)


class TestTheCommandLine:
    """stage.sh calls this as a program, so the exit codes are the contract."""

    def _run(self, *args):
        return subprocess.run(
            [sys.executable, SCRIPT, *args], capture_output=True, text=True, check=False
        )

    def test_it_reports_what_it_changed(self, tmp_path):
        path = write_object(tmp_path, ["__modules"])

        result = self._run(path, "__modules")

        assert result.returncode == 0
        assert "__modules" in result.stdout
        assert "0x200003" in result.stdout

    def test_a_missing_section_exits_non_zero(self, tmp_path):
        path = write_object(tmp_path, [".text"])

        result = self._run(path, "__modules")

        assert result.returncode == 1
        assert "__modules" in result.stderr

    def test_no_section_named_exits_non_zero(self, tmp_path):
        path = write_object(tmp_path, ["__modules"])

        assert self._run(path).returncode == 2


class TestTheBuildActuallyRunsIt:
    """A script nothing calls is a script that fixes nothing.

    The archive that failed had every other guard in place: the C smoke
    linked it, the verifier accepted it, and the one step that would have
    caught the problem was skipped on that host. So these check the wiring
    itself rather than trusting that a helper in scripts/ gets used.
    """

    def test_the_script_is_in_the_build_context(self, tmp_path):
        import build_acadsharp as ba

        ba._write_build_context(str(tmp_path), "linux")

        assert (tmp_path / "retain_sections.py").is_file()

    @pytest.mark.parametrize("plat", ["linux", "musl"])
    def test_the_dockerfile_copies_it_and_installs_a_python_to_run_it(self, plat):
        import build_acadsharp as ba

        dockerfile = ba.make_dockerfile("3.7.1-viprs.1", plat, "amd64")

        assert "retain_sections.py" in dockerfile
        assert "python3" in dockerfile

    @pytest.mark.parametrize("plat", ["linux", "musl"])
    def test_staging_retains_the_module_section(self, plat):
        import build_acadsharp as ba

        stage = ba.stage_script(plat)

        assert "retain_sections.py" in stage
        assert "__modules" in stage

    def test_it_runs_before_the_archive_is_assembled(self):
        """Flags set after `ar` would go into a file nothing reads again."""
        import build_acadsharp as ba

        stage = ba.stage_script("linux")

        assert stage.index("retain_sections.py") < stage.index("ar qc")

    def test_a_failure_to_retain_stops_the_static_archive_shipping(self):
        """Shared-only is a documented outcome; an unlinkable archive is not.

        This asserts the branch rather than the substring. The first
        version looked for `MERGE_OK=0` within 400 characters and could
        not tell "sets a variable that stops the ship" from "sets a
        variable nothing reads": appending `|| true` to the retain call
        re-ships the exact #67 archive and kept it green.
        """
        import build_acadsharp as ba

        stage = ba.stage_script("linux")
        at = stage.index("retain_sections.py")
        call = stage[at : stage.index("fact retained_module_sections", at)]

        assert "|| true" not in call, "a swallowed failure leaves the archive unretained"
        assert "else" in call and "MERGE_OK=0" in call, (
            "the failure arm has to set MERGE_OK=0, not just run and be ignored"
        )
        # And something downstream has to still read it, or the assignment
        # above is a write to a dead variable.
        assert 'if [ "$MERGE_OK" != "1" ]' in stage[at:], (
            "MERGE_OK is set and never consumed after this point"
        )

    def test_the_loop_actually_visits_the_merged_objects(self):
        """Pointing the loop at an empty directory kept every test green.

        `RETAINED` would be 0, the archives would be dropped, and the cell
        would publish shared-only, which is the failure this whole change
        exists to stop.
        """
        import build_acadsharp as ba

        stage = ba.stage_script("linux")
        at = stage.index("RETAINED=0")
        loop = stage[at : stage.index("fact retained_module_sections", at)]

        assert '"$MERGE_DIR"/*.o' in loop, (
            "the loop has to read the extracted members, which is where the objects are"
        )


def make_archive(members):
    """A GNU `ar` archive of (name, bytes) pairs, short names only."""
    out = bytearray(b"!<arch>\n")
    for name, blob in members:
        header = f"{name:<16}{'0':<12}{'0':<6}{'0':<6}{'100644':<8}{len(blob):<10}".encode()
        out += header + b"`\n" + blob
        if len(blob) % 2:
            out += b"\n"
    return bytes(out)


class TestReadingAnArchive:
    """The check has to read a finished archive, not the loose objects.

    What ships is the archive, and the one thing that mattered here was
    whether the flag survived into it.
    """

    def test_it_finds_the_member_carrying_the_section(self, tmp_path):
        good = make_object(
            ["__modules"], {"__modules": SHF_ALLOC | SHF_GNU_RETAIN}, osabi=ELFOSABI_GNU
        )
        path = tmp_path / "lib.a"
        path.write_bytes(make_archive([("other.o", make_object([".text"])), ("mod.o", good)]))

        found = retain_sections.check(str(path), ["__modules"])

        assert found == [("mod.o", "__modules", SHF_ALLOC | SHF_GNU_RETAIN, True)]

    def test_an_unretained_section_is_reported_as_such(self, tmp_path):
        path = tmp_path / "lib.a"
        path.write_bytes(make_archive([("mod.o", make_object(["__modules"]))]))

        found = retain_sections.check(str(path), ["__modules"])

        assert [entry[3] for entry in found] == [False]

    def test_a_bare_object_works_too(self, tmp_path):
        path = write_object(tmp_path, ["__modules"])

        found = retain_sections.check(path, ["__modules"])

        assert found[0][1] == "__modules"

    def test_members_that_are_not_elf64_are_skipped_rather_than_misread(self, tmp_path):
        """An archive's symbol index is not an object and must not be parsed as one."""
        path = tmp_path / "lib.a"
        path.write_bytes(
            make_archive(
                [
                    ("/", b"\x00" * 64),
                    ("junk.o", b"nothing like an ELF file at all, but long enough" * 4),
                    (
                        "mod.o",
                        make_object(
                            ["__modules"], {"__modules": SHF_GNU_RETAIN}, osabi=ELFOSABI_GNU
                        ),
                    ),
                ]
            )
        )

        found = retain_sections.check(str(path), ["__modules"])

        assert [entry[0] for entry in found] == ["mod.o"]

    def test_finding_nothing_is_an_error_not_a_pass(self, tmp_path):
        """`nothing to check` and `everything checked was fine` must differ.

        This is the whole reason the check exists. A verifier that reports
        green because it found nothing to look at is what shipped four
        archives with an unlinkable static half.
        """
        path = tmp_path / "lib.a"
        path.write_bytes(make_archive([("plain.o", make_object([".text"]))]))

        with pytest.raises(ValueError) as problem:
            retain_sections.check(str(path), ["__modules"])

        assert "nothing to check" in str(problem.value)


class TestCheckOnTheCommandLine:
    def _run(self, *args):
        return subprocess.run(
            [sys.executable, SCRIPT, "--check", *args], capture_output=True, text=True, check=False
        )

    def test_a_retained_section_exits_zero(self, tmp_path):
        path = tmp_path / "lib.a"
        path.write_bytes(
            make_archive(
                [
                    (
                        "mod.o",
                        make_object(
                            ["__modules"], {"__modules": SHF_GNU_RETAIN}, osabi=ELFOSABI_GNU
                        ),
                    )
                ]
            )
        )

        result = self._run(str(path), "__modules")

        assert result.returncode == 0
        assert "retained" in result.stdout

    def test_an_unretained_section_exits_non_zero_and_says_why(self, tmp_path):
        path = tmp_path / "lib.a"
        path.write_bytes(make_archive([("mod.o", make_object(["__modules"]))]))

        result = self._run(str(path), "__modules")

        assert result.returncode == 1
        assert "NOT RETAINED" in result.stdout
        assert "start-stop-gc" in result.stderr

    def test_it_round_trips_with_the_setter(self, tmp_path):
        """Set the flag, then check it, through the two public entry points."""
        obj = write_object(tmp_path, ["__modules"])
        assert self._run(obj, "__modules").returncode == 1

        retain_sections.retain(obj, ["__modules"])

        assert self._run(obj, "__modules").returncode == 0


class TestAStaticTargetCannotShipSharedOnly:
    """The degradation path that would have published this silently.

    Every failure in the staging script's static branch drops the two
    archives and lets the cell finish, so the manifest says
    `static_certified: false`, the verifier accepts it because shared-only
    is a legal outcome, and the release page shows `false` in a column.
    Retaining `__modules` adds a new way to reach that state, and one that
    can hit every ELF target at once, so the driver now refuses it.
    """

    def test_every_static_target_must_certify(self):
        import build_acadsharp as ba

        driver = open(ba.__file__).read()
        at = driver.index('in STATIC_TARGETS and facts.get("static_ok")')

        assert "raise RuntimeError" in driver[at : at + 400]

    def test_it_is_checked_before_the_archive_is_verified(self):
        """Verifying first would report the shared-only archive as fine."""
        import build_acadsharp as ba

        driver = open(ba.__file__).read()

        assert driver.index('in STATIC_TARGETS and facts.get("static_ok")') < driver.index(
            "verify_archive(path, log_file, job)"
        )

    def test_the_four_linux_rids_are_the_static_targets(self):
        """The mac cell has never built a static half, and cannot: NativeAOT
        emits Mach-O there and the driver sets WANT_STATIC=0 for it. So
        `static_ok 0` on mac is the configuration, not a regression."""
        import build_acadsharp as ba

        assert ba.STATIC_TARGETS == (
            "linux-x64",
            "linux-arm64",
            "linux-musl-x64",
            "linux-musl-arm64",
        )
        assert not any("osx" in rid for rid in ba.STATIC_TARGETS)


class TestTheFlagNeedsTheOsAbiByteToo:
    """Half the fix is invisible to binutils without it.

    `SHF_GNU_RETAIN` sits in the OS-specific flag range, so binutils reads
    it as "retain" only when the object declares a GNU OS ABI. On an
    ELFOSABI_NONE object `readelf` prints `WAo`, an unnamed OS-specific
    flag, rather than `WAR`. lld honours the bit either way, which is why
    the lld-only measurement said the byte was unnecessary and the
    binutils measurement said it was not. GAS stamps ELFOSABI_GNU
    whenever it assembles a section with the `R` flag, so setting it is
    what a compiler would have produced.
    """

    def test_setting_the_flag_also_sets_the_os_abi(self, tmp_path):
        path = write_object(tmp_path, ["__modules"])
        assert open(path, "rb").read()[EI_OSABI] == 0

        retain_sections.retain(path, ["__modules"])

        assert open(path, "rb").read()[EI_OSABI] == ELFOSABI_GNU

    def test_an_object_with_the_flag_but_no_gnu_abi_is_not_retained(self, tmp_path):
        """The state the first version of this fix shipped in."""
        path = tmp_path / "lib.a"
        path.write_bytes(
            make_archive(
                [("mod.o", make_object(["__modules"], {"__modules": SHF_GNU_RETAIN}, osabi=0))]
            )
        )

        found = retain_sections.check(str(path), ["__modules"])

        assert [entry[3] for entry in found] == [False]

    def test_the_os_abi_is_not_touched_when_no_section_matched(self, tmp_path):
        """A refusal must not leave the object half-changed."""
        path = write_object(tmp_path, [".text"])
        before = open(path, "rb").read()

        with pytest.raises(ValueError):
            retain_sections.retain(path, ["__modules"])

        assert open(path, "rb").read() == before


class TestAllThreeEncapsulationSectionsAreRetained:
    """`__modules` is the one that dies; it is not the only one exposed.

    The bootstrapper references six encapsulation symbols, around
    `__modules`, `__managedcode` and `__unbox`. The other two survive only
    because ILC emits each as one monolithic section, so any live symbol
    keeps the whole thing: measured on the real object, 59471 relocations
    reach `__managedcode` and 1259 reach `__unbox`, against zero for
    `__modules`. That is a layout accident, not a guarantee, and retaining
    all three costs nothing (identical binary size, byte-identical
    `.init_array`).
    """

    def test_staging_names_all_three(self):
        import build_acadsharp as ba

        stage = ba.stage_script("linux")
        at = stage.index("retain_sections.py")
        call = stage[at : at + 200]

        for section in ("__modules", "__managedcode", "__unbox"):
            assert section in call, f"{section} is exposed the same way and is not retained"

    def test_the_verifier_checks_all_three(self):
        with open(os.path.join(SCRIPTS, "verify_archive.sh")) as f:
            verifier = f.read()

        at = verifier.index("--check")
        call = verifier[at : at + 200]

        for section in ("__modules", "__managedcode", "__unbox"):
            assert section in call

    def test_there_is_no_upper_bound_on_how_many_objects_carry_it(self):
        """An exact count would redden a release for a working archive.

        `__modules` is an encapsulation array and N contributors is its
        designed shape, so the day ILC splits its output or a second
        NativeAOT library joins the merge, an `-ne 1` check fails on an
        archive that links perfectly.
        """
        import build_acadsharp as ba

        stage = ba.stage_script("linux")

        assert '"$RETAINED" -lt 1' in stage
        assert '"$RETAINED" -ne 1' not in stage


class TestTheFixtureSourcesStillFormat:
    """`INIT_SOURCE` is a %-formatted template, and a comment broke it.

    I wrote a comment containing a literal `%d` inside that template, so
    `INIT_SOURCE % value` raised `TypeError: not enough arguments`. Every
    test that builds a compiled fixture died, and none of them run on a
    host without a C compiler, so my local run was green and CI was not.
    """

    def test_both_variants_render_with_no_specifier_left(self):
        import test_verify_archive as fixtures

        for value in (0, 1):
            rendered = fixtures.INIT_SOURCE % value
            assert "%" not in rendered, (
                "a stray format specifier survives, so the next one takes an argument "
                "that is not there"
            )
