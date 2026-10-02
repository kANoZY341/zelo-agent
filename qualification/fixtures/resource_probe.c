/* Trusted bounded cgroup diagnostic, never a browser workload or host stress tool.
 * The fixed cgroup limits are verified before any resource-pressure operation.
 * No network, credentials, host mounts, arbitrary program or input is accepted. */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/prctl.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

static long number(const char *path) {
    char buffer[128] = {0}; char *end = NULL;
    FILE *f = fopen(path, "r");
    if (!f || !fgets(buffer, sizeof buffer, f)) exit(90);
    fclose(f); errno = 0;
    long result = strtol(buffer, &end, 10);
    if (errno || end == buffer) exit(91);
    return result;
}
static void dump(const char *path) {
    char line[256]; FILE *f = fopen(path, "r");
    if (!f) exit(92);
    printf("FILE %s\n", path);
    for (int n = 0; n < 40 && fgets(line, sizeof line, f); n++) fputs(line, stdout);
    fclose(f);
}
static double wall(void) {
    struct timespec t; if (clock_gettime(CLOCK_MONOTONIC, &t)) exit(93);
    return t.tv_sec + t.tv_nsec / 1e9;
}
static void verify(void) {
    if (getuid() != 65534 || getpid() != 1) exit(94);
    if (number("/sys/fs/cgroup/memory.max") != 33554432L ||
        number("/sys/fs/cgroup/memory.swap.max") != 0 ||
        number("/sys/fs/cgroup/pids.max") != 12 ||
        prctl(PR_GET_NO_NEW_PRIVS, 0, 0, 0, 0) != 1) exit(95);
    FILE *f = fopen("/sys/fs/cgroup/cpu.max", "r"); long q=0,p=0;
    if (!f || fscanf(f, "%ld %ld", &q, &p) != 2) exit(96);
    fclose(f); if (q != 25000 || p != 100000) exit(97);
    puts("LIMITS_VERIFIED memory=33554432 swap=0 pids=12 cpu=25000/100000 uid=65534 nnp=1");
}
int main(int argc, char **argv) {
    setvbuf(stdout, NULL, _IONBF, 0); alarm(12);
    if (argc != 2) return 89;
    verify();
    if (!strcmp(argv[1], "boundary")) {
        errno=0; int fd=open("/must-not-create",O_WRONLY|O_CREAT,0600);
        if (fd>=0 || errno!=EROFS) return 10;
        if (access("/home/zelo",F_OK)==0 || access("/var/run/docker.sock",F_OK)==0) return 11;
        dump("/proc/net/route"); dump("/proc/net/dev");
        dump("/sys/fs/cgroup/memory.max"); dump("/sys/fs/cgroup/memory.swap.max");
        dump("/sys/fs/cgroup/pids.max"); dump("/sys/fs/cgroup/cpu.max");
        puts("BOUNDARY_OK readonly_root=1 host_home=absent docker_socket=absent");
    } else if (!strcmp(argv[1], "memory")) {
        for (int i=0;i<64;i++) { volatile unsigned char *p=malloc(1048576);
            if(!p) return 12;
            for(size_t j=0;j<1048576;j+=4096) p[j]=(unsigned char)i; }
        return 13; /* Reaching 64 MiB contradicts the verified 32 MiB limit. */
    } else if (!strcmp(argv[1], "pids") || !strcmp(argv[1], "cleanup")) {
        pid_t kids[24]; int n=0, denied=0;
        int target=!strcmp(argv[1], "cleanup")?2:24;
        for(int i=0;i<target;i++) { pid_t pid=fork();
            if(pid<0){denied=errno==EAGAIN;break;}
            if(pid==0){alarm(10);for(;;) pause();}
            kids[n++]=pid; }
        if(!strcmp(argv[1],"cleanup")) { puts("CHILDREN_READY");for(;;) pause(); }
        dump("/sys/fs/cgroup/pids.events");
        for(int i=0;i<n;i++) kill(kids[i],SIGKILL);
        for(int i=0;i<n;i++) while(waitpid(kids[i],NULL,0)<0 && errno==EINTR) {}
        printf("PIDS_RESULT children=%d denied=%d reaped=%d\n",n,denied,n);
        if(!denied || n>11) return 14;
    } else if (!strcmp(argv[1], "cpu")) {
        volatile unsigned long x=1; double end=wall()+2.0;
        while(wall()<end) for(int i=0;i<10000;i++) x=x*1664525UL+1013904223UL;
        dump("/sys/fs/cgroup/cpu.stat"); puts("CPU_PROBE_COMPLETE");
    } else if (!strcmp(argv[1], "tmpfs")) {
        int fd=open("/tmp/probe",O_WRONLY|O_CREAT|O_EXCL,0600); if(fd<0) return 15;
        char data[4096]={0}; long written=0; int denied=0;
        for(int i=0;i<4096;i++) { ssize_t n=write(fd,data,sizeof data);
            if(n<0){denied=errno==ENOSPC;break;}written+=n; }
        close(fd); printf("TMPFS_RESULT bytes=%ld denied=%d\n",written,denied);
        if(!denied || written>8388608L) return 16;
    } else return 88;
    return 0;
}
