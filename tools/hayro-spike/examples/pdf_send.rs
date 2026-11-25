// Compile probe: does `Pdf` implement Send? A failed build of this example is the answer "no".
use hayro::hayro_syntax::Pdf;

fn needs<T: Send>() {}

fn main() {
    needs::<Pdf>();
    println!("Pdf: Send");
}
