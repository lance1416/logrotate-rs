/* Linux-only observation gate. The real rename runs unchanged, then this
 * process stops before returning to its caller. Only the exact state path
 * supplied by the controller is intercepted. No upstream source is patched. */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

int rename(const char *oldpath, const char *newpath)
{
    int (*real_rename)(const char *, const char *);
    const char *state = getenv("REFERENCE_STATE");
    const char *marker = getenv("REFERENCE_REPLACED");
    int result;
    int saved_errno;
    *(void **)(&real_rename) = dlsym(RTLD_NEXT, "rename");
    if (real_rename == NULL)
        _exit(120);
    result = real_rename(oldpath, newpath);
    saved_errno = errno;
    if (result == 0 && state != NULL && marker != NULL &&
        strcmp(newpath, state) == 0) {
        int fd = open(marker, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0600);
        if (fd == -1 || close(fd) == -1)
            _exit(121);
        if (raise(SIGSTOP) != 0)
            _exit(122);
    }
    errno = saved_errno;
    return result;
}
