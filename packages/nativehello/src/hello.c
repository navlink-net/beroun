// Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
/* The test package's native part: answers GET with {"hello":"native","target":"..."} on the port it is given. */
#include <arpa/inet.h>
#include <netinet/in.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>

#if defined(__aarch64__)
#define CPU "arm64"
#else
#define CPU "x86_64"
#endif
#ifdef __ANDROID__
#define OS "android"
#else
#define OS "linux"
#endif

/* on Android the program is a library the native host loads, and its entry is beroun_main */
#ifdef __ANDROID__
int beroun_main(int argc, char **argv) {
#else
int main(int argc, char **argv) {
#endif
    int port = argc > 1 ? atoi(argv[1]) : 49201;
    int s = socket(AF_INET, SOCK_STREAM, 0), one = 1;
    setsockopt(s, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one);
    struct sockaddr_in a = {.sin_family = AF_INET, .sin_port = htons(port)};
    a.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    if (bind(s, (struct sockaddr *)&a, sizeof a) || listen(s, 8)) { perror("hello"); return 1; }
    for (;;) {
        int c = accept(s, NULL, NULL);
        if (c < 0) continue;
        char buf[1024];
        (void)read(c, buf, sizeof buf);
        const char *body = "{\"hello\":\"native\",\"target\":\"" OS "-" CPU "\"}";
        char head[256];
        int n = snprintf(head, sizeof head, "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: %zu\r\nConnection: close\r\n\r\n", strlen(body));
        (void)write(c, head, n);
        (void)write(c, body, strlen(body));
        close(c);
    }
}
