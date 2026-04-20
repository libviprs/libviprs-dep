"""Tests for MemoryScheduler (memory-aware parallel build gating)."""

import threading
import time
from unittest.mock import MagicMock

from build_pdfium import MemoryScheduler


class TestFitsWithinBudget:
    def test_four_builds_under_16gb_budget(self):
        progress = MagicMock()
        sched = MemoryScheduler(budget_mb=16384, per_build_mb=4096, progress=progress)
        sched.reserve("a")
        sched.reserve("b")
        sched.reserve("c")
        sched.reserve("d")
        assert sched.reserved_mb == 16384
        progress.set_queued.assert_not_called()

    def test_reserve_and_release_roundtrip(self):
        progress = MagicMock()
        sched = MemoryScheduler(budget_mb=8192, per_build_mb=4096, progress=progress)
        sched.reserve("a")
        assert sched.reserved_mb == 4096
        sched.release()
        assert sched.reserved_mb == 0


class TestQueuesWhenOverBudget:
    def test_third_build_waits_until_release(self):
        progress = MagicMock()
        sched = MemoryScheduler(budget_mb=8192, per_build_mb=4096, progress=progress)
        sched.reserve("a")
        sched.reserve("b")

        done = threading.Event()

        def worker():
            sched.reserve("c")
            done.set()

        t = threading.Thread(target=worker, daemon=True)
        t.start()
        time.sleep(0.1)
        assert not done.is_set()
        progress.set_queued.assert_called_once()
        args, _ = progress.set_queued.call_args
        assert args[0] == "c"
        assert "waiting for memory" in args[1]

        sched.release()
        t.join(timeout=2)
        assert done.is_set()

    def test_release_wakes_all_waiters(self):
        progress = MagicMock()
        sched = MemoryScheduler(budget_mb=4096, per_build_mb=4096, progress=progress)
        sched.reserve("holder")

        started = []
        lock = threading.Lock()

        def worker(name):
            sched.reserve(name)
            with lock:
                started.append(name)
            sched.release()

        threads = [threading.Thread(target=worker, args=(f"t{i}",), daemon=True) for i in range(3)]
        for t in threads:
            t.start()
        time.sleep(0.1)
        assert started == []

        sched.release()
        for t in threads:
            t.join(timeout=2)
        assert sorted(started) == ["t0", "t1", "t2"]


class TestDeadlockGuard:
    def test_single_build_over_budget_still_runs(self):
        # Budget < per_build would otherwise block forever. The first
        # reservation must bypass the wait.
        progress = MagicMock()
        sched = MemoryScheduler(budget_mb=1024, per_build_mb=4096, progress=progress)
        sched.reserve("a")
        assert sched.reserved_mb == 4096
        progress.set_queued.assert_not_called()

    def test_second_caller_still_queues_even_when_first_is_over_budget(self):
        progress = MagicMock()
        sched = MemoryScheduler(budget_mb=1024, per_build_mb=4096, progress=progress)
        sched.reserve("a")

        done = threading.Event()

        def worker():
            sched.reserve("b")
            done.set()

        t = threading.Thread(target=worker, daemon=True)
        t.start()
        time.sleep(0.1)
        assert not done.is_set()

        sched.release()
        t.join(timeout=2)
        assert done.is_set()


class TestQueueAnnouncement:
    def test_set_queued_called_at_most_once_per_reservation(self):
        progress = MagicMock()
        sched = MemoryScheduler(budget_mb=4096, per_build_mb=4096, progress=progress)
        sched.reserve("a")

        def worker():
            sched.reserve("b")

        t = threading.Thread(target=worker, daemon=True)
        t.start()
        time.sleep(0.2)
        sched.release()
        t.join(timeout=2)
        # 'b' waited, got announced once, then proceeded — no double-announce
        # even though notify_all may have fired multiple times.
        assert progress.set_queued.call_count == 1
