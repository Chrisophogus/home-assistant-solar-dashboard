"""Seccomp layer; launcher MUST first chroot and drop all privileges."""
import ctypes
import errno


def install_seccomp():
    sc = ctypes.CDLL('libseccomp.so.2', use_errno=True)
    sc.seccomp_init.argtypes = [ctypes.c_uint32]
    sc.seccomp_init.restype = ctypes.c_void_p
    sc.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    sc.seccomp_rule_add.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint]
    sc.seccomp_load.argtypes = [ctypes.c_void_p]
    sc.seccomp_release.argtypes = [ctypes.c_void_p]
    ctx = sc.seccomp_init(0x7fff0000)
    if not ctx:
        raise RuntimeError('seccomp unavailable')
    try:
        for name in ('socket', 'socketpair', 'connect', 'sendto', 'sendmsg', 'execve', 'execveat', 'fork', 'vfork', 'clone', 'clone3', 'ptrace', 'mount', 'unshare', 'setns', 'bpf', 'io_uring_setup'):
            num = sc.seccomp_syscall_resolve_name(name.encode())
            if num < 0 or sc.seccomp_rule_add(ctx, 0x50000 | errno.EPERM, num, 0):
                raise RuntimeError('Cannot deny ' + name)
        if sc.seccomp_load(ctx):
            raise RuntimeError('Cannot load seccomp')
    finally:
        sc.seccomp_release(ctx)
