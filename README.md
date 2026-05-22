# Kerala Election Financial Disclosure Toolkit

Research tooling for collecting, parsing, and analyzing Kerala Assembly election candidate affidavit disclosures from MyNeta/ADR.

This repository is designed as a **public-interest research toolkit**, not as a redistributed dataset. It intentionally excludes raw HTML, scraped CSVs, logs, local caches, and environment files from version control.

## What This Project Does

The pipeline supports a reproducible workflow:

1. Collect candidate profile links from MyNeta Kerala election pages.
2. Cache candidate profile HTML locally with conservative request settings.
3. Parse declared assets, liabilities, income, criminal-case counts, education, profession, PAN status, and source URLs.
4. Combine election years into a normalized research table.
5. Generate financial-disclosure anomaly rankings and charts for manual review.
6. Produce a LaTeX research note that frames results as open questions, not legal conclusions.

## What This Project Does Not Do

- It does not prove corruption or illegality.
- It does not redistribute the full MyNeta/ADR database.
- It does not include raw scraped HTML or full candidate-level CSV outputs in Git.
- It does not bypass rate limits, authentication, CAPTCHAs, or access controls.

All findings should be checked against original affidavits, Election Commission records, court documents, company filings, land records, and other primary sources before being cited as evidence.

## Repository Contents

```text
myneta_pipeline.py                 # crawl, cache, parse, combine, basic flags
analyze_financials.py              # clean dataset, repeat matching, initial flags
deep_financial_analysis.py         # richer scoring, rankings, charts
scripts/run_kerala_background.sh   # long-run helper
data/analysis/paper/*.tex          # research-paper source, no bundled data required
requirements.txt
DATA_NOTICE.md
ETHICS.md
```

Generated data is intentionally ignored:

```text
data/
logs/
*.csv
*.html
*.json
*.pdf
*.xlsx
.env*
.venv/
```

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Minimal Demo

For a small local test, fetch only a few pages:

```bash
python myneta_pipeline.py crawl --year 2026 --max-summary-pages 1 --max-candidates 10
python myneta_pipeline.py parse --year 2026
python analyze_financials.py --years 2026
```

The generated files will appear under `data/`, which is ignored by Git.

## Full Local Workflow

Run this only if you have reviewed the source site's terms and are comfortable that your use is permitted.

```bash
python myneta_pipeline.py crawl --year 2026 --delay-min 2 --delay-max 4
python myneta_pipeline.py crawl --year 2021 --delay-min 2 --delay-max 4
python myneta_pipeline.py crawl --year 2016 --delay-min 2 --delay-max 4

python myneta_pipeline.py parse --year 2026
python myneta_pipeline.py parse --year 2021
python myneta_pipeline.py parse --year 2016

python myneta_pipeline.py combine --years 2016 2021 2026
python analyze_financials.py --years 2016 2021 2026
python deep_financial_analysis.py
```

## Analysis Philosophy

The scoring model is a triage tool. It prioritizes manual review using signals such as:

- year-relative asset percentile;
- absolute declared assets;
- liabilities and debt-to-asset ratio;
- assets relative to declared income;
- repeat-candidate asset growth and CAGR;
- criminal-case count;
- PAN and missing-data signals.

These signals are not accusations. They are prompts for document-based research.

## Research Paper

The paper source is available at:

```text
data/analysis/paper/kerala_financial_disclosure_anomalies.tex
```

The LaTeX file uses PGFPlots/TikZ charts directly, so it can be pasted into Overleaf without uploading generated chart images.

## Responsible Use

MyNeta/ADR and Election Commission affidavit data are public-interest resources. Respect source terms, cite sources, and avoid redistributing bulk scraped data without permission.

Recommended public-sharing approach:

- share code and methodology;
- share aggregate charts and research questions;
- keep raw HTML and full candidate CSVs private unless permission is obtained;
- cite MyNeta/ADR and ECI;
- verify any high-risk record manually before publication.

See [DATA_NOTICE.md](DATA_NOTICE.md) and [ETHICS.md](ETHICS.md).

