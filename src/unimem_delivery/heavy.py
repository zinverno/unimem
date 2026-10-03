"""One local heavy process slot shared by ASR and explicit vision operations."""

import fcntl
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def heavy_slot(
    data_dir: Path, stopping: threading.Event, *, required: bool = True
) -> Iterator[int | None]:
    if not required:
        yield -1
        return
    # ponytail: one slot per API data directory; no scheduling framework.
    # Children inherit the descriptor, retaining the slot if the API disappears.
    with (data_dir / "heavy-worker.lock").open("a+b") as lock:
        while not stopping.is_set():
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                stopping.wait(0.1)
            else:
                yield lock.fileno()
                return
        yield None
