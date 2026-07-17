#!/usr/bin/env python3
"""Daily pull of secondary-market pricing for Colorado Avalanche home games."""

import csv
import logging
import os
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import yaml

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger("pull_prices")

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.yaml"
CSV_PATH = ROOT / "data" / "prices.csv"

FIELDNAMES = [
    "pull_date",
    "game_date",
    "game_time_local",
    "opponent",
    "day_of_week",
    "days_until_game",
    "tier",
    "source",
    "onsale_status",
    "lowest_price",
    "average_price",
    "median_price",
    "highest_price",
    "listing_count",
    "resale_min",
    "resale_max",
    "primary_min",
    "primary_max",
    "avg_pct_of_114_face",
    "avg_pct_of_363_face",
]

REQUEST_TIMEOUT = 15


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def fmt_num(value):
    """Round to 2 decimals for CSV output; None becomes an empty string."""
    if value is None:
        return ""
    return round(float(value), 2)


# --------------------------------------------------------------------------
# SeatGeek
# --------------------------------------------------------------------------

def fetch_seatgeek_events(config):
    client_id = os.environ.get("SEATGEEK_CLIENT_ID")
    client_secret = os.environ.get("SEATGEEK_CLIENT_SECRET")
    if not client_id:
        log.warning("SEATGEEK_CLIENT_ID not set; skipping SeatGeek")
        return None

    params = {
        "performers.slug": config["seatgeek"]["performer_slug"],
        "venue.slug": config["seatgeek"]["venue_slug"],
        "per_page": 100,
        "client_id": client_id,
    }
    if client_secret:
        params["client_secret"] = client_secret

    try:
        resp = requests.get(
            "https://api.seatgeek.com/2/events", params=params, timeout=REQUEST_TIMEOUT
        )
        resp.raise_for_status()
        payload = resp.json()
    except requests.RequestException as e:
        log.error("SeatGeek request failed: %s", e)
        return None
    except ValueError as e:
        log.error("SeatGeek returned invalid JSON: %s", e)
        return None

    events = payload.get("events")
    if events is None:
        log.warning("SeatGeek response missing 'events' key; treating as zero events")
        return []

    return [parse_seatgeek_event(ev, config) for ev in events]


def parse_seatgeek_event(ev, config):
    event_id = ev.get("id")

    datetime_local = ev.get("datetime_local")
    game_date = None
    game_time_local = ""
    if datetime_local:
        try:
            dt = datetime.fromisoformat(datetime_local)
            game_date = dt.date()
            game_time_local = dt.strftime("%H:%M")
        except ValueError:
            log.warning(
                "SeatGeek event %s has unparseable datetime_local=%r", event_id, datetime_local
            )
    else:
        log.warning("SeatGeek event %s missing datetime_local", event_id)

    home_slug = config["seatgeek"]["performer_slug"]
    opponent = None
    for performer in ev.get("performers", []) or []:
        if performer.get("slug") != home_slug:
            opponent = performer.get("name")
            break
    if opponent is None:
        log.warning("SeatGeek event %s: could not determine opponent from performers", event_id)

    stats = ev.get("stats")
    if not stats:
        log.warning("SeatGeek event %s missing 'stats' object", event_id)
        stats = {}

    return {
        "event_id": event_id,
        "game_date": game_date,
        "game_time_local": game_time_local,
        "opponent": opponent,
        "onsale_status": None,
        "lowest_price": stats.get("lowest_price"),
        "average_price": stats.get("average_price"),
        "median_price": stats.get("median_price"),
        "highest_price": stats.get("highest_price"),
        "listing_count": stats.get("listing_count"),
        "resale_min": None,
        "resale_max": None,
        "primary_min": None,
        "primary_max": None,
    }


# --------------------------------------------------------------------------
# Ticketmaster
# --------------------------------------------------------------------------

def fetch_ticketmaster_events(config):
    api_key = os.environ.get("TICKETMASTER_API_KEY")
    if not api_key:
        log.warning("TICKETMASTER_API_KEY not set; skipping Ticketmaster")
        return None

    attraction_id = config["ticketmaster"]["attraction_id"]
    venue_id = config["ticketmaster"]["venue_id"]

    all_events = []
    page = 0
    try:
        while True:
            params = {
                "apikey": api_key,
                "attractionId": attraction_id,
                "venueId": venue_id,
                "size": 200,
                "page": page,
            }
            resp = requests.get(
                "https://app.ticketmaster.com/discovery/v2/events.json",
                params=params,
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            payload = resp.json()
            events = (payload.get("_embedded") or {}).get("events", [])
            all_events.extend(events)
            page_info = payload.get("page") or {}
            total_pages = page_info.get("totalPages", 1)
            page += 1
            if page >= total_pages:
                break
    except requests.RequestException as e:
        log.error("Ticketmaster request failed: %s", e)
        return None
    except ValueError as e:
        log.error("Ticketmaster returned invalid JSON: %s", e)
        return None

    return [parse_ticketmaster_event(ev, attraction_id) for ev in all_events]


def parse_ticketmaster_event(ev, avs_attraction_id):
    event_id = ev.get("id")

    dates = ev.get("dates") or {}
    start = dates.get("start") or {}
    game_date = None
    if start.get("localDate"):
        try:
            game_date = date.fromisoformat(start["localDate"])
        except ValueError:
            log.warning(
                "Ticketmaster event %s has unparseable localDate=%r",
                event_id,
                start.get("localDate"),
            )
    else:
        log.warning("Ticketmaster event %s missing dates.start.localDate", event_id)

    game_time_local = ""
    if start.get("localTime"):
        game_time_local = start["localTime"][:5]

    onsale_status = (dates.get("status") or {}).get("code")
    if onsale_status is None:
        log.warning("Ticketmaster event %s missing dates.status.code", event_id)

    opponent = None
    attractions = ((ev.get("_embedded") or {}).get("attractions")) or []
    for att in attractions:
        if att.get("id") != avs_attraction_id:
            opponent = att.get("name")
            break
    if opponent is None:
        # Fall back to parsing the event name if the attractions embed is absent.
        name = ev.get("name", "")
        if " vs. " in name:
            opponent = name.split(" vs. ", 1)[1].strip()
        elif " at " in name:
            opponent = name.split(" at ", 1)[0].strip()
    if opponent is None:
        log.warning("Ticketmaster event %s: could not determine opponent", event_id)

    price_ranges = ev.get("priceRanges")
    lowest_price = None
    highest_price = None
    if price_ranges:
        pr = price_ranges[0]
        lowest_price = pr.get("min")
        highest_price = pr.get("max")
    elif onsale_status == "onsale":
        # Missing priceRanges on an off-sale event is expected; on an
        # on-sale event it's a genuine schema surprise worth flagging.
        log.warning("Ticketmaster event %s is onsale but has no priceRanges", event_id)

    return {
        "event_id": event_id,
        "game_date": game_date,
        "game_time_local": game_time_local,
        "opponent": opponent,
        "onsale_status": onsale_status,
        "lowest_price": lowest_price,
        "average_price": None,
        "median_price": None,
        "highest_price": highest_price,
        "listing_count": None,
        "resale_min": None,
        "resale_max": None,
        "primary_min": None,
        "primary_max": None,
    }


# --------------------------------------------------------------------------
# CSV row assembly / idempotency
# --------------------------------------------------------------------------

def build_csv_row(parsed, source, pull_date, config):
    faces = config["faces"]
    tiers = config["tiers"]

    game_date = parsed["game_date"]
    if game_date is None:
        day_of_week = ""
        days_until_game = ""
    else:
        day_of_week = game_date.strftime("%a")
        days_until_game = (game_date - pull_date).days

    opponent = parsed["opponent"] or ""
    tier = tiers.get(opponent, "UNMAPPED")
    if tier == "UNMAPPED":
        log.warning("Opponent %r not found in config.yaml tiers map", opponent)

    average_price = parsed.get("average_price")
    avg_pct_114 = ""
    avg_pct_363 = ""
    if average_price is not None:
        avg_pct_114 = round((float(average_price) / faces["sec_114"] - 1) * 100, 1)
        avg_pct_363 = round((float(average_price) / faces["sec_363"] - 1) * 100, 1)

    listing_count = parsed.get("listing_count")

    return {
        "pull_date": pull_date.isoformat(),
        "game_date": game_date.isoformat() if game_date else "",
        "game_time_local": parsed["game_time_local"],
        "opponent": opponent,
        "day_of_week": day_of_week,
        "days_until_game": days_until_game,
        "tier": tier,
        "source": source,
        "onsale_status": parsed.get("onsale_status") or "",
        "lowest_price": fmt_num(parsed.get("lowest_price")),
        "average_price": fmt_num(parsed.get("average_price")),
        "median_price": fmt_num(parsed.get("median_price")),
        "highest_price": fmt_num(parsed.get("highest_price")),
        "listing_count": listing_count if listing_count is not None else "",
        "resale_min": fmt_num(parsed.get("resale_min")),
        "resale_max": fmt_num(parsed.get("resale_max")),
        "primary_min": fmt_num(parsed.get("primary_min")),
        "primary_max": fmt_num(parsed.get("primary_max")),
        "avg_pct_of_114_face": avg_pct_114,
        "avg_pct_of_363_face": avg_pct_363,
    }


def read_existing_keys():
    if not CSV_PATH.exists():
        return set()
    keys = set()
    with open(CSV_PATH, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            keys.add((row["pull_date"], row["game_date"], row["source"]))
    return keys


def append_rows(rows):
    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    write_header = (not CSV_PATH.exists()) or CSV_PATH.stat().st_size == 0
    with open(CSV_PATH, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if write_header:
            writer.writeheader()
        for row in rows:
            writer.writerow(row)


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main():
    config = load_config()
    tzinfo = ZoneInfo(config["timezone"])
    pull_date = datetime.now(tzinfo).date()

    existing_keys = read_existing_keys()

    seatgeek_events = fetch_seatgeek_events(config)
    ticketmaster_events = fetch_ticketmaster_events(config)

    games_found = {}
    new_rows = []
    skipped = 0

    for source, parsed_events in (
        ("seatgeek", seatgeek_events),
        ("ticketmaster", ticketmaster_events),
    ):
        if parsed_events is None:
            games_found[source] = "FAILED"
            continue

        future_events = [e for e in parsed_events if e["game_date"] and e["game_date"] >= pull_date]
        games_found[source] = len(future_events)

        for parsed in future_events:
            key = (pull_date.isoformat(), parsed["game_date"].isoformat(), source)
            if key in existing_keys:
                skipped += 1
                continue
            new_rows.append(build_csv_row(parsed, source, pull_date, config))
            existing_keys.add(key)

    if new_rows:
        append_rows(new_rows)

    summary = ", ".join(f"{src}={count}" for src, count in games_found.items())
    log.info(
        "Run summary: games found (%s), rows written=%d, rows skipped=%d",
        summary,
        len(new_rows),
        skipped,
    )

    if seatgeek_events is None and ticketmaster_events is None:
        log.error("Both SeatGeek and Ticketmaster failed; exiting nonzero")
        sys.exit(1)


if __name__ == "__main__":
    main()
