from __future__ import annotations

import argparse
import csv
import hashlib
import random
import re
import sys
import time
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qs, urljoin, urlparse

import pandas as pd
import requests
from bs4 import BeautifulSoup
from tqdm import tqdm

try:
    from rapidfuzz import fuzz
except ModuleNotFoundError:
    fuzz = None


BASE = "https://www.myneta.info"
DATA_DIR = Path("data")
RAW_DIR = DATA_DIR / "raw_html"
PROCESSED_DIR = DATA_DIR / "processed"
BLOCK_STATUSES = {403, 429, 500, 502, 503}


@dataclass(frozen=True)
class Election:
    year: int

    @property
    def slug(self) -> str:
        return f"Kerala{self.year}"

    @property
    def base_url(self) -> str:
        return f"{BASE}/{self.slug}/"

    @property
    def summary_url(self) -> str:
        return (
            f"{self.base_url}index.php?action=summary"
            "&subAction=candidates_analyzed&sort=candidate"
        )

    @property
    def raw_dir(self) -> Path:
        return RAW_DIR / self.slug

    @property
    def summary_dir(self) -> Path:
        return self.raw_dir / "summary"

    @property
    def candidate_dir(self) -> Path:
        return self.raw_dir / "candidates"

    @property
    def links_csv(self) -> Path:
        return PROCESSED_DIR / f"myneta_kerala_{self.year}_candidate_links.csv"

    @property
    def details_csv(self) -> Path:
        return PROCESSED_DIR / f"myneta_kerala_{self.year}_financial_details.csv"


@dataclass
class Throttle:
    delay_min: float
    delay_max: float
    slow_seconds: float
    multiplier: float = 1.0

    def delay(self) -> float:
        return random.uniform(self.delay_min, self.delay_max) * self.multiplier

    def observe(self, elapsed: float) -> None:
        if elapsed >= self.slow_seconds:
            self.multiplier = min(self.multiplier * 1.5, 8.0)
        elif elapsed < max(0.5, self.slow_seconds / 3):
            self.multiplier = max(self.multiplier * 0.9, 0.5)


def normalize_space(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(value.replace("\xa0", " ").split())


def to_base10_string(value: int, base: int) -> str:
    digits = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ+/"
    if value == 0:
        return "0"
    output = ""
    while value > 0:
        output = digits[value % base] + output
        value = (value - (value % base)) // base
    return output


def decode_myneta_token(token: str, source_base: int, target_base: int) -> str:
    digits = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ+/"
    allowed = digits[:source_base]
    total = 0
    for power, char in enumerate(reversed(token)):
        if char not in allowed:
            continue
        total += allowed.index(char) * (source_base**power)
    return to_base10_string(total, target_base)


def decode_myneta_obfuscated_scripts(html: str) -> str:
    """Decode MyNeta's inline document.write/eval rows into normal HTML."""
    eval_pattern = re.compile(
        r'eval\(function\(h,u,n,t,e,r\).*?\}\("([^"]+)",(\d+),"([^"]+)",(\d+),(\d+),(\d+)\)\)',
        flags=re.S,
    )

    def decode_eval(match: re.Match[str]) -> str:
        payload, _unused, alphabet, offset, delimiter_index, _r = match.groups()
        offset_int = int(offset)
        base = int(delimiter_index)
        delimiter = alphabet[base]
        decoded = ""
        for token in payload.split(delimiter):
            if not token:
                continue
            numeric = token
            for index, char in enumerate(alphabet):
                numeric = numeric.replace(char, str(index))
            try:
                decoded += chr(int(decode_myneta_token(numeric, base, 10)) - offset_int)
            except ValueError:
                continue
        decoded = decoded.encode("latin1", errors="ignore").decode("utf-8", errors="ignore")
        decoded_parts = []
        for write_match in re.finditer(r"document\.write\('(.*?)'\);", decoded, flags=re.S):
            part = write_match.group(1)
            part = part.replace("\\'", "'").replace('\\"', '"').replace("\\/", "/")
            decoded_parts.append(part)
        return "\n".join(decoded_parts)

    def replace_script(match: re.Match[str]) -> str:
        content = match.group(1)
        if "eval(function" not in content:
            return match.group(0)
        decoded = eval_pattern.sub(decode_eval, content)
        return decoded if decoded.strip() else match.group(0)

    return re.sub(r"<script[^>]*>(.*?)</script>", replace_script, html, flags=re.S | re.I)


def rupees_to_int(value: str | None) -> int | None:
    if not value:
        return None
    text = normalize_space(value)
    match = re.search(r"(?:Rs\.?|₹)?\s*([0-9][0-9,]*)", text, flags=re.I)
    if not match:
        return None
    return int(match.group(1).replace(",", ""))


def int_text(value: int | None) -> str:
    return "" if value is None else str(value)


def safe_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]


def candidate_id_from_url(url: str) -> str:
    query = parse_qs(urlparse(url).query)
    candidate_id = query.get("candidate_id", [""])[0]
    return candidate_id or safe_hash(url)


def summary_url_for_page(election: Election, page: int) -> str:
    if page <= 1:
        return election.summary_url
    return f"{election.summary_url}&page={page}"


def summary_file_for_page(election: Election, page: int) -> Path:
    return election.summary_dir / f"summary_page_{page:03d}.html"


def candidate_file_for_url(election: Election, url: str) -> Path:
    return election.candidate_dir / f"candidate_{candidate_id_from_url(url)}.html"


def make_session(contact: str) -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": f"KeralaElectionResearch/1.0 ({contact})",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Connection": "close",
        }
    )
    return session


def fetch_html(
    session: requests.Session,
    url: str,
    out_file: Path,
    throttle: Throttle,
    force: bool,
) -> bool:
    out_file.parent.mkdir(parents=True, exist_ok=True)
    if out_file.exists() and out_file.stat().st_size > 0 and not force:
        return True

    delay = throttle.delay()
    print(f"sleep {delay:.1f}s x{throttle.multiplier:.2f} -> {url}")
    time.sleep(delay)

    started = time.monotonic()
    response = session.get(url, timeout=45)
    throttle.observe(time.monotonic() - started)
    if response.status_code in BLOCK_STATUSES:
        print(f"stopping on status {response.status_code}: {url}", file=sys.stderr)
        return False
    response.raise_for_status()
    out_file.write_text(response.text, encoding="utf-8", errors="replace")
    return True


def discover_page_count(html: str) -> int:
    soup = BeautifulSoup(html, "lxml")
    text = normalize_space(soup.get_text(" "))
    match = re.search(r"of\s+(\d+)\s+pages", text, flags=re.I)
    if match:
        return int(match.group(1))

    pages = [1]
    for a in soup.find_all("a", href=True):
        query = parse_qs(urlparse(a["href"]).query)
        for value in query.get("page", []):
            if value.isdigit():
                pages.append(int(value))
    return max(pages)


def find_summary_table(soup: BeautifulSoup):
    for table in soup.find_all("table"):
        text = normalize_space(table.get_text(" "))
        if all(key in text for key in ("Candidate", "Constituency", "Party")):
            if "Total Assets" in text or "Liabilities" in text:
                return table
    return None


def parse_summary_html(election: Election, html_file: Path) -> list[dict[str, str]]:
    html = html_file.read_text(encoding="utf-8", errors="replace")
    soup = BeautifulSoup(decode_myneta_obfuscated_scripts(html), "lxml")
    table = find_summary_table(soup)
    if table is None:
        return []

    rows: list[dict[str, str]] = []
    for tr in table.find_all("tr"):
        cells = tr.find_all(["td", "th"])
        if len(cells) < 7:
            continue
        if normalize_space(cells[0].get_text()).lower() in {"sno", "sl no"}:
            continue

        link = cells[1].find("a", href=True)
        if not link:
            continue

        profile_url = urljoin(election.base_url, link["href"])
        sno = normalize_space(cells[0].get_text())
        if not sno or not re.search(r"\d", sno):
            continue

        rows.append(
            {
                "year": str(election.year),
                "sno": sno,
                "candidate_name": normalize_space(link.get_text(" ")),
                "constituency": normalize_space(cells[2].get_text(" ")),
                "party": normalize_space(cells[3].get_text(" ")),
                "criminal_cases": normalize_space(cells[4].get_text(" ")),
                "education": normalize_space(cells[5].get_text(" ")),
                "total_assets_text": normalize_space(cells[6].get_text(" ")),
                "total_assets": int_text(rupees_to_int(cells[6].get_text(" "))),
                "liabilities_text": normalize_space(cells[7].get_text(" ")) if len(cells) > 7 else "",
                "liabilities": int_text(rupees_to_int(cells[7].get_text(" "))) if len(cells) > 7 else "",
                "profile_url": profile_url,
                "raw_summary_file": str(html_file),
            }
        )
    return rows


def write_rows(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def extract_after_label(text: str, label: str) -> str:
    pattern = rf"{re.escape(label)}\s*:?\s*(.+?)(?=\n[A-Z][A-Za-z /&().-]{{2,}}\s*:|\n###|\Z)"
    match = re.search(pattern, text, flags=re.I | re.S)
    return normalize_space(match.group(1)) if match else ""


def soup_lines(soup: BeautifulSoup) -> str:
    lines = [normalize_space(line) for line in soup.get_text("\n").splitlines()]
    return "\n".join(line for line in lines if line)


def section_between(text: str, start: str, stops: Iterable[str]) -> str:
    start_match = re.search(re.escape(start), text, flags=re.I)
    if not start_match:
        return ""
    tail = text[start_match.end() :]
    stop_positions = [
        match.start()
        for stop in stops
        if (match := re.search(re.escape(stop), tail, flags=re.I))
    ]
    return tail[: min(stop_positions)] if stop_positions else tail


def last_rupee_in_section(section: str) -> int | None:
    values = re.findall(r"Rs\s*([0-9][0-9,]*)", section, flags=re.I)
    if not values:
        values = re.findall(r"\b([0-9][0-9,]{3,})\b", section)
    if not values:
        return None
    return int(values[-1].replace(",", ""))


def first_rupee_after(text: str, label: str) -> int | None:
    match = re.search(re.escape(label) + r".{0,120}?Rs\s*([0-9][0-9,]*)", text, flags=re.I | re.S)
    if not match:
        return None
    return int(match.group(1).replace(",", ""))


def html_label_value(soup: BeautifulSoup, label: str) -> str:
    label_clean = label.rstrip(":").lower()
    for bold in soup.find_all("b"):
        bold_text = normalize_space(bold.get_text(" ")).rstrip(":").lower()
        if bold_text != label_clean:
            continue
        parts = []
        for sibling in bold.next_siblings:
            name = getattr(sibling, "name", None)
            if name == "br":
                break
            if name == "b":
                break
            value = normalize_space(sibling.get_text(" ") if hasattr(sibling, "get_text") else str(sibling))
            if value:
                parts.append(value)
        return normalize_space(" ".join(parts))
    return ""


def candidate_name_from_soup(soup: BeautifulSoup, election: Election) -> str:
    for h2 in soup.find_all("h2"):
        value = normalize_space(h2.get_text(" "))
        if value and value.lower() != f"kerala {election.year}".lower():
            return value
    breadcrumb = soup.find("div", class_=re.compile("w3-panel"))
    if breadcrumb:
        bold = breadcrumb.find("b")
        if bold:
            return normalize_space(bold.get_text(" "))
    return ""


def parse_candidate_html(election: Election, html_file: Path, profile_url: str = "") -> dict[str, str]:
    html = html_file.read_text(encoding="utf-8", errors="replace")
    soup = BeautifulSoup(html, "lxml")
    text = soup_lines(soup)

    name = candidate_name_from_soup(soup, election)
    h5 = soup.find("h5")
    constituency_detail = normalize_space(h5.get_text(" ")) if h5 else ""

    movable_section = section_between(
        text,
        "Details of Movable Assets",
        ["Details of Immovable Assets", "Details of Liabilities"],
    )
    immovable_section = section_between(
        text,
        "Details of Immovable Assets",
        ["Details of Liabilities", "Profession or Occupation"],
    )
    liability_section = section_between(
        text,
        "Details of Liabilities",
        ["Profession or Occupation", "Sources Of Income"],
    )

    education_block = section_between(
        text,
        "Educational Details",
        ["Details of PAN", "Details of Criminal Cases"],
    )
    income_section = section_between(
        text,
        "Details of PAN and status of Income Tax return",
        ["Data Readability Report of PAN", "Details of Criminal Cases"],
    )
    source_income_section = section_between(
        text,
        "Sources Of Income (Details)",
        ["Contracts with appropriate Govt.", "If you notice any discrepancy"],
    )

    criminal_match = re.search(r"Number of Criminal Cases:\s*(\d+)", text, flags=re.I)
    age_match = re.search(r"\bAge:\s*(\d+)", text, flags=re.I)
    education_category_match = re.search(r"Category:\s*([^\n]+)", education_block, flags=re.I)

    pan_given = ""
    pan_match = re.search(r"\bself\s+([YN])\b", income_section, flags=re.I)
    if pan_match:
        pan_given = pan_match.group(1).upper()

    latest_self_income = first_rupee_after(income_section, "self")
    latest_spouse_income = first_rupee_after(income_section, "spouse")

    readability = {}
    for label in ("PAN and Income Tax", "Criminal Cases", "Movable Assets", "Immovable Assets", "Liabilities"):
        match = re.search(
            rf"Data Readability Report of {re.escape(label)}\s*:?\s*([^\n]+)",
            text,
            flags=re.I,
        )
        readability[label.lower().replace(" ", "_")] = normalize_space(match.group(1)) if match else ""

    result = {
        "year": str(election.year),
        "candidate_name": name,
        "constituency_detail": constituency_detail,
        "party": html_label_value(soup, "Party"),
        "age": age_match.group(1) if age_match else "",
        "self_profession": html_label_value(soup, "Self Profession"),
        "spouse_profession": html_label_value(soup, "Spouse Profession"),
        "criminal_cases": criminal_match.group(1) if criminal_match else "",
        "education_category": normalize_space(education_category_match.group(1)) if education_category_match else "",
        "education_details": normalize_space(education_block),
        "pan_given_self": pan_given,
        "latest_self_itr_income": int_text(latest_self_income),
        "latest_spouse_itr_income": int_text(latest_spouse_income),
        "source_of_income": normalize_space(source_income_section),
        "total_assets": int_text(first_rupee_after(text, "Assets:")),
        "liabilities": int_text(first_rupee_after(text, "Liabilities:")),
        "movable_assets": int_text(last_rupee_in_section(movable_section)),
        "immovable_assets": int_text(last_rupee_in_section(immovable_section)),
        "liabilities_calculated": int_text(last_rupee_in_section(liability_section)),
        "readability_pan_income_tax": readability.get("pan_and_income_tax", ""),
        "readability_criminal_cases": readability.get("criminal_cases", ""),
        "readability_movable_assets": readability.get("movable_assets", ""),
        "readability_immovable_assets": readability.get("immovable_assets", ""),
        "readability_liabilities": readability.get("liabilities", ""),
        "source_url": profile_url,
        "raw_html_file": str(html_file),
    }
    return result


def crawl(args: argparse.Namespace) -> None:
    election = Election(args.year)
    session = make_session(args.contact)
    throttle = Throttle(args.delay_min, args.delay_max, args.slow_seconds)

    first_file = summary_file_for_page(election, 1)
    ok = fetch_html(session, election.summary_url, first_file, throttle, args.force)
    if not ok:
        raise SystemExit(1)

    discovered_pages = discover_page_count(first_file.read_text(encoding="utf-8", errors="replace"))
    page_count = args.summary_pages or discovered_pages
    if args.max_summary_pages:
        page_count = min(page_count, args.max_summary_pages)
    print(f"summary pages: {page_count} (discovered {discovered_pages})")

    for page in range(2, page_count + 1):
        ok = fetch_html(
            session,
            summary_url_for_page(election, page),
            summary_file_for_page(election, page),
            throttle,
            args.force,
        )
        if not ok:
            raise SystemExit(1)
        if args.pause_every and page % args.pause_every == 0:
            print(f"pause {args.pause_seconds:.1f}s after {page} summary pages")
            time.sleep(args.pause_seconds)

    rows: list[dict[str, str]] = []
    for html_file in sorted(election.summary_dir.glob("summary_page_*.html")):
        rows.extend(parse_summary_html(election, html_file))

    seen = set()
    unique_rows = []
    for row in rows:
        if row["profile_url"] in seen:
            continue
        seen.add(row["profile_url"])
        unique_rows.append(row)

    write_rows(election.links_csv, unique_rows)
    print(f"candidate links: {len(unique_rows)} -> {election.links_csv}")

    candidates = unique_rows[: args.max_candidates] if args.max_candidates else unique_rows
    for index, row in enumerate(tqdm(candidates, desc=f"download {election.slug} candidates"), start=1):
        out_file = candidate_file_for_url(election, row["profile_url"])
        ok = fetch_html(
            session,
            row["profile_url"],
            out_file,
            throttle,
            args.force,
        )
        if not ok:
            raise SystemExit(1)
        if args.pause_every and index % args.pause_every == 0:
            print(f"pause {args.pause_seconds:.1f}s after {index} candidate pages")
            time.sleep(args.pause_seconds)


def parse(args: argparse.Namespace) -> None:
    election = Election(args.year)
    if not election.links_csv.exists():
        summary_rows = []
        for html_file in sorted(election.summary_dir.glob("summary_page_*.html")):
            summary_rows.extend(parse_summary_html(election, html_file))
        write_rows(election.links_csv, summary_rows)

    links = pd.read_csv(election.links_csv, dtype=str).fillna("")
    details: list[dict[str, str]] = []
    for row in links.to_dict("records"):
        html_file = candidate_file_for_url(election, row["profile_url"])
        if not html_file.exists():
            continue
        parsed = parse_candidate_html(election, html_file, row["profile_url"])
        for key in ("candidate_name", "party", "criminal_cases"):
            if not parsed.get(key) and row.get(key):
                parsed[key] = row[key]
        parsed["constituency"] = row.get("constituency", "")
        parsed["summary_total_assets"] = row.get("total_assets", "")
        parsed["summary_liabilities"] = row.get("liabilities", "")
        details.append(parsed)

    write_rows(election.details_csv, details)
    print(f"parsed candidates: {len(details)} -> {election.details_csv}")


def combine(args: argparse.Namespace) -> None:
    frames = []
    for year in args.years:
        election = Election(int(year))
        if election.details_csv.exists():
            frames.append(pd.read_csv(election.details_csv, dtype=str).fillna(""))
    if not frames:
        raise SystemExit("No per-year detail CSVs found.")
    combined = pd.concat(frames, ignore_index=True)
    out_file = PROCESSED_DIR / "kerala_candidates_financial_2016_2021_2026.csv"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(out_file, index=False)
    print(f"combined rows: {len(combined)} -> {out_file}")


def best_prior_match(row: pd.Series, prior: pd.DataFrame) -> tuple[int, pd.Series | None]:
    if prior.empty:
        return 0, None
    target = f"{row.get('candidate_name', '')} {row.get('party', '')}".strip()
    best_score = 0
    best_row = None
    for _, candidate in prior.iterrows():
        candidate_key = f"{candidate.get('candidate_name', '')} {candidate.get('party', '')}".strip()
        if fuzz is not None:
            score = fuzz.token_sort_ratio(target, candidate_key)
        else:
            target_tokens = " ".join(sorted(target.lower().split()))
            candidate_tokens = " ".join(sorted(candidate_key.lower().split()))
            score = int(100 * SequenceMatcher(None, target_tokens, candidate_tokens).ratio())
        if row.get("constituency") and row.get("constituency") == candidate.get("constituency"):
            score += 8
        if score > best_score:
            best_score = score
            best_row = candidate
    return best_score, best_row


def flags(args: argparse.Namespace) -> None:
    combined_file = PROCESSED_DIR / "kerala_candidates_financial_2016_2021_2026.csv"
    if not combined_file.exists():
        combine(args)

    df = pd.read_csv(combined_file, dtype=str).fillna("")
    for column in ("total_assets", "liabilities", "criminal_cases"):
        df[f"{column}_num"] = pd.to_numeric(df[column], errors="coerce")

    rows = []
    years = sorted(int(year) for year in args.years)
    for year in years[1:]:
        current = df[df["year"].astype(str) == str(year)]
        prior = df[df["year"].astype(str).isin([str(y) for y in years if y < year])]
        for _, row in current.iterrows():
            score, match = best_prior_match(row, prior)
            if match is None or score < args.match_threshold:
                continue
            old_assets = pd.to_numeric(match.get("total_assets"), errors="coerce")
            new_assets = pd.to_numeric(row.get("total_assets"), errors="coerce")
            asset_growth_ratio = ""
            asset_growth_amount = ""
            if pd.notna(old_assets) and pd.notna(new_assets):
                asset_growth_amount = int(new_assets - old_assets)
                if old_assets > 0:
                    asset_growth_ratio = round(float(new_assets / old_assets), 3)

            flags_found = []
            if isinstance(asset_growth_ratio, float) and asset_growth_ratio >= args.asset_growth_ratio:
                flags_found.append("high_asset_growth_ratio")
            if isinstance(asset_growth_amount, int) and asset_growth_amount >= args.asset_growth_amount:
                flags_found.append("high_asset_growth_amount")
            if pd.to_numeric(row.get("liabilities"), errors="coerce") and pd.to_numeric(row.get("liabilities"), errors="coerce") >= args.high_liability:
                flags_found.append("high_liabilities")
            if pd.to_numeric(row.get("criminal_cases"), errors="coerce") and pd.to_numeric(row.get("criminal_cases"), errors="coerce") >= args.criminal_cases:
                flags_found.append("many_criminal_cases")

            if flags_found:
                rows.append(
                    {
                        "candidate_name": row.get("candidate_name", ""),
                        "party": row.get("party", ""),
                        "constituency": row.get("constituency", ""),
                        "year": row.get("year", ""),
                        "matched_prior_year": match.get("year", ""),
                        "matched_prior_name": match.get("candidate_name", ""),
                        "match_score": score,
                        "prior_assets": match.get("total_assets", ""),
                        "current_assets": row.get("total_assets", ""),
                        "asset_growth_amount": asset_growth_amount,
                        "asset_growth_ratio": asset_growth_ratio,
                        "current_liabilities": row.get("liabilities", ""),
                        "current_criminal_cases": row.get("criminal_cases", ""),
                        "flags": "|".join(flags_found),
                        "source_url": row.get("source_url", ""),
                        "prior_source_url": match.get("source_url", ""),
                    }
                )

    out_file = PROCESSED_DIR / "kerala_candidate_pattern_flags.csv"
    write_rows(out_file, rows)
    print(f"flagged rows: {len(rows)} -> {out_file}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Kerala MyNeta cache-first scraper and parser")
    sub = parser.add_subparsers(required=True)

    crawl_parser = sub.add_parser("crawl", help="download summary pages, extract links, download candidate pages")
    crawl_parser.add_argument("--year", type=int, required=True, choices=[2016, 2021, 2026])
    crawl_parser.add_argument("--summary-pages", type=int, default=0, help="pin page count; 0 means discover")
    crawl_parser.add_argument("--max-summary-pages", type=int, default=0, help="debug limit for summary pages")
    crawl_parser.add_argument("--max-candidates", type=int, default=0, help="debug limit for candidate pages")
    crawl_parser.add_argument("--delay-min", type=float, default=2.0)
    crawl_parser.add_argument("--delay-max", type=float, default=4.0)
    crawl_parser.add_argument("--slow-seconds", type=float, default=8.0)
    crawl_parser.add_argument("--pause-every", type=int, default=100)
    crawl_parser.add_argument("--pause-seconds", type=float, default=60.0)
    crawl_parser.add_argument("--contact", default="contact: research@example.com")
    crawl_parser.add_argument("--force", action="store_true")
    crawl_parser.set_defaults(func=crawl)

    parse_parser = sub.add_parser("parse", help="parse cached candidate pages into CSV")
    parse_parser.add_argument("--year", type=int, required=True, choices=[2016, 2021, 2026])
    parse_parser.set_defaults(func=parse)

    combine_parser = sub.add_parser("combine", help="combine parsed year CSVs")
    combine_parser.add_argument("--years", nargs="+", default=["2016", "2021", "2026"])
    combine_parser.set_defaults(func=combine)

    flags_parser = sub.add_parser("flags", help="generate basic cross-year red-flag pattern file")
    flags_parser.add_argument("--years", nargs="+", default=["2016", "2021", "2026"])
    flags_parser.add_argument("--match-threshold", type=int, default=88)
    flags_parser.add_argument("--asset-growth-ratio", type=float, default=3.0)
    flags_parser.add_argument("--asset-growth-amount", type=int, default=10_000_000)
    flags_parser.add_argument("--high-liability", type=int, default=10_000_000)
    flags_parser.add_argument("--criminal-cases", type=int, default=5)
    flags_parser.set_defaults(func=flags)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if hasattr(args, "delay_min") and args.delay_max < args.delay_min:
        parser.error("--delay-max must be >= --delay-min")
    args.func(args)


if __name__ == "__main__":
    main()
