"""Poll Resolve without blocking the native UI or retaining stale SDK proxies."""
import json
import os
import subprocess
import tempfile
import time

from .core.resolve_status import ResolveStatus
from .separation import worker_python

# Legacy wire marker: keep compatibility with already-running status windows.
STATUS_PREFIX = 'HVPC_RESOLVE_STATUS='
STATUS_CODES = {'ready', 'unavailable', 'no_project', 'wrong_project', 'no_timeline',
                'wrong_timeline', 'old_version', 'no_context'}


class ResolveMonitor:
    interval = 5.0
    timeout = 8.0

    def __init__(self, root, path):
        self.root, self.path = root, path
        self.state = ResolveStatus()
        self.process = self.output = None
        self.started = self.next_check = 0.0
        self.timed_out = False

    def refresh(self):
        if self.process is None:
            self.next_check = 0.0
            self.state = ResolveStatus()

    def tick(self):
        instant = time.monotonic()
        if self.process is not None:
            if self.process.poll() is not None:
                try:
                    self.output.seek(0)
                    lines = self.output.read(16384).decode('utf-8', errors='replace').splitlines()
                    value = next(json.loads(line[len(STATUS_PREFIX):]) for line in reversed(lines)
                                 if line.startswith(STATUS_PREFIX))
                    code = value.get('code')
                    if self.timed_out or self.process.returncode != 0 or code not in STATUS_CODES:
                        code = 'unavailable'
                    self.state = ResolveStatus(code)
                except (OSError, ValueError, TypeError, AttributeError, StopIteration):
                    self.state = ResolveStatus('unavailable')
                finally:
                    self.output.close()
                    self.process = self.output = None
                    self.next_check = instant + self.interval
            elif instant - self.started >= self.timeout and not self.timed_out:
                self.timed_out = True
                self.state = ResolveStatus('unavailable')
                try:
                    self.process.kill()  # only our probe, never Resolve or the download worker
                except OSError:
                    pass  # it may have exited between poll() and kill()
        if self.process is None and instant >= self.next_check:
            try:
                self.output = tempfile.TemporaryFile()
                self.process = subprocess.Popen(
                    [str(worker_python()), str(self.root / 'src/mvsep_cli.py'), '--resolve-status', str(self.path)],
                    cwd=str(self.root), stdin=subprocess.DEVNULL, stdout=self.output, stderr=subprocess.DEVNULL,
                    close_fds=True, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                self.started, self.timed_out = instant, False
            except (OSError, RuntimeError):
                if self.output:
                    self.output.close()
                self.output = self.process = None
                self.state = ResolveStatus('unavailable')
                self.next_check = instant + self.interval
        return self.state

    def close(self):
        if self.process is not None:
            try:
                if self.process.poll() is None:
                    self.process.kill()
                self.process.wait(timeout=1)
            except (OSError, subprocess.TimeoutExpired):
                pass
            self.process = None
        if self.output:
            self.output.close()
            self.output = None
