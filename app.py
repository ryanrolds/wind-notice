"""Flask web app that serves the sailing forecast report."""

import logging
import os
import threading
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from flask import Flask

from wind_notice import (
    LOCATION_NAME,
    generate_report,
    load_email_config,
    send_email,
)

logger = logging.getLogger(__name__)

# Cached report state
_report_lock = threading.Lock()
_report_html = None
_report_plain = None
_report_updated = None


def refresh_report():
    """Fetch a fresh forecast and cache the report."""
    global _report_html, _report_plain, _report_updated
    try:
        logger.info("Refreshing forecast report...")
        plain, html = generate_report()
        with _report_lock:
            _report_html = html
            _report_plain = plain
            _report_updated = datetime.now(timezone.utc)
        logger.info("Forecast report updated at %s", _report_updated.isoformat())
    except Exception:
        logger.exception("Failed to refresh forecast report")


def send_email_report():
    """Send the current cached report via email."""
    with _report_lock:
        plain = _report_plain
        html = _report_html

    if plain is None or html is None:
        logger.warning("No report available to email, skipping")
        return

    try:
        config = load_email_config()
    except ValueError:
        logger.warning("Email not configured, skipping send")
        return

    now = datetime.now()
    subject = f"Sailing Forecast — {LOCATION_NAME} — {now.strftime('%b %d, %Y')}"
    try:
        send_email(subject, plain, html, config)
        logger.info("Forecast emailed to %s", config["EMAIL_TO"])
    except Exception:
        logger.exception("Failed to send forecast email")


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

    # Schedule periodic report refresh
    interval_hours = int(os.environ.get("REFRESH_INTERVAL_HOURS", "6"))
    scheduler = BackgroundScheduler(daemon=True)
    scheduler.add_job(refresh_report, "interval", hours=interval_hours, id="refresh_forecast")

    # Schedule email send (default: 9 AM Pacific daily)
    email_cron = os.environ.get("EMAIL_CRON", "0 9 * * *")
    email_tz = os.environ.get("EMAIL_TIMEZONE", "America/Los_Angeles")
    email_enabled = os.environ.get("EMAIL_ENABLED", "false").lower() == "true"
    if email_enabled:
        scheduler.add_job(
            send_email_report,
            CronTrigger.from_crontab(email_cron, timezone=email_tz),
            id="email_forecast",
        )
        logger.info("Email scheduled: cron='%s' tz=%s", email_cron, email_tz)

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
