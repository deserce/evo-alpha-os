"""Shared nonblocking lock for research import, sync and rollback writers."""
from contextlib import contextmanager
import fcntl
from pathlib import Path


class UpdateLocked(RuntimeError):
    """Another catalogue writer is active."""


@contextmanager
def writer_lock(target: str | Path):
    """Hold a stable lock inode; never unlink the lock file after release."""
    path = Path(str(Path(target).resolve()) + '.update.lock')
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise UpdateLocked('research writer already active') from exc
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
