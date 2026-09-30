import os
import subprocess
import sys
import time

import pytest

from scripts.task_lock import _pid_exists


@pytest.mark.skipif(os.name != "nt", reason="Windows-specific regression")
def test_pid_probe_does_not_terminate_live_process():
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(10)"])
    try:
        time.sleep(0.2)
        assert _pid_exists(proc.pid) is True
        assert proc.poll() is None
    finally:
        proc.terminate()
        proc.wait(timeout=5)


@pytest.mark.skipif(os.name != "nt", reason="Windows-specific regression")
def test_pid_probe_rejects_completed_process():
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait(timeout=5)

    assert _pid_exists(proc.pid) is False
