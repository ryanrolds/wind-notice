"""Flask web app that serves the sailing forecast report."""

import logging
import os
import threading
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from flask import Flask, request

from wind_notice import (
    LOCATION_NAME,
    _simulation_block,
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
_last_fetch_error = None
_last_fetch_error_at = None


def refresh_report():
    """Fetch a fresh forecast and cache the report."""
    global _report_html, _report_plain, _scored_days, _report_updated
    global _last_fetch_error, _last_fetch_error_at
    try:
        logger.info("Refreshing forecast report...")
        plain, html, scored_days = generate_report()
        with _report_lock:
            _report_html = html
            _report_plain = plain
            _scored_days = scored_days
            _report_updated = datetime.now(timezone.utc)
            _last_fetch_error = None
            _last_fetch_error_at = None
        logger.info("Forecast report updated at %s", _report_updated.isoformat())
    except Exception as exc:
        logger.exception("Failed to refresh forecast report")
        with _report_lock:
            _last_fetch_error = str(exc) or exc.__class__.__name__
            _last_fetch_error_at = datetime.now(timezone.utc)


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
        f"www.fernridgewind.com"
    )
    html = (
        f"<h2>Sailing looks great this afternoon!</h2>"
        f"<p><strong>Score:</strong> {today_day['score']} ({today_day['rating']})<br>"
        f"<strong>Wind:</strong> {today_day['wind_avg']:.0f} mph {compass}<br>"
        f"<strong>Temp:</strong> {today_day['temp_avg']:.0f}\u00b0F, {sky}</p>"
        f"<p><a href=\"https://www.fernridgewind.com\">Full forecast</a></p>"
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
            err = _last_fetch_error
            err_at = _last_fetch_error_at
            updated = _report_updated

        if err is not None:
            err_at_str = err_at.strftime("%b %d, %Y %H:%M UTC") if err_at else "unknown"
            updated_str = updated.strftime("%b %d, %Y %H:%M UTC") if updated else None
            stale_line = (
                f"<p>Last successful update: {updated_str}. Showing a cached report below may be stale.</p>"
                if updated_str else ""
            )
            banner = (
                '<div style="font-family:Arial,Helvetica,sans-serif;background:#c62828;'
                'color:#fff;padding:16px 20px;text-align:center">'
                '<strong>Forecast data unavailable.</strong> '
                f'Could not reach Open-Meteo after retries (last attempted {err_at_str}). '
                f'<code style="color:#ffcdd2">{err}</code>'
                f'{stale_line}'
                '</div>'
            )
            if html is None:
                return (
                    '<!DOCTYPE html><html><head><meta charset="utf-8">'
                    '<title>Forecast unavailable</title></head>'
                    f'<body style="margin:0">{banner}</body></html>',
                    503,
                )
            # Splice banner in at the top of <body> if possible, else prepend.
            marker = "<body"
            idx = html.find(marker)
            if idx != -1:
                body_open_end = html.find(">", idx)
                if body_open_end != -1:
                    return html[: body_open_end + 1] + banner + html[body_open_end + 1:]
            return banner + html

        if html is None:
            return "<h2>Forecast loading, check back shortly...</h2>", 503
        return html

    @app.route("/simulation")
    def simulation():
        """Standalone simulation page — used for OG image screenshots and debugging.

        Query params: cloud, wind, gust, precip, temp, weather_code (all optional).
        Add og=true to overlay a centered branding title for share-card renders.
        """
        def f(name, default):
            try:
                return float(request.args.get(name, default))
            except (TypeError, ValueError):
                return default

        def i(name, default):
            try:
                return int(request.args.get(name, default))
            except (TypeError, ValueError):
                return default

        sim = _simulation_block(
            cond_cloud=f("cloud", 30),
            cond_wind=f("wind", 12),
            cond_gust=f("gust", 18),
            cond_precip=f("precip", 0.0),
            cond_temp=f("temp", 75),
            cond_weather_code=i("weather_code", 0),
        )

        og = request.args.get("og", "").lower() in ("1", "true", "yes")
        overlay = ""
        if og:
            overlay = (
                '<div style="position:fixed;left:0;right:0;bottom:0;padding:0 40px 56px 40px;'
                'z-index:2;text-align:center;font-family:Arial,Helvetica,sans-serif;'
                'pointer-events:none">'
                '<h1 style="color:white;font-size:76px;margin:0 0 12px 0;letter-spacing:0.5px;'
                'text-shadow:0 4px 18px rgba(0,0,0,0.55), 0 2px 6px rgba(0,0,0,0.45)">'
                'Fern Ridge Sailing Forecast</h1>'
                '<p style="color:rgba(255,255,255,0.95);font-size:30px;margin:0;'
                'text-shadow:0 2px 10px rgba(0,0,0,0.6), 0 1px 4px rgba(0,0,0,0.5)">'
                'www.fernridgewind.com</p>'
                '</div>'
            )

        return (
            '<!DOCTYPE html><html><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<title>Simulation — Fern Ridge Sailing Forecast</title>'
            '<style>html,body{margin:0;padding:0;overflow:hidden;background:#0a3d91}</style>'
            '</head><body>'
            f'{sim}{overlay}'
            '</body></html>'
        )

    @app.route("/robots.txt")
    def robots():
        body = (
            "User-agent: *\n"
            "Allow: /\n"
        )
        return body, 200, {"Content-Type": "text/plain; charset=utf-8"}

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
