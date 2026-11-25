"""Red tests for #170: the hayro spike has to cover what #164 left open.

The spike in tools/hayro-spike and its write-up in docs/hayro-spike.md are the
deliverable, so these tests read what the spike committed: the harness source
(does it have a mode for each open question), the fixtures it used, the result
CSVs it produced, and the write-up (does it carry the numbers and the decision).
Nothing here needs Rust or a PDF library, so it runs in the repo's normal pytest.
"""

import csv
import hashlib
import re
from pathlib import Path

import pytest

SPIKE = Path(__file__).resolve().parent.parent
REPO = SPIKE.parent.parent
DOC = REPO / "docs" / "hayro-spike.md"
FIXTURES = SPIKE / "fixtures"
RESULTS = SPIKE / "results"

# The rotation correctness test in libviprs-tests (rotation_libvips_pdfium_parity.rs)
# compares per-pixel mean absolute difference against this.
ROTATION_TOLERANCE = 2.0

MODES = ["rotation", "fullres", "strips", "formats", "hostile", "threads", "password"]


def rows(name):
    path = RESULTS / f"{name}.csv"
    assert path.is_file(), f"{path} is missing, run run_cases.py and commit its output"
    with path.open(newline="") as f:
        out = list(csv.DictReader(f))
    assert out, f"{path} has a header and no rows"
    return out


def doc_text():
    assert DOC.is_file()
    return DOC.read_text()


def section(heading):
    """The body of the level-2 section whose title contains `heading`."""
    parts = re.split(r"^## ", doc_text(), flags=re.M)
    for part in parts[1:]:
        title, _, body = part.partition("\n")
        if heading.lower() in title.lower():
            return body
    pytest.fail(f"docs/hayro-spike.md has no section about {heading!r}")


# harness


@pytest.mark.parametrize("mode", MODES)
def test_harness_has_a_mode_for_each_open_question(mode):
    src = (SPIKE / "src" / "main.rs").read_text()
    assert f'"{mode}"' in src, f"main.rs does not dispatch a {mode!r} mode"


def test_harness_still_does_the_old_single_render():
    # run.py from #164 calls: hayro-spike <pdf> <png> <maxdim>
    assert (
        "into_png"
        in (SPIKE / "src" / "main.rs").read_text() + (SPIKE / "src" / "cases.rs").read_text()
    )


def test_strips_use_render_into_with_a_transform():
    src = (SPIKE / "src" / "cases.rs").read_text()
    assert "render_into" in src
    assert "translate" in src.lower()


@pytest.mark.parametrize("probe", ["pdf_send", "pdf_sync", "cache_send", "context_send"])
def test_thread_probes_exist(probe):
    assert (SPIKE / "examples" / f"{probe}.rs").is_file()


def test_driver_scripts_exist():
    for name in ["run_cases.py", "gen_fixtures.py"]:
        assert (SPIKE / name).is_file()


# fixtures


def test_fixture_manifest_matches_the_files():
    manifest = FIXTURES / "MANIFEST.csv"
    assert manifest.is_file()
    listed = list(csv.DictReader(manifest.open(newline="")))
    names = {r["name"] for r in listed}
    for needed in [
        "canonical.pdf",
        "canonical_rotated_90.pdf",
        "canonical_rotated_180.pdf",
        "canonical_rotated_270.pdf",
        "password.pdf",
        "owner-password-only.pdf",
    ]:
        assert needed in names
    for r in listed:
        data = (FIXTURES / r["name"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == r["sha256"], r["name"]
        assert r["source"], f"{r['name']} has no recorded origin"


@pytest.mark.parametrize("angle", ["", "_rotated_90", "_rotated_180", "_rotated_270"])
def test_rotation_goldens_are_here(angle):
    assert (FIXTURES / "goldens" / f"canonical{angle}.png").is_file()


def test_no_fixture_is_larger_than_a_megabyte():
    # the big sheets are named in the results, never committed
    for p in FIXTURES.rglob("*"):
        if p.is_file():
            assert p.stat().st_size < 1_000_000, p


# results


def test_rotation_has_all_four_goldens_and_the_dimensions():
    rs = rows("rotation")
    assert {r["file"] for r in rs} == {
        "canonical.pdf",
        "canonical_rotated_90.pdf",
        "canonical_rotated_180.pdf",
        "canonical_rotated_270.pdf",
    }
    for r in rs:
        assert r["golden_size"] and r["hayro_size"]
        mean = float(r["mean_abs_vs_golden"])
        assert (r["within_tolerance"] == "yes") == (mean <= ROTATION_TOLERANCE), r["file"]


def test_fullres_covers_two_a0_sheets_at_300_dpi():
    rs = rows("fullres")
    rendered = {(r["file"], r["dpi"]) for r in rs if r["status"] == "OK"}
    a0 = {f for f, dpi in rendered if dpi == "300"}
    assert len(a0) >= 2, rendered
    for r in rs:
        assert r["status"] in {"OK", "ERR", "PANIC", "CRASH", "TIMEOUT", "OOM"}
        if r["status"] == "OK":
            assert float(r["hayro_s"]) > 0 and float(r["hayro_peak_rss_mb"]) > 0


def test_fullres_records_the_u16_limit():
    assert any(r["file"].startswith("u16-limit") for r in rows("fullres"))


def test_strips_compare_to_a_single_shot_render_and_to_cost():
    rs = rows("strips")
    assert {"full_s", "strips_s", "max_abs_diff", "strip_rows", "peak_rss_mb"} <= set(rs[0])
    assert any(r["layout"] == "strips" for r in rs)
    assert any(r["layout"] == "tiles" for r in rs)
    assert any(r["file"].startswith("canonical_rotated") for r in rs)
    assert any("A0" in r["file"] for r in rs)


def test_formats_cover_jbig2_jpx_and_ccitt():
    rs = rows("formats")
    assert {"jbig2", "jpx", "ccitt"} <= {r["codec"] for r in rs}
    for codec in ["jbig2", "jpx", "ccitt"]:
        assert any(r["codec"] == codec and r["status"] == "OK" for r in rs) or any(
            r["codec"] == codec and r["status"] != "OK" and r["note"] for r in rs
        ), f"{codec} has no row that either rendered or says why not"


def test_hostile_has_the_attack_classes_and_a_verdict_for_each():
    rs = rows("hostile")
    cases = {r["case"] for r in rs}
    for needed in [
        "empty",
        "garbage",
        "truncated-half",
        "xref-offsets-wrong",
        "startxref-wrong",
        "trailer-missing",
        "page-tree-cycle",
        "form-xobject-recursion",
        "deep-q-nesting",
        "huge-mediabox",
        "flate-bomb-content",
        "huge-declared-image",
        "mutants",
    ]:
        assert needed in cases, needed
    for r in rs:
        assert r["hayro"] in {"OK", "ERR", "PANIC", "CRASH", "TIMEOUT", "OOM"}
        assert r["pdfium"] in {"OK", "ERR", "CRASH", "TIMEOUT", "OOM", "n/a"}


def test_xref_recovery_is_measured_against_pdfium():
    rs = {r["case"]: r for r in rows("hostile")}
    for case in ["xref-offsets-wrong", "startxref-wrong", "trailer-missing"]:
        assert rs[case]["hayro"] != "" and rs[case]["pdfium"] != ""


def test_threads_has_probes_and_scaling():
    rs = rows("threads")
    kinds = {r["kind"] for r in rs}
    assert {"probe", "scaling", "determinism"} <= kinds
    probes = {r["name"]: r["result"] for r in rs if r["kind"] == "probe"}
    assert set(probes) == {"Pdf: Send", "Pdf: Sync", "RenderCache: Send", "RenderContext: Send"}
    assert {r["threads"] for r in rs if r["kind"] == "scaling"} >= {"1", "2", "4"}
    assert all(
        r["result"] in {"yes", "no", "same", "differs"} or r["kind"] == "scaling" for r in rs
    )


def test_password_separates_none_wrong_and_right():
    rs = {r["case"]: r for r in rows("password")}
    for needed in [
        "user-password:none",
        "user-password:wrong",
        "user-password:right",
        "owner-password-only:none",
    ]:
        assert needed in rs, needed
    assert rs["user-password:none"]["hayro"] != rs["user-password:right"]["hayro"]


# write-up


@pytest.mark.parametrize(
    "heading",
    ["Rotation", "Full resolution", "Strip", "JBIG2", "Hostile", "Thread", "Password", "Decision"],
)
def test_doc_has_a_section_for_each_item(heading):
    assert section(heading).strip()


def test_doc_no_longer_lists_the_items_as_not_covered():
    assert "Not covered yet" not in doc_text()


def test_doc_sections_carry_numbers_not_only_prose():
    for heading in ["Rotation", "Full resolution", "Strip", "Hostile", "Thread"]:
        assert re.search(r"\d+(\.\d+)?\s*(s|MB|%|ms|x)\b", section(heading)), heading


def test_doc_decision_is_a_recommendation_and_two_alternatives():
    body = section("Decision")
    assert "pdf-hayro" in body
    assert re.search(r"^\W*Recommendation", body, flags=re.M | re.I)
    alts = re.findall(r"^\W*Alternative\s+[AB]\b", body, flags=re.M | re.I)
    assert len(alts) == 2, alts


def test_doc_names_the_arch_and_pdfium_baseline_of_the_new_runs():
    text = doc_text().lower()
    assert "arm64" in text
    assert "pypdfium2" in text


def test_doc_and_harness_follow_the_house_style():
    for p in [DOC, SPIKE / "run_cases.py", SPIKE / "gen_fixtures.py"]:
        assert p.is_file(), p
        t = p.read_text()
        assert "\u2014" not in t, f"em dash in {p.name}"
        assert not re.search(r"\b(claude|anthropic|chatgpt|generated by|co-authored)\b", t, re.I)
        assert "/Users/" not in t and "/home/" not in t, f"local path in {p.name}"
