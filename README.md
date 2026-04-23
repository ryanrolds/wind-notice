# Fern Ridge Sailing Forecast

7-day weather forecast for Fern Ridge Reservoir (near Eugene, OR), scored for sailing quality. Served as a web page that auto-refreshes on a schedule.

Each day is scored 0–100 based on conditions during sailing hours (11 AM – 5 PM):

| Factor | Weight | Ideal |
|--------|--------|-------|
| Wind speed | 35% | 10–15 mph (under 8 or over 17 = dealbreaker) |
| Gust spread | 20% | < 6 mph spread |
| Precipitation | 15% | Dry |
| Cloud cover | 10% | Partly cloudy (30–70%) |
| Temperature | 10% | 75–95°F (under 70 or over 105 = dealbreaker) |
| Wind direction | 10% | W/NW (best fetch) |

Ratings: Excellent (80+), Good (65–79), Fair (50–64), Poor (35–49), Unfavorable (<35)

Wind and temperature have hard cutoffs — if either is outside the usable range, the day is capped at Unfavorable regardless of other conditions.

## Local Development

```bash
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your settings
```

Run the web server:
```bash
python3 app.py
# Visit http://localhost:5000
```

Print forecast to terminal (no web server):
```bash
python3 wind_notice.py --no-email
```

Send forecast via email (requires AWS SES credentials):
```bash
python3 wind_notice.py
```

## Docker

```bash
docker compose up --build
# Visit http://localhost:5000
```

The forecast refreshes every 6 hours by default (configurable via `REFRESH_INTERVAL_HOURS`).

## Email

Email is sent via AWS SES. Set these environment variables:

- `AWS_REGION` — SES region (e.g., `us-west-2`)
- `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` — IAM credentials with `ses:SendEmail` permission
- `EMAIL_FROM` — verified sender address
- `EMAIL_TO` — recipient address(es), comma-separated

## Deployment

The app deploys to Kubernetes via Helm and ArgoCD:

- Docker image pushed to `zot.pedanticorderliness.com/wind-notice`
- Helm chart at `infrastructure/charts/wind-notice`
- ArgoCD syncs from the `app-of-apps` chart
- Live at `wind.pedanticorderliness.com`

AWS credentials are stored as SOPS-encrypted secrets in the infrastructure repo.

## Data Source

Weather data from [Open-Meteo](https://open-meteo.com/) (free, no API key needed).
