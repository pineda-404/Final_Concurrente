"""
Worker Node - Distributed training and prediction server
WITH DISTRIBUTED TRAINING SUPPORT (Opción 1: Paralelismo de Datos)

Handles:
- TRAIN: Receive inputs/outputs, DISTRIBUTE training across nodes
- SUB_TRAIN: Handle subset training from leader
- PREDICT: Load model and make predictions
- LIST_MODELS: List available trained models

Uses RAFT consensus for replication.
"""
import socket
import threading
import os
import argparse
import http.server
import socketserver
import json
import base64
import glob
import re
import math
from datetime import datetime
from src.raft import RaftNode, NotLeader
import subprocess
import tempfile
import uuid

STORAGE_DIR = 'worker_storage'
MODELS_DIR = 'models'
LOG_FILE = 'worker.log'
JAVA_DIR = 'java'


def log(msg: str):
    now = datetime.utcnow().isoformat() 
    line = f"{now} {msg}\n"
    print(line, end='')
    parent = os.path.dirname(LOG_FILE) or '.'
    try:
        os.makedirs(parent, exist_ok=True)
    except Exception:
        pass
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(line)


class WorkerTCPHandler(threading.Thread):
    def __init__(self, conn, addr, storage_dir, models_dir, raft_node, peers_info):
        super().__init__(daemon=True)
        self.conn = conn
        self.addr = addr
        self.storage_dir = storage_dir
        self.models_dir = models_dir
        self.raft_node = raft_node
        self.peers_info = peers_info  # List of (host, port) for worker TCP

    def run(self):
        try:
            # Read message until newline
            data = b''
            while True:
                chunk = self.conn.recv(4096)
                if not chunk:
                    break
                data += chunk
                if b'\n' in chunk or len(data) > 1024 * 1024:
                    break

            if not data:
                return

            # Try to parse as JSON message
            try:
                msg = json.loads(data.decode('utf-8').strip())
                msg_type = msg.get('type', 'PUT')
            except json.JSONDecodeError:
                # Legacy format: assume PUT with binary data following header
                self._handle_legacy_put(data)
                return

            # Route to appropriate handler
            if msg_type == 'TRAIN':
                self._handle_train(msg)
            elif msg_type == 'SUB_TRAIN':
                self._handle_sub_train(msg)
            elif msg_type == 'PREDICT':
                self._handle_predict(msg)
            elif msg_type == 'LIST_MODELS':
                self._handle_list_models()
            else:
                self._send_response({'status': 'ERROR', 'message': f'Unknown type: {msg_type}'})

        except Exception as e:
            log(f"Handler error: {e}")
            try:
                self._send_response({'status': 'ERROR', 'message': str(e)})
            except:
                pass
        finally:
            try:
                self.conn.close()
            except:
                pass

    def _send_response(self, obj):
        self.conn.sendall((json.dumps(obj) + '\n').encode('utf-8'))

    def _handle_train(self, msg):
        """Handle TRAIN request: DISTRIBUTED training with data parallelism."""
        inputs = msg.get('inputs', [])
        outputs = msg.get('outputs', [])

        if not inputs or not outputs:
            self._send_response({'status': 'ERROR', 'message': 'Missing inputs or outputs'})
            return

        if len(inputs) != len(outputs):
            self._send_response({'status': 'ERROR', 'message': 'Inputs/outputs length mismatch'})
            return

        log(f"TRAIN request from {self.addr}: {len(inputs)} samples")

        try:
            # Check if we are leader
            if not self.raft_node.is_leader():
                leader = self.raft_node.leader
                if leader:
                    self._send_response({'status': 'REDIRECT', 'leader': leader})
                    return
                self._send_response({'status': 'ERROR', 'message': 'No leader available'})
                return

            # DISTRIBUTED TRAINING
            num_nodes = len(self.peers_info) + 1  # peers + self

            if num_nodes > 1:
                log(f"Starting DISTRIBUTED training across {num_nodes} nodes")
                model_id = self._distributed_train(inputs, outputs, num_nodes)
            else:
                log(f"Starting SINGLE-NODE training (no peers available)")
                model_id = self._single_node_train(inputs, outputs)

            if not model_id:
                self._send_response({'status': 'ERROR', 'message': 'Training failed'})
                return

            # ✅ Responder al cliente inmediatamente (evita timeout)
            self._send_response({'status': 'OK', 'model_id': model_id})

            # ✅ Replicar en RAFT en background (no bloquea al cliente)
            entry = {
                'action': 'MODEL_TRAINED',
                'model_id': model_id,
                'samples': len(inputs),
                'distributed': num_nodes > 1
            }
            threading.Thread(
                target=self._replicate_entry_safe,
                args=(entry,),
                daemon=True
            ).start()

        except NotLeader as nl:
            log(f"Not leader, redirect to {nl.leader}")
            self._send_response({'status': 'REDIRECT', 'leader': nl.leader})
        except Exception as e:
            log(f"TRAIN handler error: {e}")
            self._send_response({'status': 'ERROR', 'message': str(e)})



    def _replicate_entry_safe(self, entry):
        try:
            log(f"Replicating entry via RAFT: {entry}")
            success = self.raft_node.replicate(entry)
            log(f"RAFT replicate result: {success}")
        except NotLeader as nl:
            log(f"Not leader during async replication, leader={nl.leader}")
        except Exception as e:
            log(f"Async replication error: {e}")

    def _distributed_train(self, inputs, outputs, num_nodes):
        """
        🆕 DISTRIBUTED TRAINING: Divide data among nodes
        
        Strategy:
        1. Split data into num_nodes chunks
        2. Send chunks to peers for parallel training
        3. Each node trains its subset
        4. Aggregate results (here: train final model with all data)
        """
        # Split data into chunks
        chunks = self._split_data(inputs, outputs, num_nodes)
        
        # My chunk (leader trains first chunk)
        my_inputs, my_outputs = chunks[0]
        log(f"Leader training chunk 0: {len(my_inputs)} samples")
        
        my_model_path = self._train_chunk(my_inputs, my_outputs, chunk_id=0)
        
        if not my_model_path:
            log("Leader chunk training failed")
            return None
        
        partial_models = [my_model_path]
        
        # Send chunks to peers for parallel training
        threads = []
        results_lock = threading.Lock()
        
        for i, peer_addr in enumerate(self.peers_info):
            if i + 1 >= len(chunks):
                break
            
            peer_inputs, peer_outputs = chunks[i + 1]
            
            t = threading.Thread(
                target=self._send_train_to_peer,
                args=(peer_addr, peer_inputs, peer_outputs, i+1, partial_models, results_lock)
            )
            threads.append(t)
            t.start()
        
        # Wait for all peers to finish training
        for t in threads:
            t.join(timeout=180)  # 3 minutes max per peer
        
        log(f"Distributed training complete: {len(partial_models)} partial models")
        
        # Aggregate: Train final model with ALL data combined
        # (This is simpler than averaging weights)
        final_model_id = self._aggregate_train(inputs, outputs)
        
        return final_model_id

    def _split_data(self, inputs, outputs, num_chunks):
        """
        Split data into chunks for distributed training.
        Uses modulo to distribute remainder samples evenly.
        
        Examples:
          4 samples, 3 chunks -> [2, 1, 1]
          10 samples, 3 chunks -> [4, 3, 3]
          3 samples, 3 chunks -> [1, 1, 1]
        """
        total = len(inputs)
        chunk_size = total // num_chunks
        remainder = total % num_chunks  # Samples sobrantes
        
        chunks = []
        start = 0
        
        for i in range(num_chunks):
            # Los primeros 'remainder' chunks reciben 1 sample extra
            current_size = chunk_size + (1 if i < remainder else 0)
            end = start + current_size
            
            # Asegurar que no excedemos el total
            end = min(end, total)
            
            chunk_inputs = inputs[start:end]
            chunk_outputs = outputs[start:end]
            
            chunks.append((chunk_inputs, chunk_outputs))
            
            log(f"Chunk {i}: samples {start}-{end-1} ({len(chunk_inputs)} total)")
            
            start = end  # Mover el inicio al final del chunk actual
        
        return chunks

    def _train_chunk(self, inputs, outputs, chunk_id):
        """Train a model with a subset of data."""
        os.makedirs(self.models_dir, exist_ok=True)
        
        train_id = f"{uuid.uuid4().hex[:8]}_chunk{chunk_id}"
        inputs_file = os.path.join(self.models_dir, f'inputs_{train_id}.csv')
        outputs_file = os.path.join(self.models_dir, f'outputs_{train_id}.csv')
        model_path = os.path.join(self.models_dir, f'model_{train_id}.bin')

        # Write CSV files
        try:
            with open(inputs_file, 'w') as f:
                for row in inputs:
                    f.write(','.join(str(x) for x in row) + '\n')

            with open(outputs_file, 'w') as f:
                for row in outputs:
                    if isinstance(row, list):
                        f.write(','.join(str(x) for x in row) + '\n')
                    else:
                        f.write(str(row) + '\n')

            log(f"Chunk {chunk_id}: Training data saved: {inputs_file}, {outputs_file}")

            # Run Java training
            model_id = self._run_java_training(inputs_file, outputs_file, model_path)

            # Cleanup temp files
            try:
                os.remove(inputs_file)
                os.remove(outputs_file)
            except:
                pass

            if model_id:
                log(f"Chunk {chunk_id}: Training successful, model_id={model_id}")
                return model_path
            else:
                log(f"Chunk {chunk_id}: Training failed")
                return None

        except Exception as e:
            log(f"Chunk {chunk_id}: Training error: {e}")
            return None

    def _send_train_to_peer(self, peer_addr, inputs, outputs, chunk_id, results_list, lock):
        """Send SUB_TRAIN request to a peer node."""
        host, port = peer_addr
        
        msg = {
            'type': 'SUB_TRAIN',
            'inputs': inputs,
            'outputs': outputs,
            'chunk_id': chunk_id
        }
        
        try:
            log(f"Sending SUB_TRAIN to peer {host}:{port}, chunk {chunk_id}, {len(inputs)} samples")
            
            with socket.create_connection((host, port), timeout=180) as s:
                s.sendall((json.dumps(msg) + '\n').encode('utf-8'))
                
                # Wait for response
                response_data = b''
                while True:
                    chunk = s.recv(4096)
                    if not chunk:
                        break
                    response_data += chunk
                    if b'\n' in chunk:
                        break
                
                resp = json.loads(response_data.decode('utf-8').strip())
                
                if resp.get('status') == 'OK':
                    model_path = resp.get('model_path')
                    log(f"Peer {host}:{port} completed chunk {chunk_id}, model: {model_path}")
                    
                    with lock:
                        results_list.append(model_path)
                else:
                    log(f"Peer {host}:{port} chunk {chunk_id} failed: {resp}")
                    
        except Exception as e:
            log(f"Error sending SUB_TRAIN to peer {host}:{port}: {e}")

    def _aggregate_train(self, inputs, outputs):
        """
        Aggregate distributed training results.
        
        Simple approach: Train a final model with ALL data.
        (More sophisticated: average weights from partial models)
        """
        log(f"Aggregating: Training final model with all {len(inputs)} samples")
        return self._single_node_train(inputs, outputs)

    def _single_node_train(self, inputs, outputs):
        """Train a model on a single node (original behavior)."""
        os.makedirs(self.models_dir, exist_ok=True)
        
        train_id = str(uuid.uuid4())[:8]
        inputs_file = os.path.join(self.models_dir, f'inputs_{train_id}.csv')
        outputs_file = os.path.join(self.models_dir, f'outputs_{train_id}.csv')
        model_path = os.path.join(self.models_dir, f'model_{train_id}.bin')

        # Write CSV files
        with open(inputs_file, 'w') as f:
            for row in inputs:
                f.write(','.join(str(x) for x in row) + '\n')

        with open(outputs_file, 'w') as f:
            for row in outputs:
                if isinstance(row, list):
                    f.write(','.join(str(x) for x in row) + '\n')
                else:
                    f.write(str(row) + '\n')

        log(f"Training data saved: {inputs_file}, {outputs_file}")

        # Run Java training
        model_id = self._run_java_training(inputs_file, outputs_file, model_path)

        # Cleanup temp files
        try:
            os.remove(inputs_file)
            os.remove(outputs_file)
        except:
            pass

        return model_id

    def _handle_sub_train(self, msg):
        """
        🆕 Handle SUB_TRAIN request: Train a subset of data (from leader).
        
        This is called by peers when leader distributes training.
        """
        inputs = msg.get('inputs', [])
        outputs = msg.get('outputs', [])
        chunk_id = msg.get('chunk_id', 0)

        log(f"SUB_TRAIN request from {self.addr}: chunk {chunk_id}, {len(inputs)} samples")

        if not inputs or not outputs:
            self._send_response({'status': 'ERROR', 'message': 'Missing inputs or outputs'})
            return

        # Train this chunk
        model_path = self._train_chunk(inputs, outputs, chunk_id)

        if model_path:
            self._send_response({
                'status': 'OK',
                'model_path': model_path,
                'chunk_id': chunk_id
            })
        else:
            self._send_response({
                'status': 'ERROR',
                'message': f'Chunk {chunk_id} training failed'
            })

    def _run_java_training(self, inputs_file, outputs_file, model_path):
        """Run Java TrainingModule and return model ID."""
        try:
            cmd = [
                'java', '-cp', JAVA_DIR, 'TrainingModule',
                'train', inputs_file, outputs_file, '1000', model_path
            ]
            log(f"Running: {' '.join(cmd)}")

            p = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True
            )

            model_id = None
            for line in p.stdout:
                line = line.strip()
                log(f"JAVA: {line}")
                if line.startswith('MODEL_ID:'):
                    model_id = line.split(':', 1)[1]

            p.wait()
            log(f"Java training finished (exit {p.returncode})")

            return model_id if p.returncode == 0 else None

        except Exception as e:
            log(f"Java training error: {e}")
            return None

    def _handle_predict(self, msg):
        """Handle PREDICT request: load model and make prediction."""
        model_id = msg.get('model_id')
        input_data = msg.get('input', [])

        if not model_id:
            self._send_response({'status': 'ERROR', 'message': 'Missing model_id'})
            return

        log(f"PREDICT request from {self.addr}: model={model_id}, input={input_data}")

        # Find model file
        model_path = self._find_model(model_id)
        if not model_path:
            self._send_response({'status': 'ERROR', 'message': f'Model not found: {model_id}'})
            return

        # Run Java prediction
        try:
            input_str = ','.join(str(x) for x in input_data)
            cmd = ['java', '-cp', JAVA_DIR, 'TrainingModule', 'predict', model_path, input_str]
            log(f"Running: {' '.join(cmd)}")

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

            output = None
            for line in result.stdout.split('\n'):
                if line.startswith('PREDICTION:'):
                    pred_str = line.split(':', 1)[1]
                    output = [float(x) for x in pred_str.split(',')]
                    break

            if output is not None:
                self._send_response({'status': 'OK', 'output': output})
            else:
                log(f"Prediction failed: {result.stdout} {result.stderr}")
                self._send_response({'status': 'ERROR', 'message': 'Prediction failed'})

        except Exception as e:
            log(f"Predict error: {e}")
            self._send_response({'status': 'ERROR', 'message': str(e)})

    def _find_model(self, model_id):
        """Find model file by ID (partial match supported)."""
        # Look for exact match first
        for pattern in [
            os.path.join(self.models_dir, f'model_{model_id}.bin'),
            os.path.join(self.models_dir, f'*{model_id}*.bin'),
        ]:
            matches = glob.glob(pattern)
            if matches:
                return matches[0]
        return None

    def _handle_list_models(self):
        """Handle LIST_MODELS request."""
        log(f"LIST_MODELS request from {self.addr}")
        
        models = []
        pattern = os.path.join(self.models_dir, '*.bin')
        for path in glob.glob(pattern):
            fname = os.path.basename(path)
            # Extract model ID from filename
            match = re.search(r'model_(.+)\.bin', fname)
            if match:
                models.append(match.group(1))
            else:
                models.append(fname)

        self._send_response({'status': 'OK', 'models': models})

    def _handle_legacy_put(self, data):
        """Handle legacy PUT format for backward compatibility."""
        try:
            header = data.decode('utf-8', errors='ignore')
            header_obj = json.loads(header)
            fname = header_obj.get('filename')
            size = header_obj.get('size', 0)
            log(f"Legacy PUT {fname} size={size} from {self.addr}")

            received = b''
            while len(received) < size:
                chunk = self.conn.recv(min(4096, size - len(received)))
                if not chunk:
                    break
                received += chunk

            entry = {'filename': fname, 'data_b64': base64.b64encode(received).decode('ascii')}
            try:
                success = raft_node.replicate(entry)
                if success:
                    os.makedirs(self.storage_dir, exist_ok=True)
                    path = os.path.join(self.storage_dir, fname)
                    with open(path, 'wb') as f:
                        f.write(received)
                    log(f"Committed and stored {path}")
                    self._send_response({'status': 'OK'})
                else:
                    self._send_response({'status': 'FAIL'})
            except NotLeader as nl:
                self._send_response({'status': 'REDIRECT', 'leader': nl.leader})
        except Exception as e:
            log(f"Legacy PUT error: {e}")


def start_tcp_server(bind_host: str, bind_port: int, storage_dir: str, models_dir: str, raft_node, peers_info):
    log(f"Starting worker TCP server on {bind_host}:{bind_port}")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((bind_host, bind_port))
        s.listen()
        while True:
            conn, addr = s.accept()
            t = WorkerTCPHandler(conn, addr, storage_dir, models_dir, raft_node, peers_info)
            t.start()


class MonitorHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # Suppress default logging

    def do_GET(self):
        if self.path == '/logs':
            self._serve_logs()
        elif self.path == '/status':
            self._serve_status()
        elif self.path == '/models':
            self._serve_models()
        elif self.path == '/':
            self._serve_dashboard()
        else:
            self.send_error(404)

    def _serve_logs(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/plain; charset=utf-8')
        self.end_headers()
        try:
            with open(LOG_FILE, 'r', encoding='utf-8') as f:
                self.wfile.write(f.read().encode('utf-8'))
        except FileNotFoundError:
            self.wfile.write(b'No logs yet')

    def _serve_status(self):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        status = {
            'state': raft_node.state,
            'term': raft_node.current_term,
            'leader': raft_node.leader,
            'log_length': len(raft_node.log),
            'commit_index': raft_node.commit_index
        }
        self.wfile.write(json.dumps(status, indent=2).encode('utf-8'))

    def _serve_models(self):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        models = []
        pattern = os.path.join(MODELS_DIR, '*.bin')
        for path in glob.glob(pattern):
            models.append(os.path.basename(path))
        self.wfile.write(json.dumps({'models': models}, indent=2).encode('utf-8'))

    def _serve_dashboard(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        html = f'''<!DOCTYPE html>
<html>
<head>
    <title>Worker Monitor (DISTRIBUTED)</title>
    <style>
        body {{ font-family: monospace; background: #1a1a2e; color: #eee; padding: 20px; }}
        h1 {{ color: #00d4ff; }}
        .card {{ background: #16213e; padding: 15px; margin: 10px 0; border-radius: 8px; }}
        .label {{ color: #888; }}
        .leader {{ color: #00ff88; }}
        .follower {{ color: #ffaa00; }}
        .candidate {{ color: #ff6b6b; }}
        .badge {{ background: #ff4444; color: white; padding: 2px 8px; border-radius: 4px; }}
        pre {{ background: #0f0f23; padding: 10px; overflow-x: auto; max-height: 400px; }}
    </style>
</head>
<body>
    <h1>🖥️ Worker Monitor <span class="badge">DISTRIBUTED</span></h1>
    <div class="card">
        <div class="label">RAFT Status</div>
        <div id="status">Loading...</div>
    </div>
    <div class="card">
        <div class="label">Trained Models</div>
        <div id="models">Loading...</div>
    </div>
    <div class="card">
        <div class="label">Recent Logs</div>
        <pre id="logs">Loading...</pre>
    </div>
    <script>
        async function refresh() {{
            try {{
                const status = await fetch('/status').then(r => r.json());
                const stateClass = status.state;
                document.getElementById('status').innerHTML = `
                    <span class="${{stateClass}}">${{status.state.toUpperCase()}}</span> | 
                    Term: ${{status.term}} | Leader: ${{JSON.stringify(status.leader)}} |
                    Log: ${{status.log_length}} entries | Commit: ${{status.commit_index}}
                `;
            }} catch(e) {{ document.getElementById('status').textContent = 'Error'; }}

            try {{
                const models = await fetch('/models').then(r => r.json());
                document.getElementById('models').innerHTML = models.models.length 
                    ? models.models.map(m => `<div>📦 ${{m}}</div>`).join('')
                    : '<em>No models yet</em>';
            }} catch(e) {{ document.getElementById('models').textContent = 'Error'; }}

            try {{
                const logs = await fetch('/logs').then(r => r.text());
                const lines = logs.split('\\n').slice(-50).join('\\n');
                document.getElementById('logs').textContent = lines || 'No logs';
            }} catch(e) {{ document.getElementById('logs').textContent = 'Error'; }}
        }}
        refresh();
        setInterval(refresh, 3000);
    </script>
</body>
</html>'''
        self.wfile.write(html.encode('utf-8'))


def start_monitor(bind_host: str, monitor_port: int):
    log(f"Starting monitor HTTP server on {bind_host}:{monitor_port}")
    handler = MonitorHandler
    with socketserver.TCPServer((bind_host, monitor_port), handler) as httpd:
        httpd.serve_forever()


def main():
    parser = argparse.ArgumentParser(description='Worker node with DISTRIBUTED training')
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--port', type=int, default=9000)
    parser.add_argument('--monitor-port', type=int, default=8000)
    parser.add_argument('--peers', nargs='*', help='peers as host:port', default=[])
    parser.add_argument('--raft-port', type=int, default=10000, help='port for raft RPCs')
    parser.add_argument('--storage-dir', default=None, help='Directory to store files')
    parser.add_argument('--java-dir', default='java', help='Directory with Java classes')
    args = parser.parse_args()

    peers = []
    for p in args.peers:
        h, po = p.split(':')
        peers.append((h, int(po)))

    # Configure directories
    global STORAGE_DIR, MODELS_DIR, LOG_FILE, JAVA_DIR
    if args.storage_dir:
        STORAGE_DIR = args.storage_dir
    else:
        STORAGE_DIR = f'node{args.port - 9000}_storage'
    MODELS_DIR = os.path.join(STORAGE_DIR, 'models')
    LOG_FILE = os.path.join(STORAGE_DIR, f'worker.log')
    JAVA_DIR = args.java_dir

    os.makedirs(STORAGE_DIR, exist_ok=True)
    os.makedirs(MODELS_DIR, exist_ok=True)

    # Start RAFT node
    global raft_node
    raft_peers = []
    for (h, p) in peers:
        raft_peers.append((h, args.raft_port + (p - args.port)))
    raft_node = RaftNode(
        node_id=f"{args.host}:{args.port}",
        host=args.host,
        port=args.raft_port,
        peers=raft_peers,
        worker_port=args.port
    )
    raft_node.start()

    log(f"Worker started with DISTRIBUTED TRAINING support")
    log(f"Host: {args.host}, Port: {args.port}, RAFT Port: {args.raft_port}")
    log(f"Storage: {STORAGE_DIR}, Models: {MODELS_DIR}")
    log(f"Worker Peers: {peers}, RAFT Peers: {raft_peers}")

    # Start monitor
    mt = threading.Thread(target=start_monitor, args=(args.host, args.monitor_port), daemon=True)
    mt.start()

    # Start TCP server (blocking) - pass peers_info for distribution
    start_tcp_server(args.host, args.port, STORAGE_DIR, MODELS_DIR, raft_node, peers)


if __name__ == '__main__':
    main()
