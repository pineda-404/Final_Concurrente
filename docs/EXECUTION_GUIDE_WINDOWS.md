# Guía de ejecución (Windows)

Este documento explica cómo preparar, ejecutar y probar el proyecto en Windows. Está pensado para la entrega académica descrita en `Final_cc4P1-252_v03.pdf`.

Resumen rápido
- Workers: servidor TCP que recibe archivos, replica por consenso (Raft simplificado) y expone un monitor HTTP en `/logs`.
- Cliente: CLI (`src/client.py`) para subir archivos.
- Módulo de entrenamiento: `java/TrainingModule.java` (JDK 8+).
- Benchmark: `tools/benchmark.py` para generar y enviar 1000 archivos.

Prerequisitos (Windows)
- Python 3.8 o superior instalado y en PATH.
- Java JDK 8 o superior instalado y en PATH (`java` y `javac` disponibles).
- Git (opcional) para clonar el repositorio.

Preparar el entorno
1. Abrir PowerShell y situarse en la carpeta del proyecto.

```powershell
# ejemplo: abrir PowerShell y cambiar de directorio
cd 'C:\Users\luisa\Desktop\UNI\7-CICLO\Concurrente\Laboratorios\Final'
```

2. Crear y activar un entorno virtual (recomendado):

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install --upgrade pip
pip install -r requirements.txt
```

Compilar el módulo Java
```powershell
cd java
javac TrainingModule.java
cd ..
```

Estructura importante
- `src/worker.py` — server TCP + monitor HTTP + integración Raft.
- `src/client.py` — cliente CLI para `put`.
- `src/raft.py` — Raft simplificado (demostrativo).
- `java/TrainingModule.java` — módulo de entrenamiento (simulador).
- `tools/benchmark.py` — script de carga concurrente.
- `docs/` — diagramas y plantillas de informe.

Ejecutar nodos (local, 3 workers)
Abra tres ventanas de PowerShell o ejecute en segundo plano; aquí mostramos comandos separados.

```powershell
# Worker 1 (puerto worker 9000, monitor 8000, raft 10000)
python -m src.worker --host 127.0.0.1 --port 9000 --monitor-port 8000 --raft-port 10000 --peers 127.0.0.1:9001 127.0.0.1:9002 --storage-dir .\node0_storage

# Worker 2
python -m src.worker --host 127.0.0.1 --port 9001 --monitor-port 8001 --raft-port 10001 --peers 127.0.0.1:9000 127.0.0.1:9002 --storage-dir .\node1_storage

# Worker 3
python -m src.worker --host 127.0.0.1 --port 9002 --monitor-port 8002 --raft-port 10002 --peers 127.0.0.1:9000 127.0.0.1:9001 --storage-dir .\node2_storage
```

Notas:
- Espere ~3–6 segundos tras arrancar para que los nodos se inicien y se elija un líder.
- El `--raft-port` es el puerto donde el nodo Raft escucha RPCs; el worker anuncia el puerto worker (p.ej. 9000) como líder para que el cliente pueda redirigir.

**Flujo de Ejecución**
- **Arranque del nodo:** cada worker crea su `--storage-dir` y el archivo de log, luego inicia el servidor de monitor HTTP, el nodo Raft (elección/heartbeat) y finalmente el servidor TCP que recibe uploads.
- **Elección de líder:** tras arrancar los Raft peers se realiza la elección (esperar ~3–6 s). El líder anunciará su `worker_port` para redirecciones cliente.
- **Cliente → Worker (upload):**
	- El cliente abre conexión TCP al `host:port` del worker y envía una línea JSON con metadatos (ej.: `filename`, `size`, `content_type`, `sha256`, `run_after_commit`, `upload_id`) seguida inmediatamente por el payload de bytes.
	- Si el worker contactado es un seguidor, responde `{'status':'REDIRECT','leader':[host,port]}`; el cliente debe reintentar contra el leader.
- **Leader → Consenso (Raft):**
	- El leader recibe la petición, crea una entrada de log (por ejemplo con `data_b64`) y llama a `raft_node.replicate(entry)`.
	- El leader envía `AppendEntries` a los peers; cuando una mayoría confirma la replicación, el leader avanza `commit_index`.
- **Aplicación y respuesta:**
	- Al aplicarse (commit) la entrada, el leader decodifica los bytes y persiste el fichero en `--storage-dir` (p.ej. `worker_storage_9000/archivo.txt`) y devuelve `{'status':'OK'}` al cliente.
	- Si `run_after_commit` está activo, el worker lanza `TrainingModule` en background y registra su salida en el log.
- **Errores / fallos:**
	- Si la replicación no alcanza mayoría, el leader puede devolver `{'status':'FAIL'}` y registrar el evento.
	- Si el contenido falla la verificación `sha256`, el worker debe registrar y rechazar el upload.
- **Operaciones avanzadas:** para archivos grandes usar chunking con metadatos por chunk (`chunk_index`, `chunk_count`, `chunk_sha256`) y reensamblado en el servidor.

Subir un archivo desde el cliente
```powershell
python -m src.client --host 127.0.0.1 --port 9000 put C:\ruta\a\archivo.txt
python -m src.client --host 127.0.0.1 --port 9000 put C:\Users\luisa\Desktop\UNI\7-CICLO\Concurrente\Laboratorios\Final\archivo.txt
python -m src.client --host 127.0.0.1 --port 9000 put C:\Users\luisa\Desktop\UNI\7-CICLO\Concurrente\Laboratorios\Final\Fases.png
```

Comportamiento esperado:
- Si el worker contactado es seguidor, responderá con JSON `{'status':'REDIRECT','leader':[host,port]}` y el cliente reintentará contra el líder.
- Cuando el líder replica y obtiene mayoría, persiste el archivo en `worker_storage_<port>/` o en el `--storage-dir` configurado.

Consultar logs y monitor
- Abrir en el navegador: `http://127.0.0.1:8000/logs` (o 8001/8002 según node) para ver los registros del worker.

Ejecutar el benchmark (1000 archivos)
```powershell
python tools/benchmark.py
```

Opciones: ejecutar con `python tools/benchmark.py --help` (puede editar el script para pasar host/port/total).

Ejecutar pruebas automáticas
```powershell
pytest -q
```

Consideraciones para LAN/Wi‑Fi (despliegue en varias máquinas)
- Use la IP de la interfaz de red de la máquina en `--host` (p.ej. `192.168.1.10`) y abra los puertos en el firewall si es necesario.
- Inicie cada worker en su máquina respectiva; en `--peers` incluya las IP:puerto de los demás.
- Asegúrese de fijar `--raft-port` y `--port` por nodo, y de que la comunicación entre máquinas es posible (pruebe con `telnet IP PORT`).

Limitaciones y recomendaciones
- El Raft implementado es una versión simplificada (suficiente para la evaluación académica). No está optimizado para producción ni cubre persistencia de estado en disco entre reinicios.
- Se recomienda: pruebas adicionales de tolerancia a fallos, persistencia del log, manejo de retransmisiones y reconexiones, y mejorar la seguridad (autenticación).
- La interfaz cliente actual es CLI; puede añadir GUI (Tkinter) si desea interfaz gráfica para subir/descargar archivos.

Archivos para entrega (subir a Univirtual)
- Incluir únicamente el código fuente (carpetas `src/`, `java/`, `tools/`), `docs/` (reportes y diagramas) y PDFs (`Informe.pdf`, `Presentacion.pdf`) generados por el equipo.
- No incluir binarios o historiales de ejecución innecesarios.

Solución de problemas (rápida)
- Si cliente nunca recibe `OK`: revise `logs` en el monitor y verifique que el líder se haya elegido.
- Si `pytest` falla por import error: ejecute `pytest` desde la raíz del repo (donde está `src/`).
- Si Java no se ejecuta: verifique que `java -version` y `javac -version` funcionan en PowerShell.

Contacto y próximos pasos
- Si quiere que refinemos Raft, prepare pruebas de caída de nodo (kill) y reingreso para validar recuperación y lo implemento.

---
Documento generado automáticamente dentro del repositorio. Si desea un PDF listo para entrega, puedo convertirlo y añadir `Informe.pdf` y `Presentacion.pdf` basados en la plantilla.

**Cambios recientes y ejecución en la nueva versión**
- El repositorio ahora incluye implementaciones y herramientas adicionales: un worker en Kotlin (carpeta `kotlin/`), un worker en Go (`go/`) y utilidades nuevas en `src/` como `train_client.py` y `test_client.py`.
- El worker Python soporta ahora RPCs JSON con tipos: `TRAIN`, `PREDICT`, `LIST_MODELS` y mantiene compatibilidad con el formato `PUT` heredado para subir archivos binarios.

**Nuevos clientes y ejemplos de uso**
- Enviar datos para entrenamiento (archivo CSV o inline):
```powershell
python -m src.train_client --host 127.0.0.1 --port 9000 train inputs.csv outputs.csv
# o para pruebas rápidas inline:
python -m src.train_client --host 127.0.0.1 --port 9000 train-inline "0,0;0,1;1,0;1,1" "0;1;1;0"
```
- Listar modelos y pedir predicción:
```powershell
python -m src.test_client --host 127.0.0.1 --port 9000 list
python -m src.test_client --host 127.0.0.1 --port 9000 predict <model_id> 1,0,1,0
```
- El cliente legacy para subir ficheros sigue disponible:
```powershell
python -m src.client --host 127.0.0.1 --port 9000 put .\archivo.txt
```

**Workers en varios lenguajes (build / run)**
- Worker Python (sin build):
```powershell
python -m src.worker --host 127.0.0.1 --port 9000 --monitor-port 8000 --raft-port 10000 --peers 127.0.0.1:9001 127.0.0.1:9002 --storage-dir .\node0_storage
```
- Worker Go (desde `go/`):
```powershell
cd go
go run . --host 127.0.0.1 --port 9002 --monitor-port 8002 --raft-port 10002 --peers 127.0.0.1:9000 127.0.0.1:9001
```
- Worker Kotlin (usar Gradle o jar en `kotlin/`):
```powershell
cd kotlin
gradle build
java -jar build\libs\worker-kotlin-1.0.jar --host 127.0.0.1 --port 9001 --monitor-port 8001 --raft-port 10001 --peers 127.0.0.1:9000 127.0.0.1:9002
```

**Compilar / preparar módulo Java de entrenamiento**
- Asegúrese de compilar `TrainingModule.java` (y `NeuralNetwork.java` si existe) en la carpeta `java/`:
```powershell
cd java
javac TrainingModule.java NeuralNetwork.java
cd ..
```
El worker invoca `java -cp java TrainingModule ...` para entrenamiento y predicción.

**Resumen del flujo actualizado**
- El monitor HTTP ahora expone `/status` (estado RAFT), `/models` (lista modelos) y `/logs`.
- Para entrenar: el cliente `train_client` envía un JSON `{'type':'TRAIN', 'inputs': [...], 'outputs': [...]}` al worker; el leader ejecuta el entrenamiento (invocando Java), registra el `model_id`, replica la entrada via Raft y guarda el archivo de modelo en `--storage-dir/models`.
- Para predecir: `test_client` envía `{'type':'PREDICT', 'model_id':..., 'input': [...]}` y el worker ejecuta `TrainingModule predict` y devuelve `{'status':'OK','output':[...]]}`.

Si quieres, actualizo también los ejemplos concretos en la parte inicial del documento (comandos por línea) para mostrar cómo levantar un clúster mixto Python/Go/Kotlin paso a paso.
