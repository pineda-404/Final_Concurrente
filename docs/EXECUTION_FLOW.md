# EXECUTION FLOW — Flujo de ejecución

Este documento describe cómo ejecutar el proyecto desde cero con un cluster heterogéneo (Python + Go + Kotlin).

---

## 1) Requisitos previos

- **Python 3.8+** (virtualenv recomendado)
- **Java JDK 8+** (javac/java) para el módulo de entrenamiento
- **Go 1.18+** para el worker Go
- **Kotlin** (kotlinc) para el worker Kotlin

---

## 2) Compilar componentes (única vez)

```bash
# Desde la raíz del repositorio

# 1. Compilar Java (módulo de entrenamiento)
cd java && javac *.java && cd ..

# 2. Compilar Go worker
cd go && go build -o worker . && cd ..

# 3. Compilar Kotlin worker
cd kotlin && kotlinc src/main/kotlin/*.kt -include-runtime -d worker.jar && cd ..
```

---

## 3) Estructura del proyecto

```
├── src/                    # Worker Python + Clientes
│   ├── worker.py           # Worker principal
│   ├── raft.py             # RAFT consensus
│   ├── train_client.py     # Cliente para entrenar
│   └── test_client.py      # Cliente para predecir/listar
├── java/                   # Módulo de entrenamiento (MLP)
│   ├── NeuralNetwork.java
│   └── TrainingModule.java
├── go/                     # Worker Go
│   ├── main.go
│   ├── raft.go
│   └── worker              # Binario compilado
└── kotlin/                 # Worker Kotlin
    ├── src/main/kotlin/
    └── worker.jar          # JAR compilado
```

---

## 4) Ejecutar cluster heterogéneo (3 nodos, 3 lenguajes)

Abrir 3 terminales y ejecutar:

### Terminal 1: Worker Python (puerto 9000)
```bash
python3 -m src.worker \
  --host 127.0.0.1 --port 9000 \
  --monitor-port 8000 --raft-port 10000 \
  --peers 127.0.0.1:9001 127.0.0.1:9002 \
  --storage-dir node0_storage
```

### Terminal 2: Worker Go (puerto 9001)
```bash
./go/worker \
  --host 127.0.0.1 --port 9001 \
  --monitor-port 8001 --raft-port 10001 \
  --peers 127.0.0.1:9000,127.0.0.1:9002
```

### Terminal 3: Worker Kotlin (puerto 9002)
```bash
java -jar kotlin/worker.jar \
  --host 127.0.0.1 --port 9002 \
  --monitor-port 8002 --raft-port 10002 \
  --peers 127.0.0.1:9000,127.0.0.1:9001
```

**Nota:** Esperar ~5 segundos para que RAFT elija un líder.

---

## 5) Comandos del cliente

### Entrenar un modelo (ejemplo XOR)
```bash
python3 -m src.train_client --host 127.0.0.1 --port 9000 \
  train-inline "0,0;0,1;1,0;1,1" "0;1;1;0"
```

### Entrenar desde archivos CSV
```bash
python3 -m src.train_client --host 127.0.0.1 --port 9000 \
  train inputs.csv outputs.csv
```

### Listar modelos disponibles
```bash
python3 -m src.test_client --host 127.0.0.1 --port 9000 list
```

### Hacer predicción
```bash
python3 -m src.test_client --host 127.0.0.1 --port 9000 \
  predict <model_id> "1,0"
```

---

## 6) Verificar estado del cluster

### Monitor HTTP
- **Estado RAFT:** `http://127.0.0.1:8000/status`
- **Lista modelos:** `http://127.0.0.1:8000/models`
- **Logs:** `http://127.0.0.1:8000/logs`

### Verificar archivos
```bash
# Ver modelos en cada nodo
ls node0_storage/models/*.bin  # Python
ls node1_storage/models/*.bin  # Go
ls node2_storage/models/*.bin  # Kotlin

# Ver estado RAFT persistido
cat node0_storage/raft_state.json
```

---

## 7) Flujo de entrenamiento distribuido

```
1. Cliente envía TRAIN al líder
          │
          ▼
2. Líder divide datos en N chunks
          │
          ▼
3. Líder envía SUB_TRAIN a cada follower
          │
   ┌──────┴──────┐
   ▼             ▼
Go entrena   Kotlin entrena
chunk 1      chunk 2
   │             │
   └──────┬──────┘
          ▼
4. Líder agrega resultados (entrena modelo final)
          │
          ▼
5. Líder replica .bin vía RAFT (STORE_FILE)
          │
          ▼
6. Response: {status: OK, model_id: ...}
```

---

## 8) Almacenamiento

Cada worker crea su directorio `nodeX_storage/`:

```
nodeX_storage/
├── models/              # Modelos entrenados (.bin)
├── raft_state.json      # Estado RAFT persistido
└── worker_XXXX.log      # Logs del worker
```

---

## 9) Solución de problemas

| Problema | Solución |
|----------|----------|
| "Connection refused" | Verificar que los workers estén corriendo |
| "REDIRECT" response | El cliente contactó un follower, reintenta automáticamente |
| Java no encontrado | Verificar `java -version` y `javac -version` |
| Modelo no encontrado | Usar `list` para ver modelos disponibles |

---

## 10) Comandos de referencia rápida

```bash
# Limpiar storage
rm -rf node*_storage

# Ver líder actual
curl http://127.0.0.1:8000/status | jq .leader

# Benchmark 100 predicciones
for i in $(seq 1 100); do
  python3 -m src.test_client --port 9000 predict <model_id> "1,0" &
done
wait
```

---

**Última actualización:** 2025-12-18

