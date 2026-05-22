from __future__ import annotations

import argparse
import math
import os
import textwrap
from pathlib import Path

os.environ.setdefault("XDG_CACHE_HOME", str(Path(".cache").resolve()))
os.environ.setdefault("MPLCONFIGDIR", str(Path(".matplotlib-cache").resolve()))

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


PROCESSED_DIR = Path("data/processed")
DEFAULT_OUT_DIR = Path("data/analysis")
PLOTS_DIRNAME = "plots"


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise SystemExit(f"Missing required file: {path}")
    return pd.read_csv(path)


def crore(value: float | int | None) -> str:
    if pd.isna(value):
        return ""
    return f"{float(value) / 10_000_000:.2f} cr"


def pct(value: float | int | None) -> str:
    if pd.isna(value):
        return ""
    return f"{float(value) * 100:.1f}%"


def safe_num(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def percentile_within_year(df: pd.DataFrame, col: str) -> pd.Series:
    out = pd.Series(np.nan, index=df.index, dtype="float64")
    for _, idx in df.groupby("year").groups.items():
        values = df.loc[idx, col]
        if values.notna().sum() <= 1:
            continue
        out.loc[idx] = values.rank(pct=True, method="average")
    return out


def log1p_positive(series: pd.Series) -> pd.Series:
    values = safe_num(series).clip(lower=0)
    return np.log1p(values)


def evidence_join(items: list[str]) -> str:
    return "; ".join(item for item in items if item)


def score_candidates(clean: pd.DataFrame, repeats: pd.DataFrame) -> pd.DataFrame:
    df = clean.copy()
    for col in [
        "year",
        "age",
        "criminal_cases",
        "latest_self_itr_income",
        "latest_spouse_itr_income",
        "total_assets",
        "movable_assets",
        "immovable_assets",
        "liabilities",
        "liabilities_calculated",
        "debt_to_asset_ratio",
        "assets_to_self_income_ratio",
    ]:
        if col in df.columns:
            df[col] = safe_num(df[col])

    df["combined_declared_income"] = (
        df["latest_self_itr_income"].fillna(0) + df["latest_spouse_itr_income"].fillna(0)
    )
    df["assets_to_combined_income_ratio"] = (
        df["total_assets"] / df["combined_declared_income"].replace({0: np.nan})
    )
    df["asset_percentile_year"] = percentile_within_year(df, "total_assets")
    df["liability_percentile_year"] = percentile_within_year(df, "liabilities")
    df["criminal_percentile_year"] = percentile_within_year(df, "criminal_cases")
    df["income_mismatch_percentile_year"] = percentile_within_year(
        df.assign(_log_ratio=log1p_positive(df["assets_to_combined_income_ratio"])),
        "_log_ratio",
    )

    repeat_cols = [
        "source_url",
        "prior_year",
        "year_gap",
        "match_score",
        "prior_candidate_name",
        "prior_party",
        "prior_constituency",
        "prior_total_assets",
        "asset_growth_amount",
        "asset_growth_ratio",
        "asset_cagr",
        "prior_liabilities",
        "prior_source_url",
    ]
    repeat_lookup = repeats[repeat_cols].copy() if not repeats.empty else pd.DataFrame(columns=repeat_cols)
    ranked = df.merge(repeat_lookup, on="source_url", how="left")
    ranked["is_repeat_match"] = ranked["prior_year"].notna()

    scores = []
    reasons = []
    confidence = []
    for _, row in ranked.iterrows():
        score = 0.0
        ev: list[str] = []

        total_assets = row.get("total_assets")
        liabilities = row.get("liabilities")
        criminal_cases = row.get("criminal_cases")
        debt_ratio = row.get("debt_to_asset_ratio")
        self_income_ratio = row.get("assets_to_self_income_ratio")
        combined_income_ratio = row.get("assets_to_combined_income_ratio")
        asset_pct = row.get("asset_percentile_year")
        liability_pct = row.get("liability_percentile_year")
        criminal_pct = row.get("criminal_percentile_year")
        mismatch_pct = row.get("income_mismatch_percentile_year")
        cagr = row.get("asset_cagr")
        growth = row.get("asset_growth_amount")
        match = row.get("match_score")

        if pd.notna(asset_pct):
            score += 18 * float(asset_pct)
            if asset_pct >= 0.99:
                ev.append(f"top 1% assets in {int(row['year'])}")
            elif asset_pct >= 0.95:
                ev.append(f"top 5% assets in {int(row['year'])}")
        if pd.notna(total_assets):
            if total_assets >= 100_000_000:
                score += 12
                ev.append(f"assets over 10 crore ({crore(total_assets)})")
            elif total_assets >= 50_000_000:
                score += 6
                ev.append(f"assets over 5 crore ({crore(total_assets)})")

        if pd.notna(liability_pct):
            score += 10 * float(liability_pct)
        if pd.notna(liabilities):
            if liabilities >= 50_000_000:
                score += 10
                ev.append(f"liabilities over 5 crore ({crore(liabilities)})")
            elif liabilities >= 10_000_000:
                score += 4
                ev.append(f"liabilities over 1 crore ({crore(liabilities)})")
        if pd.notna(debt_ratio):
            if debt_ratio >= 1:
                score += 13
                ev.append(f"liabilities exceed assets ({debt_ratio:.2f}x)")
            elif debt_ratio >= 0.5:
                score += 5
                ev.append(f"debt/assets over 50% ({debt_ratio:.2f}x)")

        if pd.notna(mismatch_pct):
            score += 12 * float(mismatch_pct)
        if pd.notna(combined_income_ratio):
            if combined_income_ratio >= 100 and pd.notna(total_assets) and total_assets >= 10_000_000:
                score += 13
                ev.append(f"assets >=100x family ITR income ({combined_income_ratio:.1f}x)")
            elif combined_income_ratio >= 50 and pd.notna(total_assets) and total_assets >= 10_000_000:
                score += 7
                ev.append(f"assets >=50x family ITR income ({combined_income_ratio:.1f}x)")
        elif pd.notna(total_assets) and total_assets >= 10_000_000:
            score += 4
            ev.append("high assets but no usable family ITR income")

        if pd.notna(self_income_ratio):
            if self_income_ratio >= 100 and pd.notna(total_assets) and total_assets >= 10_000_000:
                score += 6
                ev.append(f"assets >=100x self ITR income ({self_income_ratio:.1f}x)")
            elif self_income_ratio >= 50 and pd.notna(total_assets) and total_assets >= 10_000_000:
                score += 3
                ev.append(f"assets >=50x self ITR income ({self_income_ratio:.1f}x)")

        if pd.notna(criminal_pct):
            score += 8 * float(criminal_pct)
        if pd.notna(criminal_cases):
            if criminal_cases >= 10:
                score += 10
                ev.append(f"10+ criminal cases ({int(criminal_cases)})")
            elif criminal_cases >= 5:
                score += 5
                ev.append(f"5+ criminal cases ({int(criminal_cases)})")

        if pd.notna(cagr):
            if cagr >= 0.50:
                score += 24
                ev.append(f"repeat asset CAGR >=50% ({pct(cagr)})")
            elif cagr >= 0.25:
                score += 15
                ev.append(f"repeat asset CAGR >=25% ({pct(cagr)})")
            elif cagr >= 0.12:
                score += 6
                ev.append(f"repeat asset CAGR >=12% ({pct(cagr)})")
        if pd.notna(growth):
            if growth >= 50_000_000:
                score += 18
                ev.append(f"repeat asset growth over 5 crore (+{crore(growth)})")
            elif growth >= 10_000_000:
                score += 8
                ev.append(f"repeat asset growth over 1 crore (+{crore(growth)})")
            elif growth <= -50_000_000:
                score += 6
                ev.append(f"large declared asset drop ({crore(growth)})")

        if str(row.get("pan_given_self", "")).strip().lower() in {"n", "no", "false"}:
            score += 4
            ev.append("PAN not declared")

        if bool(row.get("is_repeat_match")):
            if pd.notna(match) and match >= 100:
                confidence.append("high_repeat_match")
            elif pd.notna(match) and match >= 92:
                confidence.append("medium_repeat_match")
            else:
                confidence.append("review_repeat_match")
        else:
            confidence.append("single_election_record")
            score *= 0.88
            ev.append("single-election record: no growth history available")

        scores.append(round(score, 2))
        reasons.append(evidence_join(ev))

    ranked["suspicion_score"] = scores
    ranked["confidence_level"] = confidence
    ranked["ranking_reasons"] = reasons
    ranked["rank"] = ranked["suspicion_score"].rank(ascending=False, method="first").astype(int)

    ordered = [
        "rank",
        "suspicion_score",
        "confidence_level",
        "ranking_reasons",
        "year",
        "candidate_name",
        "canonical_name",
        "party",
        "constituency",
        "total_assets",
        "liabilities",
        "debt_to_asset_ratio",
        "latest_self_itr_income",
        "latest_spouse_itr_income",
        "combined_declared_income",
        "assets_to_self_income_ratio",
        "assets_to_combined_income_ratio",
        "criminal_cases",
        "asset_percentile_year",
        "liability_percentile_year",
        "income_mismatch_percentile_year",
        "criminal_percentile_year",
        "is_repeat_match",
        "prior_year",
        "year_gap",
        "match_score",
        "prior_candidate_name",
        "prior_party",
        "prior_constituency",
        "prior_total_assets",
        "asset_growth_amount",
        "asset_growth_ratio",
        "asset_cagr",
        "prior_liabilities",
        "source_url",
        "prior_source_url",
    ]
    for col in ordered:
        if col not in ranked.columns:
            ranked[col] = pd.NA
    return ranked[ordered].sort_values(["suspicion_score", "total_assets"], ascending=[False, False])


def build_person_rollup(ranked: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for name, group in ranked.groupby("canonical_name", dropna=False):
        if not name:
            continue
        best = group.sort_values("suspicion_score", ascending=False).iloc[0]
        years = sorted(int(y) for y in group["year"].dropna().unique())
        parties = sorted(str(x) for x in group["party"].dropna().unique() if str(x))
        constituencies = sorted(str(x) for x in group["constituency"].dropna().unique() if str(x))
        rows.append(
            {
                "person_rank": 0,
                "person_suspicion_score": round(
                    float(group["suspicion_score"].max())
                    + min(len(years) - 1, 2) * 4
                    + (6 if group["is_repeat_match"].any() else 0),
                    2,
                ),
                "canonical_name": name,
                "best_candidate_name": best["candidate_name"],
                "years_seen": "|".join(str(y) for y in years),
                "records_seen": len(group),
                "parties_seen": "|".join(parties),
                "constituencies_seen": "|".join(constituencies),
                "max_total_assets": group["total_assets"].max(),
                "max_liabilities": group["liabilities"].max(),
                "max_criminal_cases": group["criminal_cases"].max(),
                "max_asset_cagr": group["asset_cagr"].max(),
                "max_asset_growth_amount": group["asset_growth_amount"].max(),
                "best_record_year": best["year"],
                "best_record_score": best["suspicion_score"],
                "best_record_reasons": best["ranking_reasons"],
                "best_source_url": best["source_url"],
            }
        )
    out = pd.DataFrame(rows).sort_values(
        ["person_suspicion_score", "max_total_assets"], ascending=[False, False]
    )
    out["person_rank"] = range(1, len(out) + 1)
    return out


def save_plot(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    plt.tight_layout()
    plt.savefig(path, dpi=180, bbox_inches="tight")
    plt.close()


def plot_outputs(clean: pd.DataFrame, ranked: pd.DataFrame, repeats: pd.DataFrame, out_dir: Path) -> list[Path]:
    plots = out_dir / PLOTS_DIRNAME
    plots.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []

    plot_df = clean.copy()
    plot_df["total_assets_crore"] = plot_df["total_assets"] / 10_000_000
    plot_df["liabilities_crore"] = plot_df["liabilities"] / 10_000_000

    plt.figure(figsize=(8, 5))
    summary = plot_df.groupby("year")["total_assets_crore"].agg(["median", "mean"])
    summary.plot(kind="bar", ax=plt.gca(), color=["#2f6f73", "#c9822b"])
    plt.ylabel("Assets (crore rupees)")
    plt.xlabel("Election year")
    plt.title("Candidate Assets: Median vs Mean")
    plt.xticks(rotation=0)
    paths.append(plots / "assets_median_mean_by_year.png")
    save_plot(paths[-1])

    plt.figure(figsize=(8, 5))
    data = [
        plot_df.loc[plot_df["year"] == year, "total_assets_crore"].dropna().clip(upper=100)
        for year in sorted(plot_df["year"].dropna().unique())
    ]
    plt.boxplot(
        data,
        tick_labels=[str(int(y)) for y in sorted(plot_df["year"].dropna().unique())],
        showfliers=False,
    )
    plt.ylabel("Assets (crore rupees, clipped at 100)")
    plt.xlabel("Election year")
    plt.title("Asset Distribution by Year")
    paths.append(plots / "asset_distribution_by_year.png")
    save_plot(paths[-1])

    plt.figure(figsize=(9, 5))
    flag_counts = ranked.assign(flagged=ranked["suspicion_score"] >= 55).groupby("year")["flagged"].mean() * 100
    flag_counts.plot(kind="bar", color="#8f3f2b")
    plt.ylabel("Share of records scoring 55+ (%)")
    plt.xlabel("Election year")
    plt.title("High-Risk Triage Share by Year")
    plt.xticks(rotation=0)
    paths.append(plots / "high_risk_share_by_year.png")
    save_plot(paths[-1])

    plt.figure(figsize=(10, 6))
    major_parties = {
        "BJP",
        "INC",
        "CPI(M)",
        "CPI",
        "IND",
        "IUML",
        "Indian Union Muslim League",
        "Kerala Congress",
        "Kerala Congress (M)",
    }
    top_parties = set(
        plot_df.groupby("party")["total_assets"]
        .median()
        .dropna()
        .sort_values(ascending=False)
        .head(12)
        .index
    )
    top_parties.update(p for p in major_parties if p in set(plot_df["party"].dropna()))
    party_year = (
        plot_df[plot_df["party"].isin(top_parties)]
        .groupby(["party", "year"])["total_assets_crore"]
        .median()
        .unstack("year")
        .sort_values(by=max(plot_df["year"].dropna()), ascending=False)
    )
    party_year.plot(kind="bar", ax=plt.gca(), width=0.85)
    plt.ylabel("Median assets (crore rupees)")
    plt.xlabel("Party")
    plt.title("Median Assets by Party and Year")
    plt.xticks(rotation=45, ha="right")
    paths.append(plots / "party_median_assets_by_year.png")
    save_plot(paths[-1])

    plt.figure(figsize=(8, 5))
    r = repeats.dropna(subset=["asset_cagr", "asset_growth_amount"]).copy()
    r = r[(r["prior_total_assets"] > 0) & (r["asset_growth_amount"].abs() < 1_000_000_000)]
    plt.scatter(
        r["asset_cagr"].clip(-1, 2) * 100,
        r["asset_growth_amount"] / 10_000_000,
        alpha=0.45,
        s=24,
        color="#356a9a",
    )
    plt.axvline(25, color="#c9822b", linestyle="--", linewidth=1)
    plt.axvline(50, color="#8f3f2b", linestyle="--", linewidth=1)
    plt.ylabel("Asset growth (crore rupees)")
    plt.xlabel("Asset CAGR (%)")
    plt.title("Repeat Candidate Asset Growth vs CAGR")
    paths.append(plots / "repeat_growth_vs_cagr.png")
    save_plot(paths[-1])

    plt.figure(figsize=(8, 5))
    top = ranked.head(25).sort_values("suspicion_score")
    labels = [textwrap.shorten(str(x), width=34, placeholder="...") for x in top["candidate_name"]]
    plt.barh(labels, top["suspicion_score"], color="#783f8e")
    plt.xlabel("Suspicion score")
    plt.title("Top 25 Candidate-Election Triage Scores")
    paths.append(plots / "top25_candidate_scores.png")
    save_plot(paths[-1])

    return paths


def write_report(
    ranked: pd.DataFrame,
    people: pd.DataFrame,
    clean: pd.DataFrame,
    repeats: pd.DataFrame,
    plots: list[Path],
    out_dir: Path,
) -> Path:
    report = out_dir / "deep_financial_analysis_report.md"
    yearly = []
    for year, group in ranked.groupby("year"):
        yearly.append(
            f"- {int(year)}: {len(group)} records, "
            f"median assets {crore(group['total_assets'].median())}, "
            f"90th percentile assets {crore(group['total_assets'].quantile(0.90))}, "
            f"high-risk score>=55 records {(group['suspicion_score'] >= 55).sum()}"
        )

    top_lines = []
    for _, row in ranked.head(30).iterrows():
        top_lines.append(
            f"- #{int(row['rank'])} score {row['suspicion_score']}: "
            f"{row['candidate_name']} ({int(row['year'])}, {row['party']}, {row['constituency']}) "
            f"assets={crore(row['total_assets'])}; reasons={row['ranking_reasons']}"
        )

    person_lines = []
    for _, row in people.head(20).iterrows():
        person_lines.append(
            f"- #{int(row['person_rank'])} score {row['person_suspicion_score']}: "
            f"{row['best_candidate_name']} years={row['years_seen']} "
            f"max_assets={crore(row['max_total_assets'])}; reasons={row['best_record_reasons']}"
        )

    plot_lines = [f"- `{path.relative_to(out_dir)}`" for path in plots]

    text = "\n".join(
        [
            "# Deep Kerala Candidate Financial Triage",
            "",
            "This is a prioritization report, not proof of corruption or illegality. "
            "Use the ranking to decide which MyNeta/ADR affidavit pages to manually verify first.",
            "",
            "## Method",
            "",
            "- Every candidate-election row is ranked, including one-election-only candidates.",
            "- Repeat candidates receive extra weight for high asset CAGR and large absolute asset growth.",
            "- Single-election candidates are still ranked, but marked `single_election_record` because there is no growth history.",
            "- The score combines year-relative asset percentile, liabilities, debt/assets, income mismatch, criminal cases, PAN status, and repeat-growth signals.",
            "",
            "## Coverage",
            "",
            f"- candidate-election records ranked: {len(ranked)}",
            f"- person-level rollups ranked: {len(people)}",
            f"- repeat matches used: {len(repeats)}",
            "",
            "## Year Signals",
            "",
            *yearly,
            "",
            "## Top Candidate-Election Suspect Ranking",
            "",
            *top_lines,
            "",
            "## Top Person-Level Suspect Ranking",
            "",
            *person_lines,
            "",
            "## Plots",
            "",
            *plot_lines,
            "",
            "## Suggested Manual Review Order",
            "",
            "1. Start with `candidate_election_suspect_ranking.csv` rank 1-100.",
            "2. Cross-check source URLs and original affidavits, especially fuzzy repeat matches below perfect confidence.",
            "3. For single-election records, treat the ranking as cross-sectional only: high assets/income mismatch can be suspicious, but no trend can be inferred.",
            "4. Use `person_suspect_ranking.csv` to inspect repeat candidates across years.",
            "",
        ]
    )
    report.write_text(text, encoding="utf-8")
    return report


def run(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    clean = read_csv(PROCESSED_DIR / "kerala_financial_clean_2016_2021_2026.csv")
    repeats = read_csv(PROCESSED_DIR / "kerala_repeat_candidate_growth.csv")

    ranked = score_candidates(clean, repeats)
    people = build_person_rollup(ranked)

    ranked_path = out_dir / "candidate_election_suspect_ranking.csv"
    people_path = out_dir / "person_suspect_ranking.csv"
    ranked.to_csv(ranked_path, index=False)
    people.to_csv(people_path, index=False)

    plots = plot_outputs(clean, ranked, repeats, out_dir)
    report = write_report(ranked, people, clean, repeats, plots, out_dir)

    print(f"candidate ranking: {len(ranked)} -> {ranked_path}")
    print(f"person ranking: {len(people)} -> {people_path}")
    print(f"plots: {len(plots)} -> {out_dir / PLOTS_DIRNAME}")
    print(f"report: {report}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Deeper financial triage ranking and charts.")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    args = parser.parse_args()
    run(args.out_dir)


if __name__ == "__main__":
    main()
