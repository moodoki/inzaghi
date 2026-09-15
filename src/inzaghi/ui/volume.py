"""A pool of threads the interpreter will not wait for on the way out.

A read of a channel's volume cannot be cancelled once it has begun, only waited
for: the call is inside a sync client, and that client answers when it likes.
So the app is careful never to have more than one scan out at a time, and to
give up on the ones that are out rather than joining them at exit.

``ThreadPoolExecutor`` will not let it.  Its workers are ordinary threads, and
it registers them with an ``atexit`` hook that joins every one of them --
``shutdown(wait=False)`` declines to wait, and then the interpreter waits
anyway, after the screen has been handed back and there is nothing left to
look at.  Quitting a session whose volume has gone quiet sits there until the
read returns, which is where it came in.

Hence this: the same bounded queue-and-threads arrangement, on daemon threads,
which nothing joins.  ``submit`` returns a real ``concurrent.futures.Future``,
because ``loop.run_in_executor`` wraps one.
"""

from __future__ import annotations

import queue
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable


class VolumePool:
    """Run callables on at most ``max_workers`` daemon threads."""

    def __init__(self, max_workers: int, name: str) -> None:
        self._max_workers = max_workers
        self._name = name
        self._work: queue.SimpleQueue = queue.SimpleQueue()
        self._lock = threading.Lock()
        self._threads: list[threading.Thread] = []
        self._idle = 0
        self._closed = False

    def submit(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Future:
        """Queue ``fn`` and hand back the future that will hold its result."""
        future: Future = Future()
        with self._lock:
            if self._closed:
                raise RuntimeError("the volume pool has been shut down")
            self._work.put((future, fn, args, kwargs))
            self._hire()
        return future

    def _hire(self) -> None:
        """Start a thread if nobody is free to take the work.  Holds the lock.

        The bound is the point: a read that has wedged holds its thread for as
        long as the volume stays quiet, and a poll every couple of seconds must
        not answer that by starting a thread every couple of seconds.  Miscount
        the idle ones under a race and the cost is one spare thread, still
        under the bound.
        """
        if self._idle or len(self._threads) >= self._max_workers:
            return
        thread = threading.Thread(
            target=self._serve, name=f"{self._name}-{len(self._threads)}", daemon=True
        )
        self._threads.append(thread)
        thread.start()

    def _serve(self) -> None:
        while True:
            with self._lock:
                self._idle += 1
            item = self._work.get()
            with self._lock:
                self._idle -= 1
            if item is None:
                self._work.put(None)  # whoever else is waiting is leaving too
                return
            future, fn, args, kwargs = item
            if future.set_running_or_notify_cancel():
                try:
                    future.set_result(fn(*args, **kwargs))
                except BaseException as exc:  # it belongs to whoever asked, not here
                    future.set_exception(exc)
            # Not held until the next piece of work arrives: a snapshot is a
            # channel's whole contents, and this thread may wait here for hours.
            del item, future

    def shutdown(self) -> None:
        """Take no more work, drop what is queued, and wait for none of it.

        A thread still inside a read stays inside it; nothing can call it back.
        But it is a daemon, so quitting is quitting.
        """
        with self._lock:
            if self._closed:
                return
            self._closed = True
        while True:
            try:
                item = self._work.get_nowait()
            except queue.Empty:
                break
            if item is not None:
                item[0].cancel()
        self._work.put(None)


class WorkerPool(ThreadPoolExecutor):
    """A ``ThreadPoolExecutor`` by type only, so that a loop will accept it.

    Textual runs every ``@work(thread=True)`` on the loop's *default* executor,
    and in this app those are all calls that wait on a channel's volume too:
    sending, deleting a sync client's leftovers, launching a file the client
    has evicted, re-reading the delivered file the preview is showing -- and
    that last one goes out on every poll.  They have the exit problem above,
    and a worse one on top of it: ``asyncio.run``, which is how Textual runs
    the app, awaits ``loop.shutdown_default_executor()`` on the way out, and
    that joins the pool before ``App.run`` has even returned.  The screen is
    still there and the app no longer answers.

    ``loop.set_default_executor`` refuses anything that is not a
    ``ThreadPoolExecutor``, so this is one.  It simply never uses any of it:
    the base class is never asked for a thread, every submission goes to the
    daemon threads of a ``VolumePool``, and shutting it down returns instead
    of waiting, which is the whole point of being here.
    """

    def __init__(self, max_workers: int, name: str) -> None:
        super().__init__(max_workers=max_workers, thread_name_prefix=name)
        self._pool = VolumePool(max_workers, name=name)

    def submit(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Future:
        return self._pool.submit(fn, *args, **kwargs)

    def shutdown(self, wait: bool = True, *, cancel_futures: bool = False) -> None:
        """Never waits, whatever it is asked.  ``wait`` is the caller's hope."""
        self._pool.shutdown()
