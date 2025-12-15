import socket
import json
import argparse
import os
import time


def send_file(host: str, port: int, filepath: str):
    fname = os.path.basename(filepath)
    with open(filepath, 'rb') as f:
        data = f.read()

    header = json.dumps({'filename': fname, 'size': len(data)})
    attempt = 0
    cur_host, cur_port = host, port
    while attempt < 3:
        try:
            with socket.create_connection((cur_host, cur_port), timeout=10) as s:
                s.sendall(header.encode('utf-8'))
                s.sendall(data)
                resp = s.recv(4096)
                try:
                    obj = json.loads(resp.decode('utf-8'))
                except Exception:
                    print('Response (raw):', resp)
                    return
                if obj.get('status') == 'OK':
                    print('Uploaded to', cur_host, cur_port)
                    return
                if obj.get('status') == 'REDIRECT':
                    leader = obj.get('leader')
                    if leader:
                        # leader is [host, port]
                        cur_host, cur_port = leader[0], int(leader[1])
                        print('Redirecting to leader', cur_host, cur_port)
                        attempt += 1
                        continue
                print('Server response:', obj)
                return
        except Exception as e:
            print('Send error', e)
            attempt += 1
            time.sleep(0.5)
    print('Failed to upload after retries')


def main():
    parser = argparse.ArgumentParser(description='Client to upload files to a worker')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=9000)
    parser.add_argument('action', choices=['put'], help='action')
    parser.add_argument('path', help='file path to upload')
    args = parser.parse_args()

    if args.action == 'put':
        send_file(args.host, args.port, args.path)


if __name__ == '__main__':
    main()
