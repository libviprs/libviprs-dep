// hayro-spike: one binary, one mode per open question of #164 and #170. Every mode prints one line of
// space separated key=value pairs on stdout, which run_cases.py turns into the CSVs under results/.
// With no mode word the arguments are the old ones (<pdf> <png> <maxdim>) that run.py still uses.
mod cases;

fn main() {
    let a: Vec<String> = std::env::args().collect();
    let line = match a.get(1).map(String::as_str) {
        Some("rotation") => cases::rotation(&a[2], &a[3]),
        Some("fullres") => cases::fullres(&a[2..]),
        Some("strips") => cases::strips(&a[2..]),
        Some("formats") => cases::formats(&a[2], &a[3], &a[4], a.get(5)),
        Some("hostile") if a.get(2).map(String::as_str) == Some("mutants") => {
            cases::mutants(&a[3], &a[4], &a[5])
        }
        Some("hostile") => cases::hostile(&a[2]),
        Some("threads") => cases::threads(&a[2], &a[3], &a[4], &a[5], a.get(6).is_some()),
        Some("password") => cases::password(&a[2], &a[3], a.get(4)),
        _ => cases::legacy(&a[1], &a[2], &a[3]),
    };
    println!("{line}");
}
