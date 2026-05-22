from __future__ import annotations

import argparse
import math
import re
from difflib import SequenceMatcher
from pathlib import Path

import pandas as pd

try:
    from rapidfuzz import fuzz
except ModuleNotFoundError:
    fuzz = None


PROCESSED_DIR = Path("data/processed")


KEEP_COLUMNS = [
    "year",
    "candidate_name",
    "constituency",
    "party",
    "age",
    "criminal_cases",
    "education_category",
    "self_profession",
    "spouse_profession",
    "pan_given_self",
    "latest_self_itr_income",
    "latest_spouse_itr_income",
    "total_assets",
    "movable_assets",
    "immovable_assets",
    "liabilities",
    "liabilities_calculated",
    "source_url",
    "raw_html_file",
]


def normalize_text(value: object) -> str:
    if pd.isna(value):
        return ""
    return " ".join(str(value).replace("\xa0", " ").split())


def canonical_name(value: object) -> str:
    text = normalize_text(value).upper()
    text = re.sub(r"\(WINNER\)", "", text)
    text = re.sub(r"\b(S/O|D/O|W/O|C/O)\b.*", "", text)
    text = re.sub(r"\b(ADV|ADVOCATE|DR|PROF|SRI|SMT|SHRI)\.?\b", "", text)
    text = re.sub(r"[^A-Z0-9 ]+", " ", text)
    return " ".join(text.split())


def match_score(left: str, right: str) -> int:
    if fuzz is not None:
        return int(fuzz.token_sort_ratio(left, right))
    left_sorted = " ".join(sorted(left.lower().split()))
    right_sorted = " ".join(sorted(right.lower().split()))
    return int(100 * SequenceMatcher(None, left_sorted, right_sorted).ratio())


def to_num(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def load_years(years: list[int]) -> pd.DataFrame:
    frames = []
    for year in years:
        path = PROCESSED_DIR / f"myneta_kerala_{year}_financial_details.csv"
        if not path.exists():
            print(f"missing: {path}")
            continue
        df = pd.read_csv(path, dtype=str).fillna("")
        for col in KEEP_COLUMNS:
            if col not in df.columns:
                df[col] = ""
        frames.append(df[KEEP_COLUMNS].copy())
    if not frames:
        raise SystemExit("No per-year CSVs found. Run crawl/parse first.")

    df = pd.concat(frames, ignore_index=True)
    df["year"] = to_num(df["year"]).astype("Int64")
    for col in [
        "age",
        "criminal_cases",
        "latest_self_itr_income",
        "latest_spouse_itr_income",
        "total_assets",
        "movable_assets",
        "immovable_assets",
        "liabilities",
        "liabilities_calculated",
    ]:
        df[col] = to_num(df[col])
    df["canonical_name"] = df["candidate_name"].map(canonical_name)
    df["debt_to_asset_ratio"] = df["liabilities"] / df["total_assets"].replace({0: pd.NA})
    df["assets_to_self_income_ratio"] = df["total_assets"] / df["latest_self_itr_income"].replace({0: pd.NA})
    return df


def find_prior_matches(df: pd.DataFrame, threshold: int) -> pd.DataFrame:
    matches = []
    years = sorted(int(y) for y in df["year"].dropna().unique())
    for current_year in years:
        current = df[df["year"] == current_year]
        prior = df[df["year"] < current_year]
        if prior.empty:
            continue
        for _, row in current.iterrows():
            name = row["canonical_name"]
            if not name:
                continue

            candidates = prior.copy()
            same_name = candidates[candidates["canonical_name"] == name]
            if not same_name.empty:
                candidates = same_name

            best_score = -1
            best = None
            for _, candidate in candidates.iterrows():
                score = match_score(name, candidate["canonical_name"])
                if row["party"] and row["party"] == candidate["party"]:
                    score += 3
                if row["constituency"] and row["constituency"] == candidate["constituency"]:
                    score += 4
                if score > best_score:
                    best_score = score
                    best = candidate

            if best is None or best_score < threshold:
                continue

            year_gap = int(row["year"] - best["year"])
            old_assets = best["total_assets"]
            new_assets = row["total_assets"]
            growth_amount = pd.NA
            growth_ratio = pd.NA
            cagr = pd.NA
            if pd.notna(old_assets) and pd.notna(new_assets):
                growth_amount = new_assets - old_assets
                if old_assets > 0:
                    growth_ratio = new_assets / old_assets
                    if year_gap > 0 and new_assets >= 0:
                        cagr = math.pow(new_assets / old_assets, 1 / year_gap) - 1

            matches.append(
                {
                    "candidate_name": row["candidate_name"],
                    "canonical_name": row["canonical_name"],
                    "party": row["party"],
                    "constituency": row["constituency"],
                    "year": int(row["year"]),
                    "prior_year": int(best["year"]),
                    "year_gap": year_gap,
                    "match_score": best_score,
                    "prior_candidate_name": best["candidate_name"],
                    "prior_party": best["party"],
                    "prior_constituency": best["constituency"],
                    "prior_total_assets": old_assets,
                    "total_assets": new_assets,
                    "asset_growth_amount": growth_amount,
                    "asset_growth_ratio": growth_ratio,
                    "asset_cagr": cagr,
                    "prior_liabilities": best["liabilities"],
                    "liabilities": row["liabilities"],
                    "criminal_cases": row["criminal_cases"],
                    "source_url": row["source_url"],
                    "prior_source_url": best["source_url"],
                }
            )
    return pd.DataFrame(matches)


def build_flags(clean: pd.DataFrame, repeats: pd.DataFrame) -> pd.DataFrame:
    rows = []
    repeat_by_key = {}
    if not repeats.empty:
        for _, row in repeats.iterrows():
            repeat_by_key[(row["canonical_name"], row["year"])] = row

    for _, row in clean.iterrows():
        flags = []
        score = 0
        total_assets = row["total_assets"]
        liabilities = row["liabilities"]
        income = row["latest_self_itr_income"]

        if pd.isna(total_assets):
            flags.append("missing_total_assets")
            score += 1
        elif total_assets >= 100_000_000:
            flags.append("assets_over_10_crore")
            score += 2
        elif total_assets >= 50_000_000:
            flags.append("assets_over_5_crore")
            score += 1

        if pd.notna(liabilities) and liabilities >= 50_000_000:
            flags.append("liabilities_over_5_crore")
            score += 2
        if pd.notna(row["debt_to_asset_ratio"]) and row["debt_to_asset_ratio"] >= 1:
            flags.append("liabilities_exceed_assets")
            score += 2
        if pd.notna(row["criminal_cases"]) and row["criminal_cases"] >= 5:
            flags.append("criminal_cases_5_plus")
            score += 1

        if pd.notna(total_assets) and pd.notna(income) and income > 0:
            ratio = total_assets / income
            if total_assets >= 10_000_000 and ratio >= 100:
                flags.append("assets_100x_self_itr_income")
                score += 2
            elif total_assets >= 10_000_000 and ratio >= 50:
                flags.append("assets_50x_self_itr_income")
                score += 1

        repeat = repeat_by_key.get((row["canonical_name"], row["year"]))
        if repeat is not None:
            if pd.notna(repeat["asset_cagr"]) and repeat["asset_cagr"] >= 0.50:
                flags.append("repeat_candidate_asset_cagr_50pct_plus")
                score += 3
            elif pd.notna(repeat["asset_cagr"]) and repeat["asset_cagr"] >= 0.25:
                flags.append("repeat_candidate_asset_cagr_25pct_plus")
                score += 2
            if pd.notna(repeat["asset_growth_amount"]) and repeat["asset_growth_amount"] >= 50_000_000:
                flags.append("repeat_candidate_asset_growth_5_crore_plus")
                score += 2
            elif pd.notna(repeat["asset_growth_amount"]) and repeat["asset_growth_amount"] >= 10_000_000:
                flags.append("repeat_candidate_asset_growth_1_crore_plus")
                score += 1

        if flags:
            rows.append(
                {
                    "risk_score": score,
                    "flags": "|".join(flags),
                    "year": row["year"],
                    "candidate_name": row["candidate_name"],
                    "party": row["party"],
                    "constituency": row["constituency"],
                    "total_assets": total_assets,
                    "liabilities": liabilities,
                    "debt_to_asset_ratio": row["debt_to_asset_ratio"],
                    "latest_self_itr_income": income,
                    "assets_to_self_income_ratio": row["assets_to_self_income_ratio"],
                    "criminal_cases": row["criminal_cases"],
                    "source_url": row["source_url"],
                }
            )
    out = pd.DataFrame(rows)
    if not out.empty:
        out = out.sort_values(["risk_score", "total_assets"], ascending=[False, False])
    return out


def write_summary(clean: pd.DataFrame, repeats: pd.DataFrame, flags: pd.DataFrame, years: list[int]) -> None:
    lines = [
        "# Kerala Candidate Financial Analysis",
        "",
        "This is a triage report based on MyNeta/ADR affidavit data. Flags are not proof of wrongdoing; verify against affidavits and ECI records before making claims.",
        "",
        "## Coverage",
        "",
    ]
    for year in years:
        subset = clean[clean["year"] == year]
        lines.append(
            f"- {year}: {len(subset)} candidates, "
            f"{subset['total_assets'].notna().sum()} with assets parsed, "
            f"{subset['liabilities'].notna().sum()} with liabilities parsed"
        )
    lines.extend(["", "## Outputs", ""])
    lines.append("- `kerala_financial_clean_2016_2021_2026.csv`: trimmed financial dataset")
    lines.append("- `kerala_repeat_candidate_growth.csv`: matched repeat candidates with asset growth and CAGR")
    lines.append("- `kerala_financial_anomaly_flags.csv`: high-signal red flags for review")
    lines.append("- `kerala_year_party_summary.csv`: party/year aggregate financial summary")
    lines.extend(["", "## Top Red Flags", ""])
    if flags.empty:
        lines.append("No red flags generated with current thresholds.")
    else:
        top = flags.head(15)
        for _, row in top.iterrows():
            assets = row["total_assets"]
            asset_text = "" if pd.isna(assets) else f"Rs {int(assets):,}"
            lines.append(
                f"- score {int(row['risk_score'])}: {row['candidate_name']} ({int(row['year'])}, {row['party']}, {row['constituency']}) "
                f"assets={asset_text}; flags={row['flags']}"
            )
    lines.extend(["", "## Repeat Candidates", ""])
    lines.append(f"- repeat matches found: {len(repeats)}")
    if not repeats.empty:
        high_growth = repeats[repeats["asset_cagr"].notna()].sort_values("asset_cagr", ascending=False).head(10)
        for _, row in high_growth.iterrows():
            lines.append(
                f"- {row['candidate_name']}: {int(row['prior_year'])}->{int(row['year'])}, "
                f"CAGR={row['asset_cagr']:.1%}, growth=Rs {int(row['asset_growth_amount']):,}"
            )
    (PROCESSED_DIR / "kerala_analysis_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def analyze(args: argparse.Namespace) -> None:
    years = [int(year) for year in args.years]
    clean = load_years(years)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    clean_path = PROCESSED_DIR / "kerala_financial_clean_2016_2021_2026.csv"
    clean.to_csv(clean_path, index=False)

    repeats = find_prior_matches(clean, args.match_threshold)
    repeats_path = PROCESSED_DIR / "kerala_repeat_candidate_growth.csv"
    repeats.to_csv(repeats_path, index=False)

    flags = build_flags(clean, repeats)
    flags_path = PROCESSED_DIR / "kerala_financial_anomaly_flags.csv"
    flags.to_csv(flags_path, index=False)

    party_summary = (
        clean.groupby(["year", "party"], dropna=False)
        .agg(
            candidate_count=("candidate_name", "count"),
            total_assets_median=("total_assets", "median"),
            total_assets_mean=("total_assets", "mean"),
            liabilities_median=("liabilities", "median"),
            criminal_cases_sum=("criminal_cases", "sum"),
            criminal_cases_mean=("criminal_cases", "mean"),
        )
        .reset_index()
        .sort_values(["year", "candidate_count"], ascending=[True, False])
    )
    party_summary.to_csv(PROCESSED_DIR / "kerala_year_party_summary.csv", index=False)
    write_summary(clean, repeats, flags, years)

    print(f"clean: {len(clean)} -> {clean_path}")
    print(f"repeat matches: {len(repeats)} -> {repeats_path}")
    print(f"flags: {len(flags)} -> {flags_path}")
    print(f"summary: {PROCESSED_DIR / 'kerala_analysis_summary.md'}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build clean Kerala MyNeta financial analysis datasets")
    parser.add_argument("--years", nargs="+", default=["2016", "2021", "2026"])
    parser.add_argument("--match-threshold", type=int, default=90)
    args = parser.parse_args()
    analyze(args)


if __name__ == "__main__":
    main()
