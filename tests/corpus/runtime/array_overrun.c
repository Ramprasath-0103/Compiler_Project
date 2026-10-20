#include <stdio.h>

int main(void) {
    int numbers[4] = {1, 2, 3, 4};
    for (int i = 0; i <= 1000000; i++) {
        numbers[i] = i;
    }
    printf("%d\n", numbers[0]);
    return 0;
}
