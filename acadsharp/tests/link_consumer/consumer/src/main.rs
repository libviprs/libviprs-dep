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

    // Two constant-returning exports prove the link arrived and nothing
    // above it (#120). Everything past this point reaches managed code: the
    // capabilities call fills a struct the runtime owns, and opening a
    // drawing walks the reader, which is where a missing trimmer root
    // (`MissingMethodException`) or an uninvariant culture (the
    // `CadHeader -> DateTime.Now -> TimeZoneInfo` FailFast) shows up.
    let mut caps = acadsharp_sys::Capabilities {
        struct_size: std::mem::size_of::<acadsharp_sys::Capabilities>() as u32,
        struct_version: 1,
        ..Default::default()
    };
    let mut required = 0u64;
    let status = unsafe {
        acadsharp_sys::viprs_acad_get_capabilities_v1(
            &mut caps,
            std::ptr::null_mut(),
            0,
            &mut required,
        )
    };
    assert_eq!(status, 0, "viprs_acad_get_capabilities_v1 returned {status}");
    println!("DWG_VERSION_MIN={}", caps.dwg_version_min);
    println!("DWG_VERSION_MAX={}", caps.dwg_version_max);

    let path = std::env::var("ACADSHARP_SMOKE_DWG")
        .expect("set ACADSHARP_SMOKE_DWG to a small DWG; link_consumer_smoke.sh does");
    let bytes = std::fs::read(&path).unwrap_or_else(|e| panic!("cannot read {path}: {e}"));
    let mut handle: *mut acadsharp_sys::Handle = std::ptr::null_mut();
    let status = unsafe {
        acadsharp_sys::viprs_acad_open_memory(
            bytes.as_ptr(),
            bytes.len() as u64,
            std::ptr::null(),
            &mut handle,
        )
    };
    assert_eq!(status, 0, "viprs_acad_open_memory returned {status} for {path}");
    assert!(!handle.is_null(), "viprs_acad_open_memory returned 0 and no handle");
    unsafe { acadsharp_sys::viprs_acad_close(handle) };
    println!("DOCUMENT_OPENED=1");
}
