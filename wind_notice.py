#!/usr/bin/env python3
"""Fern Ridge Sailing Forecast - 7-day forecast scored for sailing quality."""

import argparse
import hashlib
import logging
import os
import random
import sys
import time
from datetime import datetime

import boto3
import requests
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

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
WEIGHT_GUSTS = 0.10
WEIGHT_PRECIP = 0.20
WEIGHT_TEMP = 0.15
WEIGHT_CLOUD = 0.05
WEIGHT_DIRECTION = 0.15

# Rating thresholds
RATINGS = [
    (80, "Excellent"),
    (65, "Good"),
    (50, "Fair"),
    (35, "Poor"),
    (0, "Unfavorable"),
]


def fetch_forecast(max_attempts=20, base_delay=1.0, max_delay=30.0):
    """Fetch 7-day hourly forecast from Open-Meteo with exponential backoff.

    Retries transient failures (connection errors, timeouts, 5xx, 429) up to
    max_attempts times. 4xx errors other than 429 are raised immediately since
    retrying won't help. Backoff is base_delay * 2**attempt + small jitter,
    clamped to max_delay.
    """
    params = {
        "latitude": LATITUDE,
        "longitude": LONGITUDE,
        "hourly": "wind_speed_10m,wind_gusts_10m,wind_direction_10m,temperature_2m,precipitation,cloud_cover,weather_code",
        "wind_speed_unit": "mph",
        "temperature_unit": "fahrenheit",
        "precipitation_unit": "inch",
        "timezone": "auto",
        "forecast_days": 7,
    }
    last_exc = None
    for attempt in range(max_attempts):
        try:
            resp = requests.get(API_URL, params=params, timeout=15)
            if resp.status_code == 429 or resp.status_code >= 500:
                resp.raise_for_status()
            resp.raise_for_status()
            return resp.json()
        except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as exc:
            # Don't retry on non-retryable HTTP errors (4xx except 429).
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status is not None and 400 <= status < 500 and status != 429:
                raise
            last_exc = exc
            if attempt == max_attempts - 1:
                break
            delay = min(max_delay, base_delay * (2 ** attempt)) + random.uniform(0, base_delay)
            logger.warning(
                "Open-Meteo request failed (attempt %d/%d): %s — retrying in %.1fs",
                attempt + 1, max_attempts, exc, delay,
            )
            time.sleep(delay)
    raise last_exc


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
                "weather_codes": [],
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
            day["weather_codes"].append(hourly["weather_code"][i] or 0)

    return [days[k] for k in sorted(days)]


def _exp_tail(distance, scale):
    """Exponential decay tail: e^(-distance/scale)."""
    import math
    return math.exp(-distance / scale)


def score_wind(speeds):
    """Score wind speed 0.0-1.0. Ideal: 10-15 mph with long tails."""
    avg = sum(speeds) / len(speeds)
    if 10 <= avg <= 15:
        return 1.0
    elif avg < 10:
        return _exp_tail(10 - avg, 0.8)
    else:
        return _exp_tail(avg - 15, 4)


def score_gusts(speeds, gusts):
    """Score gusts 0.0-1.0. Ideal: 12-18 mph with long tails."""
    max_gust = max(gusts)
    if 12 <= max_gust <= 18:
        return 1.0
    elif max_gust < 12:
        return _exp_tail(12 - max_gust, 4)
    else:
        return _exp_tail(max_gust - 18, 5)


def score_precipitation(precips):
    """Score precipitation 0.0-1.0. Exponential decay from 0."""
    total = sum(precips)
    if total < 0.01:
        return 1.0
    else:
        return _exp_tail(total, 0.1)


def score_cloud_cover(clouds):
    """Score cloud cover 0.0-1.0. Partly cloudy (30-70%) is ideal with long tails."""
    avg = sum(clouds) / len(clouds)
    if 30 <= avg <= 70:
        return 1.0
    elif avg < 30:
        return _exp_tail(30 - avg, 15)
    else:
        return _exp_tail(avg - 70, 15)


def score_temperature(temps):
    """Score temperature 0.0-1.0. Ideal: 75-95F with long tails."""
    avg = sum(temps) / len(temps)
    if 75 <= avg <= 95:
        return 1.0
    elif avg < 75:
        return _exp_tail(75 - avg, 3)
    else:
        return _exp_tail(avg - 95, 8)


def score_direction(directions):
    """Score wind direction 0.0-1.0. N preferred with smooth cosine falloff."""
    import math
    scores = []
    for d in directions:
        diff = abs(d)
        if diff > 180:
            diff = 360 - diff
        # Cosine decay: 1.0 at N, ~0.07 at S
        scores.append(0.5 * (1 + math.cos(math.radians(diff))))
    return sum(scores) / len(scores)


def compass_direction(degrees):
    """Convert degrees to compass direction string."""
    dirs = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
            "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
    idx = round(degrees / 22.5) % 16
    return dirs[idx]


def dominant_weather_code(codes):
    """Return the most severe weather code (highest value)."""
    return max(codes) if codes else 0


def score_day(day):
    """Compute weighted composite score for a day. Returns 0-100."""
    ws = score_wind(day["wind_speeds"])
    gs = score_gusts(day["wind_speeds"], day["wind_gusts"])
    ps = score_precipitation(day["precipitations"])
    cs = score_cloud_cover(day["cloud_cover"])
    ts = score_temperature(day["temperatures"])
    ds = score_direction(day["wind_directions"])

    composite = (
        ws * WEIGHT_WIND
        + gs * WEIGHT_GUSTS
        + ps * WEIGHT_PRECIP
        + cs * WEIGHT_CLOUD
        + ts * WEIGHT_TEMP
        + ds * WEIGHT_DIRECTION
    )

    composite = composite ** 1.5
    day["score"] = round(composite * 100)
    day["rating"] = next(label for threshold, label in RATINGS if day["score"] >= threshold)
    day["wind_avg"] = sum(day["wind_speeds"]) / len(day["wind_speeds"])
    day["gust_max"] = max(day["wind_gusts"])
    day["temp_avg"] = sum(day["temperatures"]) / len(day["temperatures"])
    day["precip_total"] = sum(day["precipitations"])
    day["dir_avg"] = sum(day["wind_directions"]) / len(day["wind_directions"])
    day["cloud_avg"] = sum(day["cloud_cover"]) / len(day["cloud_cover"])
    day["weather_code"] = dominant_weather_code(day["weather_codes"])

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


def get_score_color(score):
    """Return a hex color for a component score (0-100)."""
    if score >= 80:
        return "#2e7d32"  # green
    elif score >= 60:
        return "#558b2f"  # light green
    elif score >= 40:
        return "#f9a825"  # amber
    elif score >= 20:
        return "#e65100"  # orange
    else:
        return "#b71c1c"  # red


def _compute_simulation_js_version():
    """Hash static/simulation.js for cache busting. Computed once at import."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "simulation.js")
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()[:12]
    except OSError:
        return ""


_SIMULATION_JS_VERSION = _compute_simulation_js_version()


def _simulation_block(cond_cloud, cond_wind, cond_gust, cond_precip, cond_temp, cond_weather_code):
    """Return the <canvas> + simulation bootstrap for inclusion in a page.

    The heavy simulation JS lives in static/simulation.js so browsers can
    cache it; here we just emit the canvas, inject initial weather values as
    a small global, and load the external script. The script URL carries a
    content-hash query string so browsers re-fetch it when the file changes.
    """
    src = "/static/simulation.js"
    if _SIMULATION_JS_VERSION:
        src += "?v=" + _SIMULATION_JS_VERSION
    return (
        '    <canvas id="bg-canvas" style="position:fixed;top:0;left:0;'
        'width:100%;height:100%;z-index:0"></canvas>\n'
        '    <script>window.WX_INITIAL = {'
        f'"cloud": {cond_cloud:.0f}, "wind": {cond_wind:.1f}, '
        f'"gust": {cond_gust:.1f}, "precip": {cond_precip:.2f}, '
        f'"temp": {cond_temp:.1f}, "weather_code": {cond_weather_code}'
        '};</script>\n'
        f'    <script src="{src}"></script>'
    )


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
            f"Cloud: {day['cloud_avg']:.0f}%  |  Rain: {day['precip_total']:.2f} in"
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
    lines.append("Sailing hours: 11 AM – 5 PM  |  Ideal wind: 10-15 mph from N")
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

        wc = get_score_color(cs['wind'])
        gc = get_score_color(cs['gusts'])
        dc = get_score_color(cs['direction'])
        tc = get_score_color(cs['temp'])
        cc = get_score_color(cs['cloud'])
        pc = get_score_color(cs['precip'])

        is_race_day = d.weekday() == 1 and 5 <= d.month <= 9
        day_margin = "18px 0 0 0" if is_race_day else "18px 0"
        day_pos = ";position:relative;z-index:2" if is_race_day else ""

        html_rows.append(f"""
    <div class="day-card" style="background:#fff;border-radius:8px;padding:14px 16px;margin:{day_margin};box-shadow:0 1px 3px rgba(0,0,0,0.12){day_pos}">
        <table style="width:100%;border-collapse:collapse;font-size:0.95em">
        <tr class="day-row">
            <td class="day-name" style="padding:7px 8px;font-weight:bold">{date_str}</td>
            <td class="day-rating" style="padding:7px 8px;text-align:center" title="Weighted composite score (power curve p=1.5)&#10;Wind:{cs['wind']} Gusts:{cs['gusts']} Precip:{cs['precip']} Cloud:{cs['cloud']} Temp:{cs['temp']} Dir:{cs['direction']}">
                <span style="background:{color};color:#fff;padding:3px 10px;border-radius:4px;font-weight:bold">
                    {day['score']} — {day['rating']}
                </span>
            </td>
            <td class="day-wind" style="padding:7px 8px;color:{wc}" title="Wind score: {cs['wind']}/100 (weight 35%)&#10;Avg: {day['wind_avg']:.1f} mph&#10;Ideal: 10–15 mph"><span>{day['wind_avg']:.0f} mph avg</span>, <span style="color:{gc}" title="Gust score: {cs['gusts']}/100 (weight 10%)&#10;Max gust: {day['gust_max']:.1f} mph&#10;Ideal: 12–18 mph">gusts {day['gust_max']:.0f} mph</span></td>
            <td class="day-dir" style="padding:7px 8px;color:{dc}" title="Direction score: {cs['direction']}/100 (weight 15%)&#10;Avg: {day['dir_avg']:.0f}° ({compass})&#10;Ideal: North (0°)">{compass}</td>
            <td class="day-temp" style="padding:7px 8px;color:{tc}" title="Temp score: {cs['temp']}/100 (weight 15%)&#10;Avg: {day['temp_avg']:.1f}°F&#10;Ideal: 75–95°F">{day['temp_avg']:.0f}°F</td>
            <td class="day-cloud" style="padding:7px 8px;color:{cc}" title="Cloud score: {cs['cloud']}/100 (weight 5%)&#10;Avg: {day['cloud_avg']:.1f}%&#10;Ideal: 30–70%">{day['cloud_avg']:.0f}%</td>
            <td class="day-rain" style="padding:7px 8px;color:{pc}" title="Precip score: {cs['precip']}/100 (weight 20%)&#10;Total: {day['precip_total']:.3f} in&#10;Ideal: &lt; 0.01 in">{day['precip_total']:.2f} in</td>
        </tr>
        <tr class="day-detail">
            <td colspan="7" style="padding:6px 8px 6px 8px;font-size:0.85em;color:#555">
                Wind: {" &nbsp;|&nbsp; ".join(f"<b>{w['name']}</b> {w['avg']:.0f} (g{w['gust_max']:.0f})" for w in day["wind_windows"])}
            </td>
        </tr>
        <tr class="day-detail">
            <td colspan="7" style="padding:6px 8px 6px 8px;font-size:0.85em;color:#666">
                Wind:{cs['wind']} | Gusts:{cs['gusts']} | Precip:{cs['precip']} | Cloud:{cs['cloud']} | Temp:{cs['temp']} | Dir:{cs['direction']}
            </td>
        </tr>
        </table>
    </div>""")

        if is_race_day:
            html_rows.append("""
    <a href="https://www.facebook.com/groups/132672990250578/" target="_blank" rel="noopener" style="text-decoration:none;color:inherit;display:block;margin:-26px 6px 18px 6px;position:relative;z-index:1">
    <div style="background:linear-gradient(135deg,#fff3e0,#ffcc80);padding:36px 16px 12px 16px;border-radius:8px;box-shadow:0 1px 3px rgba(0,0,0,0.12)">
        <strong>Tuesday night Beer Can Race</strong> at Fern Ridge — join the Facebook group for details →
    </div>
    </a>""")

    today = scored_days[0]
    cond_cloud = today["cloud_avg"]
    cond_wind = today["wind_avg"]
    cond_gust = today["gust_max"]
    cond_precip = today["precip_total"]
    cond_temp = today["temp_avg"]
    cond_weather_code = today["weather_code"]

    # Body background matches front wave (waterColors[2]) for seamless appearance
    wc = cond_weather_code
    if wc >= 95:
        body_bg = '#1a2327'  # storm
    elif 71 <= wc <= 77 or 85 <= wc <= 86:
        body_bg = '#263238'  # snow
    elif wc in (45, 48):
        body_bg = '#1d4d9c'  # fog (water tinted by fog overlay)
    elif wc >= 51:
        body_bg = '#1a2327'  # rain/drizzle/freezing rain
    elif cond_precip > 0.1:
        body_bg = '#1a2327'
    else:
        body_bg = '#0a3d91'

    best_date_str = best["date"].strftime("%A, %b %d")
    simulation_block = _simulation_block(cond_cloud, cond_wind, cond_gust, cond_precip, cond_temp, cond_weather_code)
    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Fern Ridge Sailing Forecast — {now.strftime('%b %d, %Y')}</title>
<meta name="description" content="Best day: {best_date_str} — Score {best['score']} ({best['rating']}). Wind {cond_wind:.0f} mph, gusts {cond_gust:.0f} mph, {cond_cloud:.0f}% cloud cover.">
<meta name="author" content="Ryan R. Olds">
<link rel="canonical" href="https://www.fernridgewind.com/">
<meta property="og:title" content="Fern Ridge Sailing Forecast — {now.strftime('%b %d, %Y')}">
<meta property="og:description" content="Best day: {best_date_str} — Score {best['score']} ({best['rating']}). Wind {cond_wind:.0f} mph, gusts {cond_gust:.0f} mph, {cond_cloud:.0f}% cloud cover.">
<meta property="og:type" content="website">
<meta property="og:locale" content="en_US">
<meta property="og:site_name" content="Fern Ridge Sailing Forecast">
<meta property="og:url" content="https://www.fernridgewind.com/">
<meta property="og:image" content="https://www.fernridgewind.com/static/og-image.png">
<meta property="og:image:secure_url" content="https://www.fernridgewind.com/static/og-image.png">
<meta property="og:image:type" content="image/png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:image:alt" content="Sailboat on Fern Ridge reservoir under a sunny sky with clouds">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="Fern Ridge Sailing Forecast — {now.strftime('%b %d, %Y')}">
<meta name="twitter:description" content="Best day: {best_date_str} — Score {best['score']} ({best['rating']}). Wind {cond_wind:.0f} mph, gusts {cond_gust:.0f} mph, {cond_cloud:.0f}% cloud cover.">
<meta name="twitter:image" content="https://www.fernridgewind.com/static/og-image.png">
<meta name="twitter:image:alt" content="Sailboat on Fern Ridge reservoir under a sunny sky with clouds">
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><path d='M0 80 Q15 75 30 80 Q45 85 60 80 Q75 75 90 80 Q95 82 100 80 L100 100 L0 100Z' fill='%231565c0'/><path d='M0 88 Q20 83 40 88 Q60 93 80 88 Q90 85 100 88 L100 100 L0 100Z' fill='%230d47a1'/><path d='M50 10 L50 80 L20 80 Z' fill='%232196f3'/><path d='M50 20 L50 70 L75 70 Z' fill='%2364b5f6'/><path d='M15 82 L85 82 Q90 90 80 90 L20 90 Q10 90 15 82Z' fill='%23e53935'/></svg>">
<style>
  @media screen and (max-width: 600px) {{
    body {{ font-size: 16px !important; }}
    h2 {{ font-size: 1.3em; }}

    .day-card table {{ width: 100% !important; }}

    .day-card {{ margin: 18px 0 !important; }}

    .day-row {{
      display: flex;
      flex-wrap: wrap;
      position: relative;
      padding: 4px 0 10px 0;
    }}
    .day-row td {{
      display: block;
      padding: 5px 0 !important;
      text-align: left !important;
    }}
    .day-name {{
      width: 100%;
      font-size: 1.15em !important;
      padding: 0 0 6px 0 !important;
    }}
    .day-rating {{
      position: absolute;
      top: 4px;
      right: 0;
      padding: 0 !important;
    }}
    .day-wind {{
      width: 100%;
      margin-top: 12px !important;
      padding: 6px 0 5px 0 !important;
      order: 1;
    }}
    .day-temp {{ width: 50%; padding: 5px 0 !important; order: 2; }}
    .day-cloud {{ width: 50%; padding: 5px 0 !important; order: 3; }}
    .day-dir {{ width: 50%; padding: 5px 0 !important; order: 4; }}
    .day-rain {{ width: 50%; padding: 5px 0 !important; order: 5; }}
    .day-wind::before {{ content: "Wind: "; font-weight: bold; color: #555; }}
    .day-dir::before {{ content: "Dir: "; font-weight: bold; color: #555; }}
    .day-temp::before {{ content: "Temp: "; font-weight: bold; color: #555; }}
    .day-cloud::before {{ content: "Cloud: "; font-weight: bold; color: #555; }}
    .day-rain::before {{ content: "Rain: "; font-weight: bold; color: #555; }}

    .day-detail td {{
      display: block;
      padding: 5px 0 !important;
      font-size: 0.85em !important;
    }}
  }}
</style>
</head>
<body style="font-family:Arial,Helvetica,sans-serif;margin:0;color:#333;background:{body_bg}">
    {simulation_block}
    <div style="position:relative;z-index:1;max-width:700px;margin:0 auto;padding:0 12px">
    <div style="text-align:center;padding:28px 0 176px 0">
        <h2 style="color:white;margin:0 0 6px 0;text-shadow:0 2px 8px rgba(0,0,0,0.5), 0 1px 3px rgba(0,0,0,0.3)">Fern Ridge Sailing Forecast</h2>
        <p style="color:rgba(255,255,255,0.95);margin:0;text-shadow:0 2px 8px rgba(0,0,0,0.6), 0 1px 3px rgba(0,0,0,0.4), 0 0 12px rgba(0,0,0,0.3)">{now.strftime('%A, %B %d, %Y')}</p>
    </div>

    <div style="background:linear-gradient(135deg,#fff8e1,#ffecb3);padding:12px 16px;border-radius:8px;margin:18px 0;box-shadow:0 1px 3px rgba(0,0,0,0.12)">
        <strong>Best day:</strong> {best_date_str}
        — Score {best['score']} ({best['rating']})
    </div>

    <a href="https://rec.eugene-or.gov/OR/city-of-eugene-or/catalog/index?filter=&category%5B11898%5D=1&" target="_blank" rel="noopener" style="text-decoration:none;color:inherit;display:block;margin:18px 0">
    <div style="background:linear-gradient(135deg,#e3f2fd,#bbdefb);padding:12px 16px;border-radius:8px;box-shadow:0 1px 3px rgba(0,0,0,0.12)">
        <strong>New to sailing?</strong> Take lessons with Eugene Parks &amp; Rec →
    </div>
    </a>

    <div class="forecast-wrap">
        {"".join(html_rows)}
    </div>

    <p style="font-size:0.8em;color:rgba(255,255,255,0.35);margin-top:20px;padding-bottom:4px">
        Scoring: Wind 35% | Gusts 20% | Precip 15% | Cloud 10% | Temp 10% | Direction 10%<br>
        Sailing hours: 11 AM – 5 PM | Ideal wind: 10–15 mph from N<br>
        Location: {LOCATION_NAME} ({LATITUDE}, {LONGITUDE})<br>
        Data: <a href="https://open-meteo.com" style="color:rgba(255,255,255,0.45)">Open-Meteo.com</a>
    </p>
    <p id="wx-debug" style="font-size:0.7em;color:rgba(255,255,255,0.35);margin:0;padding-bottom:8px;word-break:break-all"></p>
    <p style="font-size:0.7em;color:rgba(255,255,255,0.4);margin:0;padding-bottom:16px">&copy; {now.year} Ryan R. Olds — <a href="https://github.com/ryanrolds" style="color:rgba(255,255,255,0.5)">GitHub</a> · <a href="https://www.linkedin.com/in/ryanrolds/" style="color:rgba(255,255,255,0.5)">LinkedIn</a></p>
    </div>
</body>
</html>"""

    return plain, html


def load_email_config():
    """Load email config from environment variables. Returns dict or raises."""
    required = ["AWS_REGION", "EMAIL_FROM", "EMAIL_TO"]
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
            "Set AWS_REGION, EMAIL_FROM, and EMAIL_TO. AWS credentials are loaded\n"
            "from the standard chain (env vars, ~/.aws, or IAM role)."
        )

    return config


def send_email(subject, plain, html, config):
    """Send email via AWS SES."""
    ses = boto3.client("ses", region_name=config["AWS_REGION"])
    ses.send_email(
        Source=config["EMAIL_FROM"],
        Destination={"ToAddresses": [a.strip() for a in config["EMAIL_TO"].split(",")]},
        Message={
            "Subject": {"Charset": "UTF-8", "Data": subject},
            "Body": {
                "Text": {"Charset": "UTF-8", "Data": plain},
                "Html": {"Charset": "UTF-8", "Data": html},
            },
        },
    )



def generate_report():
    """Fetch forecast, score days, and return (plain_text, html, scored_days) tuple."""
    data = fetch_forecast()
    days = parse_forecast(data)
    if not days:
        raise RuntimeError("No forecast data available")
    scored_days = [score_day(day) for day in days]
    plain, html = format_report(scored_days)
    return plain, html, scored_days


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
    except (BotoCoreError, ClientError) as e:
        print(f"Error sending email: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
