import socket
import threading
import os
import argparse
import http.server
import socketserver
import json
import base64
from datetime import datetime
from src.raft import RaftNode, NotLeader
import subprocess

STORAGE_DIR = 'worker_storage'
LOG_FILE = 'worker.log'


def log(msg: str):
    now = datetime.utcnow().isoformat()
    line = f"{now} {msg}\n"
    print(line, end='')
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(line)
STORAGE_DIR = 'worker_storage'
LOG_FILE = 'worker.log'
class WorkerTCPHandler(threading.Thread):
    def __init__(self, conn, addr, peers):
        super().__init__(daemon=True)
        self.conn = conn
        self.addr = addr
    os.makedirs(os.path.dirname(LOG_FILE) or '.', exist_ok=True)
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(line)
    def run(self):
        try:
            data = self.conn.recv(4096)
            if not data:
                return
            header = data.decode('utf-8', errors='ignore')
            # simple protocol: JSON header line then file bytes
            header_obj = json.loads(header)
            fname = header_obj.get('filename')
            size = header_obj.get('size', 0)
            log(f"Received PUT {fname} size={size} from {self.addr}")

            # receive remaining bytes
            received = b''
            while len(received) < size:
                chunk = self.conn.recv(min(4096, size - len(received)))
                if not chunk:
                    break
                received += chunk
            # Use Raft to replicate the entry. Encode file bytes as base64 to include in JSON command.
            entry = {'filename': fname, 'data_b64': base64.b64encode(received).decode('ascii')}
            try:
                # raft_node is injected as global singleton in main
                success = raft_node.replicate(entry)
                if success:
                    # persist locally as committed
                    os.makedirs(STORAGE_DIR, exist_ok=True)
                    path = os.path.join(STORAGE_DIR, fname)
                    with open(path, 'wb') as f:
                        f.write(received)
                    log(f"Committed and stored {path}")
                        self.conn.sendall(json.dumps({'status': 'OK'}).encode('utf-8'))
                        # optionally run Java training module asynchronously
                        try:
                            if globals().get('RUN_TRAIN'):
                                # run in background so worker keeps serving
                                threading.Thread(target=run_java_training, args=(path,), daemon=True).start()
                        except Exception as e:
                            log(f"Failed to start training: {e}")
                else:
                    log(f"Replication failed for {fname}")
                    self.conn.sendall(json.dumps({'status': 'FAIL'}).encode('utf-8'))
            except NotLeader as nl:
                # inform client of leader address for redirect
                log(f"Not leader, redirect to {nl.leader}")
                self.conn.sendall(json.dumps({'status': 'REDIRECT', 'leader': nl.leader}).encode('utf-8'))
        except Exception as e:
            log(f"Handler error: {e}")
        finally:
            try:
                self.conn.close()
            except:
                pass


def start_tcp_server(bind_host: str, bind_port: int, peers):
    log(f"Starting worker TCP server on {bind_host}:{bind_port}")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((bind_host, bind_port))
        s.listen()
        while True:
            conn, addr = s.accept()
            t = WorkerTCPHandler(conn, addr, peers)
            t.start()


class MonitorHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/logs':
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain; charset=utf-8')
            self.end_headers()
            try:
                with open(LOG_FILE, 'r', encoding='utf-8') as f:
                    self.wfile.write(f.read().encode('utf-8'))
            except FileNotFoundError:
                self.wfile.write(b'')
            return
        return http.server.SimpleHTTPRequestHandler.do_GET(self)


def start_monitor(bind_host: str, monitor_port: int):
    log(f"Starting monitor HTTP server on {bind_host}:{monitor_port}")
    os.chdir('.')
    handler = MonitorHandler
    with socketserver.TCPServer((bind_host, monitor_port), handler) as httpd:
        httpd.serve_forever()


def run_java_training(file_path: str):
    """Invoke the Java TrainingModule on the given file path (assumes compiled class available).
    This runs `java -cp java TrainingModule <file>` and logs its output to worker.log."""
    try:
        cmd = ['java', '-cp', 'java', 'TrainingModule', file_path]
        log(f"Starting Java training for {file_path}: {' '.join(cmd)}")
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        for line in p.stdout:
            log(f"TRAIN: {line.strip()}")
        p.wait()
        log(f"Java training finished for {file_path} (exit {p.returncode})")
    except Exception as e:
        log(f"Error running Java training: {e}")


def main():
    parser = argparse.ArgumentParser(description='Worker node (socket + monitor)')
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--port', type=int, default=9000)
    parser.add_argument('--monitor-port', type=int, default=8000)
    parser.add_argument('--peers', nargs='*', help='peers as host:port', default=[])
    parser.add_argument('--raft-port', type=int, default=10000, help='port for raft RPCs')
    parser.add_argument('--run-train', action='store_true', help='Run Java TrainingModule after commit')
    parser.add_argument('--storage-dir', default=None, help='Directory to store files and logs')
    args = parser.parse_args()

    peers = []
    for p in args.peers:
        h, po = p.split(':')
        peers.append((h, int(po)))

    # start monitor in a thread
    mt = threading.Thread(target=start_monitor, args=(args.host, args.monitor_port), daemon=True)
    mt.start()

    # start raft node (use raft port and peers mapped to raft ports)
    global raft_node
    raft_peers = []
    for (h, p) in peers:
        # assume raft peers use port = p + 1000 by default
        # if user configured explicit raft peers, they can set accordingly later
        raft_peers.append((h, args.raft_port + (p - args.port)))
    raft_node = RaftNode(node_id=f"{args.host}:{args.port}", host=args.host, port=args.raft_port + (args.port - args.port), peers=raft_peers, worker_port=args.port)
    raft_node.start()

    # allow worker to run training module after commit if requested
    global RUN_TRAIN
    RUN_TRAIN = args.run_train

    # configure storage dir and log file per instance
    global STORAGE_DIR, LOG_FILE
    if args.storage_dir:
        STORAGE_DIR = args.storage_dir
    else:
        STORAGE_DIR = f'worker_storage_{args.port}'
    # log file inside storage dir
    LOG_FILE = os.path.join(STORAGE_DIR, f'worker_{args.port}.log')

    start_tcp_server(args.host, args.port, peers)


if __name__ == '__main__':
    main()
