/*
 * mpereira-backup-launch: what launchd starts. It starts PROGRAM with the same arguments, passes on SIGTERM, SIGINT
 * and SIGHUP, waits for it, and exits as it exits.
 *
 * Why it exists: macOS gives Full Disk Access to an app or a binary, and checks it against the "responsible" process,
 * which for a LaunchAgent is the program that launchd starts. Given to /usr/bin/python3, the access would go to every
 * Python script. Given to this small binary, it goes only to the backups. It must start PROGRAM as a child (posix_spawn)
 * and not replace itself with it (exec): after an exec, macOS would check PROGRAM instead.
 *
 * The role builds it with PROGRAM set at compile time, so it cannot start anything else:
 *   cc -O2 -DPROGRAM='"/usr/local/bin/mpereira-backup"' -o mpereira-backup-launch mpereira-backup-launch.c
 *   codesign --force --sign - mpereira-backup-launch
 * A new build has a new code hash, and macOS then asks for Full Disk Access again: the role builds it only when this
 * file or PROGRAM changes.
 */
#include <errno.h>
#include <signal.h>
#include <spawn.h>
#include <stdio.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

#ifndef PROGRAM
#error "build with -DPROGRAM='\"/path/to/program\"'"
#endif

extern char **environ;
static volatile pid_t child = 0;

static void pass_on(int sig) {
    if (child > 0)
        kill(child, sig);
}

int main(int argc, char **argv) {
    (void)argc;
    struct sigaction sa;
    memset(&sa, 0, sizeof sa);
    sa.sa_handler = pass_on;
    sigemptyset(&sa.sa_mask);
    sigaction(SIGTERM, &sa, NULL);
    sigaction(SIGINT, &sa, NULL);
    sigaction(SIGHUP, &sa, NULL);

    argv[0] = PROGRAM;
    pid_t pid;
    int rc = posix_spawn(&pid, PROGRAM, NULL, NULL, argv, environ);
    if (rc != 0) {
        fprintf(stderr, "mpereira-backup-launch: cannot start %s: %s\n", PROGRAM, strerror(rc));
        return 127;
    }
    child = pid;

    int status;
    while (waitpid(pid, &status, 0) < 0) {
        if (errno != EINTR) {
            perror("mpereira-backup-launch: waitpid");
            return 1;
        }
    }
    if (WIFEXITED(status))
        return WEXITSTATUS(status);
    if (WIFSIGNALED(status))
        return 128 + WTERMSIG(status);
    return 1;
}
