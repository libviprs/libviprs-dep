/* Startup only: open the library, call the cheapest export, stop. */
#include <dlfcn.h>
#include <stdio.h>
typedef unsigned int (*abi_fn)(void);
int main(int argc, char **argv) {
  void *h = dlopen(argv[1], RTLD_NOW);
  if (!h) { fprintf(stderr, "DLOPEN_FAILED: %s\n", dlerror()); return 3; }
  abi_fn abi = (abi_fn)dlsym(h, "viprs_acad_abi_version");
  if (!abi) return 4;
  printf("ABI=%u\n", abi());
  return 0;
}
