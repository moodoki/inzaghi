"""The pools the app's I/O runs on, and what quitting does to them.

A read of a channel's volume cannot be called back, so quitting has to be
allowed to leave one where it is.  ``ThreadPoolExecutor`` will not allow it:
it joins every worker at interpreter exit, after the screen has been handed
back.  These are the properties that replace it.
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from inzaghi.ui.volume import VolumePool, WorkerPool


def running(prefix: str) -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name.startswith(prefix)]


def test_work_runs_somewhere_that_is_not_here():
    pool = VolumePool(2, name="test-elsewhere")
    try:
        thread = pool.submit(threading.current_thread).result(timeout=5)
        assert thread is not threading.current_thread()
        assert thread.name.startswith("test-elsewhere")
    finally:
        pool.shutdown()


def test_every_thread_is_a_daemon():
    """Which is the whole point: nothing joins a daemon on the way out."""
    pool = VolumePool(2, name="test-daemon")
    try:
        assert pool.submit(threading.current_thread).result(timeout=5).daemon
    finally:
        pool.shutdown()


def test_the_answer_to_a_read_that_failed():
    pool = VolumePool(1, name="test-raise")
    try:
        future = pool.submit(lambda: 1 / 0)
        with pytest.raises(ZeroDivisionError):
            future.result(timeout=5)
    finally:
        pool.shutdown()


def test_a_wedged_read_costs_one_thread_and_not_one_per_poll():
    """The bound: six reads that never come back are still four threads."""
    pool = VolumePool(4, name="test-bound")
    held = threading.Event()
    started = threading.Semaphore(0)

    def wedged() -> None:
        started.release()
        held.wait(10)

    try:
        queued = [pool.submit(wedged) for _ in range(6)]
        for _ in range(4):
            assert started.acquire(timeout=5), "a thread never started"
        assert not started.acquire(timeout=0.2), "the bound did not hold"
        assert len(running("test-bound")) == 4

        started_at = time.monotonic()
        pool.shutdown()
        assert time.monotonic() - started_at < 1.0, "shutdown waited"
        assert queued[-1].cancelled(), "work nobody had begun was left pending"
    finally:
        held.set()


def test_a_pool_that_has_been_shut_down_takes_no_more_work():
    pool = VolumePool(1, name="test-closed")
    pool.shutdown()
    with pytest.raises(RuntimeError):
        pool.submit(threading.current_thread)


def test_an_idle_thread_is_used_again_rather_than_replaced():
    pool = VolumePool(4, name="test-reuse")
    try:
        for _ in range(5):
            pool.submit(threading.current_thread).result(timeout=5)
        assert len(running("test-reuse")) == 1
    finally:
        pool.shutdown()


# -- the pool Textual's own thread workers land in -------------------------


def test_a_loop_accepts_the_worker_pool():
    """``set_default_executor`` refuses anything that is not a pool."""
    pool = WorkerPool(2, name="test-default")
    loop = asyncio.new_event_loop()
    try:
        loop.set_default_executor(pool)
        thread = loop.run_until_complete(
            loop.run_in_executor(None, threading.current_thread)
        )
        assert thread.daemon
        assert thread.name.startswith("test-default")
    finally:
        loop.close()


def test_asking_the_worker_pool_to_wait_does_not_make_it():
    """``asyncio.run`` asks for exactly this, and asks before the app returns."""
    pool = WorkerPool(1, name="test-nowait")
    held = threading.Event()
    running_now = threading.Event()

    def wedged() -> None:
        running_now.set()
        held.wait(10)

    try:
        pool.submit(wedged)
        assert running_now.wait(5)
        started_at = time.monotonic()
        pool.shutdown(wait=True)
        assert time.monotonic() - started_at < 1.0
    finally:
        held.set()
