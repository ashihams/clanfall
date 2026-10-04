import queue
import threading
import time
from typing import Any, Callable, Optional


class WriteBehind:
    """
    Serial background writer for a remote store. Callers enqueue writes and keep
    going; anything that reads or wipes the remote calls flush() first, so ordering
    is preserved. A failed write is re-raised on the next submit()/flush() (letting
    the caller fail over), and queued writes after a failure are dropped rather than
    applied to a store that is already inconsistent.
    """

    def __init__(self, name: str):
        self._q: "queue.Queue[tuple]" = queue.Queue()
        self._error: Optional[BaseException] = None
        self._thread = threading.Thread(target=self._run, name=f"{name}-writer", daemon=True)
        self._thread.start()

    def submit(self, fn: Callable[..., Any], *args: Any) -> None:
        self.raise_pending()
        self._q.put((fn, args))

    def flush(self, timeout: Optional[float] = None) -> None:
        deadline = None if timeout is None else time.monotonic() + timeout
        while self._q.unfinished_tasks:
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError("remote writes still pending")
            time.sleep(0.01)
        self.raise_pending()

    def raise_pending(self) -> None:
        if self._error is not None:
            err, self._error = self._error, None
            raise err

    def discard_pending_error(self) -> None:
        self._error = None

    def _run(self) -> None:
        while True:
            fn, args = self._q.get()
            try:
                if self._error is None:
                    fn(*args)
            except BaseException as exc:
                self._error = exc
            finally:
                self._q.task_done()
