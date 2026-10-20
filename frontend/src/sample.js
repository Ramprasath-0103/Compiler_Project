// Loaded on first open so the page does something before anything is typed.
export const SAMPLE = `#include <stdio.h>

int classify(int a, int b, int c, int d) {
    if (a > 0) {
        if (b > 0) {
            if (c > 0) {
                if (d > 0) {
                    if (a > b) {
                        return 1;
                    }
                }
            }
        }
    }
    return 0;
}

int main(void) {
    int count = 0
    printf("%d\\n", classify(1, 2, 3, 4));
    return 0;
}
`;
