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
    compass_direction,
    generate_report,
    load_email_config,
    send_email,
)

logger = logging.getLogger(__name__)

# Cached report state
_report_lock = threading.Lock()
_report_html = None
_report_plain = None
_scored_days = None
_report_updated = None


def refresh_report():
    """Fetch a fresh forecast and cache the report."""
    global _report_html, _report_plain, _scored_days, _report_updated
    try:
        logger.info("Refreshing forecast report...")
        plain, html, scored_days = generate_report()
        with _report_lock:
            _report_html = html
            _report_plain = plain
            _scored_days = scored_days
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


def send_afternoon_alert():
    """Send an email alert if this afternoon is a great day for sailing."""
    with _report_lock:
        scored_days = _scored_days

    if scored_days is None:
        logger.warning("No scored days available for afternoon alert, skipping")
        return

    today = datetime.now().date()
    today_day = next((d for d in scored_days if d["date"] == today), None)
    if today_day is None:
        logger.info("No forecast data for today, skipping afternoon alert")
        return

    min_score = int(os.environ.get("ALERT_MIN_SCORE", "65"))
    if today_day["score"] < min_score:
        logger.info(
            "Today's score %d is below alert threshold %d, skipping",
            today_day["score"],
            min_score,
        )
        return

    try:
        config = load_email_config()
    except ValueError:
        logger.warning("Email not configured, skipping afternoon alert")
        return

    cloud_pct = today_day["cloud_avg"]
    if cloud_pct <= 20:
        sky = "clear skies"
    elif cloud_pct <= 50:
        sky = "partly cloudy"
    elif cloud_pct <= 80:
        sky = "mostly cloudy"
    else:
        sky = "overcast"

    compass = compass_direction(today_day["dir_avg"])
    subject = f"Great sailing this afternoon! Score: {today_day['score']}"
    plain = (
        f"Sailing looks great this afternoon! Score: {today_day['score']} ({today_day['rating']}). "
        f"Wind {today_day['wind_avg']:.0f} mph {compass}, "
        f"{today_day['temp_avg']:.0f}\u00b0F, {sky}. "
        f"wind.pedanticorderliness.com"
    )
    html = (
        f"<h2>Sailing looks great this afternoon!</h2>"
        f"<p><strong>Score:</strong> {today_day['score']} ({today_day['rating']})<br>"
        f"<strong>Wind:</strong> {today_day['wind_avg']:.0f} mph {compass}<br>"
        f"<strong>Temp:</strong> {today_day['temp_avg']:.0f}\u00b0F, {sky}</p>"
        f"<p><a href=\"https://wind.pedanticorderliness.com\">Full forecast</a></p>"
    )

    try:
        send_email(subject, plain, html, config)
        logger.info("Afternoon sailing alert emailed to %s", config["EMAIL_TO"])
    except Exception:
        logger.exception("Failed to send afternoon alert email")


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
    email_tz = os.environ.get("TIMEZONE", "America/Los_Angeles")
    email_enabled = os.environ.get("EMAIL_ENABLED", "false").lower() == "true"
    if email_enabled:
        scheduler.add_job(
            send_email_report,
            CronTrigger.from_crontab(email_cron, timezone=email_tz),
            id="email_forecast",
        )
        logger.info("Email scheduled: cron='%s' tz=%s", email_cron, email_tz)

    # Schedule afternoon sailing alert email
    alert_enabled = os.environ.get("ALERT_ENABLED", "false").lower() == "true"
    if alert_enabled:
        alert_cron = os.environ.get("ALERT_CRON", "0 11 * * *")
        scheduler.add_job(
            send_afternoon_alert,
            CronTrigger.from_crontab(alert_cron, timezone=email_tz),
            id="afternoon_alert",
        )
        logger.info("Afternoon alert scheduled: cron='%s' tz=%s", alert_cron, email_tz)

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
