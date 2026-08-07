"""Supervise one native solver process and capture its callback stream."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import queue
import signal
import subprocess
import threading
import time
from typing import Any


EVENT_PREFIX = "TIG_EVENT\t"


@dataclass
class CapturedSolution:
    callback_index: int
    callback_wallclock_ms: float
    solution: Any


@dataclass
class CapturedSnapshot:
    requested_wallclock_ms: float
    captured: CapturedSolution


@dataclass
class SolverProcessResult:
    ready: bool = False
    completion_seen: bool = False
    timed_out: bool = False
    startup_timed_out: bool = False
    exit_code: int | None = None
    outcome: str | None = None
    solver_wallclock_ms: float | None = None
    error: str | None = None
    callback_count: int = 0
    latest_solution: CapturedSolution | None = None
    all_solutions: list[CapturedSolution] = field(default_factory=list)
    snapshots: list[CapturedSnapshot] = field(default_factory=list)
    stdout_log: list[str] = field(default_factory=list)
    stderr_log: list[str] = field(default_factory=list)


def run_solver_process(
    binary: Path,
    *,
    instance: Path,
    algorithm_seed: str | None,
    hyperparameters: dict[str, Any] | None,
    timeout_seconds: float,
    save_all_solutions: bool = False,
    snapshot_interval_seconds: float | None = None,
    snapshot_times_seconds: list[float] | None = None,
    startup_timeout_seconds: float = 60.0,
    subcommand: str | None = None,
) -> SolverProcessResult:
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    if snapshot_interval_seconds is not None and snapshot_interval_seconds <= 0:
        raise ValueError("snapshot interval must be positive")

    command = [str(binary)]
    if subcommand is not None:
        command.append(subcommand)
    command.extend(["--instance", str(instance)])
    if algorithm_seed is not None:
        command.extend(["--algorithm-seed", algorithm_seed])
    if hyperparameters is not None:
        command.extend(
            [
                "--hyperparameters-json",
                json.dumps(hyperparameters, separators=(",", ":"), sort_keys=True),
            ]
        )

    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        start_new_session=(os.name == "posix"),
    )
    assert process.stdout is not None
    assert process.stderr is not None

    messages: queue.Queue[tuple[str, str | None]] = queue.Queue()

    def read_stream(kind: str, stream: Any) -> None:
        try:
            for line in iter(stream.readline, ""):
                messages.put((kind, line.rstrip("\n")))
        finally:
            messages.put((kind, None))

    stdout_thread = threading.Thread(
        target=read_stream, args=("stdout", process.stdout), daemon=True
    )
    stderr_thread = threading.Thread(
        target=read_stream, args=("stderr", process.stderr), daemon=True
    )
    stdout_thread.start()
    stderr_thread.start()

    result = SolverProcessResult()
    launched_at = time.monotonic()
    ready_at: float | None = None
    streams_done: set[str] = set()
    completion_seen = False
    snapshot_times = sorted(set(snapshot_times_seconds or []))
    if any(value <= 0 for value in snapshot_times):
        _terminate(process)
        raise ValueError("snapshot times must be positive")
    next_snapshot_index = 0
    next_interval = snapshot_interval_seconds
    last_snapshotted_callback = 0

    def capture_due_snapshots(now: float) -> None:
        nonlocal next_snapshot_index, next_interval, last_snapshotted_callback
        if ready_at is None:
            return
        elapsed_seconds = now - ready_at
        due: list[float] = []
        if snapshot_times:
            while (
                next_snapshot_index < len(snapshot_times)
                and snapshot_times[next_snapshot_index] <= elapsed_seconds
            ):
                due.append(snapshot_times[next_snapshot_index])
                next_snapshot_index += 1
        elif next_interval is not None and snapshot_interval_seconds is not None:
            while next_interval <= elapsed_seconds:
                due.append(next_interval)
                next_interval += snapshot_interval_seconds

        for requested_seconds in due:
            latest = result.latest_solution
            if latest is None or latest.callback_index == last_snapshotted_callback:
                continue
            result.snapshots.append(
                CapturedSnapshot(
                    requested_wallclock_ms=requested_seconds * 1000.0,
                    captured=latest,
                )
            )
            last_snapshotted_callback = latest.callback_index

    while True:
        now = time.monotonic()
        capture_due_snapshots(now)
        if ready_at is None and now - launched_at >= startup_timeout_seconds:
            result.startup_timed_out = True
            result.error = "Solver did not become ready before the startup timeout"
            _terminate(process)
        elif ready_at is not None and now - ready_at >= timeout_seconds:
            result.timed_out = True
            result.solver_wallclock_ms = (now - ready_at) * 1000.0
            result.error = f"Solver reached the {timeout_seconds:g} second timeout"
            _terminate(process)

        wait_timeout = 0.02
        if ready_at is None:
            wait_timeout = min(
                wait_timeout,
                max(0.0, startup_timeout_seconds - (time.monotonic() - launched_at)),
            )
        elif not result.timed_out:
            wait_timeout = min(
                wait_timeout,
                max(0.0, timeout_seconds - (time.monotonic() - ready_at)),
            )
        try:
            kind, line = messages.get(timeout=wait_timeout)
        except queue.Empty:
            if process.poll() is not None and streams_done == {"stdout", "stderr"}:
                break
            continue

        if line is None:
            streams_done.add(kind)
        elif kind == "stderr":
            result.stderr_log.append(line)
        elif not line.startswith(EVENT_PREFIX):
            result.stdout_log.append(line)
        else:
            try:
                event = json.loads(line[len(EVENT_PREFIX) :])
            except json.JSONDecodeError:
                result.stdout_log.append(line)
                continue
            event_type = event.get("type")
            if event_type == "ready":
                if ready_at is None:
                    ready_at = time.monotonic()
                    result.ready = True
            elif event_type == "solution":
                result.callback_count += 1
                captured = CapturedSolution(
                    callback_index=result.callback_count,
                    callback_wallclock_ms=float(event["callback_wallclock_ms"]),
                    solution=event["solution"],
                )
                result.latest_solution = captured
                if save_all_solutions:
                    result.all_solutions.append(captured)
            elif event_type == "complete":
                completion_seen = True
                result.completion_seen = True
                result.outcome = event.get("outcome")
                result.solver_wallclock_ms = float(event["solver_wallclock_ms"])
                result.error = event.get("error")

        if process.poll() is not None and streams_done == {"stdout", "stderr"}:
            break

    result.exit_code = process.wait()
    stdout_thread.join(timeout=1)
    stderr_thread.join(timeout=1)
    process.stdout.close()
    process.stderr.close()
    if not result.timed_out and not result.startup_timed_out and not completion_seen:
        result.error = result.error or (
            f"Solver exited with code {result.exit_code} without a completion event"
        )
    return result


def _terminate(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
        process.wait(timeout=0.5)
        return
    except (ProcessLookupError, subprocess.TimeoutExpired):
        pass
    if process.poll() is None:
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except ProcessLookupError:
            pass
