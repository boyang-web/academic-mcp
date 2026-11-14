"""Cross-process protection for persistent browser profiles."""
import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path

class InterProcessLock:
    """Cross-process mutex over the shared browser profile.

    Several MCP client sessions (or a client plus a CLI run) can point at one
    data dir; two processes driving the same Chromium persistent profile
    corrupt it and overwrite each other's cookies. The OS releases the lock
    when the holder dies, so a crash cannot strand it.
    """

    def __init__(self, path, timeout=90.0):
        self.path = Path(path)
        self.timeout = timeout
        self._fd = None

    @property
    def held(self):
        return self._fd is not None

    def _try_acquire(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self.path, os.O_CREAT | os.O_RDWR)
        except OSError:
            return False
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            if sys.platform == 'win32':
                import msvcrt
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return False
        self._fd = fd
        return True

    async def acquire(self):
        deadline = datetime.now().timestamp() + self.timeout
        while True:
            if await asyncio.to_thread(self._try_acquire):
                return True
            if datetime.now().timestamp() >= deadline:
                return False
            await asyncio.sleep(0.5)

    def release(self):
        if self._fd is None:
            return
        try:
            os.lseek(self._fd, 0, os.SEEK_SET)
            if sys.platform == 'win32':
                import msvcrt
                msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._fd, fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            os.close(self._fd)
            self._fd = None
