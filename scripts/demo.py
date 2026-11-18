"""One-command demo launcher: Phoenix (traces) in the background, then the Streamlit UI. Ctrl+C stops both.

    uv run python scripts/demo.py

Phoenix is skipped, with a message, if something already listens on port 6006. Reset the demo data from the UI sidebar
(or: uv run python -m backend.reset seeds/base.json). Needs OPENROUTER_API_KEY in .env to run tasks.
"""
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PHOENIX_PORT = 6006


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def stop(proc: subprocess.Popen | None, name: str) -> None:
    """SIGINT to the whole process group (like Ctrl+C), then terminate, then kill."""
    if proc is None or proc.poll() is not None:
        return
    for sig, wait in ((signal.SIGINT, 5), (signal.SIGTERM, 5), (signal.SIGKILL, 2)):
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            return
        try:
            proc.wait(timeout=wait)
            print(f"{name} stopped.")
            return
        except subprocess.TimeoutExpired:
            continue


def main() -> int:
    phoenix = None
    if port_in_use(PHOENIX_PORT):
        print(f"Port {PHOENIX_PORT} is already in use: assuming Phoenix is running, not starting another one.")
    else:
        print(f"Starting Phoenix on http://localhost:{PHOENIX_PORT} ...")
        phoenix = subprocess.Popen([sys.executable, "-m", "phoenix.server.main", "serve"], cwd=ROOT,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        time.sleep(1.0)
        if phoenix.poll() is not None:
            print("Phoenix did not start (continuing without traces).")
            phoenix = None
    ui = None
    try:
        print("Starting the UI (Ctrl+C to stop everything) ...")
        ui = subprocess.Popen([sys.executable, "-m", "streamlit", "run", "ui/app.py"], cwd=ROOT, start_new_session=True)
        return ui.wait()
    except KeyboardInterrupt:
        print("\nStopping ...")
        return 0
    finally:
        stop(ui, "UI")
        stop(phoenix, "Phoenix")


if __name__ == "__main__":
    sys.exit(main())
