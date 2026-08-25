"""The single-instance lock.

A COM port allows exactly one holder, and two processes on one GPIB board
interleave transactions into garbled replies.  So "am I the only recorder" has
to be answerable, and it has to stay answerable after a crash.
"""

import json
import os
import subprocess
import sys
import textwrap
import time

import pytest

from lschart.ipc import AlreadyRunning, InstanceLock


def test_acquiring_creates_the_file_and_its_parents(tmp_path):
    path = tmp_path / "nested" / "run.lock"
    with InstanceLock(path) as lock:
        assert lock.held
        assert path.exists()


def test_a_second_lock_in_this_process_is_refused(tmp_path):
    path = tmp_path / "run.lock"
    with InstanceLock(path):
        with pytest.raises(AlreadyRunning):
            InstanceLock(path).acquire()


def test_the_refusal_names_who_holds_it(tmp_path):
    """"Another instance is running" is useless without "which one"."""
    path = tmp_path / "run.lock"
    with InstanceLock(path):
        with pytest.raises(AlreadyRunning) as exc:
            InstanceLock(path).acquire()
    assert exc.value.holder["pid"] == os.getpid()
    assert str(os.getpid()) in str(exc.value)


def test_releasing_lets_the_next_one_in(tmp_path):
    path = tmp_path / "run.lock"
    first = InstanceLock(path).acquire()
    first.release()
    with InstanceLock(path) as second:
        assert second.held


def test_two_different_paths_do_not_collide(tmp_path):
    """The escape hatch: two genuinely different rigs, two lock files."""
    with InstanceLock(tmp_path / "a.lock"), InstanceLock(tmp_path / "b.lock"):
        pass


def test_a_failed_attempt_leaves_the_holders_record_intact(tmp_path):
    """Truncating on the way to a refused lock would erase the running
    process's own diagnostics."""
    path = tmp_path / "run.lock"
    with InstanceLock(path):
        with pytest.raises(AlreadyRunning):
            InstanceLock(path).acquire()
        assert json.loads(path.read_text())["pid"] == os.getpid()


def test_the_file_survives_release(tmp_path):
    """Unlinking races with a process that has already opened it and is
    waiting to lock -- which is how a lock file gets deleted out from under a
    live holder."""
    path = tmp_path / "run.lock"
    InstanceLock(path).acquire().release()
    assert path.exists()


def test_a_killed_process_releases_the_lock(tmp_path):
    """The reason this is an OS lock and not a PID file.

    A process that is killed -- or that loses power -- never runs cleanup, so
    a lock that depended on cleanup would be stale forever.  The kernel drops
    this one.

    This runs on Windows too.  It used to be skipped there for "POSIX signal
    semantics", but nothing here is a signal: ``Popen.kill()`` is
    TerminateProcess, and TerminateProcess drops the handle's locks exactly as
    SIGKILL does.  Skipping it hid the fact that the Windows lock was taken on
    a byte that moved with the file position, so a second instance never
    collided with the first at all.
    """
    path = tmp_path / "run.lock"
    script = textwrap.dedent(f"""
        import sys, time
        sys.path.insert(0, {str(os.getcwd())!r})
        from lschart.ipc import InstanceLock
        InstanceLock({str(path)!r}).acquire()
        print("holding", flush=True)
        time.sleep(60)
    """)
    proc = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, text=True)
    try:
        assert proc.stdout.readline().strip() == "holding"
        with pytest.raises(AlreadyRunning):
            InstanceLock(path).acquire()
        proc.kill()
        proc.wait(timeout=10)
    finally:
        if proc.poll() is None:  # pragma: no cover
            proc.kill()

    # No cleanup ran in that process, and yet the kernel drops the lock.
    #
    # Not necessarily by the time `wait()` returns, though.  On Windows the
    # handle is closed during process *teardown*, which lags the exit code by a
    # few milliseconds: retrying immediately is refused roughly one attempt in
    # three.  That is a property of the platform and not of this test, and it
    # has an operational edge -- a supervisor that restarts the recorder the
    # instant it dies can be refused its own lock.  See
    # docs/recorder/windows.md.
    deadline = time.monotonic() + 5.0
    while True:
        try:
            lock = InstanceLock(path).acquire()
            break
        except AlreadyRunning:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.05)
    try:
        assert lock.held, "the kernel released it when the process died"
    finally:
        lock.release()
