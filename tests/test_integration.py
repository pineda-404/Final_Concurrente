import os
import time
import tempfile
import shutil
import subprocess
import sys
import socket
from pathlib import Path

import pytest

from src.client import send_file


def find_free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.mark.timeout(120)
def test_replication_and_redirect(tmp_path):
    repo_root = Path.cwd()

    # prepare three nodes with distinct ports and temp storage dirs
    ports = [9000, 9001, 9002]
    raft_ports = [10000, 10001, 10002]
    procs = []
    storages = []

    try:
        for i in range(3):
            storage = str(tmp_path / f'node{i}_storage')
            storages.append(storage)
            cmd = [sys.executable, '-m', 'src.worker',
                   '--host', '127.0.0.1', '--port', str(ports[i]),
                   '--monitor-port', str(8000 + i), '--raft-port', str(raft_ports[i]),
                   '--peers']
            # peers list: other worker ports
            peers = [f'127.0.0.1:{p}' for j, p in enumerate(ports) if j != i]
            cmd.extend(peers)
            cmd.extend(['--storage-dir', storage])
            # start process in repo_root so module import works
            p = subprocess.Popen(cmd, cwd=str(repo_root), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            procs.append(p)

        # allow nodes time to start and elect leader
        time.sleep(5)

        # create a small file to upload
        testfile = tmp_path / 'upload.txt'
        testfile.write_text('hello world')

        # send to node 2 (may be follower) to test redirect handling
        send_file('127.0.0.1', ports[1], str(testfile))

        # allow replication time
        time.sleep(3)

        # check that at least two storages contain the file
        found = 0
        for s in storages:
            p = Path(s) / 'upload.txt'
            if p.exists():
                found += 1

        assert found >= 2, f"File replicated to {found} nodes, expected >=2"

    finally:
        for p in procs:
            try:
                p.terminate()
            except Exception:
                pass
        for p in procs:
            try:
                p.wait(timeout=2)
            except Exception:
                p.kill()