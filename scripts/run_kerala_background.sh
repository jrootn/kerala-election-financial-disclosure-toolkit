#!/usr/bin/env bash
set -euo pipefail

mkdir -p logs data/processed

echo "[$(date -Is)] Starting Kerala MyNeta background pipeline"

for year in 2021 2016; do
  echo "[$(date -Is)] Crawling Kerala ${year}"
  python myneta_pipeline.py crawl --year "${year}" --delay-min 1 --delay-max 2 --pause-every 150 --pause-seconds 30

  echo "[$(date -Is)] Parsing Kerala ${year}"
  python myneta_pipeline.py parse --year "${year}"
done

echo "[$(date -Is)] Re-parsing Kerala 2026 with latest parser"
python myneta_pipeline.py parse --year 2026

echo "[$(date -Is)] Building combined CSV"
python myneta_pipeline.py combine --years 2016 2021 2026

echo "[$(date -Is)] Building financial analysis outputs"
python analyze_financials.py --years 2016 2021 2026

echo "[$(date -Is)] Done"
