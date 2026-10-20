#include <stdio.h>
#include <string.h>

void greet(const char *name) {
    char buffer[8];
    strcpy(buffer, name);
    printf("hello %s\n", buffer);
}

int main(void) {
    greet("a name far longer than eight bytes");
    return 0;
}
