/* Fixed qualification PID1, derived from Zelo's owned pipe diagnostic.
 * Parent builds this offline. No runtime compiler or caller-selected argv.
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <sys/wait.h>
#include <unistd.h>

static void stop(int signal_number) {
    (void)signal_number;
    /* Namespace PID1 exit terminates remaining namespace children. */
    _exit(124);
}

int main(void) {
    struct sigaction action = {0};
    action.sa_handler = stop;
    sigemptyset(&action.sa_mask);
    if (sigaction(SIGALRM, &action, 0) || sigaction(SIGTERM, &action, 0) ||
        sigaction(SIGINT, &action, 0)) return 10;
    alarm(25);
    if (getuid() != 65534 || geteuid() != 65534 || getpid() != 1) return 11;
    pid_t child = fork();
    if (child < 0) return 12;
    if (!child) {
        if (dup2(0, 3) < 0 || dup2(1, 4) < 0) _exit(13);
        int fd = open("/dev/null", O_RDWR);
        if (fd < 0) _exit(14);
        if (dup2(fd, 0) < 0 || dup2(fd, 1) < 0) _exit(15);
        if (fd > 4) close(fd);
        execl("/opt/browser/chrome-headless-shell", "chrome-headless-shell",
              "--remote-debugging-pipe", "--no-first-run", "--no-default-browser-check",
              "--disable-background-networking", "--disable-component-update",
              "--user-data-dir=/tmp/profile", (char *)0);
        _exit(127);
    }
    int status;
    while (waitpid(child, &status, 0) < 0) {
        if (errno != EINTR) return 16;
    }
    return WIFEXITED(status) ? WEXITSTATUS(status) : 17;
}
