//! The generated view of the VIPRS CAD C ABI, plus a parser for the stream.
//!
//! Nothing in this crate declares a struct or an entry point by hand. Every
//! declaration in `abi` is generated from `include/viprs_acadsharp.h` at build
//! time by `build.rs`, which is the property that makes a reordered field a
//! build failure here instead of a plausible wrong number at run time.
//!
//! What is written by hand is the layout table in `tests/layout.rs`. If that
//! were generated from the header too, reordering two fields would move both
//! the struct and its expected offsets, and the test would pass.

#![allow(non_camel_case_types)]
#![allow(non_upper_case_globals)]

pub mod wire;

pub mod abi {
    //! Generated from the published header. Do not edit; edit the header.
    #![allow(non_camel_case_types)]
    #![allow(dead_code)]
    include!(concat!(env!("OUT_DIR"), "/abi.rs"));
}

pub use abi::*;
