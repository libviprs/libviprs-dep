// The modes of hayro-spike. Nothing here is library code: each function opens a PDF, renders it the way the
// question asks and returns one result line. A panic inside hayro is caught and reported as PANIC, because
// "does it panic on hostile input" is one of the questions.
use hayro::hayro_interpret::util::TransformExt;
use hayro::hayro_interpret::InterpreterSettings;
use hayro::hayro_syntax::Pdf;
use hayro::kurbo::Affine;
use hayro::vello_cpu::color::palette::css::WHITE;
use hayro::vello_cpu::{Pixmap, RasterizerSettings, RenderContext, Resources, TargetInit};
use hayro::{render, render_into, PixmapSettings, RenderCache, RenderSettings};
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::sync::Arc;
use std::time::Instant;

fn peak_rss_kb() -> u64 {
    // Linux only, which is where the spike runs (a container).
    std::fs::read_to_string("/proc/self/status")
        .ok()
        .and_then(|s| {
            s.lines()
                .find(|l| l.starts_with("VmHWM:"))
                .and_then(|l| l.split_whitespace().nth(1).and_then(|v| v.parse().ok()))
        })
        .unwrap_or(0)
}

fn fnv(data: &[u8]) -> u64 {
    data.iter().fold(0xcbf29ce484222325u64, |h, b| {
        (h ^ *b as u64).wrapping_mul(0x100000001b3)
    })
}

fn open(path: &str, password: &str) -> Result<Pdf, String> {
    let data = Arc::new(std::fs::read(path).map_err(|e| format!("IO {e}"))?);
    Pdf::new_with_password(data, password).map_err(|e| format!("{e:?}"))
}

fn one_pixmap(pdf: &Pdf, page: usize, sx: f32, sy: f32) -> Result<Pixmap, String> {
    let page = pdf.pages().get(page).ok_or("NoSuchPage")?;
    let pm = PixmapSettings {
        x_scale: sx,
        y_scale: sy,
        bg_color: WHITE,
    };
    Ok(render(
        page,
        &RenderCache::new(),
        &InterpreterSettings::default(),
        &RenderSettings::default(),
        &pm,
    ))
}

// One pixmap of exactly `w` x `h` pixels, whatever the page size: the page is stretched to fit, the way a
// backend asked for "the page at this pixel size" has to do it (the plain render() truncates w * scale).
fn exact(pdf: &Pdf, page: usize, w: u16, h: u16) -> Result<Pixmap, String> {
    let (pw, ph) = pdf
        .pages()
        .get(page)
        .ok_or("NoSuchPage")?
        .render_dimensions();
    let base = Affine::scale_non_uniform(w as f64 / pw as f64, h as f64 / ph as f64)
        * pdf.pages()[page].initial_transform(true).to_kurbo();
    Ok(render_piece(
        pdf,
        page,
        &RenderCache::new(),
        base,
        (0, 0, w, h),
    ))
}

// A PNG for *.png, the raw premultiplied RGBA bytes for *.raw (a 140 MP PNG would swamp the timing), nothing for "-".
fn save(pm: Pixmap, out: &str) {
    if out.ends_with(".raw") {
        std::fs::write(out, pm.data_as_u8_slice()).unwrap();
    } else if out != "-" {
        std::fs::write(out, pm.into_png().unwrap()).unwrap();
    }
}

// Runs `f`, turning a panic into the PANIC status so one hostile file cannot end the run.
fn guarded(f: impl FnOnce() -> Result<String, String>) -> String {
    match catch_unwind(AssertUnwindSafe(f)) {
        Ok(Ok(s)) => format!("status=OK {s}"),
        Ok(Err(e)) => format!("status=ERR note={}", e.replace(' ', "_")),
        Err(_) => "status=PANIC".to_string(),
    }
}

// The #164 single render: <pdf> <png> <maxdim>, page 1, long side scaled to maxdim.
pub fn legacy(path: &str, out: &str, maxdim: &str) -> String {
    let maxdim: f32 = maxdim.parse().unwrap();
    let t = Instant::now();
    let r = catch_unwind(AssertUnwindSafe(|| {
        let pdf = open(path, "")?;
        let (w, h) = pdf.pages()[0].render_dimensions();
        let s = maxdim / w.max(h);
        let pm = one_pixmap(&pdf, 0, s, s)?;
        std::fs::write(out, pm.into_png().unwrap()).unwrap();
        Ok::<_, String>((w, h, s))
    }));
    match r {
        Ok(Ok((w, h, s))) => format!("OK {w} {h} {s} {:.3}", t.elapsed().as_secs_f64()),
        Ok(Err(e)) => format!("ERR {e}"),
        Err(_) => "PANIC".to_string(),
    }
}

// Rotation goldens: render at 72 dpi (1 pt = 1 px), the scale the libviprs-tests goldens use.
pub fn rotation(path: &str, out: &str) -> String {
    guarded(|| {
        let t = Instant::now();
        let pdf = open(path, "")?;
        let (w, h) = pdf.pages()[0].render_dimensions();
        let plain = one_pixmap(&pdf, 0, 1.0, 1.0)?;
        let (tw, th) = (plain.width(), plain.height());
        let pm = exact(&pdf, 0, w.ceil() as u16, h.ceil() as u16)?;
        let secs = t.elapsed().as_secs_f64();
        let (pw, ph) = (pm.width(), pm.height());
        save(pm, out);
        Ok(format!(
            "page_pt={w}x{h} plain_render={tw}x{th} pixmap={pw}x{ph} secs={secs:.4}"
        ))
    })
}

// fullres <pdf> <out> <a> <b> <page> [scale]: without "scale", a x b is the pixel size, rendered exactly (the
// pdfium side of the comparison asks for the same size). With "scale", a and b are the x and y scale handed to
// the plain render(), which is how the u16 ceiling is probed. out may be .png, .raw or "-".
pub fn fullres(a: &[String]) -> String {
    let (x, y): (f32, f32) = (a[2].parse().unwrap(), a[3].parse().unwrap());
    let page: usize = a[4].parse().unwrap();
    let by_scale = a.get(5).is_some();
    guarded(|| {
        let t = Instant::now();
        let pdf = open(&a[0], "")?;
        let parse = t.elapsed().as_secs_f64();
        let (w, h) = pdf
            .pages()
            .get(page)
            .ok_or("NoSuchPage")?
            .render_dimensions();
        let t = Instant::now();
        let (asked, pm) = if by_scale {
            (
                format!("{}x{}", (w * x) as u64, (h * y) as u64),
                one_pixmap(&pdf, page, x, y)?,
            )
        } else {
            (
                format!("{}x{}", x as u64, y as u64),
                exact(&pdf, page, x as u16, y as u16)?,
            )
        };
        let render_s = t.elapsed().as_secs_f64();
        let got = format!("{}x{}", pm.width(), pm.height());
        let hash = fnv(pm.data_as_u8_slice());
        save(pm, &a[1]);
        let rss = peak_rss_kb();
        Ok(format!("page_pt={w}x{h} asked={asked} got={got} parse_s={parse:.3} render_s={render_s:.3} hash={hash:x} rss_kb={rss}"))
    })
}

fn base_transform(pdf: &Pdf, page: usize, scale: f64) -> (Affine, u32, u32) {
    let p = &pdf.pages()[page];
    let (w, h) = p.render_dimensions();
    // The pixel size is the page size rounded up, the way pdfium sizes it, and the page is stretched to fit
    // that size, so a single shot render and the pieces share one transform.
    let (pw, ph) = ((w as f64 * scale).ceil(), (h as f64 * scale).ceil());
    let t = Affine::scale_non_uniform(pw / w as f64, ph / h as f64)
        * p.initial_transform(true).to_kurbo();
    (t, pw as u32, ph as u32)
}

fn render_piece<'a>(
    pdf: &'a Pdf,
    page: usize,
    cache: &RenderCache<'a>,
    base: Affine,
    (x0, y0, w, h): (u32, u32, u16, u16),
) -> Pixmap {
    let mut ctx = RenderContext::new(w, h);
    let t = Affine::translate((-(x0 as f64), -(y0 as f64))) * base;
    render_into(
        &pdf.pages()[page],
        cache,
        &InterpreterSettings::default(),
        &RenderSettings::default(),
        &mut ctx,
        t,
    );
    ctx.flush();
    let mut pm = Pixmap::new(w, h);
    ctx.render_with(
        &mut pm,
        &mut Resources::default(),
        RasterizerSettings {
            target_init: TargetInit::Clear(WHITE),
            ..Default::default()
        },
    );
    pm
}

// strips <pdf> <scale> <strips|tiles> <size> <page> <check|stream>
// check:  one single-shot render, then the pieces stitched, and the two compared pixel by pixel.
// stream: only the pieces, each dropped once hashed, which is what peak memory would be in libviprs.
pub fn strips(a: &[String]) -> String {
    let scale: f64 = a[1].parse().unwrap();
    let layout = a[2].as_str();
    let size: u32 = a[3].parse().unwrap();
    let page: usize = a[4].parse().unwrap();
    let check = a[5] == "check";
    guarded(|| {
        let pdf = open(&a[0], "")?;
        let (base, pw, ph) = base_transform(&pdf, page, scale);
        let full = if check {
            let t = Instant::now();
            let pm = exact(&pdf, page, pw as u16, ph as u16)?;
            Some((pm, t.elapsed().as_secs_f64()))
        } else {
            None
        };
        let cache = RenderCache::new();
        let (step_x, step_y) = if layout == "tiles" {
            (size, size)
        } else {
            (pw, size)
        };
        let (mut max_abs, mut sum_abs, mut differing, mut pieces) = (0u8, 0u64, 0u64, 0u32);
        let mut hash = 0xcbf29ce484222325u64;
        let t = Instant::now();
        let mut y = 0;
        while y < ph {
            let mut x = 0;
            while x < pw {
                let (w, h) = (step_x.min(pw - x) as u16, step_y.min(ph - y) as u16);
                let pm = render_piece(&pdf, page, &cache, base, (x, y, w, h));
                pieces += 1;
                hash ^= fnv(pm.data_as_u8_slice()).rotate_left(pieces % 63);
                if let Some((f, _)) = &full {
                    let fd = f.data_as_u8_slice();
                    let pd = pm.data_as_u8_slice();
                    for row in 0..h as usize {
                        let fo = ((y as usize + row) * f.width() as usize + x as usize) * 4;
                        let po = row * w as usize * 4;
                        for i in 0..w as usize * 4 {
                            let d = fd[fo + i].abs_diff(pd[po + i]);
                            max_abs = max_abs.max(d);
                            sum_abs += d as u64;
                            differing += (d > 0) as u64;
                        }
                    }
                }
                x += step_x;
            }
            y += step_y;
        }
        let strips_s = t.elapsed().as_secs_f64();
        let rss = peak_rss_kb();
        let (full_s, fsize, mean) = match &full {
            Some((f, s)) => (
                format!("{s:.3}"),
                format!("{}x{}", f.width(), f.height()),
                format!("{:.4}", sum_abs as f64 / (pw as f64 * ph as f64 * 4.0)),
            ),
            None => ("".into(), "".into(), "".into()),
        };
        Ok(format!(
            "size={pw}x{ph} full_size={fsize} pieces={pieces} full_s={full_s} strips_s={strips_s:.3} max_abs={max_abs} mean_abs={mean} differing={differing} hash={hash:x} rss_kb={rss}"
        ))
    })
}

// formats <pdf> <png|-> <maxdim> [page]: one page, long side scaled to maxdim, plus how many pages the file has.
pub fn formats(path: &str, out: &str, maxdim: &str, page: Option<&String>) -> String {
    let maxdim: f32 = maxdim.parse().unwrap();
    let page: usize = page.map_or(0, |p| p.parse().unwrap());
    guarded(|| {
        let t = Instant::now();
        let pdf = open(path, "")?;
        let (w, h) = pdf
            .pages()
            .get(page)
            .ok_or("NoSuchPage")?
            .render_dimensions();
        let s = maxdim / w.max(h);
        let pm = exact(&pdf, page, (w * s).ceil() as u16, (h * s).ceil() as u16)?;
        let secs = t.elapsed().as_secs_f64();
        let (gw, gh) = (pm.width(), pm.height());
        save(pm, out);
        Ok(format!(
            "pages={} page_pt={w}x{h} pixmap={gw}x{gh} secs={secs:.3} rss_kb={}",
            pdf.pages().len(),
            peak_rss_kb()
        ))
    })
}

// hostile <pdf>: open and render page 1 at 800 px on the long side. A process level crash (stack
// overflow, abort on a failed allocation) takes this process down, and run_cases.py reads that off the exit status.
pub fn hostile(path: &str) -> String {
    guarded(|| {
        let t = Instant::now();
        let pdf = open(path, "")?;
        let pages = pdf.pages().len();
        let page = pdf.pages().first().ok_or("NoPages")?;
        let (w, h) = page.render_dimensions();
        if !(w.is_finite() && h.is_finite()) || w <= 0.0 || h <= 0.0 {
            return Err(format!("BadPageSize_{w}x{h}"));
        }
        let s = 800.0 / w.max(h);
        let pm = one_pixmap(&pdf, 0, s, s)?;
        Ok(format!(
            "pages={pages} page_pt={w}x{h} pixmap={}x{} secs={:.3} rss_kb={} hash={:x}",
            pm.width(),
            pm.height(),
            t.elapsed().as_secs_f64(),
            peak_rss_kb(),
            fnv(pm.data_as_u8_slice())
        ))
    })
}

// hostile mutants <pdf> <seed> <count>: byte level mutants of one file, each opened and rendered in this
// process under catch_unwind. Counts the outcomes. A hang or a crash ends the process and run_cases.py reports it.
pub fn mutants(path: &str, seed: &str, count: &str) -> String {
    let src = std::fs::read(path).unwrap();
    let mut state: u64 = seed.parse::<u64>().unwrap() | 1;
    let mut next = move || {
        state ^= state << 13;
        state ^= state >> 7;
        state ^= state << 17;
        state
    };
    let (mut ok, mut err, mut panic) = (0, 0, 0);
    let mut slowest = 0f64;
    let prev = std::panic::take_hook();
    std::panic::set_hook(Box::new(|_| {}));
    for _ in 0..count.parse::<u32>().unwrap() {
        let mut d = src.clone();
        match next() % 3 {
            0 => (0..1 + next() % 8).for_each(|_| {
                let i = (next() as usize) % d.len();
                d[i] = next() as u8;
            }),
            1 => d.truncate((next() as usize) % d.len()),
            _ => {
                let a = (next() as usize) % d.len();
                let b = (a + (next() as usize) % 256).min(d.len());
                d.drain(a..b);
            }
        }
        let t = Instant::now();
        let r = catch_unwind(AssertUnwindSafe(|| {
            let pdf = Pdf::new(Arc::new(d)).map_err(|e| format!("{e:?}"))?;
            let page = pdf.pages().first().ok_or("NoPages")?;
            let (w, h) = page.render_dimensions();
            if !(w.is_finite() && h.is_finite()) || w <= 0.0 || h <= 0.0 {
                return Err("BadPageSize".to_string());
            }
            let s = 400.0 / w.max(h);
            one_pixmap(&pdf, 0, s, s).map(|_| ())
        }));
        slowest = slowest.max(t.elapsed().as_secs_f64());
        match r {
            Ok(Ok(())) => ok += 1,
            Ok(Err(_)) => err += 1,
            Err(_) => panic += 1,
        }
    }
    std::panic::set_hook(prev);
    format!("status=DONE ok={ok} err={err} panic={panic} slowest_s={slowest:.4}")
}

// threads <pdf> <maxdim> <threads> <jobs> [shared]: `jobs` renders of page 1 split over `threads` threads. By
// default every thread parses its own Pdf from the shared bytes; with "shared" they all borrow one Pdf (it is
// Send and Sync, see examples/). Each render builds its own RenderCache, because the cache is not Send.
// The hashes show whether concurrent renders produce what a single thread produces.
pub fn threads(path: &str, maxdim: &str, threads: &str, jobs: &str, shared: bool) -> String {
    let maxdim: f32 = maxdim.parse().unwrap();
    let (n, jobs): (usize, usize) = (threads.parse().unwrap(), jobs.parse().unwrap());
    let data = Arc::new(std::fs::read(path).unwrap());
    let one = &Pdf::new(data.clone()).unwrap();
    let t = Instant::now();
    let hashes: Vec<u64> = std::thread::scope(|s| {
        let handles: Vec<_> = (0..n)
            .map(|k| {
                let data = data.clone();
                s.spawn(move || {
                    (0..jobs)
                        .filter(|j| j % n == k)
                        .map(|_| {
                            let own;
                            let pdf = if shared {
                                one
                            } else {
                                own = Pdf::new(data.clone()).unwrap();
                                &own
                            };
                            let (w, h) = pdf.pages()[0].render_dimensions();
                            let sc = maxdim / w.max(h);
                            fnv(one_pixmap(pdf, 0, sc, sc).unwrap().data_as_u8_slice())
                        })
                        .collect::<Vec<_>>()
                })
            })
            .collect();
        handles
            .into_iter()
            .flat_map(|h| h.join().unwrap())
            .collect()
    });
    let wall = t.elapsed().as_secs_f64();
    let same = hashes.windows(2).all(|w| w[0] == w[1]);
    format!(
        "status=OK shared_pdf={shared} threads={n} jobs={jobs} wall_s={wall:.3} jobs_per_s={:.2} identical={} hash={:x} rss_kb={}",
        jobs as f64 / wall,
        if same { "yes" } else { "no" },
        hashes[0],
        peak_rss_kb()
    )
}

// password <pdf> <password|-> [png]: opens with the given password ("-" is none) and renders page 1 at 72 dpi.
// The error is printed as hayro names it, so "none" and "wrong" can be compared.
pub fn password(path: &str, pw: &str, out: Option<&String>) -> String {
    guarded(|| {
        let pdf = open(path, if pw == "-" { "" } else { pw })?;
        let (w, h) = pdf.pages()[0].render_dimensions();
        let pm = one_pixmap(&pdf, 0, 1.0, 1.0)?;
        let size = format!("{}x{}", pm.width(), pm.height());
        if let Some(o) = out {
            std::fs::write(o, pm.into_png().unwrap()).unwrap();
        }
        Ok(format!("page_pt={w}x{h} pixmap={size}"))
    })
}
