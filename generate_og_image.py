"""Render the Open Graph / social share image by screenshotting /simulation.

Spins up the Flask app on a loopback port, points headless Chrome at
/simulation?og=true with sunny-day parameters, writes static/og-image.png
(1200x630, LinkedIn-compliant 1.91:1). Chrome is used only by this script — it
is not a runtime dependency of the web app.

Usage:
    python generate_og_image.py
"""
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

WIDTH, HEIGHT = 1200, 630
OUTPUT = Path(__file__).parent / "static" / "og-image.png"
PARAMS = "cloud=85&wind=15&gust=22&precip=0.25&temp=58&weather_code=63&horizon=350"
VIRTUAL_TIME_BUDGET_MS = 4000  # Let the simulation settle before snapshotting.


def _find_chrome():
    for name in ("google-chrome", "chromium", "chromium-browser", "chrome"):
        path = shutil.which(name)
        if path:
            return path
    raise RuntimeError("No chrome/chromium binary found on PATH")


def _pick_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_server(url, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as r:
                if r.status == 200:
                    return
        except Exception:
            time.sleep(0.1)
    raise RuntimeError(f"Server at {url} did not respond within {timeout}s")


def _start_flask(port):
    # Import lazily so the module loads even when test deps are missing.
    os.environ.setdefault("EMAIL_ENABLED", "false")
    os.environ.setdefault("ALERT_ENABLED", "false")
    from werkzeug.serving import make_server

    from wind_notice import _simulation_block  # noqa: F401  (sanity import)
    from app import create_app

    app = create_app()
    server = make_server("127.0.0.1", port, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def main():
    chrome = _find_chrome()
    port = _pick_port()
    url = f"http://127.0.0.1:{port}/simulation?{PARAMS}"

    print(f"Starting Flask on 127.0.0.1:{port} ...")
    server = _start_flask(port)
    try:
        _wait_for_server(f"http://127.0.0.1:{port}/simulation", timeout=15)
        print(f"Screenshotting {url} at {WIDTH}x{HEIGHT} ...")
        with tempfile.TemporaryDirectory() as td:
            user_data = os.path.join(td, "chrome-user-data")
            cmd = [
                chrome,
                "--headless=new",
                "--disable-gpu",
                "--no-sandbox",
                "--hide-scrollbars",
                f"--user-data-dir={user_data}",
                f"--window-size={WIDTH},{HEIGHT}",
                f"--screenshot={OUTPUT}",
                f"--virtual-time-budget={VIRTUAL_TIME_BUDGET_MS}",
                url,
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if result.returncode != 0:
                sys.stderr.write(result.stderr)
                raise SystemExit(f"chrome exited with {result.returncode}")
    finally:
        server.shutdown()

    size = OUTPUT.stat().st_size
    print(f"Wrote {OUTPUT} ({size} bytes)")


if __name__ == "__main__":
    main()
