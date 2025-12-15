**Título:** Proyecto Concurrente — Informe

**Autores:** (Nombre(s) y código(s) de alumno(s))

**Resumen:**
Breve descripción del sistema: workers, cliente, Raft simplificado, módulo de entrenamiento en Java.

**Arquitectura:**
Insertar `docs/architecture.svg`.

**Protocolo:**
Insertar `docs/protocol.svg` y describir pasos de envío, réplica y commit.

**Implementación:**
- `src/worker.py`: servidor TCP, monitor HTTP, invocación Java opcional.
- `src/raft.py`: RaftNode simplificado.
- `src/client.py`: CLI con redirección al líder.
- `java/TrainingModule.java`: módulo de entrenamiento (simulador).
- `tools/benchmark.py`: script de carga.

**Pruebas y evaluación:**
- Cómo ejecutar benchmark y medir tiempo (usar `tools/benchmark.py`).

**Despliegue:**
- Notas para LAN/WiFi: usar IPs 0.0.0.0 y abrir puertos; arrancar workers en cada máquina con `--peers` apuntando a otros nodos.

**Conclusiones:**
- Limitaciones de la implementación (Raft simplificado), mejoras futuras.

