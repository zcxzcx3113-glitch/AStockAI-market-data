# AStockAI Market Data

Public, market-wide candidate feed for the private AStockAI Android app.

## What it does

- Scans Shanghai, Shenzhen and Beijing A shares after the close on trading weekdays.
- Uses Eastmoney's full-market snapshot only for universe and capital-flow screening.
- Uses Tencent daily bars for technical scoring.
- Rechecks the final shortlist with Tencent quotes and Sina as a backup source.
- Publishes only market-wide data to `data/candidates.json`. Personal holdings are never read or uploaded.

The sources are public web market-data endpoints without an official availability SLA. A failed scan exits without replacing the last known-good feed.

## Schedule

`.github/workflows/scan-a-share.yml` runs at 15:45 Asia/Shanghai on weekdays and can also be started manually. The feed is intended for research and observation, not as investment advice or an order signal.

## Run locally

```bash
python -m unittest discover -s tests -v
python scripts/scan_market.py --output data/candidates.json --top 30
python scripts/validate_feed.py data/candidates.json
```
