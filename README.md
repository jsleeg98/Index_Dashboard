# Index_Dashboard

Asset price dashboard with cache-backed data fetches and interactive charts.

## Features
- Web dashboard with Chart.js charts
- Cached price history stored in SQLite (`data/prices.db`)
- Quick ranges (7d/1mo/3mo/6mo/1y) and custom date range
- Optional moving average and Bollinger Bands
- CSV export per selection

## Requirements
- Python 3.10+
- Packages: `yfinance`, `pandas`, `tabulate`, `flask`

Optional (for CLI chart image export): `matplotlib`

Install:
```bash
pip install yfinance pandas tabulate flask matplotlib
```

## Usage

### Web dashboard
```bash
python weekly_prices.py --web
```
Then open:
```
http://127.0.0.1:5000
```

### CLI table output
```bash
python weekly_prices.py
```

### Cache DB stats
```bash
python weekly_prices.py --db-stats
```

## Environment variables
You can set these in a `.env` file (loaded automatically):
- `WEB_HOST` (default: `0.0.0.0`)
- `WEB_PORT` (default: `5000`)

Example:
```
WEB_HOST=127.0.0.1
WEB_PORT=5000
```

## Notes
- First load may be slow if cache is empty. The app auto-fetches live data on cache miss.
- Cached data is stored in `data/prices.db`.
- CSV/PNG outputs are excluded via `.gitignore`.
