# Kerala Election Financial Disclosure Toolkit

A public-interest research toolkit for collecting, parsing, and analyzing Kerala Assembly election candidate affidavit disclosures from MyNeta/ADR.

The project helps researchers build reproducible financial-disclosure analysis from election affidavits: asset and liability trends, repeat-candidate growth, income-to-asset mismatch, and manual-review rankings. It is designed to publish **methodology and code**, not to redistribute a bulk copy of the source database.

## Why This Exists

Election affidavits are one of the few structured public records that let citizens compare candidate finances over time. This toolkit turns those disclosures into auditable research tables and triage reports so journalists, students, civic technologists, and curious citizens can ask better follow-up questions.

The output should be read as a **manual review queue**. A high score means "look here first," not "this person did something illegal."

## What The Pipeline Does

1. Collect candidate profile links from MyNeta Kerala election pages.
2. Cache candidate profile HTML locally with conservative request settings.
3. Parse declared assets, liabilities, income, criminal-case counts, education, profession, PAN status, and source URLs.
4. Combine election years into a normalized research table.
5. Match repeat candidates and calculate asset growth/CAGR.
6. Generate financial-disclosure anomaly rankings and charts for manual review.
7. Produce a LaTeX research note that frames findings as open questions.

## Scope And Limitations

This project is a research aid, not an investigative finding.

- It does not prove corruption, fraud, or illegality.
- It does not include raw scraped HTML or full candidate-level CSV outputs in Git.
- It does not bypass rate limits, authentication, CAPTCHAs, or access controls.
- It depends on the accuracy of public affidavits, parser logic, and repeat-candidate matching.

Any substantive claim should be checked against original affidavits, Election Commission records, court documents, company filings, land records, and other primary sources.

## Repository Contents

```text
myneta_pipeline.py                 # crawl, cache, parse, combine, basic flags
analyze_financials.py              # clean dataset, repeat matching, initial flags
deep_financial_analysis.py         # richer scoring, rankings, charts
scripts/run_kerala_background.sh   # long-run helper
paper/kerala_financial_disclosure_anomalies.tex
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

Generated files appear under `data/`, which is ignored by Git.

## Full Local Workflow

Run this only after reviewing the source site's terms and deciding that your use is permitted.

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

## Analysis Model

The scoring model prioritizes manual review using signals such as:

- year-relative asset percentile;
- absolute declared assets;
- liabilities and debt-to-asset ratio;
- assets relative to declared income;
- repeat-candidate asset growth and CAGR;
- criminal-case count;
- PAN and missing-data signals.

These signals are prompts for document-based research. They are not accusations.

## Research Paper

The LaTeX source is available at:

```text
paper/kerala_financial_disclosure_anomalies.tex
```

The paper uses PGFPlots/TikZ charts directly, so it can be pasted into Overleaf without uploading generated chart images.

## Responsible Sharing

MyNeta/ADR and Election Commission affidavit data are public-interest resources. Please respect source terms, cite sources, and avoid redistributing bulk scraped data without permission.

Recommended public-sharing approach:

- share code and methodology;
- share aggregate charts and research questions;
- keep raw HTML and full candidate CSVs private unless permission is obtained;
- cite MyNeta/ADR and ECI;
- verify any high-risk record manually before publication.

See [DATA_NOTICE.md](DATA_NOTICE.md) and [ETHICS.md](ETHICS.md).

