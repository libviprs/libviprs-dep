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

    if !json_bool(&text, "static_certified") {
        panic!(
            "{} says static_certified is false, so this archive ships no static \
             libraries and there is nothing to link statically",
            manifest.display()
        );
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

    println!("cargo:rustc-link-search=native={}", root.join("lib").display());

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

/// `lib/libacadsharp_native_init.a` -> `acadsharp_native_init`
fn link_name(path: &str) -> String {
    let file = path.rsplit('/').next().unwrap_or(path);
    file.trim_start_matches("lib").trim_end_matches(".a").to_string()
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
