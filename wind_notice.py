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
WEIGHT_GUSTS = 0.20
WEIGHT_PRECIP = 0.15
WEIGHT_TEMP = 0.15
WEIGHT_CLOUD = 0.05
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
    """Score gusts 0.0-1.0. Ideal: below 20 mph. Above 25 = 0."""
    max_gust = max(gusts)
    if max_gust <= 20:
        return 1.0
    elif max_gust <= 25:
        return (25 - max_gust) / 5  # gradient 20-25
    else:
        return 0.0


def score_precipitation(precips):
    """Score precipitation 0.0-1.0. Light rain is OK, heavy rain is a dealbreaker."""
    total = sum(precips)
    if total < 0.05:
        return 1.0
    elif total < 0.15:
        return 0.7
    elif total < 0.25:
        return 0.3
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
    """Score temperature 0.0-1.0. Ideal: 75-95F. Below 60 or above 105 = 0."""
    avg = sum(temps) / len(temps)
    if avg < 60 or avg > 105:
        return 0.0
    elif 75 <= avg <= 95:
        return 1.0
    elif avg < 75:
        return (avg - 60) / 15  # gradient 60-75
    else:
        return (105 - avg) / 10  # gradient 95-105


def score_direction(directions):
    """Score wind direction 0.0-1.0. N preferred (best fetch on reservoir)."""
    # Ideal: 360/0 (N)
    ideal_center = 0  # North
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
    dealbreaker = (ws == 0.0 or ts == 0.0 or ps == 0.0)

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
    <div class="day-card" style="background:#fff;border-radius:8px;padding:12px 16px;margin:10px 0;box-shadow:0 1px 3px rgba(0,0,0,0.12)">
        <table style="width:100%;border-collapse:collapse;font-size:0.95em">
        <tr class="day-row">
            <td class="day-name" style="padding:4px 8px;font-weight:bold">{date_str}</td>
            <td class="day-rating" style="padding:4px 8px;text-align:center">
                <span style="background:{color};color:#fff;padding:3px 10px;border-radius:4px;font-weight:bold">
                    {day['score']} — {day['rating']}
                </span>
            </td>
            <td class="day-wind" style="padding:4px 8px;color:{wc}">{day['wind_avg']:.0f} mph avg, <span style="color:{gc}">gusts {day['gust_max']:.0f} mph</span></td>
            <td class="day-dir" style="padding:4px 8px;color:{dc}">{compass}</td>
            <td class="day-temp" style="padding:4px 8px;color:{tc}">{day['temp_avg']:.0f}°F</td>
            <td class="day-cloud" style="padding:4px 8px;color:{cc}">{day['cloud_avg']:.0f}%</td>
            <td class="day-rain" style="padding:4px 8px;color:{pc}">{day['precip_total']:.2f} in</td>
        </tr>
        <tr class="day-detail">
            <td colspan="7" style="padding:2px 8px 4px 8px;font-size:0.85em;color:#555">
                Wind: {" &nbsp;|&nbsp; ".join(f"<b>{w['name']}</b> {w['avg']:.0f} (g{w['gust_max']:.0f})" for w in day["wind_windows"])}
            </td>
        </tr>
        <tr class="day-detail">
            <td colspan="7" style="padding:2px 8px 4px 8px;font-size:0.85em;color:#666">
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

    best_date_str = best["date"].strftime("%A, %b %d")
    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><path d='M0 80 Q15 75 30 80 Q45 85 60 80 Q75 75 90 80 Q95 82 100 80 L100 100 L0 100Z' fill='%231565c0'/><path d='M0 88 Q20 83 40 88 Q60 93 80 88 Q90 85 100 88 L100 100 L0 100Z' fill='%230d47a1'/><path d='M50 10 L50 80 L20 80 Z' fill='%232196f3'/><path d='M50 20 L50 70 L75 70 Z' fill='%2364b5f6'/><path d='M15 82 L85 82 Q90 90 80 90 L20 90 Q10 90 15 82Z' fill='%23e53935'/></svg>">
<style>
  @media screen and (max-width: 600px) {{
    body {{ font-size: 16px !important; }}
    h2 {{ font-size: 1.3em; }}

    .day-card table {{ width: 100% !important; }}

    .day-row {{
      display: flex;
      flex-wrap: wrap;
      position: relative;
      padding: 4px 0 8px 0;
    }}
    .day-row td {{
      display: block;
      padding: 2px 0 !important;
      text-align: left !important;
    }}
    .day-name {{
      width: 100%;
      font-size: 1.15em !important;
      padding: 0 0 4px 0 !important;
    }}
    .day-rating {{
      position: absolute;
      top: 4px;
      right: 0;
      padding: 0 !important;
    }}
    .day-wind {{
      width: 100%;
      margin-top: 10px !important;
      padding: 4px 0 3px 0 !important;
      order: 1;
    }}
    .day-temp {{ width: 50%; padding: 3px 0 !important; order: 2; }}
    .day-cloud {{ width: 50%; padding: 3px 0 !important; order: 3; }}
    .day-dir {{ width: 50%; padding: 3px 0 !important; order: 4; }}
    .day-rain {{ width: 50%; padding: 3px 0 !important; order: 5; }}
    .day-wind::before {{ content: "Wind: "; font-weight: bold; color: #555; }}
    .day-dir::before {{ content: "Dir: "; font-weight: bold; color: #555; }}
    .day-temp::before {{ content: "Temp: "; font-weight: bold; color: #555; }}
    .day-cloud::before {{ content: "Cloud: "; font-weight: bold; color: #555; }}
    .day-rain::before {{ content: "Rain: "; font-weight: bold; color: #555; }}

    .day-detail td {{
      display: block;
      padding: 2px 0 !important;
      font-size: 0.85em !important;
    }}
  }}
</style>
</head>
<body style="font-family:Arial,Helvetica,sans-serif;margin:0;color:#333;background:{'#90a4ae' if cond_precip > 0.1 else '#b0bec5' if cond_cloud > 70 else '#90caf9' if cond_cloud > 40 else '#42a5f5'}">
    <canvas id="bg-canvas" style="position:fixed;top:0;left:0;width:100%;height:100%;z-index:0"></canvas>
    <div style="position:relative;z-index:1;max-width:700px;margin:0 auto;padding:0 12px">
    <div style="text-align:center;padding:28px 0 144px 0">
        <h2 style="color:white;margin:0 0 6px 0;text-shadow:0 2px 8px rgba(0,0,0,0.5), 0 1px 3px rgba(0,0,0,0.3)">Fern Ridge Sailing Forecast</h2>
        <p style="color:rgba(255,255,255,0.95);margin:0;text-shadow:0 2px 8px rgba(0,0,0,0.6), 0 1px 3px rgba(0,0,0,0.4), 0 0 12px rgba(0,0,0,0.3)">{now.strftime('%A, %B %d, %Y')}</p>
    </div>
    <script>
    (function() {{
      var wx = {{ cloud: {cond_cloud:.0f}, wind: {cond_wind:.1f}, gust: {cond_gust:.1f}, precip: {cond_precip:.2f}, temp: {cond_temp:.1f} }};
      if (new URLSearchParams(window.location.search).get('random') === 'true') {{
        wx = {{ cloud: Math.random()*100, wind: Math.random()*30, gust: Math.random()*45, precip: Math.random() < 0.3 ? 0.1 + Math.random()*0.4 : 0, temp: 45+Math.random()*35 }};
      }}
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
      var windFactor = Math.min(wx.wind / 20, 1);
      var isRainy = wx.precip > 0.1;

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
      var numClouds = Math.max(2, Math.round(3 + cloudFrac * 6 + Math.random()*2));
      var clouds = [];
      for (var ci = 0; ci < numClouds; ci++) clouds.push(makeCloud());
      clouds.sort(function(a, b) {{ return a.y - b.y; }});
      for (var ci = 0; ci < clouds.length; ci++) {{
        var frac = clouds.length > 1 ? ci / (clouds.length - 1) : 1;
        var lo = isRainy ? 170 : (cloudFrac > 0.7 ? 205 : 230);
        var hi = isRainy ? 210 : (cloudFrac > 0.7 ? 240 : 255);
        var shade = Math.round(lo + frac * (hi - lo));
        clouds[ci].color = 'rgb(' + shade + ',' + shade + ',' + shade + ')';
      }}

      var rainDrops = [];
      if (isRainy) {{
        var numDrops = Math.round(80 + wx.precip * 400);
        for (var ri = 0; ri < numDrops; ri++) {{
          rainDrops.push({{ x: Math.random() * 2000, y: Math.random() * 600, len: 8 + Math.random() * 14, speed: 300 + Math.random() * 200 }});
        }}
      }}

      var t = 0;
      function draw() {{
        t += 0.016;
        ctx.clearRect(0, 0, W, H);

        var sky = ctx.createLinearGradient(0, 0, 0, H);
        if (isRainy) {{
          sky.addColorStop(0, '#78909c');
          sky.addColorStop(0.4, '#90a4ae');
          sky.addColorStop(1, '#b0bec5');
        }} else if (cloudFrac > 0.7) {{
          sky.addColorStop(0, '#90a4ae');
          sky.addColorStop(0.3, '#b0bec5');
          sky.addColorStop(0.6, '#cfd8dc');
          sky.addColorStop(1, '#eceff1');
        }} else if (cloudFrac > 0.4) {{
          sky.addColorStop(0, '#64b5f6');
          sky.addColorStop(0.3, '#90caf9');
          sky.addColorStop(0.6, '#bbdefb');
          sky.addColorStop(1, '#e3f2fd');
        }} else {{
          sky.addColorStop(0, '#1976d2');
          sky.addColorStop(0.3, '#42a5f5');
          sky.addColorStop(0.6, '#90caf9');
          sky.addColorStop(1, '#bbdefb');
        }}
        ctx.fillStyle = sky;
        ctx.fillRect(0, 0, W, H);

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

        var waveAmpScale = 0.5 + windFactor * 1.0;
        var waveSpeedScale = 0.6 + windFactor * 0.8;
        function drawWaveLayer(idx) {{
          var waterColors = isRainy ? ['#37474f','#263238','#1a2327'] : ['#1565c0','#0d47a1','#0a3d91'];
          var amp = (8 - idx * 1.5) * waveAmpScale;
          var yBase = 185 + idx * 12;
          var speed = (1 + idx * 0.4) * waveSpeedScale;
          ctx.fillStyle = waterColors[idx];
          ctx.beginPath();
          ctx.moveTo(0, H);
          for (var x = 0; x <= W; x += 4) {{
            ctx.lineTo(x, yBase + Math.sin(x*0.015 + t*speed)*amp + Math.sin(x*0.008 + t*speed*0.6)*amp*0.5);
          }}
          ctx.lineTo(W, H);
          ctx.closePath();
          ctx.fill();
        }}

        function waveY(x) {{
          var a = 6.5 * waveAmpScale;
          var s = 1.4 * waveSpeedScale;
          return 197 + Math.sin(x*0.015 + t*s)*a + Math.sin(x*0.008 + t*s*0.6)*a*0.5;
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

        drawWaveLayer(0);
        drawBoat();
        drawWaveLayer(1);

        if (isRainy) {{
          var windAngle = windFactor * 2;
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

        drawWaveLayer(2);

        requestAnimationFrame(draw);
      }}
      draw();
    }})();
    </script>

    <div style="background:linear-gradient(135deg,#fff8e1,#ffecb3);padding:12px 16px;border-radius:8px;margin:10px 0;box-shadow:0 1px 3px rgba(0,0,0,0.12)">
        <strong>Best day:</strong> {best_date_str}
        — Score {best['score']} ({best['rating']})
    </div>

    <div class="forecast-wrap">
        {"".join(html_rows)}
    </div>

    <p style="font-size:0.8em;color:rgba(255,255,255,0.7);margin-top:20px;padding-bottom:16px">
        Scoring: Wind 35% | Gusts 20% | Precip 15% | Cloud 10% | Temp 10% | Direction 10%<br>
        Sailing hours: 11 AM – 5 PM | Ideal wind: 10–15 mph from N<br>
        Location: {LOCATION_NAME} ({LATITUDE}, {LONGITUDE})<br>
        Data: <a href="https://open-meteo.com" style="color:rgba(255,255,255,0.85)">Open-Meteo.com</a>
    </p>
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
