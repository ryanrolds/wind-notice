#!/usr/bin/env python3
"""Fern Ridge Sailing Forecast - 7-day forecast scored for sailing quality."""

import argparse
import os
import sys
from datetime import datetime

import boto3
import requests
from botocore.exceptions import BotoCoreError, ClientError
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


def fetch_forecast():
    """Fetch 7-day hourly forecast from Open-Meteo."""
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

        html_rows.append(f"""
    <div class="day-card" style="background:#fff;border-radius:8px;padding:14px 16px;margin:18px 0;box-shadow:0 1px 3px rgba(0,0,0,0.12)">
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
    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta property="og:title" content="Fern Ridge Sailing Forecast — {now.strftime('%b %d, %Y')}">
<meta property="og:description" content="Best day: {best_date_str} — Score {best['score']} ({best['rating']}). Wind {cond_wind:.0f} mph, gusts {cond_gust:.0f} mph, {cond_cloud:.0f}% cloud cover.">
<meta property="og:type" content="website">
<meta property="og:locale" content="en_US">
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
    <canvas id="bg-canvas" style="position:fixed;top:0;left:0;width:100%;height:100%;z-index:0"></canvas>
    <div style="position:relative;z-index:1;max-width:700px;margin:0 auto;padding:0 12px">
    <div style="text-align:center;padding:28px 0 176px 0">
        <h2 style="color:white;margin:0 0 6px 0;text-shadow:0 2px 8px rgba(0,0,0,0.5), 0 1px 3px rgba(0,0,0,0.3)">Fern Ridge Sailing Forecast</h2>
        <p style="color:rgba(255,255,255,0.95);margin:0;text-shadow:0 2px 8px rgba(0,0,0,0.6), 0 1px 3px rgba(0,0,0,0.4), 0 0 12px rgba(0,0,0,0.3)">{now.strftime('%A, %B %d, %Y')}</p>
    </div>
    <script>
    (function() {{
      var wx = {{ cloud: {cond_cloud:.0f}, wind: {cond_wind:.1f}, gust: {cond_gust:.1f}, precip: {cond_precip:.2f}, temp: {cond_temp:.1f}, weather_code: {cond_weather_code} }};
      var params = new URLSearchParams(window.location.search);
      if (params.get('cloud') !== null) {{
        // URL parameter override mode
        wx = {{ cloud: parseFloat(params.get('cloud'))||0, wind: parseFloat(params.get('wind'))||0, gust: parseFloat(params.get('gust'))||0, precip: parseFloat(params.get('precip'))||0, temp: parseFloat(params.get('temp'))||60, weather_code: 0 }};
        wx.effectNames = params.get('effects') || '';
      }} else if (params.get('random') === 'true') {{
        wx = {{ cloud: Math.random()*100, wind: Math.random()*30, gust: Math.random()*45, precip: Math.random() < 0.3 ? 0.1 + Math.random()*0.4 : 0, temp: 45+Math.random()*35, weather_code: 0 }};
        // Weighted multi-effect selection: 40% none, 30% one, 20% two, 10% three
        var roll = Math.random();
        var numEffects = roll < 0.4 ? 0 : roll < 0.7 ? 1 : roll < 0.9 ? 2 : 3;
        var effectPool = [95, 96, 71, 45, 51, 65, 66];
        // Shuffle pool
        for (var si = effectPool.length - 1; si > 0; si--) {{
          var sj = Math.floor(Math.random() * (si + 1));
          var tmp = effectPool[si]; effectPool[si] = effectPool[sj]; effectPool[sj] = tmp;
        }}
        wx.activeEffects = [];
        for (var ei = 0; ei < numEffects; ei++) wx.activeEffects.push(effectPool[ei]);
        if (numEffects > 0) wx.precip = Math.max(wx.precip, 0.1);
      }}

      // Derive active effects
      var activeEffects = [];
      if (wx.effectNames !== undefined) {{
        // URL parameter mode: effect names passed directly
        if (wx.effectNames) activeEffects = wx.effectNames.split(',');
      }} else if (wx.activeEffects) {{
        // Random mode: map codes to effect names
        var codeToEffects = function(code) {{
          var e = [];
          if (code === 95 || code === 96 || code === 99) e.push('lightning');
          if (code === 96 || code === 99) e.push('hail');
          if ((code >= 71 && code <= 77) || (code >= 85 && code <= 86)) e.push('snow');
          if (code === 45 || code === 48) e.push('fog');
          if (code >= 51 && code <= 57) e.push('drizzle');
          if (code === 65 || code === 82) e.push('heavyrain');
          if (code === 66 || code === 67) e.push('freezingrain');
          if (code === 61 || code === 63 || (code >= 80 && code <= 81)) e.push('rain');
          return e;
        }};
        for (var ae = 0; ae < wx.activeEffects.length; ae++) {{
          var effs = codeToEffects(wx.activeEffects[ae]);
          for (var ef = 0; ef < effs.length; ef++) {{
            if (activeEffects.indexOf(effs[ef]) === -1) activeEffects.push(effs[ef]);
          }}
        }}
      }} else {{
        // Normal mode: derive from single weather code
        var wc = wx.weather_code;
        if (wc === 95 || wc === 96 || wc === 99) activeEffects.push('lightning');
        if (wc === 96 || wc === 99) activeEffects.push('hail');
        if ((wc >= 71 && wc <= 77) || (wc >= 85 && wc <= 86)) activeEffects.push('snow');
        if (wc === 45 || wc === 48) activeEffects.push('fog');
        if (wc >= 51 && wc <= 57) activeEffects.push('drizzle');
        if (wc === 65 || wc === 82) activeEffects.push('heavyrain');
        if (wc === 66 || wc === 67) activeEffects.push('freezingrain');
        if (wc === 61 || wc === 63 || (wc >= 80 && wc <= 81)) activeEffects.push('rain');
      }}

      function hasEffect(name) {{ return activeEffects.indexOf(name) !== -1; }}
      var isStormy = hasEffect('lightning');
      var isSnowy = hasEffect('snow');
      var isFoggy = hasEffect('fog');
      var isRainy = hasEffect('rain') || hasEffect('heavyrain') || hasEffect('drizzle') || hasEffect('freezingrain') || wx.precip > 0.1;
      var isPrecip = isRainy || isSnowy || hasEffect('hail');

      var c = document.getElementById('bg-canvas');
      var ctx = c.getContext('2d');
      var dpr = window.devicePixelRatio || 1;
      var W, H;
      function resize() {{
        W = window.innerWidth;
        H = window.innerHeight;
        c.width = W * dpr;
        c.height = H * dpr;
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      }}
      resize();
      window.addEventListener('resize', resize);

      var cloudFrac = wx.cloud / 100;
      if (isStormy) cloudFrac = Math.max(cloudFrac, 0.85);
      if (isFoggy) cloudFrac = Math.max(cloudFrac, 0.6);
      var windFactor = Math.min(wx.wind / 20, 1);

      function makeCloud() {{
        var numPuffs = 3 + Math.floor(Math.random() * 4);
        if (cloudFrac > 0.7) numPuffs += 2;
        var puffs = [];
        for (var i = 0; i < numPuffs; i++) {{
          puffs.push([
            (i - numPuffs/2) * (18 + Math.random()*14),
            (Math.random() - 0.5) * 25,
            15 + Math.random() * 22 + (cloudFrac > 0.5 ? 5 : 0)
          ]);
        }}
        return {{
          x: Math.random() * 900 - 100,
          y: 10 + Math.random() * 90,
          s: 0.5 + Math.random() * 0.8 + cloudFrac * 0.3,
          speed: 3 + Math.random() * 8 + windFactor * 10,
          puffs: puffs,
          color: ''
        }};
      }}
      var numClouds = cloudFrac < 0.05 ? 0 : Math.round(cloudFrac * 10 + Math.random() * 2);
      var clouds = [];
      for (var ci = 0; ci < numClouds; ci++) clouds.push(makeCloud());
      clouds.sort(function(a, b) {{ return a.y - b.y; }});
      for (var ci = 0; ci < clouds.length; ci++) {{
        var frac = clouds.length > 1 ? ci / (clouds.length - 1) : 1;
        var lo = isPrecip ? 170 : (cloudFrac > 0.7 ? 205 : 230);
        var hi = isPrecip ? 210 : (cloudFrac > 0.7 ? 240 : 255);
        var shade = Math.round(lo + frac * (hi - lo));
        clouds[ci].color = 'rgb(' + shade + ',' + shade + ',' + shade + ')';
      }}

      // --- Rain init ---
      var rainDrops = [];
      function initRain() {{
        if (!hasEffect('rain') && !isRainy) return;
        if (hasEffect('heavyrain') || hasEffect('drizzle') || hasEffect('freezingrain')) return;
        var numDrops = Math.round(80 + wx.precip * 400);
        for (var ri = 0; ri < numDrops; ri++) {{
          rainDrops.push({{ x: Math.random() * W, y: Math.random() * H, len: 8 + Math.random() * 14, speed: 300 + Math.random() * 200 }});
        }}
      }}
      initRain();

      // --- Drizzle init ---
      var drizzleDrops = [];
      function initDrizzle() {{
        if (!hasEffect('drizzle')) return;
        var numDrops = Math.round(60 + wx.precip * 200);
        for (var i = 0; i < numDrops; i++) {{
          drizzleDrops.push({{ x: Math.random() * W, y: Math.random() * H, len: 4 + Math.random() * 6, speed: 150 + Math.random() * 100 }});
        }}
      }}
      initDrizzle();

      // --- Heavy rain init ---
      var heavyDrops = [];
      var splashes = [];
      function initHeavyRain() {{
        if (!hasEffect('heavyrain')) return;
        var numDrops = Math.round(200 + wx.precip * 600);
        for (var i = 0; i < numDrops; i++) {{
          heavyDrops.push({{ x: Math.random() * W, y: Math.random() * H, len: 14 + Math.random() * 18, speed: 450 + Math.random() * 250 }});
        }}
      }}
      initHeavyRain();

      // --- Freezing rain init ---
      var freezeDrops = [];
      function initFreezingRain() {{
        if (!hasEffect('freezingrain')) return;
        var numDrops = Math.round(80 + wx.precip * 400);
        for (var i = 0; i < numDrops; i++) {{
          freezeDrops.push({{ x: Math.random() * W, y: Math.random() * H, len: 8 + Math.random() * 14, speed: 300 + Math.random() * 200 }});
        }}
      }}
      initFreezingRain();

      // --- Snow init ---
      var snowFlakes = [];
      function initSnow() {{
        if (!hasEffect('snow')) return;
        var numFlakes = Math.round(60 + Math.random() * 40);
        for (var i = 0; i < numFlakes; i++) {{
          snowFlakes.push({{ x: Math.random() * W, y: Math.random() * 220 - 20, r: 1.5 + Math.random() * 3, speed: 30 + Math.random() * 40, drift: (Math.random() - 0.3) * 0.8 }});
        }}
      }}
      initSnow();

      // --- Hail init ---
      var hailStones = [];
      function initHail() {{
        if (!hasEffect('hail')) return;
        var numStones = Math.round(30 + Math.random() * 20);
        for (var i = 0; i < numStones; i++) {{
          hailStones.push({{ x: Math.random() * W, y: Math.random() * 210 - 15, r: 2 + Math.random() * 3, vy: 200 + Math.random() * 150, vx: 0, bouncing: false }});
        }}
      }}
      initHail();

      // --- Lightning state ---
      var lightningFlash = 0;
      var lightningBolts = [];
      var lightningTimer = 3 + Math.random() * 5;
      function makeBolt() {{
        var x = 50 + Math.random() * (W ? W - 100 : 600);
        var yStart = 20 + Math.random() * 40;
        var yEnd = 170 + Math.random() * 30;
        var segments = [];
        var cx = x, cy = yStart;
        var steps = 6 + Math.floor(Math.random() * 5);
        for (var i = 0; i < steps; i++) {{
          var nx = cx + (Math.random() - 0.5) * 40;
          var ny = cy + (yEnd - yStart) / steps;
          segments.push([cx, cy, nx, ny]);
          cx = nx; cy = ny;
        }}
        return {{ segments: segments, life: 0.3, age: 0 }};
      }}

      // --- Fog state ---
      var fogOffset1 = Math.random() * 1000;
      var fogOffset2 = Math.random() * 1000;

      function lerpHex(a, b, t) {{
        var ar = parseInt(a.slice(1,3),16), ag = parseInt(a.slice(3,5),16), ab = parseInt(a.slice(5,7),16);
        var br = parseInt(b.slice(1,3),16), bg = parseInt(b.slice(3,5),16), bb = parseInt(b.slice(5,7),16);
        var r = Math.round(ar+(br-ar)*t), g = Math.round(ag+(bg-ag)*t), bl = Math.round(ab+(bb-ab)*t);
        return '#'+((1<<24)|(r<<16)|(g<<8)|bl).toString(16).slice(1);
      }}

      var t = 0;
      function draw() {{
        t += 0.016;
        ctx.clearRect(0, 0, W, H);

        // --- Sky gradient ---
        var sky = ctx.createLinearGradient(0, 0, 0, H);
        if (isStormy) {{
          sky.addColorStop(0, '#546e7a');
          sky.addColorStop(0.4, '#78909c');
          sky.addColorStop(1, '#90a4ae');
        }} else if (isSnowy) {{
          sky.addColorStop(0, '#b0bec5');
          sky.addColorStop(0.3, '#cfd8dc');
          sky.addColorStop(0.6, '#e0e0e0');
          sky.addColorStop(1, '#eceff1');
        }} else if (isFoggy) {{
          sky.addColorStop(0, '#90a4ae');
          sky.addColorStop(0.3, '#b0bec5');
          sky.addColorStop(0.6, '#cfd8dc');
          sky.addColorStop(1, '#e0e0e0');
        }} else if (isPrecip) {{
          sky.addColorStop(0, '#78909c');
          sky.addColorStop(0.4, '#90a4ae');
          sky.addColorStop(1, '#b0bec5');
        }} else {{
          var clearSky = ['#1976d2','#42a5f5','#90caf9','#bbdefb'];
          var partSky  = ['#64b5f6','#90caf9','#bbdefb','#e3f2fd'];
          var overSky  = ['#90a4ae','#b0bec5','#cfd8dc','#eceff1'];
          var skyStops = [0, 0.3, 0.6, 1];
          var palA, palB, blend;
          if (cloudFrac <= 0.5) {{
            palA = clearSky; palB = partSky;
            blend = cloudFrac / 0.5;
          }} else {{
            palA = partSky; palB = overSky;
            blend = (cloudFrac - 0.5) / 0.5;
          }}
          for (var i = 0; i < 4; i++) {{
            sky.addColorStop(skyStops[i], lerpHex(palA[i], palB[i], blend));
          }}
        }}
        ctx.fillStyle = sky;
        ctx.fillRect(0, 0, W, H);

        // --- Lightning flash overlay ---
        if (lightningFlash > 0) {{
          ctx.fillStyle = 'rgba(255,255,255,' + (lightningFlash * 0.6) + ')';
          ctx.fillRect(0, 0, W, H);
          lightningFlash -= 0.016 * 4;
          if (lightningFlash < 0) lightningFlash = 0;
        }}

        // --- Sun ---
        if (cloudFrac < 0.7 && !isStormy && !isFoggy) {{
          var sunAlpha = Math.min(1, (1 - cloudFrac / 0.7));
          ctx.save();
          // Glow
          ctx.beginPath();
          ctx.arc(W * 0.82, 55, 45, 0, Math.PI * 2);
          ctx.fillStyle = 'rgba(255,235,130,' + (sunAlpha * 0.15) + ')';
          ctx.fill();
          // Disc
          ctx.beginPath();
          ctx.arc(W * 0.82, 55, 22, 0, Math.PI * 2);
          ctx.fillStyle = 'rgba(255,220,80,' + sunAlpha + ')';
          ctx.shadowColor = 'rgba(255,200,50,' + (sunAlpha * 0.8) + ')';
          ctx.shadowBlur = 30;
          ctx.fill();
          ctx.restore();
        }}

        // --- Clouds ---
        for (var i = 0; i < clouds.length; i++) {{
          var cl = clouds[i];
          cl.x += cl.speed * 0.016;
          if (cl.x > W + 100) cl.x = -140 * cl.s;
          ctx.save();
          ctx.translate(cl.x, cl.y);
          ctx.scale(cl.s, cl.s);
          ctx.fillStyle = cl.color;
          for (var p = 0; p < cl.puffs.length; p++) {{
            ctx.beginPath();
            ctx.arc(cl.puffs[p][0], cl.puffs[p][1], cl.puffs[p][2], 0, Math.PI*2);
            ctx.fill();
          }}
          ctx.restore();
        }}

        // --- Lightning bolts ---
        if (hasEffect('lightning')) {{
          lightningTimer -= 0.016;
          if (lightningTimer <= 0) {{
            lightningBolts.push(makeBolt());
            lightningFlash = 1;
            lightningTimer = 3 + Math.random() * 5;
          }}
          for (var li = lightningBolts.length - 1; li >= 0; li--) {{
            var bolt = lightningBolts[li];
            bolt.age += 0.016;
            if (bolt.age > bolt.life) {{ lightningBolts.splice(li, 1); continue; }}
            var alpha = 1 - bolt.age / bolt.life;
            ctx.save();
            ctx.shadowColor = 'rgba(200,220,255,0.8)';
            ctx.shadowBlur = 15;
            ctx.strokeStyle = 'rgba(255,255,255,' + alpha + ')';
            ctx.lineWidth = 2.5;
            ctx.beginPath();
            for (var seg = 0; seg < bolt.segments.length; seg++) {{
              var s = bolt.segments[seg];
              ctx.moveTo(s[0], s[1]);
              ctx.lineTo(s[2], s[3]);
            }}
            ctx.stroke();
            ctx.restore();
          }}
        }}

        // --- Fog background layer (behind waves) ---
        if (hasEffect('fog')) {{
          fogOffset1 += 0.3;
          fogOffset2 += 0.5;
          ctx.save();
          var fogGrad = ctx.createLinearGradient(0, 100, 0, 220);
          fogGrad.addColorStop(0, 'rgba(200,210,220,0)');
          fogGrad.addColorStop(0.4, 'rgba(200,210,220,0.35)');
          fogGrad.addColorStop(1, 'rgba(200,210,220,0.5)');
          ctx.fillStyle = fogGrad;
          ctx.fillRect(0, 100, W, 120);
          ctx.restore();
        }}

        // --- Wave helpers ---
        var waveAmpScale = 0.5 + windFactor * 1.0;
        var waveSpeedScale = 0.6 + windFactor * 0.8;
        var waterColors;
        if (isSnowy) {{
          waterColors = ['#455a64','#37474f','#263238'];
        }} else if (isPrecip || isStormy) {{
          waterColors = ['#37474f','#263238','#1a2327'];
        }} else {{
          waterColors = ['#1565c0','#0d47a1','#0a3d91'];
        }}
        function drawWaveLayer(idx) {{
          var amp = (8 - idx * 1.5) * waveAmpScale;
          var yBase = 185 + idx * 12;
          var speed = (1 + idx * 0.4) * waveSpeedScale;
          ctx.fillStyle = waterColors[idx];
          ctx.beginPath();
          ctx.moveTo(0, H);
          var phaseOff = idx * 2.1;
          for (var x = 0; x <= W; x += 4) {{
            ctx.lineTo(x, yBase + Math.sin(x*0.015 + t*speed + phaseOff)*amp + Math.sin(x*0.008 + t*speed*0.6 + phaseOff)*amp*0.5);
          }}
          ctx.lineTo(W, H);
          ctx.closePath();
          ctx.fill();
        }}

        function waveY(x) {{
          var a = 6.5 * waveAmpScale;
          var s = 1.4 * waveSpeedScale;
          return 197 + Math.sin(x*0.015 + t*s + 2.1)*a + Math.sin(x*0.008 + t*s*0.6 + 2.1)*a*0.5;
        }}

        function drawBoat() {{
          var boatX = W * 0.75;
          var bob = Math.sin(t * 2.2) * 3;
          var boatY = waveY(boatX) - 23 + bob;
          var dx = 4;
          var tilt = Math.atan2(waveY(boatX + dx) - waveY(boatX - dx), dx * 2);
          ctx.save();
          ctx.translate(boatX, boatY);
          ctx.rotate(tilt);

          ctx.strokeStyle = 'rgba(255,255,255,0.9)';
          ctx.lineWidth = 2;
          ctx.beginPath();
          ctx.moveTo(0, -75);
          ctx.lineTo(0, 18);
          ctx.stroke();

          ctx.fillStyle = 'rgba(255,255,255,0.92)';
          ctx.beginPath();
          ctx.moveTo(0, -72);
          ctx.lineTo(0, 15);
          ctx.lineTo(-34, 15);
          ctx.closePath();
          ctx.fill();

          ctx.fillStyle = 'rgba(255,255,255,0.7)';
          ctx.beginPath();
          ctx.moveTo(0, -58);
          ctx.lineTo(0, 10);
          ctx.lineTo(24, 10);
          ctx.closePath();
          ctx.fill();

          ctx.fillStyle = '#e53935';
          ctx.beginPath();
          ctx.moveTo(-36, 17);
          ctx.lineTo(36, 17);
          ctx.quadraticCurveTo(42, 30, 32, 33);
          ctx.lineTo(-32, 33);
          ctx.quadraticCurveTo(-42, 30, -36, 17);
          ctx.closePath();
          ctx.fill();
          ctx.restore();
        }}

        // --- Draw order: wave0, boat, wave1, precipitation, wave2, fog foreground ---
        drawWaveLayer(0);
        drawBoat();
        drawWaveLayer(1);

        var windAngle = windFactor * 2;

        // --- Draw rain ---
        if (rainDrops.length > 0) {{
          ctx.strokeStyle = 'rgba(200,210,220,0.4)';
          ctx.lineWidth = 1;
          for (var ri = 0; ri < rainDrops.length; ri++) {{
            var rd = rainDrops[ri];
            rd.y += rd.speed * 0.016;
            rd.x += windAngle * rd.speed * 0.008;
            if (rd.y > H) {{ rd.y = -rd.len; rd.x = Math.random() * W; }}
            if (rd.x > W) rd.x -= W;
            ctx.beginPath();
            ctx.moveTo(rd.x, rd.y);
            ctx.lineTo(rd.x + windAngle * rd.len * 0.3, rd.y + rd.len);
            ctx.stroke();
          }}
        }}

        // --- Draw drizzle ---
        if (drizzleDrops.length > 0) {{
          ctx.strokeStyle = 'rgba(200,210,220,0.25)';
          ctx.lineWidth = 0.5;
          for (var di = 0; di < drizzleDrops.length; di++) {{
            var dd = drizzleDrops[di];
            dd.y += dd.speed * 0.016;
            dd.x += windAngle * dd.speed * 0.005;
            if (dd.y > H) {{ dd.y = -dd.len; dd.x = Math.random() * W; }}
            if (dd.x > W) dd.x -= W;
            ctx.beginPath();
            ctx.moveTo(dd.x, dd.y);
            ctx.lineTo(dd.x + windAngle * dd.len * 0.2, dd.y + dd.len);
            ctx.stroke();
          }}
        }}

        // --- Draw heavy rain + splashes ---
        if (heavyDrops.length > 0) {{
          ctx.strokeStyle = 'rgba(200,210,220,0.5)';
          ctx.lineWidth = 2;
          for (var hi = 0; hi < heavyDrops.length; hi++) {{
            var hd = heavyDrops[hi];
            hd.y += hd.speed * 0.016;
            hd.x += windAngle * hd.speed * 0.008;
            var surfY = waveY(hd.x);
            if (hd.y > surfY) {{
              splashes.push({{ x: hd.x, y: surfY, r: 0, maxR: 4 + Math.random() * 4, speed: 30 + Math.random() * 20 }});
              hd.y = -hd.len; hd.x = Math.random() * W;
            }}
            if (hd.x > W) hd.x -= W;
            ctx.beginPath();
            ctx.moveTo(hd.x, hd.y);
            ctx.lineTo(hd.x + windAngle * hd.len * 0.3, hd.y + hd.len);
            ctx.stroke();
          }}
          // Draw splashes
          for (var si = splashes.length - 1; si >= 0; si--) {{
            var sp = splashes[si];
            sp.r += sp.speed * 0.016;
            if (sp.r > sp.maxR) {{ splashes.splice(si, 1); continue; }}
            var alpha = 1 - sp.r / sp.maxR;
            ctx.strokeStyle = 'rgba(200,210,220,' + (alpha * 0.5) + ')';
            ctx.lineWidth = 1;
            ctx.beginPath();
            ctx.arc(sp.x, sp.y, sp.r, Math.PI, 0);
            ctx.stroke();
          }}
        }}

        // --- Draw freezing rain ---
        if (freezeDrops.length > 0) {{
          ctx.strokeStyle = 'rgba(150,200,240,0.5)';
          ctx.lineWidth = 1;
          for (var fi = 0; fi < freezeDrops.length; fi++) {{
            var fd = freezeDrops[fi];
            fd.y += fd.speed * 0.016;
            fd.x += windAngle * fd.speed * 0.008;
            if (fd.y > H) {{ fd.y = -fd.len; fd.x = Math.random() * W; }}
            if (fd.x > W) fd.x -= W;
            ctx.beginPath();
            ctx.moveTo(fd.x, fd.y);
            ctx.lineTo(fd.x + windAngle * fd.len * 0.3, fd.y + fd.len);
            ctx.stroke();
          }}
        }}

        // --- Draw snow ---
        if (snowFlakes.length > 0) {{
          ctx.fillStyle = 'rgba(255,255,255,0.8)';
          for (var sni = 0; sni < snowFlakes.length; sni++) {{
            var sf = snowFlakes[sni];
            sf.y += sf.speed * 0.016;
            sf.x += sf.drift + windFactor * 0.5;
            var surfY = waveY(sf.x);
            if (sf.y > surfY) {{ sf.y = -5; sf.x = Math.random() * W; }}
            if (sf.x > W) sf.x -= W;
            if (sf.x < 0) sf.x += W;
            ctx.beginPath();
            ctx.arc(sf.x, sf.y, sf.r, 0, Math.PI * 2);
            ctx.fill();
          }}
        }}

        // --- Draw hail (bounces off boat, resets on waves) ---
        if (hailStones.length > 0) {{
          var boatX = W * 0.75;
          var boatBob = Math.sin(t * 2.2) * 3;
          var boatDeckY = waveY(boatX) - 23 + boatBob + 17;
          for (var hi2 = 0; hi2 < hailStones.length; hi2++) {{
            var hs = hailStones[hi2];
            if (hs.bouncing) {{
              hs.vy += 400 * 0.016; // gravity
              hs.y += hs.vy * 0.016;
              hs.x += hs.vx * 0.016;
              if (hs.y > H + 20) {{
                hs.x = Math.random() * W;
                hs.y = -5;
                hs.vy = 200 + Math.random() * 150;
                hs.vx = 0;
                hs.bouncing = false;
              }}
            }} else {{
              hs.y += hs.vy * 0.016;
              hs.x += windFactor * 1.5;
              // Check if hitting boat deck
              if (hs.x > boatX - 36 && hs.x < boatX + 36 && hs.y > boatDeckY) {{
                hs.bouncing = true;
                hs.vy = -(80 + Math.random() * 60);
                hs.vx = (Math.random() - 0.5) * 40;
                hs.y = boatDeckY;
              }} else {{
                var surfY = waveY(hs.x);
                if (hs.y > surfY) {{
                  hs.x = Math.random() * W;
                  hs.y = -5;
                  hs.vy = 200 + Math.random() * 150;
                  hs.vx = 0;
                }}
              }}
            }}
            if (hs.x > W) hs.x -= W;
            if (hs.x < 0) hs.x += W;
            ctx.fillStyle = 'rgba(220,230,240,0.85)';
            ctx.beginPath();
            ctx.arc(hs.x, hs.y, hs.r, 0, Math.PI * 2);
            ctx.fill();
          }}
        }}

        // --- Front wave ---
        drawWaveLayer(2);

        // --- Fog foreground overlay ---
        if (hasEffect('fog')) {{
          ctx.save();
          var fogFG = ctx.createLinearGradient(0, 140, 0, 220);
          fogFG.addColorStop(0, 'rgba(200,210,220,0)');
          fogFG.addColorStop(0.5, 'rgba(200,210,220,0.12)');
          fogFG.addColorStop(1, 'rgba(200,210,220,0.12)');
          ctx.fillStyle = fogFG;
          ctx.fillRect(0, 140, W, H - 140);
          ctx.restore();
        }}

        requestAnimationFrame(draw);
      }}
      draw();

      document.addEventListener('DOMContentLoaded', function() {{
        var dbg = document.getElementById('wx-debug');
        if (dbg) {{
          var qs = '?cloud=' + wx.cloud.toFixed(0) + '&wind=' + wx.wind.toFixed(1) + '&gust=' + wx.gust.toFixed(1) + '&precip=' + wx.precip.toFixed(2) + '&temp=' + wx.temp.toFixed(1);
          if (activeEffects.length) qs += '&effects=' + activeEffects.join(',');
          dbg.textContent = qs + ' ';
          var rlink = document.createElement('a');
          rlink.href = '?random=true';
          rlink.textContent = 'Randomize';
          rlink.style.color = 'rgba(255,255,255,0.45)';
          dbg.appendChild(rlink);
        }}
      }});
    }})();
    </script>

    <div style="background:linear-gradient(135deg,#fff8e1,#ffecb3);padding:12px 16px;border-radius:8px;margin:10px 0;box-shadow:0 1px 3px rgba(0,0,0,0.12)">
        <strong>Best day:</strong> {best_date_str}
        — Score {best['score']} ({best['rating']})
    </div>

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
