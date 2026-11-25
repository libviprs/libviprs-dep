// Compile probe: does `RenderContext` implement Send? A failed build of this example is the answer "no".
use hayro::vello_cpu::RenderContext;

fn needs<T: Send>() {}

fn main() {
    needs::<RenderContext>();
    println!("RenderContext: Send");
}
