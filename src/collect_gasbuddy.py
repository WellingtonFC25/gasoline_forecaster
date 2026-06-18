from __future__ import annotations

import json
import re
import time
from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup

from utils import (
    RETAIL_DIR,
    ensure_directories,
    generate_station_id,
    log_message,
    normalize_text,
    timestamp_for_filename,
    timestamp_iso,
)

try:
    from selenium import webdriver
    from selenium.common.exceptions import WebDriverException
    from selenium.webdriver.chrome.options import Options as ChromeOptions
    from selenium.webdriver.chrome.service import Service as ChromeService
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait
    from webdriver_manager.chrome import ChromeDriverManager

    SELENIUM_AVAILABLE = True
except Exception:
    webdriver = None
    WebDriverException = Exception
    ChromeOptions = None
    ChromeService = None
    ChromeDriverManager = None
    By = None
    EC = None
    WebDriverWait = None
    SELENIUM_AVAILABLE = False

# FIX (c): Added Bronx to AREA_URLS
AREA_URLS = {
    "Manhattan": "https://www.gasbuddy.com/gasprices/new-york/manhattan",
    "Brooklyn": "https://www.gasbuddy.com/gasprices/new-york/brooklyn",
    "Queens": "https://www.gasbuddy.com/gasprices/new-york/queens",
    "Staten Island": "https://www.gasbuddy.com/gasprices/new-york/staten-island",
    "Bronx": "https://www.gasbuddy.com/gasprices/new-york/bronx",
}

SNAPSHOT_COLUMNS = [
    "station_id",
    "station_name",
    "address",
    "area",
    "price_regular",
    "timestamp",
    "source_url",
]

OUTPUT_STEM = "retail_snapshot"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

PRICE_RE = re.compile(r"(?<!\d)([1-8]\.\d{2,3})(?!\d)")
APOLLO_STATE_PATTERNS = [
    re.compile(r"window\.__APOLLO_STATE__\s*=\s*(.*?);\s*window\.gbcsrf", re.S),
    re.compile(r"window\.__APOLLO_STATE__\s*=\s*(.*?);\s*</script>", re.S),
]
ADDRESS_RE = re.compile(
    r"\b\d{1,5}\s+[A-Za-z0-9][A-Za-z0-9.\-'\s]{2,80}\b"
    r"(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Lane|Ln|Drive|Dr|Place|Pl|"
    r"Parkway|Pkwy|Court|Ct|Terrace|Ter|Way|Highway|Hwy|Expressway|Expy)\b",
    re.IGNORECASE,
)
CANDIDATE_SELECTORS = [
    "[data-testid*='station']",
    "[class*='StationDisplay']",
    "[class*='stationDisplay']",
    "[class*='GenericStationListItem']",
    "[class*='StationItem']",
    "[class*='station-card']",
    "article",
    "li",
]
NOISE_TERMS = {
    "regular", "midgrade", "premium", "diesel", "reported", "ago",
    "minutes", "hours", "day", "cash", "credit", "member", "membership",
    "gasbuddy", "loading", "directions",
}


def build_driver():
    if not SELENIUM_AVAILABLE:
        return None
    try:
        options = ChromeOptions()
        options.add_argument("--headless")
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--window-size=1600,2200")
        options.add_argument(f"--user-agent={USER_AGENT}")
        service = ChromeService(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
        driver.set_page_load_timeout(45)
        return driver
    except Exception as exc:
        log_message(
            f"Chrome WebDriver unavailable ({exc}).",
            level="WARNING",
            log_name="retail_collection.log",
        )
        return None


def fetch_html_with_selenium(driver, url: str) -> str:
    driver.get(url)
    # FIX (a): Wait for body first, then wait explicitly for station cards to render.
    # Replaces the old sleep(2) + 4 generic scrolls (~8s) with a targeted wait.
    WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
    try:
        # Wait up to 30s for at least one station card to appear in the DOM
        WebDriverWait(driver, 30).until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, "[class*='stationListItem___']")
            )
        )
    except Exception:
        # Fallback sleep if the selector never appears (e.g. zero results page)
        log_message(
            f"Station cards not found via WebDriverWait for {url}, using sleep fallback.",
            level="WARNING",
            log_name="retail_collection.log",
        )
        time.sleep(5)
    # One final scroll to trigger any lazy-loaded cards below the fold
    driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
    time.sleep(2)
    return driver.page_source


def fetch_html_with_requests(url: str) -> str:
    response = requests.get(
        url,
        timeout=30,
        headers={
            "User-Agent": USER_AGENT,
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    if response.status_code in {403, 429} and "Just a moment..." in response.text:
        raise RuntimeError(
            "Direct request blocked by GasBuddy anti-bot challenge; Selenium is required."
        )
    response.raise_for_status()
    return response.text


def parse_price_from_text(text: str) -> float | None:
    for match in PRICE_RE.findall(text):
        price = float(match)
        if 1.0 <= price <= 8.0:
            return price
    return None


def parse_address_from_lines(lines: list[str]) -> str:
    for line in lines:
        match = ADDRESS_RE.search(line)
        if match:
            return normalize_text(match.group(0))
    return ""


def is_name_candidate(line: str) -> bool:
    lowered = line.lower()
    if len(line) < 2 or len(line) > 120:
        return False
    if ADDRESS_RE.search(line) or PRICE_RE.search(line):
        return False
    if re.fullmatch(r"[$0-9.\s]+", line):
        return False
    return not any(term in lowered for term in NOISE_TERMS)


def dedupe_lines(lines: list[str]) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for line in lines:
        normalized = normalize_text(line)
        if not normalized:
            continue
        lowered = normalized.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        cleaned.append(normalized)
    return cleaned


def parse_station_name(lines: list[str], address: str) -> str:
    if address:
        for idx, line in enumerate(lines):
            if address.lower() in line.lower():
                for back_idx in range(idx - 1, -1, -1):
                    candidate = lines[back_idx]
                    if is_name_candidate(candidate):
                        return candidate
                break
    for line in lines:
        if is_name_candidate(line):
            return line
    return ""


def parse_station_text(
    block_text: str, area: str, source_url: str, collected_at: str
) -> dict | None:
    lines = dedupe_lines(block_text.splitlines())
    if not lines:
        return None
    joined_text = "\n".join(lines)
    price = parse_price_from_text(joined_text)
    if price is None:
        return None
    address = parse_address_from_lines(lines)
    name = parse_station_name(lines, address)
    if not name and not address:
        return None
    return {
        "station_id": generate_station_id(name or "unknown", address),
        "station_name": name,
        "address": address,
        "area": area,
        "price_regular": price,
        "timestamp": collected_at,
        "source_url": source_url,
    }


def parse_apollo_state(html: str) -> dict | None:
    for pattern in APOLLO_STATE_PATTERNS:
        match = pattern.search(html)
        if not match:
            continue
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
    return None


def format_address(address: dict | None) -> str:
    if not isinstance(address, dict):
        return ""
    parts = [
        address.get("line1"),
        address.get("line2"),
        address.get("locality"),
        address.get("region"),
        address.get("postalCode"),
    ]
    return normalize_text(", ".join(part for part in parts if part))


def choose_regular_price(station: dict) -> float | None:
    price_reports = station.get('prices({"fuel":1})') or []
    if not isinstance(price_reports, list):
        return None
    for report in price_reports:
        if not isinstance(report, dict):
            continue
        cash_price = (report.get("cash") or {}).get("price")
        credit_price = (report.get("credit") or {}).get("price")
        for candidate in (cash_price, credit_price):
            try:
                price = float(candidate)
            except (TypeError, ValueError):
                continue
            if 1.0 <= price <= 8.0:
                return price
    return None


def parse_station_records_from_apollo_state(
    html: str, area: str, source_url: str, collected_at: str
) -> list[dict]:
    state = parse_apollo_state(html)
    if not state:
        return []
    records: list[dict] = []
    for key, station in state.items():
        if not key.startswith("Station:") or not isinstance(station, dict):
            continue
        station_name = normalize_text(
            station.get("name")
            or next(
                (
                    brand.get("name")
                    for brand in station.get("brands", [])
                    if isinstance(brand, dict) and brand.get("name")
                ),
                "",
            )
        )
        address = format_address(station.get("address"))
        price = choose_regular_price(station)
        if not station_name or not address or price is None:
            continue
        records.append(
            {
                "station_id": generate_station_id(station_name, address),
                "station_name": station_name,
                "address": address,
                "area": area,
                "price_regular": price,
                "timestamp": collected_at,
                "source_url": source_url,
            }
        )
    return deduplicate_records(records)


# FIX (b): New direct DOM parser targeting GasBuddy's current CSS module class names.
# Uses partial class matching ([class*='...']) to be resilient to CSS hash changes.
def parse_station_records_from_dom(
    html: str, area: str, source_url: str, collected_at: str
) -> list[dict]:
    """
    Extracts station data directly from GasBuddy's rendered DOM using stable
    CSS module class name fragments. Each station card has a container whose
    class contains 'stationListItem___'. Within each card:
      - Station name: heading tag with class containing 'stationNameHeader'
      - Address:      div with class containing 'address___' and 'StationDisplay'
      - Price:        span with class containing 'StationDisplayPrice-module__price___'
    """
    soup = BeautifulSoup(html, "html.parser")
    records: list[dict] = []

    # Each station card: exactly one div per station with class containing 'stationListItem___'
    station_cards = soup.find_all(
        "div",
        class_=lambda c: c and "stationListItem___" in " ".join(c)
    )

    if not station_cards:
        log_message(
            f"{area}: DOM parser found no station cards "
            f"(selector 'stationListItem___' matched 0 elements).",
            level="WARNING",
            log_name="retail_collection.log",
        )
        return []

    log_message(
        f"{area}: DOM parser found {len(station_cards)} station card(s).",
        log_name="retail_collection.log",
    )

    for card in station_cards:
        try:
            # Station name: heading with class containing 'stationNameHeader'
            name_tag = card.find(
                lambda t: t.name in ("h2", "h3", "h4")
                and any("stationNameHeader" in c for c in t.get("class", []))
            )
            station_name = normalize_text(
                name_tag.get_text(strip=True) if name_tag else ""
            )

            # Address: div with class containing both 'address___' and 'StationDisplay'
            addr_tag = card.find(
                "div",
                class_=lambda c: c and any(
                    "address___" in part and "StationDisplay" in part
                    for part in (c if isinstance(c, list) else c.split())
                )
            )
            if addr_tag:
                addr_lines = [
                    normalize_text(line)
                    for line in addr_tag.get_text("\n", strip=True).splitlines()
                    if normalize_text(line)
                ]
                # addr_lines[0] = street line e.g. "278 Greenpoint Ave"
                # addr_lines[1] = "Brooklyn, NY"
                address = addr_lines[0] if addr_lines else ""
            else:
                address = ""

            # Price: span with class containing 'StationDisplayPrice-module__price___'
            price_tag = card.find(
                "span",
                class_=lambda c: c and any(
                    "StationDisplayPrice-module__price___" in part
                    for part in (c if isinstance(c, list) else c.split())
                )
            )
            price_text = (
                price_tag.get_text(strip=True).replace("$", "").strip()
                if price_tag
                else ""
            )
            try:
                price = float(price_text)
                if not (1.0 <= price <= 8.0):
                    price = None
            except (ValueError, TypeError):
                price = None

            if not station_name or price is None:
                # Skip cards without a valid name or price
                continue

            records.append(
                {
                    "station_id": generate_station_id(station_name, address),
                    "station_name": station_name,
                    "address": address,
                    "area": area,
                    "price_regular": price,
                    "timestamp": collected_at,
                    "source_url": source_url,
                }
            )
        except Exception as exc:
            log_message(
                f"{area}: error parsing station card ({exc})",
                level="WARNING",
                log_name="retail_collection.log",
            )
            continue

    return deduplicate_records(records)


def collect_candidate_blocks(soup: BeautifulSoup) -> list[str]:
    blocks: list[str] = []
    seen: set[str] = set()
    for selector in CANDIDATE_SELECTORS:
        for tag in soup.select(selector):
            text = tag.get_text("\n", strip=True)
            normalized = normalize_text(text)
            if len(normalized) < 20 or parse_price_from_text(normalized) is None:
                continue
            key = normalized[:300].lower()
            if key in seen:
                continue
            seen.add(key)
            blocks.append(text)
            if len(blocks) >= 5:
                return blocks
    for tag in soup.find_all(["article", "li", "section", "div"]):
        text = tag.get_text("\n", strip=True)
        normalized = normalize_text(text)
        if len(normalized) < 20 or parse_price_from_text(normalized) is None:
            continue
        if not parse_address_from_lines(normalized.splitlines()):
            continue
        key = normalized[:300].lower()
        if key in seen:
            continue
        seen.add(key)
        blocks.append(text)
        if len(blocks) >= 200:
            break
    return blocks


def deduplicate_records(records: list[dict]) -> list[dict]:
    deduped: dict[str, dict] = {}
    for record in records:
        if not record:
            continue
        current = deduped.get(record["station_id"])
        if current is None:
            deduped[record["station_id"]] = record
            continue
        current_quality = bool(current.get("address")) + bool(current.get("station_name"))
        new_quality = bool(record.get("address")) + bool(record.get("station_name"))
        if new_quality >= current_quality:
            deduped[record["station_id"]] = record
    return list(deduped.values())


def parse_station_records_from_raw_text(
    raw_text: str, area: str, source_url: str, collected_at: str
) -> list[dict]:
    lines = dedupe_lines(raw_text.splitlines())
    records: list[dict] = []
    for idx, line in enumerate(lines):
        if parse_price_from_text(line) is None:
            continue
        window = lines[max(0, idx - 4) : min(len(lines), idx + 6)]
        record = parse_station_text("\n".join(window), area, source_url, collected_at)
        if record is not None:
            records.append(record)
    return deduplicate_records(records)


# FIX (c): Reordered pipeline - DOM parser first, then Apollo State, then generic text.
def parse_station_records_from_html(
    html: str, area: str, source_url: str, collected_at: str
) -> list[dict]:
    # Strategy 1: Direct DOM parsing with specific CSS module selectors (PRIMARY)
    dom_records = parse_station_records_from_dom(html, area, source_url, collected_at)
    if dom_records:
        return dom_records

    # Strategy 2: Apollo State JSON (FALLBACK)
    structured_records = parse_station_records_from_apollo_state(
        html, area, source_url, collected_at
    )
    if structured_records:
        return structured_records

    # Strategy 3: Generic block/text parsing (LAST RESORT)
    soup = BeautifulSoup(html, "html.parser")
    records: list[dict] = []
    for block_text in collect_candidate_blocks(soup):
        record = parse_station_text(block_text, area, source_url, collected_at)
        if record is not None:
            records.append(record)
    if records:
        return deduplicate_records(records)

    raw_text = soup.get_text("\n", strip=True)
    return parse_station_records_from_raw_text(raw_text, area, source_url, collected_at)


def collect_area_records(
    area: str, url: str, driver, collected_at: str
) -> list[dict]:
    if driver is not None:
        try:
            html = fetch_html_with_selenium(driver, url)
            records = parse_station_records_from_html(html, area, url, collected_at)
            if records:
                return records
            log_message(
                f"{area}: Selenium loaded the page but produced no station records, falling back.",
                level="WARNING",
                log_name="retail_collection.log",
            )
        except WebDriverException as exc:
            log_message(
                f"{area}: Selenium fetch failed ({exc}). Falling back to direct request.",
                level="WARNING",
                log_name="retail_collection.log",
            )
        except Exception as exc:
            log_message(
                f"{area}: unexpected Selenium error ({exc}). Falling back.",
                level="WARNING",
                log_name="retail_collection.log",
            )
    try:
        html = fetch_html_with_requests(url)
        return parse_station_records_from_html(html, area, url, collected_at)
    except Exception as exc:
        log_message(
            f"{area}: request fallback failed ({exc}).",
            level="ERROR",
            log_name="retail_collection.log",
        )
        return []


def collect_retail_snapshot(
    area_urls: dict[str, str] = AREA_URLS,
    output_dir: Path = RETAIL_DIR,
) -> tuple[pd.DataFrame, dict]:
    ensure_directories([output_dir])
    collected_at = timestamp_iso()
    snapshot_path = output_dir / f"{OUTPUT_STEM}_{timestamp_for_filename()}.csv"
    all_records: list[dict] = []
    driver = build_driver()
    if driver is None:
        log_message(
            "No Selenium WebDriver available. Using direct HTML requests only.",
            level="WARNING",
            log_name="retail_collection.log",
        )
    try:
        for area, url in area_urls.items():
            records = collect_area_records(area, url, driver, collected_at)
            all_records.extend(records)
            log_message(
                f"{area}: collected {len(records)} candidate station records from {url}",
                log_name="retail_collection.log",
            )
            time.sleep(1)
    finally:
        if driver is not None:
            driver.quit()
    frame = pd.DataFrame(all_records, columns=SNAPSHOT_COLUMNS)
    if frame.empty:
        frame = pd.DataFrame(columns=SNAPSHOT_COLUMNS)
    else:
        frame["price_regular"] = pd.to_numeric(frame["price_regular"], errors="coerce")
        frame = frame.dropna(subset=["price_regular"])
        frame = frame.drop_duplicates(subset=["station_id"], keep="first")
        frame = frame.sort_values(
            ["area", "station_name", "address", "station_id"]
        ).reset_index(drop=True)
    frame.to_csv(snapshot_path, index=False)
    summary = {
        "records": int(len(frame)),
        "unique_stations": int(frame["station_id"].nunique()) if not frame.empty else 0,
        "timestamp": collected_at,
        "output_path": str(snapshot_path),
    }
    log_message(
        f"Saved retail snapshot with {summary['records']} rows "
        f"({summary['unique_stations']} unique stations) to {snapshot_path.name}.",
        log_name="retail_collection.log",
    )
    return frame, summary


def main() -> None:
    _, summary = collect_retail_snapshot()
    print(
        "Retail snapshot saved to "
        f"{summary['output_path']} ({summary['records']} rows, "
        f"{summary['unique_stations']} unique stations)"
    )


if __name__ == "__main__":
    main()
