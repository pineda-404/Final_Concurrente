from src import concurrency


def test_threaded_runs():
    # Solo verificar que la función completa sin excepciones
    concurrency.run_phases_threaded(phases=[(1, 0.01), (2, 0.01)])


import asyncio


def test_async_runs():
    asyncio.run(concurrency.run_phases_async(phases=[(1, 0.01), (2, 0.01)]))
