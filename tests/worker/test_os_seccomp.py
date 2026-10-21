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


def run_bpf(program: bytes, nr: int, audit_arch: int) -> int:
    """Interpret LD|W|ABS, JEQ, JGE and RET over a packed seccomp_data."""
    data = struct.pack("<IIQ6Q", nr, audit_arch, 0, *([0] * 6))
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


@pytest.mark.parametrize(
    ("arch", "name", "nr", "audit_arch", "expected"),
    [
        ("x86_64", "keyctl", None, "x86_64", EPERM),
        ("x86_64", "io_uring_setup", None, "x86_64", EPERM),
        ("x86_64", "mount", None, "x86_64", EPERM),
        ("x86_64", None, 0, "x86_64", ALLOW),
        ("x86_64", "keyctl", None, "i386", ENOSYS),
        ("x86_64", None, X32 | 0, "x86_64", ENOSYS),
        ("aarch64", "keyctl", None, "aarch64", EPERM),
    ],
    ids=[
        "keyctl-denied",
        "io-uring-denied",
        "mount-denied",
        "read-allowed",
        "foreign-arch",
        "x32",
        "aarch64-keyctl",
    ],
)
def test_filter_decides(arch, name, nr, audit_arch, expected):
    nr = os_seccomp.DENY[arch][name] if name else nr
    assert run_bpf(os_seccomp.build(arch), nr, AUDIT[audit_arch]) == expected


def test_every_denied_call_is_denied():
    for arch, table in os_seccomp.DENY.items():
        program = os_seccomp.build(arch)
        assert len(program) % 8 == 0
        assert {run_bpf(program, nr, AUDIT[arch]) for nr in table.values()} == {EPERM}


def test_unknown_arch_refused():
    with pytest.raises(ValueError, match="riscv64"):
        os_seccomp.build("riscv64")


@pytest.mark.e2e("bwrap")
def test_filter_under_bwrap(tmp_path):
    arch = os_seccomp.machine()
    fd = os.open(tmp_path / "filter.bpf", os.O_RDWR | os.O_CREAT)
    os.write(fd, os_seccomp.build(arch))
    os.lseek(fd, 0, os.SEEK_SET)
    nrs = (os_seccomp.DENY[arch]["keyctl"], os_seccomp.DENY[arch]["io_uring_setup"])
    probe = (
        "import ctypes\n"
        "libc = ctypes.CDLL(None, use_errno=True)\n"
        f"for nr in {nrs}:\n"
        "    libc.syscall(nr, 0, 0, 0)\n"
        "    print(ctypes.get_errno())\n"
    )
    argv = [
        "bwrap", "--unshare-all", "--unshare-user", "--disable-userns", "--cap-drop", "ALL",
        "--seccomp", "3", "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc",
    ]  # fmt: skip
    py = subprocess.run(
        [*argv, sys.executable, "-I", "-c", probe],
        pass_fds=[fd],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert py.stdout.split() == [str(errno.EPERM)] * 2, py.stderr
    git = subprocess.run(
        [*argv, "git", "--version"], pass_fds=[fd], capture_output=True, text=True, timeout=60
    )
    assert git.returncode == 0 and git.stdout.startswith("git version"), git.stderr
