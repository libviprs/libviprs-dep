//! The recipe MANUAL.md documents, run for real.
//!
//! This is a `-sys` crate's build script, which is the only place the
//! recipe can be written and the exact place it can go wrong. Cargo does
//! not treat every directive the same way:
//!
//!   * `cargo:rustc-link-search` and `cargo:rustc-link-lib` travel to the
//!     link line of anything that depends on this crate.
//!   * `cargo:rustc-link-arg` does not. It binds to this package's own
//!     targets, so a requirement expressed as an argument reaches this
//!     crate's tests and examples, stays green there, and never arrives
//!     at the binary that needs it.
//!
//! That is why the runtime's static initialiser ships as its own archive
//! and is pulled in with `static:+whole-archive` rather than forced with
//! `-Wl,-u,...`, and why a non-empty `static_link_args` is a hard error
//! here rather than something to pass along and hope about.
//!
//! It handles both link modes, because on one target there is only one of
//! them. `static_certified` is false on `aarch64-apple-darwin` and always
//! has been, so until this script grew a shared branch there was nothing
//! that linked a mac archive at all: the build's own shared smoke is
//! `dlopen` on an absolute path, and `dlopen` never consults the name a
//! dylib records for itself. That is how `3.7.1-viprs.1` published a mac
//! archive no consumer could link (#95). The recorded name is checked
//! from the bytes now, by `scripts/verify_archive.sh`; this is the half
//! that asks the loader instead of asking us.
//!
//! The rpath the shared mode needs cannot be emitted from here, for the
//! `rustc-link-arg` reason above: it would reach this crate's own targets
//! and not the binary. So it goes out as `cargo:rpath`, which cargo hands
//! to a dependent's build script as `DEP_ACADSHARP_NATIVE_RPATH` because
//! this package declares `links = "acadsharp_native"`, and `consumer`'s
//! build script turns it into the flag. That is the propagation route a
//! real consumer has, and it is why the two crates are separate here.

use std::path::PathBuf;

fn main() {
    println!("cargo:rerun-if-env-changed=ACADSHARP_ARCHIVE");

    let root = PathBuf::from(
        std::env::var("ACADSHARP_ARCHIVE")
            .expect("set ACADSHARP_ARCHIVE to an unpacked acadsharp-<platform>-<cpu> directory"),
    );
    let manifest = root.join("metadata").join("LINKINFO.json");
    let text = std::fs::read_to_string(&manifest)
        .unwrap_or_else(|e| panic!("cannot read {}: {e}", manifest.display()));

    let lib_dir = root.join("lib");

    if !json_bool(&text, "static_certified") {
        // The shared recipe, which is the whole recipe on mac. Nothing
        // here is subtle the way the static one is: a shared library runs
        // its own initialisers when the loader brings it in, so there is
        // no archive to force and no order to get right.
        //
        // `shared_system_libraries` still goes on the line. Those are the
        // library's own `NEEDED`/`LC_LOAD_DYLIB` entries, which the loader
        // resolves for it, so the link would succeed without them; naming
        // them is what LINKINFO.md documents and what `acadsharp-rs` does,
        // and a recipe measured in two shapes is a recipe measured in
        // neither.
        let shared = json_string(&text, "shared_library").expect("shared_library");
        println!("cargo:rustc-link-search=native={}", lib_dir.display());
        println!("cargo:rustc-link-lib={}", link_name(&shared));
        for lib in json_string_array(&text, "shared_system_libraries") {
            println!("cargo:rustc-link-lib={lib}");
        }
        // Out as metadata, not as a flag: see the note at the top. A
        // dependent reads it as DEP_ACADSHARP_NATIVE_RPATH.
        //
        // Only on this branch. `lib/` holds the shared library and the
        // static archives together, a bare `-l` prefers the shared one,
        // so an rpath on the static path produces a binary that was meant
        // to be self-contained, links, and then quietly runs against the
        // `.dylib` next to it on the build machine.
        println!("cargo:rpath={}", lib_dir.display());
        return;
    }

    let static_lib = json_string(&text, "static_library").expect("static_library");
    let init_lib = json_string(&text, "static_init_library").expect("static_init_library");
    let link_args = json_string_array(&text, "static_link_args");
    if !link_args.is_empty() {
        panic!(
            "static_link_args is {link_args:?}. A build script cannot carry a link \
             argument to a dependent's link line: cargo:rustc-link-arg binds to this \
             package's own targets only. Whatever that argument is for has to be \
             expressed as a library, the way static_init_library is."
        );
    }

    println!("cargo:rustc-link-search=native={}", lib_dir.display());

    // Three modifiers, and all three are load-bearing.
    //
    // `+whole-archive` on the init archive, because the initialiser lives
    // in `.init_array` and nothing references it, so ordinary archive
    // semantics leave it out.
    //
    // `-bundle` on both, because with the default `+bundle` rustc packs a
    // static native library into this crate's rlib, and the rlib lands on
    // the link line *before* the whole-archived init archive. The linker
    // reads left to right: at the rlib it has no reason to pull the
    // runtime object defining `RhRegisterOSModule`, and by the time the
    // bootstrapper asks for it that archive is behind it. `-bundle` hands
    // both to the linker as `-l` flags instead, in the order below.
    //
    // And the init archive first, because it is the one with the
    // dangling references; reversed, the same link fails the same way.
    println!(
        "cargo:rustc-link-lib=static:-bundle,+whole-archive={}",
        link_name(&init_lib)
    );
    println!(
        "cargo:rustc-link-lib=static:-bundle={}",
        link_name(&static_lib)
    );
    for lib in json_string_array(&text, "static_system_libraries") {
        println!("cargo:rustc-link-lib={lib}");
    }
}

/// `lib/libacadsharp_native_init.a` -> `acadsharp_native_init`, and
/// `lib/libacadsharp_native.dylib` -> `acadsharp_native`.
///
/// The same transformation `-l` has always done: drop the `lib` prefix and
/// the extension. All three extensions, because the manifest names the
/// file and the file is a `.a` on the static path, a `.so` on Linux and a
/// `.dylib` on mac; a stem left with its extension on becomes
/// `-lacadsharp_native.dylib`, which the linker looks for as
/// `libacadsharp_native.dylib.dylib` and does not find.
fn link_name(path: &str) -> String {
    let file = path.rsplit('/').next().unwrap_or(path);
    let stem = file.trim_start_matches("lib");
    for ext in [".a", ".so", ".dylib"] {
        if let Some(cut) = stem.strip_suffix(ext) {
            return cut.to_string();
        }
    }
    stem.to_string()
}

fn field<'a>(text: &'a str, key: &str) -> Option<&'a str> {
    let at = text.find(&format!("\"{key}\""))? + key.len() + 2;
    let rest = text[at..].trim_start();
    Some(rest.strip_prefix(':')?.trim_start())
}

fn json_string(text: &str, key: &str) -> Option<String> {
    let rest = field(text, key)?;
    let rest = rest.strip_prefix('"')?;
    let end = rest.find('"')?;
    Some(rest[..end].to_string())
}

fn json_bool(text: &str, key: &str) -> bool {
    field(text, key).is_some_and(|rest| rest.starts_with("true"))
}

fn json_string_array(text: &str, key: &str) -> Vec<String> {
    let Some(rest) = field(text, key) else {
        return Vec::new();
    };
    let Some(rest) = rest.strip_prefix('[') else {
        return Vec::new();
    };
    let Some(end) = rest.find(']') else {
        return Vec::new();
    };
    rest[..end]
        .split(',')
        .filter_map(|piece| {
            let piece = piece.trim().trim_matches('"');
            (!piece.is_empty()).then(|| piece.to_string())
        })
        .collect()
}
