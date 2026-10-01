"""Private job directories; janitor never touches active jobs or unowned paths."""

import os
import shutil
import tempfile
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path


class TempFiles:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._active: set[Path] = set()

    @asynccontextmanager
    async def job(self) -> AsyncIterator[Path]:
        directory = Path(tempfile.mkdtemp(prefix="job-", dir=self.root))
        self._active.add(directory)
        try:
            (directory / ".circlebot-owner").write_text(str(os.getpid()), encoding="ascii")
            yield directory
        finally:
            shutil.rmtree(directory, ignore_errors=True)
            self._active.discard(directory)

    def cleanup(self, max_age_sec: float = 3600) -> int:
        cutoff = time.time() - max_age_sec
        removed = 0
        for directory in self.root.glob("job-*"):
            if directory in self._active or directory.is_symlink() or not directory.is_dir():
                continue
            try:
                marker = directory / ".circlebot-owner"
                if marker.is_symlink() or not marker.is_file():
                    continue
                with marker.open(encoding="ascii") as stream:
                    pid = int(stream.read(32))
                if not 0 < pid < 2**31:
                    continue
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    pass
                except PermissionError:
                    continue  # an owner we cannot inspect is not an orphan
                else:
                    continue
                if directory.stat().st_mtime <= cutoff:
                    shutil.rmtree(directory)
                    removed += 1
            except (OSError, ValueError, UnicodeError):
                continue
        return removed
