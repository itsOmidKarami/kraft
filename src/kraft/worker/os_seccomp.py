"""The os sandbox's seccomp filter: classic BPF, stdlib only, nothing of Kraft's imported.

bwrap reads it from a file descriptor (`--seccomp N`). It allows only the host's
native architecture, answers ENOSYS to any other (a foreign ABI would bypass the
table by its own syscall numbers), and answers EPERM to the denied calls and
to the `ioctl` requests that type into a terminal.
"""

from __future__ import annotations

import errno
import platform
import struct

#: The mount API of Linux 5.2+ (and 5.12's mount_setattr): numbered alike on both arches.
_NEW_MOUNT_API = {
    "open_tree": 428, "move_mount": 429, "fsopen": 430, "fsconfig": 431,
    "fsmount": 432, "fspick": 433, "mount_setattr": 442,
}  # fmt: skip
# Kernel syscall numbers: x86_64 from asm/unistd_64.h, aarch64 from the generic table.
DENY: dict[str, dict[str, int]] = {
    "x86_64": {
        "keyctl": 250, "add_key": 248, "request_key": 249,
        "io_uring_setup": 425, "io_uring_enter": 426, "io_uring_register": 427,
        "bpf": 321, "perf_event_open": 298, "userfaultfd": 323,
        "kexec_load": 246, "kexec_file_load": 320,
        "init_module": 175, "finit_module": 313, "delete_module": 176,
        "mount": 165, "umount2": 166, "pivot_root": 155,
        **_NEW_MOUNT_API,
    },
    "aarch64": {
        "keyctl": 219, "add_key": 217, "request_key": 218,
        "io_uring_setup": 425, "io_uring_enter": 426, "io_uring_register": 427,
        "bpf": 280, "perf_event_open": 241, "userfaultfd": 282,
        "kexec_load": 104, "kexec_file_load": 294,
        "init_module": 105, "finit_module": 273, "delete_module": 106,
        "mount": 40, "umount2": 39, "pivot_root": 41,
        **_NEW_MOUNT_API,
    },
}  # fmt: skip

#: `ioctl`, allowed but for the requests that push input into a terminal
#: (CVE-2017-5226): TIOCSTI fakes typed input, TIOCLINUX can paste the selection.
IOCTL = {"x86_64": 16, "aarch64": 29}
DENIED_IOCTLS = {"TIOCSTI": 0x5412, "TIOCLINUX": 0x541C}

_AUDIT_ARCH = {"x86_64": 0xC000003E, "aarch64": 0xC00000B7}
_X32_BIT = 0x40000000
_RET_ERRNO = 0x00050000
_RET_ALLOW = 0x7FFF0000
_LD_ARCH = (0x20, 0, 0, 4)  # BPF_LD|W|ABS of seccomp_data.arch
_LD_NR = (0x20, 0, 0, 0)  # ... of seccomp_data.nr
# ... of the low word of seccomp_data.args[1], both arches being little-endian: the
# kernel reads an ioctl's request as an unsigned int, so the high word is ignored.
_LD_ARG1_LO = (0x20, 0, 0, 24)


def machine() -> str:
    """The filter's key for this host, from `platform.machine()`."""
    return {"amd64": "x86_64", "arm64": "aarch64"}.get(m := platform.machine().lower(), m)


def build(arch: str) -> bytes:
    """The filter as little-endian `struct sock_filter` entries (8 bytes each)."""
    if arch not in DENY:
        raise ValueError(f"no seccomp filter for architecture {arch!r}")
    enosys, eperm = _RET_ERRNO | errno.ENOSYS, _RET_ERRNO | errno.EPERM
    prog = [_LD_ARCH, (0x15, 1, 0, _AUDIT_ARCH[arch]), (0x06, 0, 0, enosys), _LD_NR]  # JEQ, RET
    if arch == "x86_64":
        prog += [(0x35, 0, 1, _X32_BIT), (0x06, 0, 0, enosys)]  # JGE: x32 numbers
    nrs = sorted(DENY[arch].values())
    cmds = sorted(DENIED_IOCTLS.values())
    # JEQ ioctl: into its request checks, else past them (the checks and their ALLOW).
    prog += [(0x15, 0, len(cmds) + 2, IOCTL[arch]), _LD_ARG1_LO]
    for i, cmd in enumerate(cmds):
        # A hit jumps over the other requests, the ALLOW, the denied numbers and theirs.
        prog.append((0x15, len(cmds) - i + len(nrs) + 1, 0, cmd))
    prog.append((0x06, 0, 0, _RET_ALLOW))
    for i, nr in enumerate(nrs):
        prog.append((0x15, len(nrs) - i, 0, nr))  # JEQ: hit jumps to the EPERM return
    prog += [(0x06, 0, 0, _RET_ALLOW), (0x06, 0, 0, eperm)]
    return b"".join(struct.pack("<HBBI", *insn) for insn in prog)
