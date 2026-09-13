/* Opens the NativeAOT shared library by hand, resolves the exports by bare
 * name and reads a DWG with them. Built and run in a container that has never
 * seen .NET, so a pass means the library carries its own runtime. */
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef unsigned int (*abi_fn)(void);
typedef int (*describe_fn)(const char *, char *, int);
typedef int (*count_fn)(const char *);

int main(int argc, char **argv)
{
	if (argc < 3) {
		fprintf(stderr, "usage: smoke <library> <dwg>\n");
		return 2;
	}

	void *h = dlopen(argv[1], RTLD_NOW);
	if (!h) {
		fprintf(stderr, "DLOPEN_FAILED: %s\n", dlerror());
		return 3;
	}

	abi_fn abi = (abi_fn)dlsym(h, "viprs_acad_abi_version");
	describe_fn describe = (describe_fn)dlsym(h, "viprs_acad__spike_describe");
	count_fn count = (count_fn)dlsym(h, "viprs_acad__spike_entity_count");
	if (!abi || !describe || !count) {
		fprintf(stderr, "DLSYM_FAILED: %s\n", dlerror());
		return 4;
	}

	fprintf(stderr, "ABI=%u\n", abi());

	int n = count(argv[2]);
	fprintf(stderr, "ENTITY_COUNT=%d\n", n);

	size_t cap = 1 << 20;
	char *buf = malloc(cap);
	if (!buf) {
		return 5;
	}
	memset(buf, 0, cap);

	int written = describe(argv[2], buf, (int)cap);
	if (written < 0) {
		fprintf(stderr, "DESCRIBE_FAILED code=%d payload=%s\n", written, buf);
		free(buf);
		return 6;
	}

	fwrite(buf, 1, (size_t)written, stdout);
	fputc('\n', stdout);
	free(buf);
	return 0;
}
