"""
Dummy fcntl module for Windows compatibility.
Provides no-op functions and standard constants so Unix-dependent code can be imported/tested on Windows.
"""

# Common locking constants
LOCK_SH = 1
LOCK_EX = 2
LOCK_NB = 4
LOCK_UN = 8

# Common file control flags/commands
F_DUPFD = 0
F_GETFD = 1
F_SETFD = 2
F_GETFL = 3
F_SETFL = 4
F_SETLK = 6
F_SETLKW = 7
F_GETLK = 5


def flock(fd, operation):
    """No-op on Windows."""
    pass


def fcntl(fd, cmd, arg=0):
    """No-op on Windows, returns 0."""
    return 0


def ioctl(fd, request, arg=0):
    """No-op on Windows, returns 0."""
    return 0


def lockf(fd, operation, length=0, start=0, whence=0):
    """No-op on Windows."""
    pass
