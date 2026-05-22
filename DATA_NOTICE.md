# Data Notice

This repository does not include raw scraped MyNeta/ADR HTML, full candidate-level CSV exports, logs, local caches, or environment files.

## Why Data Is Not Included

The project is intended to showcase a reproducible research method while respecting source-site terms and avoiding bulk redistribution of third-party data. MyNeta/ADR candidate affidavit pages are public-interest resources, but users should review applicable terms and obtain permission before large-scale automated collection or redistribution.

## What Is Safe To Commit

- Source code.
- Documentation.
- Reproducible methodology.
- LaTeX paper source.
- Small synthetic examples, if added later.
- Aggregate statistics or charts that do not reproduce the full database.

## What Should Not Be Committed

- `data/raw_html/`
- `data/processed/*.csv`
- `data/analysis/*.csv`
- complete candidate-level scraped datasets;
- raw MyNeta/ADR HTML;
- logs containing source URLs at scale;
- `.env` files, credentials, API keys, browser sessions, or cookies.

## Reproducing Locally

Users may run the pipeline locally after reviewing the source site's terms and deciding whether their use is permitted. The generated files will be written under `data/`, which is ignored by Git.

Always cite MyNeta/ADR and verify records against original affidavits or Election Commission sources before publishing substantive claims.

