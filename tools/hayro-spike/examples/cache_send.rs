// Compile probe: does `RenderCache` implement Send? A failed build of this example is the answer "no".
use hayro::RenderCache;

fn needs<T: Send>() {}

fn main() {
    needs::<RenderCache<'static>>();
    println!("RenderCache: Send");
}
