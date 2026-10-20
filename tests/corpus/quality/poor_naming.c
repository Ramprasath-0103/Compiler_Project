#include <stdio.h>

int f(int x, int y) {
    int z = x;
    int q = y;
    int w = z * q;
    return w;
}

int main(void) {
    printf("%d\n", f(3, 4));
    return 0;
}
