#include <stdio.h>
#include <string.h>
#include <qpdf/qpdf-c.h>

/* Prints the version of the library the target resolved to, and checks it
   against the one the archive claims when a version is passed. */
int main(int argc, char **argv) {
    printf("find_package: libqpdf %s\n", qpdf_get_qpdf_version());
    if (argc > 1 && strcmp(qpdf_get_qpdf_version(), argv[1]) != 0) {
        fprintf(stderr, "find_package: expected %s\n", argv[1]);
        return 1;
    }
    return 0;
}
