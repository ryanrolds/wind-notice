"""Flask web app that serves the sailing forecast report."""

import logging
import os
import threading
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask

from wind_notice import generate_report

logger = logging.getLogger(__name__)

# Cached report state
_report_lock = threading.Lock()
_report_html = None
_report_updated = None


def refresh_report():
    """Fetch a fresh forecast and cache the HTML report."""
    global _report_html, _report_updated
    try:
        logger.info("Refreshing forecast report...")
        _, html = generate_report()
        with _report_lock:
            _report_html = html
            _report_updated = datetime.now(timezone.utc)
        logger.info("Forecast report updated at %s", _report_updated.isoformat())
    except Exception:
        logger.exception("Failed to refresh forecast report")


def create_app():
    app = Flask(__name__)

    @app.route("/")
    def index():
        with _report_lock:
            html = _report_html
        if html is None:
            return "<h2>Forecast loading, check back shortly...</h2>", 503
        return html

    @app.route("/healthz")
    def health():
        with _report_lock:
            updated = _report_updated
        if updated is None:
            return {"status": "starting"}, 503
        return {"status": "ok", "last_updated": updated.isoformat()}

    # Refresh on startup
    refresh_report()

    # Schedule periodic refresh
    interval_hours = int(os.environ.get("REFRESH_INTERVAL_HOURS", "6"))
    scheduler = BackgroundScheduler(daemon=True)
    scheduler.add_job(refresh_report, "interval", hours=interval_hours, id="refresh_forecast")
    scheduler.start()

    return app


def create_app_for_gunicorn():
    """Entry point for gunicorn."""
    return create_app()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    app = create_app()
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=True)
