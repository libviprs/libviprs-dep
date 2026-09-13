//! Just enough of the ABI to prove the link arrived.
//!
//! The real bindings belong to `acadsharp-rs`; this crate exists so the
//! packaging suite can check that the documented recipe reaches a binary
//! that only depends on it.

extern "C" {
    pub fn viprs_acad_abi_version() -> u32;
    pub fn viprs_acad_abi_fingerprint() -> u64;
}
