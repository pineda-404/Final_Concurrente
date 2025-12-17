# EXECUTION FLOW — Flujo de ejecución (versión clara y paso a paso)

Este documento describe, de forma concisa y sin redundancias, cómo ejecutar el proyecto desde cero tras los últimos cambios (workers Python/Go/Kotlin, clientes `train_client`/`test_client`, monitor HTTP y Java TrainingModule).

1) Requisitos previos
- Python 3.8+ (virtualenv recomendado).
- Java JDK (javac/java) para compilar y ejecutar el `TrainingModule`.
- Go (si va a ejecutar el worker Go).
- Gradle/JDK (si va a ejecutar el worker Kotlin) o usar `gradle wrapper` si está incluido.

2) Preparar el repositorio (única vez)
- Desde la raíz del repo:
```powershell
# activar entorno virtual (Windows PowerShell)
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```
- Compilar el módulo Java:
```powershell
cd java
javac TrainingModule.java NeuralNetwork.java  # si existe NeuralNetwork.java
cd ..
```
- (Opcional) compilar Kotlin/Go si va a usarlos.

3) Estructura relevante (resumen)
- `src/worker.py` — worker Python; expone monitor HTTP y servidor TCP JSON (TRAIN/PREDICT/LIST_MODELS) y legacy PUT.
- `src/train_client.py` — cliente para enviar datos de entrenamiento.
- `src/test_client.py` — cliente para listar y predecir con modelos entrenados.
- `src/client.py` — cliente legacy para subir ficheros binarios (PUT).
- `java/` — código Java del módulo de entrenamiento (invocado por workers).
- `go/`, `kotlin/` — implementaciones alternativas de worker (compatibles en protocolo).

4) Iniciar un clúster local (ejemplo mínimo, 3 nodos)
- En tres terminales diferentes (cada uno en la raíz del repo):
```powershell
# Nodo A (worker Python)
python -m src.worker --host 127.0.0.1 --port 9000 --monitor-port 8000 --raft-port 10000 --peers 127.0.0.1:9001 127.0.0.1:9002 --storage-dir .\node0_storage --java-dir java

# Nodo B (puede ser Kotlin o Python)
python -m src.worker --host 127.0.0.1 --port 9001 --monitor-port 8001 --raft-port 10001 --peers 127.0.0.1:9000 127.0.0.1:9002 --storage-dir .\node1_storage --java-dir java

# Nodo C (Go, Kotlin o Python)
python -m src.worker --host 127.0.0.1 --port 9002 --monitor-port 8002 --raft-port 10002 --peers 127.0.0.1:9000 127.0.0.1:9001 --storage-dir .\node2_storage --java-dir java
```
Notas:
- Cada worker crea su `--storage-dir` y `--storage-dir/models/` y el log `worker.log` dentro de ese directorio.
- Espere ~3–6 s para que Raft elija líder.

5) Verificar estado y logs
- Abrir monitor HTTP del nodo (ej. Nodo A):
  - `http://127.0.0.1:8000/status` → JSON con `state`, `term`, `leader`, `log_length`, `commit_index`.
  - `http://127.0.0.1:8000/models` → lista de modelos.
  - `http://127.0.0.1:8000/logs` → últimos registros.

6) Protocolos de cliente (resumen corto)
- JSON line protocol (preferido): el cliente envía UNA LÍNEA JSON terminada en `\n` y espera una respuesta JSON terminada en `\n`.
  - TRAIN: `{'type':'TRAIN','inputs':[[...],[...]],'outputs':[[...],[...]]}`
  - PREDICT: `{'type':'PREDICT','model_id':'<id>','input':[...]]}`
  - LIST_MODELS: `{'type':'LIST_MODELS'}`
- Legacy PUT: header JSON con `filename` y `size` seguido por payload raw bytes.

7) Flujo TRAIN (paso a paso)
1. Cliente (`src.train_client.py`) envía JSON `TRAIN` al worker (puede ser cualquier nodo).
2. Si el nodo contactado NO es líder devuelve `{'status':'REDIRECT','leader':[host,port]}` → cliente reintenta con el líder.
3. Líder crea archivos temporales en `--storage-dir/models`, ejecuta `java -cp <java_dir> TrainingModule train ...`.
4. Cuando obtiene `MODEL_ID`, líder replica la entrada con `raft_node.replicate({...})`.
5. Al confirmarse la replicación por mayoría (commit), el modelo binario queda en `--storage-dir/models/model_<id>.bin` y el cliente recibe `{'status':'OK','model_id':...}`.

8) Flujo PREDICT
1. Cliente (`src.test_client.py`) envía JSON `PREDICT` al worker.
2. Si el nodo no es líder, redirección igual que TRAIN.
3. Worker local ejecuta `java -cp <java_dir> TrainingModule predict <model> <input>` y devuelve `{'status':'OK','output':[...]} `.

9) Flujo Legacy PUT (subir fichero)
1. Cliente envía header JSON (`filename`,`size`) + `\n` seguido por bytes.
2. Si receptor es seguidor devuelve `REDIRECT`; si líder realiza `raft_node.replicate({'filename', 'data_b64'})`.
3. Al commit, líder persiste el archivo en `--storage-dir/` y devuelve `{'status':'OK'}`.

10) Almacenamiento y paths
- `--storage-dir/` contiene:
  - `worker.log` — logs del worker
  - `models/` — modelos entrenados (model_*.bin)
  - otros ficheros subidos por PUT

11) Errores y comportamiento esperado
- Si `raft_node.replicate()` no alcanza mayoría, la operación puede retornar `FAIL` y el cliente recibirá `{'status':'FAIL'}`.
- Si el cliente recibe `REDIRECT`, debe reintentar con el leader.
- Ver logs en `/logs` y estado en `/status` para diagnóstico.

12) Comandos de utilidad rápidos
```powershell
# Entrenar desde archivos CSV
python -m src.train_client --host 127.0.0.1 --port 9000 train inputs.csv outputs.csv

# Entrenar inline
python -m src.train_client --host 127.0.0.1 --port 9000 train-inline "0,0;0,1;1,0;1,1" "0;1;1;0"

# Listar modelos
python -m src.test_client --host 127.0.0.1 --port 9000 list

# Pedir predicción
python -m src.test_client --host 127.0.0.1 --port 9000 predict <model_id> 1,0,1,0

# Legacy upload
python -m src.client --host 127.0.0.1 --port 9000 put .\archivo.txt
```

13) Sugerencias operativas
- Para desarrollo, use nodos locales con `--storage-dir` distintos; compruebe `/status` para ver el líder.
- Compruebe `java -version` y `javac -version` antes de lanzar entrenamientos automáticos.
- Para tests de tolerancia a fallos: matar el proceso del líder y comprobar que otro nodo toma el liderazgo y que las operaciones siguen funcionando.

---
Documento generado automáticamente: `docs/EXECUTION_FLOW.md` (puedo ajustar el nivel de detalle o añadir diagramas si lo deseas).
