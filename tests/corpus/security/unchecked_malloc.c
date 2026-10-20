#include <stdlib.h>

int main(void) {
    int *data = malloc(1024 * sizeof(int));
    data[0] = 1;
    return 0;
}
