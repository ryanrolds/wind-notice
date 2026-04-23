#!/usr/bin/env python3
"""Fern Ridge Sailing Forecast - 7-day forecast scored for sailing quality."""

import argparse
import os
import smtplib
import sys
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests
from dotenv import load_dotenv

# Fern Ridge Reservoir coordinates
LATITUDE = 44.1206
LONGITUDE = -123.2983
LOCATION_NAME = "Fern Ridge Reservoir"

# Open-Meteo API
API_URL = "https://api.open-meteo.com/v1/forecast"

# Sailing hours (11 AM - 5 PM)
SAILING_HOUR_START = 11
SAILING_HOUR_END = 17  # exclusive (up to 4 PM hour)

# Time windows for display (all shown, but only scored windows affect the score)
DISPLAY_WINDOWS = [
    ("Morning", 8, 11),
    ("Midday", 11, 14),
    ("Afternoon", 14, 17),
    ("Evening", 17, 20),
]

# Scoring weights
WEIGHT_WIND = 0.35
WEIGHT_GUSTS = 0.20
WEIGHT_PRECIP = 0.15
WEIGHT_TEMP = 0.10
WEIGHT_CLOUD = 0.10
WEIGHT_DIRECTION = 0.10

# Rating thresholds
RATINGS = [
    (80, "Excellent"),
    (65, "Good"),
    (50, "Fair"),
    (35, "Poor"),
    (0, "Unfavorable"),
]


def fetch_forecast():
    """Fetch 7-day hourly forecast from Open-Meteo."""
    params = {
        "latitude": LATITUDE,
        "longitude": LONGITUDE,
        "hourly": "wind_speed_10m,wind_gusts_10m,wind_direction_10m,temperature_2m,precipitation,cloud_cover",
        "wind_speed_unit": "mph",
        "temperature_unit": "fahrenheit",
        "precipitation_unit": "mm",
        "timezone": "auto",
        "forecast_days": 7,
    }
    resp = requests.get(API_URL, params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()


def parse_forecast(data):
    """Group hourly data into daily summaries.

    Collects display-range hours (8 AM-8 PM for wind windows) and
    scored-range hours (11 AM-5 PM for scoring).
    """
    hourly = data["hourly"]
    times = hourly["time"]

    # Display range covers all DISPLAY_WINDOWS
    display_start = min(s for _, s, _ in DISPLAY_WINDOWS)
    display_end = max(e for _, _, e in DISPLAY_WINDOWS)

    days = {}
    for i, time_str in enumerate(times):
        dt = datetime.fromisoformat(time_str)
        hour = dt.hour
        if hour < display_start or hour >= display_end:
            continue

        date_key = dt.date().isoformat()
        if date_key not in days:
            days[date_key] = {
                "date": dt.date(),
                "display_hours": [], "display_wind": [], "display_gusts": [],
                "hours": [], "wind_speeds": [], "wind_gusts": [],
                "wind_directions": [], "temperatures": [],
                "precipitations": [], "cloud_cover": [],
            }

        day = days[date_key]
        wind = hourly["wind_speed_10m"][i] or 0
        gust = hourly["wind_gusts_10m"][i] or 0

        # Always collect for display windows
        day["display_hours"].append(hour)
        day["display_wind"].append(wind)
        day["display_gusts"].append(gust)

        # Only collect scored hours for scoring
        if SAILING_HOUR_START <= hour < SAILING_HOUR_END:
            day["hours"].append(hour)
            day["wind_speeds"].append(wind)
            day["wind_gusts"].append(gust)
            day["wind_directions"].append(hourly["wind_direction_10m"][i] or 0)
            day["temperatures"].append(hourly["temperature_2m"][i] or 0)
            day["precipitations"].append(hourly["precipitation"][i] or 0)
            day["cloud_cover"].append(hourly["cloud_cover"][i] or 0)

    return [days[k] for k in sorted(days)]


def score_wind(speeds):
    """Score wind speed 0.0-1.0. Ideal: 10-15 mph. Below 8 or above 17 = 0."""
    avg = sum(speeds) / len(speeds)
    if avg < 8 or avg > 17:
        return 0.0
    elif 10 <= avg <= 15:
        return 1.0
    elif avg < 10:
        return (avg - 8) / 2  # gradient 8-10
    else:
        return (17 - avg) / 2  # gradient 15-17


def score_gusts(speeds, gusts):
    """Score gust spread 0.0-1.0. Ideal: <6 mph spread."""
    spreads = [g - s for g, s in zip(gusts, speeds)]
    avg_spread = sum(spreads) / len(spreads)
    if avg_spread <= 6:
        return 1.0
    elif avg_spread <= 18:
        return max(0.0, 1.0 - (avg_spread - 6) / 12)
    else:
        return 0.0


def score_precipitation(precips):
    """Score precipitation 0.0-1.0. Ideal: dry."""
    total = sum(precips)
    if total == 0:
        return 1.0
    elif total <= 1:
        return 0.7
    elif total <= 5:
        return max(0.0, 0.7 - (total - 1) * 0.15)
    else:
        return 0.0


def score_cloud_cover(clouds):
    """Score cloud cover 0.0-1.0. Partly cloudy (30-70%) is ideal for comfort."""
    avg = sum(clouds) / len(clouds)
    if 30 <= avg <= 70:
        return 1.0
    elif avg < 30:
        return 0.5 + (avg / 30) * 0.5  # clear sky still decent, 0.5-1.0
    else:
        return max(0.3, 1.0 - (avg - 70) / 30 * 0.7)  # overcast less fun, 0.3-1.0


def score_temperature(temps):
    """Score temperature 0.0-1.0. Ideal: 75-95F. Below 70 or above 105 = 0."""
    avg = sum(temps) / len(temps)
    if avg < 70 or avg > 105:
        return 0.0
    elif 75 <= avg <= 95:
        return 1.0
    elif avg < 75:
        return (avg - 70) / 5  # gradient 70-75
    else:
        return (105 - avg) / 10  # gradient 95-105


def score_direction(directions):
    """Score wind direction 0.0-1.0. W/NW preferred (best fetch on reservoir)."""
    # Ideal: 270 (W) to 315 (NW)
    ideal_center = 292.5  # midpoint of W-NW
    scores = []
    for d in directions:
        # Angular distance from ideal center
        diff = abs(d - ideal_center)
        if diff > 180:
            diff = 360 - diff
        if diff <= 22.5:
            scores.append(1.0)
        elif diff <= 67.5:
            scores.append(0.7)
        elif diff <= 112.5:
            scores.append(0.4)
        else:
            scores.append(0.2)
    return sum(scores) / len(scores)


def compass_direction(degrees):
    """Convert degrees to compass direction string."""
    dirs = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
            "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
    idx = round(degrees / 22.5) % 16
    return dirs[idx]


def score_day(day):
    """Compute weighted composite score for a day. Returns 0-100."""
    ws = score_wind(day["wind_speeds"])
    gs = score_gusts(day["wind_speeds"], day["wind_gusts"])
    ps = score_precipitation(day["precipitations"])
    cs = score_cloud_cover(day["cloud_cover"])
    ts = score_temperature(day["temperatures"])
    ds = score_direction(day["wind_directions"])

    # If wind or temp is outside usable range, cap the score
    dealbreaker = (ws == 0.0 or ts == 0.0)

    composite = (
        ws * WEIGHT_WIND
        + gs * WEIGHT_GUSTS
        + ps * WEIGHT_PRECIP
        + cs * WEIGHT_CLOUD
        + ts * WEIGHT_TEMP
        + ds * WEIGHT_DIRECTION
    )

    if dealbreaker:
        composite = min(composite, 0.34)  # cap at Unfavorable

    day["score"] = round(composite * 100)
    day["rating"] = next(label for threshold, label in RATINGS if day["score"] >= threshold)
    day["wind_avg"] = sum(day["wind_speeds"]) / len(day["wind_speeds"])
    day["gust_max"] = max(day["wind_gusts"])
    day["temp_avg"] = sum(day["temperatures"]) / len(day["temperatures"])
    day["precip_total"] = sum(day["precipitations"])
    day["dir_avg"] = sum(day["wind_directions"]) / len(day["wind_directions"])
    day["cloud_avg"] = sum(day["cloud_cover"]) / len(day["cloud_cover"])

    # Wind breakdown by time window (uses full display range)
    day["wind_windows"] = []
    for name, start, end in DISPLAY_WINDOWS:
        winds = [s for h, s in zip(day["display_hours"], day["display_wind"]) if start <= h < end]
        gusts = [g for h, g in zip(day["display_hours"], day["display_gusts"]) if start <= h < end]
        if winds:
            day["wind_windows"].append({
                "name": name,
                "avg": sum(winds) / len(winds),
                "gust_max": max(gusts),
            })
        else:
            day["wind_windows"].append({"name": name, "avg": 0, "gust_max": 0})
    day["component_scores"] = {
        "wind": round(ws * 100),
        "gusts": round(gs * 100),
        "precip": round(ps * 100),
        "cloud": round(cs * 100),
        "temp": round(ts * 100),
        "direction": round(ds * 100),
    }
    return day


def get_rating_color(rating):
    """Return a hex color for the rating level."""
    colors = {
        "Excellent": "#2e7d32",
        "Good": "#558b2f",
        "Fair": "#f9a825",
        "Poor": "#e65100",
        "Unfavorable": "#b71c1c",
    }
    return colors.get(rating, "#666")


def format_report(scored_days):
    """Format the forecast report. Returns (plain_text, html) tuple."""
    now = datetime.now()
    header = f"Fern Ridge Sailing Forecast — {now.strftime('%A, %B %d, %Y')}"

    # --- Plain text ---
    lines = [header, "=" * len(header), ""]

    best = max(scored_days, key=lambda d: d["score"])
    best_date = best["date"].strftime("%A")
    lines.append(f"Best day: {best_date} (score {best['score']}, {best['rating']})")
    lines.append("")

    for day in scored_days:
        d = day["date"]
        date_str = d.strftime("%a %b %d")
        compass = compass_direction(day["dir_avg"])
        lines.append(f"{date_str}  |  {day['rating']:12s}  |  Score: {day['score']}")
        lines.append(
            f"  Wind: {day['wind_avg']:.0f} mph avg, gusts {day['gust_max']:.0f} mph  |  "
            f"Dir: {compass}  |  Temp: {day['temp_avg']:.0f}F  |  "
            f"Cloud: {day['cloud_avg']:.0f}%  |  Rain: {day['precip_total']:.1f} mm"
        )
        wind_parts = "  ".join(
            f"{w['name']}: {w['avg']:.0f} (g{w['gust_max']:.0f})"
            for w in day["wind_windows"]
        )
        lines.append(f"  Wind by window — {wind_parts}")
        cs = day["component_scores"]
        lines.append(
            f"  Components — Wind:{cs['wind']} Gusts:{cs['gusts']} "
            f"Precip:{cs['precip']} Cloud:{cs['cloud']} Temp:{cs['temp']} Dir:{cs['direction']}"
        )
        lines.append("")

    lines.append("Scoring: Wind 35% | Gusts 20% | Precip 15% | Cloud 10% | Temp 10% | Direction 10%")
    lines.append("Sailing hours: 11 AM – 5 PM  |  Ideal wind: 10-15 mph from W/NW")
    lines.append(f"Location: {LOCATION_NAME} ({LATITUDE}, {LONGITUDE})")
    lines.append("Data: Open-Meteo.com")
    plain = "\n".join(lines)

    # --- HTML ---
    html_rows = []
    for day in scored_days:
        d = day["date"]
        date_str = d.strftime("%a %b %d")
        compass = compass_direction(day["dir_avg"])
        color = get_rating_color(day["rating"])
        cs = day["component_scores"]

        html_rows.append(f"""
        <tr>
            <td style="padding:8px;font-weight:bold">{date_str}</td>
            <td style="padding:8px;text-align:center">
                <span style="background:{color};color:#fff;padding:3px 10px;border-radius:4px;font-weight:bold">
                    {day['score']} — {day['rating']}
                </span>
            </td>
            <td style="padding:8px">{day['wind_avg']:.0f} mph avg, gusts {day['gust_max']:.0f} mph</td>
            <td style="padding:8px">{compass}</td>
            <td style="padding:8px">{day['temp_avg']:.0f}°F</td>
            <td style="padding:8px">{day['cloud_avg']:.0f}%</td>
            <td style="padding:8px">{day['precip_total']:.1f} mm</td>
        </tr>
        <tr>
            <td colspan="7" style="padding:2px 8px 4px 8px;font-size:0.85em;color:#555">
                Wind: {" &nbsp;|&nbsp; ".join(f"<b>{w['name']}</b> {w['avg']:.0f} (g{w['gust_max']:.0f})" for w in day["wind_windows"])}
            </td>
        </tr>
        <tr>
            <td colspan="7" style="padding:2px 8px 8px 8px;font-size:0.85em;color:#666;border-bottom:1px solid #eee">
                Wind:{cs['wind']} | Gusts:{cs['gusts']} | Precip:{cs['precip']} | Cloud:{cs['cloud']} | Temp:{cs['temp']} | Dir:{cs['direction']}
            </td>
        </tr>""")

    best_date_str = best["date"].strftime("%A, %b %d")
    html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family:Arial,Helvetica,sans-serif;max-width:700px;margin:0 auto;color:#333">
    <h2 style="color:#1565c0">Fern Ridge Sailing Forecast</h2>
    <p style="color:#666">{now.strftime('%A, %B %d, %Y')}</p>

    <div style="background:#e3f2fd;padding:12px 16px;border-radius:6px;margin:16px 0">
        <strong>Best day:</strong> {best_date_str}
        — Score {best['score']} ({best['rating']})
    </div>

    <table style="width:100%;border-collapse:collapse;font-size:0.95em">
        <tr style="background:#f5f5f5">
            <th style="padding:8px;text-align:left">Day</th>
            <th style="padding:8px;text-align:center">Rating</th>
            <th style="padding:8px;text-align:left">Wind</th>
            <th style="padding:8px;text-align:left">Dir</th>
            <th style="padding:8px;text-align:left">Temp</th>
            <th style="padding:8px;text-align:left">Cloud</th>
            <th style="padding:8px;text-align:left">Rain</th>
        </tr>
        {"".join(html_rows)}
    </table>

    <p style="font-size:0.8em;color:#999;margin-top:20px">
        Scoring: Wind 35% | Gusts 20% | Precip 15% | Cloud 10% | Temp 10% | Direction 10%<br>
        Sailing hours: 11 AM – 5 PM | Ideal wind: 10–15 mph from W/NW<br>
        Location: {LOCATION_NAME} ({LATITUDE}, {LONGITUDE})<br>
        Data: <a href="https://open-meteo.com">Open-Meteo.com</a>
    </p>
</body>
</html>"""

    return plain, html


def load_email_config():
    """Load SMTP config from environment variables. Returns dict or raises."""
    required = ["SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "EMAIL_FROM", "EMAIL_TO"]
    config = {}
    missing = []
    for key in required:
        val = os.environ.get(key)
        if not val:
            missing.append(key)
        else:
            config[key] = val

    if missing:
        raise ValueError(
            f"Missing email config environment variables: {', '.join(missing)}\n"
            "Copy .env.example to .env and fill in your SMTP settings."
        )

    config["SMTP_PORT"] = int(config["SMTP_PORT"])
    return config


def send_email(subject, plain, html, config):
    """Send multipart email via SMTP."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = config["EMAIL_FROM"]
    msg["To"] = config["EMAIL_TO"]
    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(html, "html"))

    port = config["SMTP_PORT"]
    if port == 465:
        server = smtplib.SMTP_SSL(config["SMTP_HOST"], port, timeout=15)
    else:
        server = smtplib.SMTP(config["SMTP_HOST"], port, timeout=15)
        server.starttls()

    try:
        server.login(config["SMTP_USER"], config["SMTP_PASSWORD"])
        server.sendmail(config["EMAIL_FROM"], config["EMAIL_TO"].split(","), msg.as_string())
    finally:
        server.quit()


def generate_report():
    """Fetch forecast, score days, and return (plain_text, html) tuple."""
    data = fetch_forecast()
    days = parse_forecast(data)
    if not days:
        raise RuntimeError("No forecast data available")
    scored_days = [score_day(day) for day in days]
    return format_report(scored_days)


def main():
    parser = argparse.ArgumentParser(description="Fern Ridge Sailing Forecast")
    parser.add_argument("--no-email", action="store_true", help="Print report to stdout instead of emailing")
    args = parser.parse_args()

    load_dotenv()

    try:
        print("Fetching forecast data...")
        data = fetch_forecast()
    except requests.RequestException as e:
        print(f"Error fetching forecast: {e}", file=sys.stderr)
        sys.exit(1)

    days = parse_forecast(data)
    if not days:
        print("Error: no forecast data available.", file=sys.stderr)
        sys.exit(1)

    scored_days = [score_day(day) for day in days]
    plain, html = format_report(scored_days)

    now = datetime.now()
    subject = f"Sailing Forecast — {LOCATION_NAME} — {now.strftime('%b %d, %Y')}"

    if args.no_email:
        print(plain)
        return

    try:
        config = load_email_config()
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        send_email(subject, plain, html, config)
        print(f"Forecast sent to {config['EMAIL_TO']}")
    except smtplib.SMTPException as e:
        print(f"Error sending email: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
