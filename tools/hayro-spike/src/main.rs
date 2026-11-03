use hayro::hayro_syntax::Pdf;
use hayro::hayro_interpret::InterpreterSettings;
use hayro::{render, PixmapSettings, RenderCache, RenderSettings};
use hayro::vello_cpu::color::palette::css::WHITE;
use std::{sync::Arc, time::Instant};
fn main() {
    let a: Vec<String> = std::env::args().collect();
    let (path, out, maxdim) = (&a[1], &a[2], a[3].parse::<f32>().unwrap());
    let data = Arc::new(std::fs::read(path).unwrap());
    let t = Instant::now();
    let r = std::panic::catch_unwind(|| {
        let pdf = Pdf::new(data).map_err(|e| format!("{e:?}"))?;
        let page = &pdf.pages()[0];
        let (w, h) = page.render_dimensions();
        let s = maxdim / w.max(h);
        let pm = render(page, &RenderCache::new(), &InterpreterSettings::default(), &RenderSettings::default(), &PixmapSettings { x_scale: s, y_scale: s, bg_color: WHITE });
        std::fs::write(out, pm.into_png().unwrap()).unwrap();
        Ok::<_, String>((w, h, s))
    });
    match r {
        Ok(Ok((w, h, s))) => println!("OK {w} {h} {s} {:.3}", t.elapsed().as_secs_f64()),
        Ok(Err(e)) => println!("ERR {e}"),
        Err(_) => println!("PANIC"),
    }
}
