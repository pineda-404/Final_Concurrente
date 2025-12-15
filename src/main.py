import argparse
from src.concurrency import run_phases_threaded, run_phases_async


def main():
    parser = argparse.ArgumentParser(description="Proyecto concurrente - runner de fases")
    parser.add_argument("--mode", choices=["thread","async"], default="thread", help="Modo de concurrencia")
    args = parser.parse_args()

    if args.mode == "thread":
        run_phases_threaded()
    else:
        import asyncio
        asyncio.run(run_phases_async())


if __name__ == '__main__':
    main()
