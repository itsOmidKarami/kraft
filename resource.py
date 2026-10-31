"""
Dummy resource module for Windows compatibility.
Stubs out Unix resource management functions so the codebase can be imported and tested on Windows.
"""

# Common resource constants
RUSAGE_SELF = 0
RUSAGE_CHILDREN = -1
RUSAGE_BOTH = -2
RUSAGE_THREAD = 1

RLIMIT_CPU = 0
RLIMIT_FSIZE = 1
RLIMIT_DATA = 2
RLIMIT_STACK = 3
RLIMIT_CORE = 4
RLIMIT_RSS = 5
RLIMIT_NPROC = 6
RLIMIT_NOFILE = 7
RLIMIT_MEMLOCK = 8
RLIMIT_AS = 9


def getrusage(who):
    """Dummy rusage returning a basic object with zeroed stats."""
    class DummyRusage:
        ru_utime = 0.0
        ru_stime = 0.0
        ru_maxrss = 0
    return DummyRusage()


def getrlimit(resource):
    """Returns a dummy soft/hard limit tuple."""
    return (2048, 2048)


def setrlimit(resource, limits):
    """No-op on Windows."""
    pass


def getpagesize():
    """Returns standard page size."""
    return 4096
