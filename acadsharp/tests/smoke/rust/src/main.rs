// The Rust half of the smoke: link the NativeAOT shared library at build time
// with #[link], call the exports, print what the reader saw.
use std::ffi::CString;
use std::os::raw::{c_char, c_int, c_uint};

#[link(name = "viprs_acadsharp")]
extern "C" {
    fn viprs_acad_abi_version() -> c_uint;
    fn viprs_acad_entity_count(path: *const c_char) -> c_int;
    fn viprs_acad_describe(path: *const c_char, out: *mut u8, cap: c_int) -> c_int;
}

fn main() {
    let path = std::env::args().nth(1).expect("usage: smoke <dwg>");
    let c_path = CString::new(path).unwrap();
    unsafe {
        eprintln!("ABI={}", viprs_acad_abi_version());
        eprintln!("ENTITY_COUNT={}", viprs_acad_entity_count(c_path.as_ptr()));
        let mut buf = vec![0u8; 1 << 20];
        let n = viprs_acad_describe(c_path.as_ptr(), buf.as_mut_ptr(), buf.len() as c_int);
        if n < 0 {
            eprintln!("DESCRIBE_FAILED code={n}");
            std::process::exit(6);
        }
        print!("{}", String::from_utf8_lossy(&buf[..n as usize]));
        println!();
    }
}
