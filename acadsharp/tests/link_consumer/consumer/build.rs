//! The one line a consumer has to write for itself.
//!
//! `acadsharp-sys` can hand a dependent libraries and search paths, and it
//! cannot hand it a linker argument: `cargo:rustc-link-arg` binds to the
//! emitting package's own targets. An rpath is a linker argument, and on
//! the shared path the binary needs one, because cargo does not put a
//! build script's `rustc-link-search` directory on the loader's path.
//! Without it the binary links and then dies before `main`.
//!
//! So the sys crate publishes the directory as `cargo:rpath` and this
//! turns it into the flag. That route exists because that package declares
//! `links = "acadsharp_native"`, which is what makes cargo pass its
//! metadata to a dependent's build script as `DEP_<links>_<key>`,
//! uppercased. It is the only way a `-sys` crate can tell its dependents
//! something they have to express as an argument, and this file is here to
//! show it working end to end rather than to be clever: `libviprs-dep`
//! published a mac archive nothing could link (#95) because nothing in the
//! repository ever linked one.
//!
//! Absent on the static path, and it has to stay absent there.
//! `lib/` holds the shared library and the static archives side by side, a
//! bare `-l` prefers the shared one, so an rpath on a static link produces
//! a binary that was supposed to be self-contained and that quietly runs
//! against the `.so` beside it on the machine that built it.

fn main() {
    println!("cargo:rerun-if-env-changed=DEP_ACADSHARP_NATIVE_RPATH");
    if let Ok(dir) = std::env::var("DEP_ACADSHARP_NATIVE_RPATH") {
        println!("cargo:rustc-link-arg=-Wl,-rpath,{dir}");
    }
}
