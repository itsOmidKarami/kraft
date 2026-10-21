"""The os sandbox's seccomp filter, run through a minimal classic-BPF interpreter."""

import errno
import os
import struct
import subprocess
import sys

import pytest

from kraft.worker import os_seccomp

ALLOW = 0x7FFF0000
ERRNO = 0x00050000
EPERM, ENOSYS = ERRNO | errno.EPERM, ERRNO | errno.ENOSYS
AUDIT = {"x86_64": 0xC000003E, "aarch64": 0xC00000B7, "i386": 0x40000003}
X32 = 0x40000000


def run_bpf(program: bytes, nr: int, audit_arch: int, arg1: int = 0) -> int:
    """Interpret LD|W|ABS, JEQ, JGE and RET over a packed seccomp_data."""
    data = struct.pack("<IIQ6Q", nr, audit_arch, 0, 0, arg1, *([0] * 4))
    insns = [struct.unpack_from("<HBBI", program, i) for i in range(0, len(program), 8)]
    acc = pc = 0
    while True:
        code, jt, jf, k = insns[pc]
        pc += 1
        if code == 0x20:  # LD|W|ABS
            (acc,) = struct.unpack_from("<I", data, k)
        elif code in (0x15, 0x35):  # JEQ|K, JGE|K
            hit = acc == k if code == 0x15 else acc >= k
            pc += jt if hit else jf
        elif code == 0x06:  # RET|K
            return k
        else:
            raise AssertionError(f"opcode {code:#x} is outside the interpreter")


TIOCSTI, TIOCLINUX, TCGETS = 0x5412, 0x541C, 0x5401
_REQUEST = {
    TIOCSTI: "TIOCSTI",
    TIOCLINUX: "TIOCLINUX",
    TCGETS: "TCGETS",
    (1 << 32) | TIOCSTI: "high",
}


@pytest.mark.parametrize(
    ("arch", "name", "nr", "audit_arch", "expected", "arg1"),
    [
        *[(a, n, None, a, EPERM, None) for a, table in os_seccomp.DENY.items() for n in table],
        ("x86_64", None, 0, "x86_64", ALLOW, None),
        ("x86_64", "keyctl", None, "i386", ENOSYS, None),
        ("x86_64", None, X32 | 0, "x86_64", ENOSYS, None),
        # ioctl: a request that types into a terminal is denied, by its low 32 bits.
        *[(a, "ioctl", None, a, EPERM, TIOCSTI) for a in os_seccomp.DENY],
        *[(a, "ioctl", None, a, EPERM, TIOCLINUX) for a in os_seccomp.DENY],
        ("x86_64", "ioctl", None, "x86_64", EPERM, (1 << 32) | TIOCSTI),
        *[(a, "ioctl", None, a, ALLOW, TCGETS) for a in os_seccomp.DENY],
        # The request word means nothing to any other call (1 is write).
        ("x86_64", None, 1, "x86_64", ALLOW, TIOCSTI),
    ],
    ids=lambda v: v if isinstance(v, str) else _REQUEST.get(v),
)
def test_filter_decides(arch, name, nr, audit_arch, expected, arg1):
    program = os_seccomp.build(arch)
    assert len(program) % 8 == 0
    table = {**os_seccomp.DENY[arch], "ioctl": os_seccomp.IOCTL[arch]}
    nr = table[name] if name else nr
    assert run_bpf(program, nr, AUDIT[audit_arch], arg1 or 0) == expected


def test_unknown_arch_refused():
    with pytest.raises(ValueError, match="riscv64"):
        os_seccomp.build("riscv64")


@pytest.mark.e2e("bwrap")
def test_filter_under_bwrap(tmp_path):
    arch = os_seccomp.machine()
    nrs = (os_seccomp.DENY[arch]["keyctl"], os_seccomp.DENY[arch]["io_uring_setup"])
    probe = (
        "import ctypes\n"
        "libc = ctypes.CDLL(None, use_errno=True)\n"
        f"for nr in {nrs}:\n"
        "    libc.syscall(nr, 0, 0, 0)\n"
        "    print(ctypes.get_errno())\n"
        # On /dev/null: ENOTTY were the request let through, never a typed byte.
        f"libc.ioctl(0, {TIOCSTI}, ctypes.c_char_p(b'x'))\n"
        "print(ctypes.get_errno())\n"
    )

    def bwrap(*cmd):
        # A fresh descriptor per run: runs sharing one would share its file offset.
        fd = os.open(tmp_path / "filter.bpf", os.O_RDWR | os.O_CREAT)
        try:
            os.write(fd, os_seccomp.build(arch))
            os.lseek(fd, 0, os.SEEK_SET)
            argv = [
                "bwrap", "--unshare-all", "--unshare-user", "--disable-userns", "--cap-drop", "ALL",
                "--seccomp", str(fd), "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc",
            ]  # fmt: skip
            return subprocess.run(
                [*argv, *cmd],
                pass_fds=[fd],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=60,
            )
        finally:
            os.close(fd)

    py = bwrap(sys.executable, "-I", "-c", probe)
    assert py.stdout.split() == [str(errno.EPERM)] * 3, py.stderr
    git = bwrap("git", "--version")
    assert git.returncode == 0 and git.stdout.startswith("git version"), git.stderr
