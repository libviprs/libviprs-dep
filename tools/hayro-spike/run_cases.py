"""Runs the hayro-spike cases for #170 and writes results/*.csv.

Run it from tools/hayro-spike, in a native Linux container that has the release build of
hayro-spike (cargo build --release), pypdfium2, Pillow, numpy and pikepdf, with the sheets from
the pdf-candidates set in PDFS (default ../../pdfs-all, holding arch/, iso/ and us/). The PDFs
are not committed, the names are in the CSVs.

  python3 run_cases.py rotation|fullres|strips|formats|hostile|threads|password|all

The pdfium side is pypdfium2's bundled pdfium, not the build libviprs-dep pins. Every pdfium
call runs in a child process (python3 run_cases.py pdfium ...) so a crash is a row and not the
end of the run.
"""

import csv
import hashlib
import os
import re
import resource
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
BIN = os.environ.get("HAYRO_SPIKE", str(HERE / "target" / "release" / "hayro-spike"))
PDFS = Path(os.environ.get("PDFS", str(HERE.parent.parent / "pdfs-all")))
FIX = HERE / "fixtures"
OUT = HERE / "results"
ROTATION_TOLERANCE = 2.0  # libviprs-tests rotation_libvips_pdfium_parity.rs
A0 = [
    "iso/arasikere-gp-2015-A0",
    "iso/architectural-orders-table-A0",
    "iso/covid-vaccination-sites-england-A0",
]
KB = 1024.0


def say(*a):
    print(*a, file=sys.stderr, flush=True)


def write_csv(name, header, rows):
    OUT.mkdir(exist_ok=True)
    with (OUT / f"{name}.csv").open("w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        w.writerows([[r.get(h, "") for h in header] for r in rows])
    say(f"wrote results/{name}.csv ({len(rows)} rows)")


def kv(line):
    return dict(t.split("=", 1) for t in line.split() if "=" in t)


def classify(r, secs):
    """Outcome of a child process: its own status line, or what its death says."""
    lines = [x for x in (r.stderr or "").splitlines() if x.strip()]
    keys = ("memory allocation", "overflowed", "panicked")
    err = next((x for x in lines if any(k in x for k in keys)), lines[-1] if lines else "")[:200]
    err = re.sub(r"^.*registry/src/[^/]+/", "", err)
    if r.returncode == 0:
        line = [x for x in r.stdout.splitlines() if x.strip()]
        d = kv(line[-1]) if line else {}
        return d.get("status", "ERR"), d, err
    if "memory allocation" in err or r.returncode in (-9, 137):
        return "OOM", {}, err
    if "overflowed its stack" in err:
        return "CRASH", {"note": "stack_overflow"}, err
    return ("PANIC" if r.returncode == 101 else "CRASH"), {"note": f"rc={r.returncode}"}, err


def run(cmd, timeout=300, as_mb=0):
    def limit():
        if as_mb:
            resource.setrlimit(resource.RLIMIT_AS, (as_mb << 20, as_mb << 20))

    t = time.time()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, preexec_fn=limit)
    except subprocess.TimeoutExpired:
        return "TIMEOUT", {}, time.time() - t, ""
    secs = time.time() - t
    status, d, err = classify(r, secs)
    return status, d, secs, err


def hayro(*args, timeout=300, as_mb=0):
    return run([BIN, *map(str, args)], timeout, as_mb)


def pdfium(*args, timeout=300, as_mb=0):
    return run([sys.executable, str(Path(__file__)), "pdfium", *map(str, args)], timeout, as_mb)


def mb(d, key="rss_kb"):
    return f"{float(d[key]) / KB:.0f}" if key in d else ""


def gray_diff(raw_path, pdfium_gray, w, h):
    """Mean absolute grey difference and share of pixels over 32 levels (hayro vs pdfium)."""
    a = np.memmap(raw_path, dtype=np.uint8, mode="r", shape=(h, w, 4))
    b = np.memmap(pdfium_gray, dtype=np.uint8, mode="r", shape=(h, w))
    total, over, ink = 0.0, 0, 0
    for r0 in range(0, h, 256):
        ga = a[r0 : r0 + 256, :, :3].astype(np.float32) @ np.array(
            [0.299, 0.587, 0.114], np.float32
        )
        d = np.abs(ga - b[r0 : r0 + 256].astype(np.float32))
        total += float(d.sum())
        over += int((d > 32).sum())
        ink += int((b[r0 : r0 + 256] < 200).sum())
    return total / (w * h), over / (w * h) * 100, ink / (w * h) * 100


# pdfium child process ---------------------------------------------------------------------------


def pdfium_child(args):
    import pypdfium2 as pdfium_lib

    op = args[0]
    rss = lambda: resource.getrusage(resource.RUSAGE_SELF).ru_maxrss  # noqa: E731
    try:
        if op == "render":
            path, page, scale, out = args[1], int(args[2]), float(args[3]), args[4]
            t = time.time()
            doc = pdfium_lib.PdfDocument(path)
            bm = doc[page].render(scale=scale)
            secs = time.time() - t
            arr = bm.to_numpy()
            h, w = arr.shape[:2]
            if out != "-":
                with open(out, "wb") as f:
                    for r0 in range(0, h, 256):
                        c = arr[r0 : r0 + 256, :, :3].astype(np.float32)
                        g = c @ np.array([0.114, 0.587, 0.299], np.float32)  # BGR
                        f.write(g.astype(np.uint8).tobytes())
            print(f"status=OK size={w}x{h} secs={secs:.4f} rss_kb={rss()}")
        elif op == "rgba":
            path, page, scale, out = args[1], int(args[2]), float(args[3]), args[4]
            t = time.time()
            doc = pdfium_lib.PdfDocument(path)
            im = doc[page].render(scale=scale).to_pil().convert("RGBA")
            secs = time.time() - t
            im.save(out)
            print(f"status=OK size={im.size[0]}x{im.size[1]} secs={secs:.4f} rss_kb={rss()}")
        elif op == "strips":
            path, page, scale, rows = args[1], int(args[2]), float(args[3]), int(args[4])
            doc = pdfium_lib.PdfDocument(path)
            pg = doc[page]
            t = time.time()
            full = pg.render(scale=scale)
            full_s = time.time() - t
            h = full.height
            del full
            t = time.time()
            n = 0
            for r0 in range(0, h, rows):
                r1 = min(h, r0 + rows)
                pg.render(scale=scale, crop=(0, (h - r1) / scale, 0, r0 / scale))
                n += 1
            print(
                f"status=OK pieces={n} full_s={full_s:.4f} "
                f"crop_s={time.time() - t:.4f} rss_kb={rss()}"
            )
        elif op == "open":
            path, pw = args[1], (args[2] if len(args) > 2 and args[2] != "-" else None)
            t = time.time()
            doc = pdfium_lib.PdfDocument(path, password=pw)
            pg = doc[0]
            w, h = pg.get_size()
            if not (0 < w < 1e7 and 0 < h < 1e7):
                print(f"status=ERR note=BadPageSize_{w}x{h}")
                return
            s = 800 / max(w, h)
            bm = pg.render(scale=s)
            secs = time.time() - t
            digest = hashlib.md5(bm.to_numpy().tobytes()).hexdigest()[:12]
            print(f"status=OK secs={secs:.4f} rss_kb={rss()} hash={digest}")
        elif op == "mutants":
            data = Path(args[1]).read_bytes()
            seed, count = int(args[2]), int(args[3])
            x = seed | 1
            ok = err = 0
            for _ in range(count):
                d = bytearray(data)
                x = (x * 6364136223846793005 + 1442695040888963407) % (1 << 64)
                kind = x % 3
                if kind == 0:
                    for _ in range(1 + (x >> 8) % 8):
                        x = (x * 6364136223846793005 + 1442695040888963407) % (1 << 64)
                        d[(x >> 3) % len(d)] = x & 255
                elif kind == 1:
                    del d[(x >> 3) % len(d) :]
                else:
                    a = (x >> 3) % len(d)
                    del d[a : a + (x >> 40) % 256]
                try:
                    doc = pdfium_lib.PdfDocument(bytes(d))
                    pg = doc[0]
                    w, h = pg.get_size()
                    pg.render(scale=400 / max(w, h))
                    ok += 1
                except Exception:
                    err += 1
            print(f"status=DONE ok={ok} err={err} panic=0")
    except Exception as e:  # a typed refusal is an answer, not a crash
        name = type(e).__name__
        msg = str(e).replace(" ", "_")[:60]
        print(f"status=ERR note={name}:{msg}")


# steps ------------------------------------------------------------------------------------------


def step_rotation():
    from PIL import Image

    rows = []
    names = ["canonical", "canonical_rotated_90", "canonical_rotated_180", "canonical_rotated_270"]
    for n in names:
        pdf, golden = FIX / f"{n}.pdf", FIX / "goldens" / f"{n}.png"
        out = f"/tmp/rot-{n}.png"
        status, d, _, _ = hayro("rotation", pdf, out)
        gold = np.asarray(Image.open(golden).convert("RGBA"), dtype=np.int16)
        gsize = f"{gold.shape[1]}x{gold.shape[0]}"
        row = {"file": f"{n}.pdf", "status": status, "golden_size": gsize}
        row["page_pt"] = d.get("page_pt", "")
        row["hayro_plain_render_size"] = d.get("plain_render", "")
        row["hayro_size"] = d.get("pixmap", "")
        row["hayro_s"] = d.get("secs", "")
        if status == "OK":
            im = np.asarray(Image.open(out).convert("RGBA"), dtype=np.int16)
            hh, ww = min(im.shape[0], gold.shape[0]), min(im.shape[1], gold.shape[1])
            diff = np.abs(im[:hh, :ww] - gold[:hh, :ww])
            row["mean_abs_vs_golden"] = f"{diff.mean():.3f}"
            row["pct_over_32"] = f"{(diff.max(axis=2) > 32).mean() * 100:.3f}"
            row["within_tolerance"] = "yes" if diff.mean() <= ROTATION_TOLERANCE else "no"
        pst, pd_, _, _ = pdfium("rgba", pdf, 0, 1.0, f"/tmp/rot-{n}-pdfium.png")
        if pst == "OK":
            pim = np.asarray(Image.open(f"/tmp/rot-{n}-pdfium.png").convert("RGBA"), dtype=np.int16)
            hh, ww = min(pim.shape[0], gold.shape[0]), min(pim.shape[1], gold.shape[1])
            row["pdfium_mean_abs_vs_golden"] = (
                f"{np.abs(pim[:hh, :ww] - gold[:hh, :ww]).mean():.3f}"
            )
            row["pdfium_size"] = pd_.get("size", "")
        rows.append(row)
    cols = ["file", "status", "page_pt", "golden_size", "hayro_size", "hayro_plain_render_size"]
    cols += ["mean_abs_vs_golden", "pct_over_32", "within_tolerance", "pdfium_size"]
    cols += ["pdfium_mean_abs_vs_golden", "hayro_s"]
    write_csv("rotation", cols, rows)


def step_fullres():
    rows = []
    tmp = Path(tempfile.mkdtemp(prefix="fullres-"))
    for name in A0:
        for dpi in (150, 300):
            src = PDFS / f"{name}.pdf"
            say("fullres", name, dpi)
            gray = tmp / "pdfium.gray"
            pst, pd_, _, perr = pdfium("render", src, 0, dpi / 72, gray, timeout=900)
            row = {"file": Path(name).name, "dpi": dpi, "pdfium_status": pst}
            if pst != "OK":
                row.update(status="SKIP", note="pdfium did not render: " + perr[:100])
                rows.append(row)
                continue
            w, h = map(int, pd_["size"].split("x"))
            raw = tmp / "hayro.raw"
            status, d, secs, err = hayro("fullres", src, raw, w, h, 0, timeout=1800)
            row.update(status=status, pdfium_size=pd_["size"], pdfium_s=pd_["secs"])
            row["pdfium_peak_rss_mb"] = mb(pd_)
            row["hayro_wall_s"] = f"{secs:.2f}"
            if status == "OK":
                row["page_pt"] = d["page_pt"]
                row["got"] = d["got"]
                row["hayro_parse_s"] = d["parse_s"]
                row["hayro_s"] = f"{float(d['parse_s']) + float(d['render_s']):.4f}"
                row["hayro_peak_rss_mb"] = mb(d)
                row["mean_abs"], row["pct_diff32"], row["pdfium_ink_pct"] = (
                    f"{v:.3f}" for v in gray_diff(raw, gray, w, h)
                )
            else:
                row["note"] = (d.get("note", "") + " " + err[:100]).strip()
            rows.append(row)
            for p in (raw, gray):
                if p.exists():
                    p.unlink()
    # the u16 ceiling: the plain render() truncates width * scale to u16
    for sx in (100, 110, 150):
        status, d, secs, err = hayro("fullres", FIX / "canonical.pdf", "-", sx, 1, 0, "scale")
        row = {"file": f"u16-limit-x{sx}", "dpi": "", "status": status}
        if status == "OK":
            row.update(asked=d["asked"], got=d["got"], hayro_s=d["render_s"])
            row["hayro_peak_rss_mb"] = mb(d)
            row["note"] = "pixmap smaller than asked" if d["asked"] != d["got"] else "fits"
        else:
            row["note"] = f"asked {int(595 * sx)}x842; " + (d.get("note", "") or err[:100])
        rows.append(row)
    # and the widest exact pixmap that survives (the rasteriser works in 256 px wide tiles)
    for wpx in (65281, 65350, 65400, 65450, 65535):
        status, d, secs, err = hayro("fullres", FIX / "canonical.pdf", "-", wpx, 842, 0)
        row = {"file": f"u16-limit-exact-w{wpx}", "dpi": "", "status": status}
        if status == "OK":
            row.update(asked=d["asked"], got=d["got"], hayro_s=d["render_s"])
            row["hayro_peak_rss_mb"] = mb(d)
            row["note"] = "fits"
        else:
            row["note"] = f"asked {wpx}x842; " + (d.get("note", "") or err[:100])
        rows.append(row)
    cols = ["file", "dpi", "status", "page_pt", "asked", "got", "pdfium_size", "hayro_parse_s"]
    cols += ["hayro_s", "hayro_wall_s", "hayro_peak_rss_mb", "pdfium_s", "pdfium_peak_rss_mb"]
    cols += ["mean_abs", "pct_diff32", "pdfium_ink_pct", "note"]
    write_csv("fullres", cols, rows)


def step_strips():
    rows = []
    jobs = []
    for n in [
        "canonical",
        "canonical_rotated_90",
        "canonical_rotated_180",
        "canonical_rotated_270",
    ]:
        jobs.append((FIX / f"{n}.pdf", f"{n}.pdf", 1.0, "strips", 100, "check", 72))
        jobs.append((FIX / f"{n}.pdf", f"{n}.pdf", 1.0, "tiles", 128, "check", 72))
    for name in A0[:2]:
        src = PDFS / f"{name}.pdf"
        for layout, size in (("strips", 256), ("tiles", 512)):
            jobs.append((src, Path(name).name, 150 / 72, layout, size, "check", 150))
            jobs.append((src, Path(name).name, 150 / 72, layout, size, "stream", 150))
    jobs.append((PDFS / f"{A0[1]}.pdf", Path(A0[1]).name, 300 / 72, "strips", 256, "stream", 300))
    for src, name, scale, layout, size, mode, dpi in jobs:
        say("strips", name, dpi, layout, size, mode)
        status, d, secs, err = hayro("strips", src, scale, layout, size, 0, mode, timeout=1500)
        row = {"file": name, "dpi": dpi, "layout": layout, "mode": mode, "strip_rows": size}
        row["status"] = status
        if status == "OK":
            row.update(page_px=d["size"], pieces=d["pieces"], full_s=d["full_s"])
            row.update(strips_s=d["strips_s"], max_abs_diff=d["max_abs"], mean_abs=d["mean_abs"])
            row["differing_px"] = d["differing"]
            row["peak_rss_mb"] = mb(d)
            if d["full_s"]:
                row["cost_ratio"] = f"{float(d['strips_s']) / float(d['full_s']):.1f}"
        else:
            row["note"] = d.get("note", "") or err[:100]
        if layout == "strips" and mode == "check":
            pst, pd_, _, _ = pdfium("strips", src, 0, scale, size, timeout=900)
            if pst == "OK":
                row["pdfium_full_s"], row["pdfium_crop_s"] = pd_["full_s"], pd_["crop_s"]
        rows.append(row)
    full = {(r["file"], r["dpi"]): r["full_s"] for r in rows if r.get("full_s")}
    for r in (
        rows
    ):  # a stream row has no single shot render of its own, borrow the one from its check row
        ref = full.get((r["file"], r["dpi"]))
        if r["mode"] == "stream" and ref and r.get("strips_s"):
            r["cost_ratio"] = f"{float(r['strips_s']) / float(ref):.1f}"
    cols = ["file", "dpi", "layout", "mode", "strip_rows", "status", "page_px", "pieces", "full_s"]
    cols += ["strips_s", "cost_ratio", "max_abs_diff", "mean_abs", "differing_px", "peak_rss_mb"]
    cols += ["pdfium_full_s", "pdfium_crop_s", "note"]
    write_csv("strips", cols, rows)


def page_with(path, filter_name):
    import pikepdf

    with pikepdf.open(path) as pdf:
        for i, page in enumerate(pdf.pages):
            for _, im in page.images.items():
                f = im.get("/Filter")
                names = [str(x) for x in (f if isinstance(f, pikepdf.Array) else [f])]
                if "/" + filter_name in names:
                    return i
    return 0


def step_formats():
    cases = [
        ("ccitt", PDFS / "arch/usace_swf_gpw_daca67-06-d-0029.pdf", "CCITTFaxDecode"),
        ("ccitt", FIX / "codec-ccitt-g4.pdf", "CCITTFaxDecode"),
        ("jpx", PDFS / "arch/usace_norfolk_hoskins_creek_dredging_plans.pdf", "JPXDecode"),
        ("jpx", PDFS / "us/tm11b3.pdf", "JPXDecode"),
        ("jbig2", FIX / "codec-jbig2-generic.pdf", "JBIG2Decode"),
        ("jbig2", FIX / "codec-jbig2-symbol.pdf", "JBIG2Decode"),
    ]
    rows = []
    for codec, src, flt in cases:
        page = page_with(src, flt)
        say("formats", codec, src.name, page)
        row = {"codec": codec, "file": src.name, "page": page + 1}
        # pdfium at the scale that puts the long side at 2000 px
        import pypdfium2

        w, h = pypdfium2.PdfDocument(str(src))[page].get_size()
        scale = 2000 / max(w, h)
        gray = Path(tempfile.mkdtemp(prefix="fmt-")) / "p.gray"
        pst, pd_, _, perr = pdfium("render", src, page, scale, gray)
        raw = gray.with_name("h.raw")
        status, d, secs, err = hayro("formats", src, raw, 2000, page)
        row["status"] = status
        if pst == "OK":
            row["pdfium_s"] = pd_["secs"]
        if status == "OK":
            row["hayro_s"] = d["secs"]
            row["hayro_rss_mb"] = mb(d)
            row["size"] = d["pixmap"]
            if pst == "OK" and pd_["size"] == d["pixmap"]:
                gw, gh = map(int, d["pixmap"].split("x"))
                m, p32, _ = gray_diff(raw, gray, gw, gh)
                row["mean_abs"], row["pct_diff32"] = f"{m:.3f}", f"{p32:.3f}"
                row["note"] = "dark share hayro/pdfium: " + dark_share(raw, gray, gw, gh)
            else:
                row["note"] = "size differs from pdfium " + pd_.get("size", "")
        else:
            row["note"] = (d.get("note", "") + " " + err[:100]).strip()
        if src.name.startswith("codec-"):
            # the page maps the image 1:1 at 1240 x 1754, so a correct decode is the source bitmap
            from PIL import Image

            exact = gray.with_name("e.raw")
            st, ed, _, _ = hayro("fullres", src, exact, 1240, 1754, 0)
            if st == "OK":
                a = (
                    np.fromfile(exact, dtype=np.uint8)
                    .reshape(1754, 1240, 4)[:, :, 0]
                    .astype(np.int16)
                )
                s = np.asarray(Image.open(FIX / "codec-source.png").convert("L"), dtype=np.int16)
                row["vs_source_mean_abs"] = f"{np.abs(a - s).mean():.3f}"
                row["vs_source_pct_over_32"] = f"{(np.abs(a - s) > 32).mean() * 100:.3f}"
                exact.unlink()
        rows.append(row)
        for p in (gray, raw):
            if p.exists():
                p.unlink()
    cols = ["codec", "file", "page", "status", "size", "hayro_s", "pdfium_s", "hayro_rss_mb"]
    cols += ["mean_abs", "pct_diff32", "vs_source_mean_abs", "vs_source_pct_over_32", "note"]
    write_csv("formats", cols, rows)


def dark_share(raw, gray, w, h):
    a = np.memmap(raw, dtype=np.uint8, mode="r", shape=(h, w, 4))
    b = np.memmap(gray, dtype=np.uint8, mode="r", shape=(h, w))
    da = float((a[:, :, 0] < 128).mean() * 100)
    db = float((b < 128).mean() * 100)
    return f"{da:.2f}%/{db:.2f}%"


def step_hostile():
    import gen_fixtures

    tmp = Path(tempfile.mkdtemp(prefix="hostile-"))
    names = gen_fixtures.write_hostile(tmp)
    index = {}
    with (tmp / "INDEX.csv").open() as f:
        for r in csv.DictReader(f):
            index[r["case"]] = r["description"]
    rows = []
    for n in names:
        say("hostile", n)
        p = tmp / f"{n}.pdf"
        hs, hd, hsecs, herr = hayro("hostile", p, timeout=90, as_mb=6144)
        ps, pdd, psecs, perr = pdfium("open", p, timeout=90)
        rows.append(
            {
                "case": n,
                "description": index[n],
                "hayro": hs,
                "hayro_s": hd.get("secs", f"{hsecs:.2f}"),
                "hayro_peak_rss_mb": mb(hd),
                "hayro_note": hd.get("note", "") or (herr[:100] if hs != "OK" else ""),
                "pdfium": ps,
                "pdfium_s": pdd.get("secs", f"{psecs:.2f}"),
                "pdfium_peak_rss_mb": mb(pdd),
                "pdfium_note": pdd.get("note", "") or (perr[:100] if ps != "OK" else ""),
                "_h": hd.get("hash", ""),
                "_p": pdd.get("hash", ""),
            }
        )
    for r in rows:  # did a damaged file come out as the same picture as the untouched base?
        for side, key in (("hayro", "_h"), ("pdfium", "_p")):
            if r[key]:
                r[f"{side}_same_as_base"] = "yes" if r[key] == rows[0][key] else "no"
    # byte level mutants of two files, hayro in process under catch_unwind, pdfium in a child
    sources = [FIX / "canonical.pdf", tmp / "base.pdf"]
    tot = {"ok": 0, "err": 0, "panic": 0}
    ptot = {"ok": 0, "err": 0}
    hstatus, pstatus, slow = "OK", "OK", 0.0
    for i, src in enumerate(sources):
        say("mutants", src.name)
        st, d, secs, err = hayro("hostile", "mutants", src, 11 + i, 3000, timeout=900, as_mb=6144)
        if st != "DONE":
            hstatus = st
        else:
            for k in tot:
                tot[k] += int(d[k])
            slow = max(slow, float(d["slowest_s"]))
        st, d, secs, err = pdfium("mutants", src, 11 + i, 3000, timeout=900)
        if st != "DONE":
            pstatus = st
        else:
            ptot["ok"] += int(d["ok"])
            ptot["err"] += int(d["err"])
    if tot["panic"] and hstatus == "OK":
        hstatus = "PANIC"
    rows.append(
        {
            "case": "mutants",
            "description": "6000 byte level mutants (flips, truncations, cut chunks) of two files",
            "hayro": hstatus,
            "hayro_s": f"{slow:.3f}",
            "hayro_note": f"ok={tot['ok']};err={tot['err']};panic={tot['panic']};slowest_s={slow}",
            "pdfium": pstatus,
            "pdfium_note": f"ok={ptot['ok']};err={ptot['err']}",
        }
    )
    cols = ["case", "description", "hayro", "hayro_s", "hayro_peak_rss_mb", "hayro_note"]
    cols += ["pdfium", "pdfium_s", "pdfium_peak_rss_mb", "pdfium_note"]
    cols += ["hayro_same_as_base", "pdfium_same_as_base"]
    write_csv("hostile", cols, rows)


def step_threads():
    rows = []
    for name, ex, bound in [
        ("Pdf: Send", "pdf_send", "Send"),
        ("Pdf: Sync", "pdf_sync", "Sync"),
        ("RenderCache: Send", "cache_send", "Send"),
        ("RenderContext: Send", "context_send", "Send"),
    ]:
        r = subprocess.run(
            ["cargo", "build", "--release", "--example", ex],
            capture_output=True,
            text=True,
            cwd=HERE,
        )
        why = ""
        if r.returncode:
            line = [x for x in r.stderr.splitlines() if "cannot be" in x or "cannot" in x]
            why = line[0].strip()[:120] if line else "does not compile"
        rows.append(
            {
                "kind": "probe",
                "name": name,
                "result": "yes" if r.returncode == 0 else "no",
                "note": why,
            }
        )
    reg = Path(os.environ.get("CARGO_HOME", "/usr/local/cargo")) / "registry" / "src"
    hits = []
    for crate in list(reg.glob("*/hayro*")) + list(reg.glob("*/vello_cpu-*")):
        for f in crate.rglob("*.rs"):
            t = f.read_text(errors="ignore")
            if "static mut" in t or "thread_local!" in t:
                hits.append(f"{crate.name}/{f.name}")
    rows.append(
        {
            "kind": "audit",
            "name": "static mut or thread_local! in hayro and vello_cpu",
            "result": "yes" if hits else "no",
            "note": ";".join(hits),
        }
    )
    sheet = PDFS / "iso/bbmp-ward1-2020-A2.pdf"
    jobs = 12
    base = {}
    for shared in (False, True):
        yn = "yes" if shared else "no"
        for n in (1, 2, 4):
            say("threads", n, shared)
            args = ["threads", sheet, 1500, n, jobs] + (["shared"] if shared else [])
            best = None
            for _ in range(3):  # best of three, the host is not quiet
                st, d, secs, err = hayro(*args, timeout=600)
                if st == "OK" and (best is None or float(d["wall_s"]) < float(best["wall_s"])):
                    best = d
            if best is None:
                rows.append(
                    {
                        "kind": "scaling",
                        "name": f"{sheet.name} shared={shared}",
                        "threads": n,
                        "note": err[:100] or st,
                    }
                )
                continue
            wall = float(best["wall_s"])
            if n == 1 and not shared:
                base["wall"], base["hash"] = wall, best["hash"]
            rows.append(
                {
                    "kind": "scaling",
                    "name": sheet.name + " 1500px",
                    "threads": n,
                    "jobs": jobs,
                    "shared_pdf": "yes" if shared else "no",
                    "wall_s": best["wall_s"],
                    "jobs_per_s": best["jobs_per_s"],
                    "speedup": f"{base.get('wall', wall) / wall:.2f}",
                    "peak_rss_mb": mb(best),
                    "result": "",
                }
            )
            rows.append(
                {
                    "kind": "determinism",
                    "name": f"{n} threads, shared_pdf={yn}: all renders identical",
                    "threads": n,
                    "result": "same" if best["identical"] == "yes" else "differs",
                }
            )
            rows.append(
                {
                    "kind": "determinism",
                    "name": f"{n} threads, shared_pdf={yn}: same bytes as 1 thread",
                    "threads": n,
                    "result": "same" if best["hash"] == base.get("hash") else "differs",
                }
            )
    cols = ["kind", "name", "threads", "jobs", "shared_pdf", "wall_s", "jobs_per_s", "speedup"]
    cols += ["peak_rss_mb", "result", "note"]
    write_csv("threads", cols, rows)


def step_password():
    cases = [
        ("user-password:none", "password.pdf", "-"),
        ("user-password:wrong", "password.pdf", "wrong"),
        ("user-password:right", "password.pdf", "secret"),
        ("owner-password-only:none", "owner-password-only.pdf", "-"),
        ("owner-password-only:owner", "owner-password-only.pdf", "owner-only-secret"),
    ]
    rows = []
    for case, f, pw in cases:
        hs, hd, _, herr = hayro("password", FIX / f, pw)
        ps, pdd, _, perr = pdfium("open", FIX / f, pw)
        rows.append(
            {
                "case": case,
                "file": f,
                "password": "none" if pw == "-" else "given",
                "hayro": hs,
                "hayro_note": hd.get("note", "") or hd.get("pixmap", ""),
                "pdfium": ps,
                "pdfium_note": pdd.get("note", ""),
            }
        )
    write_csv(
        "password",
        ["case", "file", "password", "hayro", "hayro_note", "pdfium", "pdfium_note"],
        rows,
    )


STEPS = {
    "rotation": step_rotation,
    "fullres": step_fullres,
    "strips": step_strips,
    "formats": step_formats,
    "hostile": step_hostile,
    "threads": step_threads,
    "password": step_password,
}

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "pdfium":
        pdfium_child(sys.argv[2:])
    elif len(sys.argv) > 1 and sys.argv[1] in list(STEPS) + ["all"]:
        for n in STEPS if sys.argv[1] == "all" else [sys.argv[1]]:
            STEPS[n]()
    else:
        sys.exit(__doc__)
