//! A binary that depends on `acadsharp-sys` and nothing else.
//!
//! It has to be a separate crate. `acadsharp-sys`'s own tests and
//! examples get every directive its build script emits, including the
//! ones that do not propagate, so a check that lived there would stay
//! green while every downstream consumer broke.

fn main() {
    // Allocate before asking the library anything. An export that returns
    // a constant can be answered by a binary whose runtime never came up;
    // a megabyte off its heap cannot.
    let mut scratch = vec![0x5au8; 1 << 20];
    scratch[4095] = 0x5a;
    assert_eq!(scratch[4095], 0x5a);

    let version = unsafe { acadsharp_sys::viprs_acad_abi_version() };
    let fingerprint = unsafe { acadsharp_sys::viprs_acad_abi_fingerprint() };
    println!("ABI_VERSION={version}");
    println!("ABI_FINGERPRINT={fingerprint:016x}");
}
