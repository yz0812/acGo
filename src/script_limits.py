"""POSIX child launcher: set limits before exec (no threaded preexec_fn)."""
import os
from pathlib import Path
import resource
import sys


def main():
    memory_mb, timeout, cgroup, *command = sys.argv[1:]
    memory_mb, timeout = int(memory_mb), int(timeout)
    if cgroup != '-':
        Path(cgroup, 'cgroup.procs').write_text(str(os.getpid()))
    # Address space is different from RSS. V8 reserves virtual address ranges;
    # use its own heap cap plus the parent's aggregate RSS watchdog as well.
    if '-u' in command:  # Python; V8/WASM virtual reservations are not physical memory.
        address_limit = max(256, memory_mb * 2) * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (address_limit, address_limit))
    resource.setrlimit(resource.RLIMIT_CPU, (timeout, timeout + 1))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    os.execv(command[0], command)


if __name__ == '__main__':
    main()
