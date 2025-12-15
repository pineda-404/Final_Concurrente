import time
import threading
import asyncio


def phase_work(phase_id: int, duration: float = 1.0):
    """Simula trabajo de una fase (bloqueante)."""
    print(f"[Phase {phase_id}] inicio (duracion={duration}s)")
    time.sleep(duration)
    print(f"[Phase {phase_id}] fin")


def run_phases_threaded(phases=None):
    """Ejecuta fases en hilos (simultáneo)."""
    if phases is None:
        phases = [(1, 1.0), (2, 1.5), (3, 0.8)]

    threads = []
    for pid, dur in phases:
        t = threading.Thread(target=phase_work, args=(pid, dur), daemon=False)
        threads.append(t)
        t.start()

    for t in threads:
        t.join()


async def phase_work_async(phase_id: int, duration: float = 1.0):
    print(f"[Phase {phase_id}] inicio (async, duracion={duration}s)")
    await asyncio.sleep(duration)
    print(f"[Phase {phase_id}] fin (async)")


async def run_phases_async(phases=None):
    if phases is None:
        phases = [(1, 1.0), (2, 1.5), (3, 0.8)]

    tasks = [asyncio.create_task(phase_work_async(pid, dur)) for pid, dur in phases]
    await asyncio.gather(*tasks)
