#define _GNU_SOURCE
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sched.h>
#include <sys/prctl.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <unistd.h>
static void stop(int signo){(void)signo;_exit(124);}
static int readf(const char *path,char *out,size_t cap){
 int fd=open(path,O_RDONLY|O_CLOEXEC);if(fd<0)return -1;
 int n=(int)read(fd,out,cap-1);close(fd);if(n<0)return -1;out[n]=0;return n;
}
static long field(const char *data,const char *name){
 const char *p=strstr(data,name);return p?strtol(p+strlen(name),0,10):-1;
}
static unsigned long long caps(const char *data){
 const char *p=strstr(data,"CapEff:");return p?strtoull(p+7,0,16):~0ULL;
}
static int pid_depth(const char *data){
 const char*p=strstr(data,"NSpid:");if(!p)return 0;p+=6;int count=0;
 while(*p&&*p!='\n'){if(*p>='0'&&*p<='9'){count++;while(*p>='0'&&*p<='9')p++;}else p++;}return count;
}
static int cgroup(const char *name,const char *expected){
 char path[160],buf[256];snprintf(path,sizeof(path),"/sys/fs/cgroup/%s",name);
 if(readf(path,buf,sizeof(buf))<0)return 0;
 buf[strcspn(buf,"\r\n")]=0;printf("cgroup_%s=%s\n",name,buf);return strcmp(buf,expected)==0;
}
int main(void){
 setvbuf(stdout,0,_IONBF,0);struct sigaction sa={0};sa.sa_handler=stop;sigemptyset(&sa.sa_mask);
 if(sigaction(SIGALRM,&sa,0))return 10;alarm(18);
 char parent[8192];if(getuid()!=65534||getpid()!=1||readf("/proc/self/status",parent,sizeof(parent))<0)return 11;
 if(field(parent,"Seccomp:")!=2||field(parent,"NoNewPrivs:")!=1||caps(parent)!=0)return 12;
 if(!cgroup("memory.max","536870912")||!cgroup("memory.swap.max","0")||!cgroup("pids.max","128")||!cgroup("cpu.max","50000 100000"))return 13;
 errno=0;if(unshare(CLONE_NEWNS)!=-1||errno!=EPERM)return 14;
 errno=0;if(unshare(CLONE_NEWNET)!=-1||errno!=EPERM)return 15;
 errno=0;if(syscall(SYS_setns,-1,0)!=-1||errno!=EPERM)return 16;
 errno=0;if(chroot("/")!=-1||errno!=EPERM)return 17;
 errno=0;if(syscall(SYS_clone3,0,0)!=-1||errno!=ENOSYS)return 18;
 puts("initial_forbidden_syscalls=denied");
 pid_t browser=fork();if(browser<0)return 19;
 if(browser==0){
  execl("/opt/browser/chrome-headless-shell","chrome-headless-shell","--no-first-run","--no-default-browser-check","--disable-background-networking","--disable-component-update","--user-data-dir=/tmp/profile","--remote-debugging-port=0","data:text/html,<title>Zelo sandbox fixture</title><main>ZELO_NATIVE_SANDBOX_OK</main>",(char*)0);_exit(127);
 }
 int observed=0;
 for(int retry=0;retry<300&&!observed;retry++){
  DIR *dir=opendir("/proc");if(!dir)break;struct dirent *ent;
  while((ent=readdir(dir))){
   if(ent->d_name[0]<'0'||ent->d_name[0]>'9')continue;
   char path[512],cmd[8192],status[8192];snprintf(path,sizeof(path),"/proc/%s/cmdline",ent->d_name);
   int n=readf(path,cmd,sizeof(cmd));if(n<0)continue;for(int i=0;i<n;i++)if(!cmd[i])cmd[i]=' ';
   if(!strstr(cmd,"--type=renderer"))continue;
   snprintf(path,sizeof(path),"/proc/%s/status",ent->d_name);if(readf(path,status,sizeof(status))<0)continue;
   if(field(status,"Seccomp:")==2&&field(status,"Seccomp_filters:")>field(parent,"Seccomp_filters:")&&field(status,"NoNewPrivs:")==1&&caps(status)==0&&pid_depth(status)>pid_depth(parent)){
    printf("renderer_seccomp=%ld\nrenderer_filters=%ld\ncontainer_filters=%ld\nrenderer_nnp=%ld\nrenderer_caps=0\nrenderer_pid_depth=%d\ncontainer_pid_depth=%d\n",field(status,"Seccomp:"),field(status,"Seccomp_filters:"),field(parent,"Seccomp_filters:"),field(status,"NoNewPrivs:"),pid_depth(status),pid_depth(parent));
    observed=1;break;
   }
  }
  closedir(dir);if(!observed)usleep(20000);
 }
 int reaped=0;kill(browser,SIGTERM);
 for(int i=0;i<30;i++){if(waitpid(browser,0,WNOHANG)==browser){reaped=1;break;}usleep(20000);}
 if(!reaped){kill(browser,SIGKILL);while(waitpid(browser,0,0)<0&&errno==EINTR){}}
 while(waitpid(-1,0,WNOHANG)>0){}
 puts(observed?"native_renderer_probe=PASS":"native_renderer_probe=FAIL");return observed?0:20;
}
