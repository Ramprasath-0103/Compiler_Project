#include <stdio.h>

int main(void) {
    int *p = NULL;
    *p = 42;
    printf("%d\n", *p);
    return 0;
}
