// Compile probe: does `Pdf` implement Sync? A failed build of this example is the answer "no".
use hayro::hayro_syntax::Pdf;

fn needs<T: Sync>() {}

fn main() {
    needs::<Pdf>();
    println!("Pdf: Sync");
}
