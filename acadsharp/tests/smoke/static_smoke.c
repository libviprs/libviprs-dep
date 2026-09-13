/* The static half: the same calls, but the exports are linked in directly
 * from the .a rather than resolved at run time. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

extern unsigned int viprs_acad_abi_version(void);
extern int viprs_acad_describe(const char *, char *, int);
extern int viprs_acad_entity_count(const char *);

int main(int argc, char **argv)
{
	if (argc < 2) {
		fprintf(stderr, "usage: static_smoke <dwg>\n");
		return 2;
	}

	fprintf(stderr, "ABI=%u\n", viprs_acad_abi_version());
	fprintf(stderr, "ENTITY_COUNT=%d\n", viprs_acad_entity_count(argv[1]));

	size_t cap = 1 << 20;
	char *buf = malloc(cap);
	if (!buf) {
		return 5;
	}
	memset(buf, 0, cap);

	int written = viprs_acad_describe(argv[1], buf, (int)cap);
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
