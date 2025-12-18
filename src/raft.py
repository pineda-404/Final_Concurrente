import threading
import socket
import json
import time
import random
import base64
import os
from typing import List, Tuple

# Minimal Raft-like implementation for log replication (demonstration only).
# Uses TCP JSON messages terminated by newline for RPCs.

REQUEST_VOTE = 'REQUEST_VOTE'
VOTE_RESPONSE = 'VOTE_RESPONSE'
APPEND_ENTRIES = 'APPEND_ENTRIES'
APPEND_RESPONSE = 'APPEND_RESPONSE'


class RaftNode:
    def __init__(self, node_id: str, host: str, port: int, peers: List[Tuple[str,int]], worker_port: int = None, apply_callback=None, persistence_path: str = None):
        self.id = node_id
        self.host = host
        self.port = port
        self.peers = peers[:]  # list of (host, port)
        # port where the worker/file-server listens (used for leader redirect)
        self.worker_port = worker_port

        # persistence path for state
        self.persistence_path = persistence_path

        # persistent state (will be loaded from disk if available)
        self.current_term = 0
        self.voted_for = None
        self.log = []  # list of {'term':t,'command':...}

        # volatile state
        self.commit_index = -1
        self.last_applied = -1

        # leader state (only for leader)
        self.next_index = {}
        self.match_index = {}

        self.state = 'follower'  # follower, candidate, leader
        self.leader = None  # (host, port)

        self.lock = threading.Lock()
        self.election_timer = None
        self.heartbeat_interval = 1.0
        self.stopped = False

        # callback to apply committed log entries to state machine
        self.apply_callback = apply_callback

        # server thread
        self.server_thread = threading.Thread(target=self._serve, daemon=True)

        # for waiting replication
        self._replicate_cv = threading.Condition()

        # Load persisted state if available
        self._load_state()

    def _get_state_file(self):
        """Get path to state file."""
        if self.persistence_path:
            return os.path.join(self.persistence_path, 'raft_state.json')
        return None

    def _save_state(self):
        """Save persistent state (term, votedFor, log) to disk."""
        state_file = self._get_state_file()
        if not state_file:
            return
        try:
            os.makedirs(os.path.dirname(state_file), exist_ok=True)
            state = {
                'current_term': self.current_term,
                'voted_for': self.voted_for,
                'log': self.log
            }
            # Write atomically using temp file
            temp_file = state_file + '.tmp'
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(state, f)
            os.replace(temp_file, state_file)
        except Exception as e:
            print(f"RAFT: Error saving state: {e}")

    def _load_state(self):
        """Load persistent state from disk if available."""
        state_file = self._get_state_file()
        if not state_file or not os.path.exists(state_file):
            return
        try:
            with open(state_file, 'r', encoding='utf-8') as f:
                state = json.load(f)
            self.current_term = state.get('current_term', 0)
            self.voted_for = state.get('voted_for', None)
            self.log = state.get('log', [])
            print(f"RAFT: Loaded state from disk (term={self.current_term}, log_len={len(self.log)})")
        except Exception as e:
            print(f"RAFT: Error loading state: {e}")

    def start(self):
        self.server_thread.start()
        self.reset_election_timeout()


    def stop(self):
        self.stopped = True

    def reset_election_timeout(self):
        if self.election_timer:
            self.election_timer.cancel()
        timeout = random.uniform(3.0, 5.0)
        self.election_timer = threading.Timer(timeout, self._start_election)
        self.election_timer.daemon = True
        self.election_timer.start()

    def _start_election(self):
        with self.lock:
            self.state = 'candidate'
            self.current_term += 1
            self.voted_for = self.id
            self._save_state()  # Persist term and vote
            term = self.current_term
            votes = 1

        # send RequestVote to peers
        def ask(peer):
            nonlocal votes
            try:
                msg = {'type': REQUEST_VOTE, 'term': term, 'candidate_id': self.id}
                resp = self._send_rpc(peer, msg)
                if resp and resp.get('vote_granted'):
                    with self.lock:
                        votes += 1
            except Exception:
                pass

        threads = []
        for p in self.peers:
            t = threading.Thread(target=ask, args=(p,))
            t.start()
            threads.append(t)

        for t in threads:
            t.join(timeout=2.0)

        with self.lock:
            if self.state != 'candidate':
                return
            # total nodes = peers + self
            total = len(self.peers) + 1
            majority = total // 2 + 1
            if votes >= majority:
                self.state = 'leader'
                # leader should advertise worker port so clients can redirect
                leader_worker_port = self.worker_port if self.worker_port is not None else self.port
                self.leader = (self.host, leader_worker_port)
                # initialize leader state
                for p in self.peers:
                    # next_index initialized to lastLogIndex + 1
                    self.next_index[p] = len(self.log)
                    self.match_index[p] = -1
                # start heartbeat sender
                threading.Thread(target=self._leader_loop, daemon=True).start()
            else:
                # remain follower/candidate and reset timer
                self.reset_election_timeout()

    def _leader_loop(self):
        while not self.stopped and self.state == 'leader':
            self.send_heartbeats()
            time.sleep(self.heartbeat_interval)

    def send_heartbeats(self):
        for p in self.peers:
            self._send_append_entries(p, [])

    def _serve(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((self.host, self.port))
            s.listen()
            while not self.stopped:
                try:
                    conn, addr = s.accept()
                    threading.Thread(target=self._handle_conn, args=(conn,), daemon=True).start()
                except Exception:
                    pass

    def _handle_conn(self, conn):
        try:
            data = b''
            # read until newline
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                data += chunk
                if b"\n" in chunk:
                    break
            try:
                msg = json.loads(data.decode('utf-8').strip())
            except Exception:
                return
            typ = msg.get('type')
            if typ == REQUEST_VOTE:
                resp = self._handle_request_vote(msg)
                conn.sendall((json.dumps(resp) + '\n').encode('utf-8'))
            elif typ == APPEND_ENTRIES:
                resp = self._handle_append_entries(msg)
                conn.sendall((json.dumps(resp) + '\n').encode('utf-8'))
            else:
                conn.sendall((json.dumps({'error': 'unknown'}) + '\n').encode('utf-8'))
        finally:
            try:
                conn.close()
            except:
                pass

    def _handle_request_vote(self, msg):
        term = msg.get('term', 0)
        candidate = msg.get('candidate_id')
        with self.lock:
            state_changed = False
            if term > self.current_term:
                self.current_term = term
                self.voted_for = None
                self.state = 'follower'
                state_changed = True
            vote_granted = False
            if (self.voted_for is None or self.voted_for == candidate) and term >= self.current_term:
                self.voted_for = candidate
                vote_granted = True
                state_changed = True
            if state_changed:
                self._save_state()  # Persist term and vote
            self.reset_election_timeout()
        return {'type': VOTE_RESPONSE, 'term': self.current_term, 'vote_granted': vote_granted}

    def _handle_append_entries(self, msg):
        term = msg.get('term', 0)
        leader_id = msg.get('leader_id')
        entries = msg.get('entries', [])
        prev_log_index = msg.get('prev_log_index', -1)
        prev_log_term = msg.get('prev_log_term', 0)
        leader_commit = msg.get('leader_commit', -1)
        with self.lock:
            state_changed = False
            if term >= self.current_term:
                if term > self.current_term:
                    self.current_term = term
                    state_changed = True
                self.state = 'follower'
                # interpret leader id (may contain worker port)
                if isinstance(leader_id, list) and len(leader_id) == 2:
                    self.leader = (leader_id[0], leader_id[1])
                else:
                    self.leader = tuple(leader_id) if isinstance(leader_id, (list, tuple)) else leader_id

                # consistency check: prev_log_index/term must match local log
                if prev_log_index >= 0:
                    if prev_log_index >= len(self.log):
                        return {'type': APPEND_RESPONSE, 'term': self.current_term, 'success': False}
                    if self.log[prev_log_index]['term'] != prev_log_term:
                        # conflict
                        return {'type': APPEND_RESPONSE, 'term': self.current_term, 'success': False}

                # remove conflicting entries after prev_log_index
                insert_at = prev_log_index + 1
                if insert_at < len(self.log):
                    self.log = self.log[:insert_at]
                    state_changed = True

                # append new entries
                if entries:
                    for e in entries:
                        self.log.append(e)
                    state_changed = True

                # update commit index
                if leader_commit > self.commit_index:
                    self.commit_index = min(leader_commit, len(self.log) - 1)

                # save state if changed
                if state_changed:
                    self._save_state()

                # apply committed entries to state machine if callback provided
                try:
                    self._apply_committed()
                except Exception:
                    pass

                self.reset_election_timeout()
                return {'type': APPEND_RESPONSE, 'term': self.current_term, 'success': True, 'last_index': len(self.log) - 1}
            else:
                return {'type': APPEND_RESPONSE, 'term': self.current_term, 'success': False}


    def _send_rpc(self, peer, msg, timeout=2.0):
        host, port = peer
        try:
            with socket.create_connection((host, port), timeout=timeout) as s:
                s.sendall((json.dumps(msg) + '\n').encode('utf-8'))
                data = b''
                while True:
                    chunk = s.recv(4096)
                    if not chunk:
                        break
                    data += chunk
                    if b"\n" in chunk:
                        break
                return json.loads(data.decode('utf-8').strip())
        except Exception:
            return None

    def _send_append_entries(self, peer, entries):
        # send with prevLogIndex/prevLogTerm based on next_index
        with self.lock:
            next_idx = self.next_index.get(peer, len(self.log))
            prev_index = next_idx - 1
            prev_term = self.log[prev_index]['term'] if prev_index >= 0 and prev_index < len(self.log) else 0
            leader_commit = self.commit_index

        msg = {'type': APPEND_ENTRIES, 'term': self.current_term, 'leader_id': [self.host, self.worker_port if self.worker_port is not None else self.port],
               'entries': entries, 'prev_log_index': prev_index, 'prev_log_term': prev_term, 'leader_commit': leader_commit}
        return self._send_rpc(peer, msg)

    def is_leader(self):
        with self.lock:
            return self.state == 'leader'

    def replicate(self, command: dict, timeout=5.0):
        """Append command to log and replicate; return True if committed or raise redirect if not leader."""
        with self.lock:
            if not self.is_leader():
                # return leader info if known
                raise NotLeader(self.leader)
            entry = {'term': self.current_term, 'command': command}
            # append locally (optimistic)
            self.log.append(entry)
            self._save_state()  # Persist log change
            my_index = len(self.log) - 1

            # ensure next_index exists for peers
            for p in self.peers:
                if p not in self.next_index:
                    self.next_index[p] = len(self.log)


        # send append_entries to peers and update next_index/match_index
        acks = 1
        lock = self.lock

        def send_and_update(peer):
            nonlocal acks
            # send entries starting at next_index[peer]
            while True:
                with lock:
                    next_idx = self.next_index.get(peer, len(self.log))
                    # entries to send
                    entries_to_send = self.log[next_idx:]
                resp = self._send_append_entries(peer, entries_to_send)
                if resp and resp.get('success'):
                    with lock:
                        # set match_index and next_index
                        self.match_index[peer] = my_index
                        self.next_index[peer] = my_index + 1
                        acks += 1
                    break
                else:
                    # decrement next_index and retry (backoff)
                    with lock:
                        cur = self.next_index.get(peer, len(self.log))
                        if cur > 0:
                            self.next_index[peer] = cur - 1
                        else:
                            break

        threads = []
        for p in self.peers:
            t = threading.Thread(target=send_and_update, args=(p,))
            t.start()
            threads.append(t)

        start = time.time()
        for t in threads:
            remain = timeout - (time.time() - start)
            if remain > 0:
                t.join(remain)

        # check if committed by majority
        with self.lock:
            total = len(self.peers) + 1
            majority = total // 2 + 1
            # count nodes with match_index >= index
            count = 1  # leader itself
            for p in self.peers:
                if self.match_index.get(p, -1) >= my_index:
                    count += 1
            if count >= majority and self.log[my_index]['term'] == self.current_term:
                self.commit_index = my_index
                # apply committed entries locally
                try:
                    self._apply_committed()
                except Exception:
                    pass
                return True
            return False

    def _apply_committed(self):
        """Apply entries from last_applied+1..commit_index using apply_callback."""
        if not self.apply_callback:
            self.last_applied = self.commit_index
            return

        # apply in-order
        while self.last_applied < self.commit_index:
            self.last_applied += 1
            entry = None
            try:
                entry = self.log[self.last_applied]
            except Exception:
                continue
            try:
                cmd = entry.get('command') if isinstance(entry, dict) else None
                if cmd is not None:
                    # call outside lock to avoid deadlocks
                    try:
                        self.apply_callback(cmd)
                    except Exception:
                        pass
            except Exception:
                pass


class NotLeader(Exception):
    def __init__(self, leader):
        super().__init__('Not leader')
        self.leader = leader
