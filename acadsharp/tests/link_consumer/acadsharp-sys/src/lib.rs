//! Just enough of the ABI to prove the link arrived.
//!
//! The real bindings belong to `acadsharp-rs`; this crate exists so the
//! packaging suite can check that the documented recipe reaches a binary
//! that only depends on it.

/// `struct viprs_acad_capabilities_v1`, laid out as the shipped header has it.
#[repr(C)]
#[derive(Default)]
pub struct Capabilities {
    pub struct_size: u32,
    pub struct_version: u32,
    pub abi_version: u32,
    pub wire_version: u32,
    pub dwg_version_min: u32,
    pub dwg_version_max: u32,
    pub supports_block_expansion: u8,
    pub supports_warnings: u8,
    pub reserved0: u8,
    pub reserved1: u8,
    pub reserved2: u32,
}

#[repr(C)]
pub struct Handle {
    _private: [u8; 0],
}

extern "C" {
    pub fn viprs_acad_abi_version() -> u32;
    pub fn viprs_acad_abi_fingerprint() -> u64;
    pub fn viprs_acad_get_capabilities_v1(
        out: *mut Capabilities,
        version_utf8: *mut u8,
        cap: u64,
        required: *mut u64,
    ) -> u32;
    pub fn viprs_acad_open_memory(
        data: *const u8,
        data_len: u64,
        limits: *const u8,
        out: *mut *mut Handle,
    ) -> u32;
    pub fn viprs_acad_close(h: *mut Handle);
}
