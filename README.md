# Fern Ridge Sailing Forecast

7-day weather forecast for Fern Ridge Reservoir (near Eugene, OR), scored for sailing quality.

Each day is scored 0–100 based on conditions during sailing hours (8 AM – 7 PM):

| Factor | Weight | Ideal |
|--------|--------|-------|
| Wind speed | 40% | 10–15 mph |
| Gust spread | 20% | < 6 mph spread |
| Precipitation | 20% | Dry |
| Temperature | 10% | 75–95°F |
| Wind direction | 10% | W/NW (best fetch) |

Ratings: Excellent (80+), Good (65–79), Fair (50–64), Poor (35–49), Unfavorable (<35)

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your SMTP credentials
```

For Gmail, use an [App Password](https://support.google.com/accounts/answer/185833) (not your regular password).

## Usage

Print forecast to terminal:
```bash
python3 wind_notice.py --no-email
```

Send forecast via email:
```bash
python3 wind_notice.py
```

## Data Source

Weather data from [Open-Meteo](https://open-meteo.com/) (free, no API key needed).
