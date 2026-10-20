#include <stdio.h>

int main(void) {
    for (int i = 0; i < 3; i++) {
        int inner = i * 2;
    }
    printf("%d\n", inner);
    return 0;
}
