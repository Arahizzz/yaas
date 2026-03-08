/*
 * libfakeuid — LD_PRELOAD library for krun MicroVM containers.
 *
 * Problem: krun VMs boot as root (UID 0). Rootless podman maps container
 * UID 0 → host UID 1000, so file ownership is correct. But AI agents like
 * Claude Code detect root via getuid() and refuse to enable YOLO mode.
 *
 * Solution: override getuid/geteuid/getgid/getegid (and getres* variants)
 * to return the UID/GID from YAAS_FAKE_UID/YAAS_FAKE_GID env vars (default
 * 1000). The real kernel UID stays 0, so all privileged operations work.
 *
 * Sudo compatibility: since the real UID is already 0, there's no privilege
 * transition when exec'ing setuid binaries — so the dynamic linker does NOT
 * strip LD_PRELOAD. We detect setuid binaries (e.g. sudo) by checking the
 * setuid bit on /proc/self/exe and disable faking, letting sudo see real
 * UID 0. Sudo's env_reset then strips LD_PRELOAD from children (apt, dpkg),
 * so they also see real UID 0 and work normally.
 */

#define _GNU_SOURCE
#include <stdlib.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/types.h>
#include <unistd.h>

static uid_t fake_uid = 1000;
static gid_t fake_gid = 1000;
static int should_fake = 1;

__attribute__((constructor))
static void init(void) {
    /* Disable faking for setuid binaries (e.g. sudo). */
    struct stat st;
    if (stat("/proc/self/exe", &st) == 0 && (st.st_mode & S_ISUID)) {
        should_fake = 0;
        return;
    }

    const char *uid_str = getenv("YAAS_FAKE_UID");
    const char *gid_str = getenv("YAAS_FAKE_GID");
    if (uid_str) fake_uid = (uid_t)atoi(uid_str);
    if (gid_str) fake_gid = (gid_t)atoi(gid_str);
}

uid_t getuid(void)  { return should_fake ? fake_uid : (uid_t)syscall(SYS_getuid); }
uid_t geteuid(void) { return should_fake ? fake_uid : (uid_t)syscall(SYS_geteuid); }
gid_t getgid(void)  { return should_fake ? fake_gid : (gid_t)syscall(SYS_getgid); }
gid_t getegid(void) { return should_fake ? fake_gid : (gid_t)syscall(SYS_getegid); }

int getresuid(uid_t *ruid, uid_t *euid, uid_t *suid) {
    if (!should_fake)
        return syscall(SYS_getresuid, ruid, euid, suid);
    if (ruid) *ruid = fake_uid;
    if (euid) *euid = fake_uid;
    if (suid) *suid = fake_uid;
    return 0;
}

int getresgid(gid_t *rgid, gid_t *egid, gid_t *sgid) {
    if (!should_fake)
        return syscall(SYS_getresgid, rgid, egid, sgid);
    if (rgid) *rgid = fake_gid;
    if (egid) *egid = fake_gid;
    if (sgid) *sgid = fake_gid;
    return 0;
}
