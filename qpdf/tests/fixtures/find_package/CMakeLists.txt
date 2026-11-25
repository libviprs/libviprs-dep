# A cmake consumer of an unpacked qpdf archive: find_package must resolve the
# shared and the static target, with every library and include directory inside
# the prefix it is pointed at through CMAKE_PREFIX_PATH.
cmake_minimum_required(VERSION 3.16)
project(qpdf_find_package_smoke C CXX)

find_package(qpdf CONFIG REQUIRED)

add_executable(smoke_shared main.c)
target_link_libraries(smoke_shared PRIVATE qpdf::libqpdf)
set_target_properties(smoke_shared PROPERTIES LINKER_LANGUAGE CXX)

add_executable(smoke_static main.c)
target_link_libraries(smoke_static PRIVATE qpdf::libqpdf_static)
set_target_properties(smoke_static PROPERTIES LINKER_LANGUAGE CXX)
