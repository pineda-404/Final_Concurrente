# Ejecutar Nodos
```
python -m src.worker --host 0.0.0.0 --port 9000 --monitor-port 8000 --raft-port 10000 --peers 127.0.0.1:9001 127.0.0.1:9002 --storage-dir node0_storage
python -m src.worker --host 0.0.0.0 --port 9001 --monitor-port 8001 --raft-port 10001 --peers 127.0.0.1:9000 127.0.0.1:9002 --storage-dir node1_storage
python -m src.worker --host 0.0.0.0 --port 9002 --monitor-port 8002 --raft-port 10002 --peers 127.0.0.1:9000 127.0.0.1:9001 --storage-dir node2_storage
```
# Entrenar cliente
```
python -m src.train_client --host 127.0.0.1 --port 9000 train-inline "0,0;0,1;1,0;1,1" "0;1;1;0"
```