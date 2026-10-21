"""The os sandbox's seccomp filter: classic BPF, stdlib only, nothing of Kraft's imported.

bwrap reads it from a file descriptor (`--seccomp N`). It allows only the host's
native architecture, answers ENOSYS to any other (a foreign ABI would bypass the
table by its own syscall numbers), and answers EPERM to the denied calls.
"""

from __future__ import annotations

import errno
import platform
import struct

# Kernel syscall numbers: x86_64 from asm/unistd_64.h, aarch64 from the generic table.
DENY: dict[str, dict[str, int]] = {
    "x86_64": {
        "keyctl": 250, "add_key": 248, "request_key": 249,
        "io_uring_setup": 425, "io_uring_enter": 426, "io_uring_register": 427,
        "bpf": 321, "perf_event_open": 298, "userfaultfd": 323,
        "kexec_load": 246, "kexec_file_load": 320,
        "init_module": 175, "finit_module": 313, "delete_module": 176,
        "mount": 165, "umount2": 166, "pivot_root": 155,
    },
    "aarch64": {
        "keyctl": 219, "add_key": 217, "request_key": 218,
        "io_uring_setup": 425, "io_uring_enter": 426, "io_uring_register": 427,
        "bpf": 280, "perf_event_open": 241, "userfaultfd": 282,
        "kexec_load": 104, "kexec_file_load": 294,
        "init_module": 105, "finit_module": 273, "delete_module": 106,
        "mount": 40, "umount2": 39, "pivot_root": 41,
    },
}  # fmt: skip

_AUDIT_ARCH = {"x86_64": 0xC000003E, "aarch64": 0xC00000B7}
_X32_BIT = 0x40000000
_RET_ERRNO = 0x00050000
_RET_ALLOW = 0x7FFF0000
_LD_ARCH = (0x20, 0, 0, 4)  # BPF_LD|W|ABS of seccomp_data.arch
_LD_NR = (0x20, 0, 0, 0)  # ... of seccomp_data.nr


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
    for i, nr in enumerate(nrs):
        prog.append((0x15, len(nrs) - i, 0, nr))  # JEQ: hit jumps to the EPERM return
    prog += [(0x06, 0, 0, _RET_ALLOW), (0x06, 0, 0, eperm)]
    return b"".join(struct.pack("<HBBI", *insn) for insn in prog)
