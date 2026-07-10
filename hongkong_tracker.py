#!/usr/bin/env python3
"""
Hong Kong Box Office Tracker
- Fetches movie show data from cinema.com.hk
- Stores daily files in: Hongkong Data/{year}/{month}-{day}.json (minified)
- Builds a movie database: movie/data/{slug}.json (per‑day stats) and movie/index.json
All times are in Hong Kong Time (UTC+8). last_updated is in IST.
"""

import requests
import json
import os
import re
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from typing import Dict, List, Any, Optional, Tuple
import pytz

# ========== CONFIGURATION ==========
URL = "https://www.cinema.com.hk/en/movie/ticketing"

HEADERS = {
    "authority": "www.cinema.com.hk",
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "accept-encoding": "gzip, deflate, br, zstd",
    "accept-language": "en-IN,en-GB;q=0.9,en-US;q=0.8,en;q=0.7",
    "cache-control": "no-cache",
    "pragma": "no-cache",
    "sec-ch-ua": '"Google Chrome";v="149", "Chromium";v="149", "Not)A;Brand";v="24"',
    "sec-ch-ua-mobile": "?1",
    "sec-ch-ua-platform": '"iOS"',
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "same-origin",
    "sec-fetch-user": "?1",
    "upgrade-insecure-requests": "1",
    "user-agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.5 Mobile/15E148 Safari/604.1"
}

# Timezones
HK_TZ = timezone(timedelta(hours=8))       # Hong Kong Time for dates
IST = pytz.timezone("Asia/Kolkata")        # Indian Standard Time for last_updated

# ========== HELPERS ==========
def hk_now() -> datetime:
    """Current time in Hong Kong (UTC+8)."""
    return datetime.now(HK_TZ)

def get_hk_date_str() -> str:
    """Return current date in Hong Kong as YYYY-MM-DD."""
    return hk_now().strftime("%Y-%m-%d")

def get_hk_yyyymmdd() -> str:
    """Return current date as YYYYMMDD."""
    return hk_now().strftime("%Y%m%d")

def get_hk_year_month_day() -> Tuple[str, str, str]:
    """Return (year, month, day) of current HK date."""
    now = hk_now()
    return now.strftime("%Y"), now.strftime("%m"), now.strftime("%d")

def ist_timestamp() -> str:
    """Return current IST time as 'YYYY-MM-DD HH:MM IST'."""
    return datetime.now(IST).strftime("%Y-%m-%d %H:%M IST")

def slugify(title: str) -> str:
    """Generate URL‑friendly slug from movie title."""
    slug = re.sub(r'[^a-zA-Z0-9\s]', '', title).strip().lower()
    slug = re.sub(r'\s+', '-', slug)
    return slug

def date_to_datetime(date_int: int) -> datetime:
    """Convert YYYYMMDD integer to datetime (naive, but used for day counting)."""
    return datetime.strptime(str(date_int), "%Y%m%d")

def days_between_inclusive(first: int, last: int) -> int:
    """Return number of days from first to last inclusive."""
    d1 = date_to_datetime(first)
    d2 = date_to_datetime(last)
    return (d2 - d1).days + 1

# ========== FETCH & PARSE ==========
def fetch_html() -> str:
    r = requests.get(URL, headers=HEADERS)
    return r.text

def extract_all_flight_strings(html: str) -> List[str]:
    """Extract all self.__next_f.push([1, "...") strings."""
    results = []
    pattern = 'self.__next_f.push([1,'
    start_pos = 0
    while True:
        start = html.find(pattern, start_pos)
        if start == -1:
            break
        quote_start = html.find('"', start)
        if quote_start == -1:
            break
        i = quote_start + 1
        while i < len(html):
            if html[i] == '\\':
                i += 2
                continue
            if html[i] == '"':
                end = i
                break
            i += 1
        else:
            break
        raw = html[quote_start+1:end]
        try:
            decoded = json.loads('"' + raw + '"')
            results.append(decoded)
        except json.JSONDecodeError:
            pass
        start_pos = end + 1
    return results

def parse_payloads(payload_strings: List[str]) -> Dict[str, Any]:
    """Parse flight chunks into a dictionary keyed by chunk ID."""
    chunks = {}
    for payload in payload_strings:
        lines = payload.split('\n')
        for line in lines:
            line = line.strip()
            if not line:
                continue
            colon_idx = line.find(':')
            if colon_idx == -1:
                continue
            chunk_id = line[:colon_idx]
            data_str = line[colon_idx+1:]
            try:
                parsed = json.loads(data_str)
                chunks[chunk_id] = parsed
            except json.JSONDecodeError:
                continue
    return chunks

def resolve_reference(ref: str, chunks: Dict[str, Any], root_data: Any = None) -> Any:
    """Resolve a reference string like $5:1:props:... using chunks."""
    if not isinstance(ref, str) or not ref.startswith('$'):
        return ref
    path = ref[1:]
    parts = path.split(':')
    if not parts:
        return ref
    chunk_id = parts[0]
    if chunk_id in chunks:
        data = chunks[chunk_id]
    elif root_data is not None:
        data = root_data
    else:
        return ref
    for key in parts[1:]:
        if isinstance(data, dict) and key in data:
            data = data[key]
        elif isinstance(data, list) and key.isdigit():
            idx = int(key)
            if idx < len(data):
                data = data[idx]
            else:
                return ref
        else:
            return ref
    return data

def find_key(data: Any, target_key: str) -> Any:
    """Recursively find first occurrence of target_key in data."""
    if isinstance(data, dict):
        if target_key in data:
            return data[target_key]
        for v in data.values():
            res = find_key(v, target_key)
            if res is not None:
                return res
    elif isinstance(data, list):
        for item in data:
            res = find_key(item, target_key)
            if res is not None:
                return res
    return None

def build_movie_lookup(chunks: Dict[str, Any]) -> Dict[int, dict]:
    """Build a lookup: movie_id -> movie object from the 'movies' array."""
    for chunk_id, data in chunks.items():
        movies = find_key(data, 'movies')
        if movies and isinstance(movies, list):
            lookup = {}
            for movie in movies:
                if isinstance(movie, dict) and 'id' in movie:
                    lookup[movie['id']] = movie
            if lookup:
                return lookup
    return {}

def build_site_lookup(chunks: Dict[str, Any]) -> Dict[int, str]:
    """Build a lookup: site_id -> site name from 'showSites' or 'siteGroups'."""
    for chunk_id, data in chunks.items():
        sites = find_key(data, 'showSites')
        if sites and isinstance(sites, list):
            lookup = {}
            for site in sites:
                if isinstance(site, dict) and 'id' in site and 'name' in site:
                    lookup[site['id']] = site['name']
            if lookup:
                return lookup
    # fallback to siteGroups
    for chunk_id, data in chunks.items():
        groups = find_key(data, 'siteGroups')
        if groups and isinstance(groups, list):
            lookup = {}
            for group in groups:
                if isinstance(group, dict) and 'items' in group:
                    for item in group['items']:
                        if isinstance(item, dict) and 'site' in item:
                            site = item['site']
                            if isinstance(site, dict) and 'id' in site and 'name' in site:
                                lookup[site['id']] = site['name']
            if lookup:
                return lookup
    return {}

def get_shows_array(chunks: Dict[str, Any]) -> Optional[List[dict]]:
    """Find and return the 'shows' array from any chunk."""
    for chunk_id, data in chunks.items():
        shows = find_key(data, 'shows')
        if shows is not None:
            return shows
    return None

def parse_shows(shows_array: List[dict], chunks: Dict[str, Any],
                movie_lookup: Dict[int, dict], site_lookup: Dict[int, str]) -> List[Tuple[int, int, str, int, int]]:
    """
    Parse shows into a list of tuples:
      (perfIx, siteId, time, total, sold)
    Time is converted to Hong Kong local time in 'HH:MM:SS' format.
    """
    result = []
    for show in shows_array:
        try:
            show_id = show["id"]
            time_str = show["time"]
            # Convert UTC to HK time
            utc_dt = datetime.fromisoformat(time_str.replace('Z', '+00:00'))
            hk_dt = utc_dt.astimezone(HK_TZ)
            local_time = hk_dt.strftime('%H:%M:%S')  # 24-hour format with seconds
            total = show["seats"]
            sold = show["sold"]

            # Site ID
            site_obj = show.get("site", {})
            site_id = site_obj.get("id")

            # Movie ID (for verification, but we only store site & time)
            # We don't need movie id here because we're grouping by movie name later.
            # We'll resolve movie name later.
            result.append((show_id, site_id, local_time, total, sold))
        except KeyError as e:
            print(f"Skipping show due to missing key: {e}")
            continue
    return result

def get_movie_name_from_show(show: dict, chunks: Dict[str, Any],
                             movie_lookup: Dict[int, dict]) -> str:
    """Extract the movie name from a show object, handling references."""
    movie_obj = show.get("movie")
    if isinstance(movie_obj, str) and movie_obj.startswith('$'):
        movie_obj = resolve_reference(movie_obj, chunks)
    if not isinstance(movie_obj, dict):
        return "Unknown"
    name = movie_obj.get("name")
    if name:
        return name
    movie_id = movie_obj.get("id")
    if movie_id is not None and movie_id in movie_lookup:
        return movie_lookup[movie_id].get("name", f"Movie {movie_id}")
    return f"Movie {movie_id if movie_id is not None else 'unknown'}"

def fetch_and_parse() -> Tuple[Dict[str, List[Tuple[int, int, str, int, int]]], Dict[int, dict]]:
    """
    Fetch HTML, parse chunks, extract shows and movie lookup.
    Returns:
      - shows_by_movie: dict movie_name -> list of (perfIx, siteId, time, total, sold)
      - movie_lookup: dict movie_id -> full movie object (for later database building)
    """
    html = fetch_html()
    print(f"HTML length: {len(html)}")
    all_strings = extract_all_flight_strings(html)
    print(f"Found {len(all_strings)} flight strings.")
    if not all_strings:
        raise Exception("No flight strings extracted.")

    chunks = parse_payloads(all_strings)
    print(f"Parsed {len(chunks)} chunks.")

    movie_lookup = build_movie_lookup(chunks)
    print(f"Found {len(movie_lookup)} unique movies.")
    site_lookup = build_site_lookup(chunks)
    print(f"Found {len(site_lookup)} unique venues.")

    shows_array = get_shows_array(chunks)
    if shows_array is None:
        raise Exception("Could not find 'shows' array.")

    print(f"Found shows array with {len(shows_array)} entries.")

    # Group by movie name
    shows_by_movie = defaultdict(list)
    for show in shows_array:
        movie_name = get_movie_name_from_show(show, chunks, movie_lookup)
        # Parse show data
        try:
            show_id = show["id"]
            time_str = show["time"]
            utc_dt = datetime.fromisoformat(time_str.replace('Z', '+00:00'))
            hk_dt = utc_dt.astimezone(HK_TZ)
            local_time = hk_dt.strftime('%H:%M:%S')
            total = show["seats"]
            sold = show["sold"]
            site_obj = show.get("site", {})
            site_id = site_obj.get("id")
            shows_by_movie[movie_name].append((show_id, site_id, local_time, total, sold))
        except KeyError as e:
            print(f"Skipping show due to missing key: {e}")
            continue

    return dict(shows_by_movie), movie_lookup

# ========== DAILY FILE I/O ==========
def get_daily_filepath() -> str:
    year, month, day = get_hk_year_month_day()
    dir_path = os.path.join("Hongkong Data", year)
    os.makedirs(dir_path, exist_ok=True)
    return os.path.join(dir_path, f"{month}-{day}.json")

def load_existing_data(filepath: str) -> Dict[str, List[List[Any]]]:
    """Load the data portion from a daily JSON file."""
    if not os.path.exists(filepath):
        return {}
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = json.load(f)
        if isinstance(content, dict) and "data" in content:
            return content["data"]
        return content
    except:
        return {}

def merge_and_save(filepath: str, new_data: Dict[str, List[Tuple[int, int, str, int, int]]]):
    """
    Merge new_data into existing file (update by perfIx),
    then save with top-level "data" and "last_updated" (IST).
    Each entry is stored as [perfIx, siteId, time, total, sold].
    """
    existing = load_existing_data(filepath)

    for movie, entries in new_data.items():
        if movie not in existing:
            existing[movie] = []
        # Build map: perfIx -> entry
        existing_map = {entry[0]: entry for entry in existing[movie]}
        for entry in entries:
            pid = entry[0]
            existing_map[pid] = entry
        existing[movie] = list(existing_map.values())

    output = {
        "data": existing,
        "last_updated": ist_timestamp()
    }

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(output, f, separators=(',', ':'), ensure_ascii=False)
    print(f"💾 Updated {filepath} (last_updated: {output['last_updated']})")

# ========== MOVIE DATABASE BUILDER ==========
def update_movie_database(movie_lookup: Dict[int, dict]):
    """
    Scan all daily JSON files under Hongkong Data/, aggregate per movie per date,
    and write per‑movie summary files + an index.
    New format:
      - index: per movie => name, slug, totalTickets, totalShows, totalSeats,
                firstDate, lastDate, currentDate, and d (days inclusive between first and last)
      - per-movie: each day => [date (YYYYMMDD), tickets, shows, seats, venueCount]
    All gross fields are removed.
    """
    print("\n📊 Building movie database...")
    base_dir = "Hongkong Data"
    if not os.path.exists(base_dir):
        print("⚠️ No Hongkong Data found.")
        return

    daily_files = []
    for year_dir in os.listdir(base_dir):
        year_path = os.path.join(base_dir, year_dir)
        if not os.path.isdir(year_path):
            continue
        for file in os.listdir(year_path):
            if file.endswith(".json") and "-" in file:
                month_day = file.replace(".json", "")
                month, day = month_day.split("-")
                date_str = f"{year_dir}{month}{day}"  # YYYYMMDD
                daily_files.append((date_str, os.path.join(year_path, file)))

    if not daily_files:
        print("⚠️ No daily files found.")
        return

    # Aggregate: movie -> date -> stats
    movie_agg: Dict[str, Dict[str, Dict[str, Any]]] = defaultdict(lambda: defaultdict(lambda: {
        "shows": 0,
        "seats": 0,
        "sold": 0,
        "venues": set()
    }))

    for date_str, filepath in daily_files:
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = json.load(f)
            if isinstance(content, dict) and "data" in content:
                data = content["data"]
            else:
                data = content
        except:
            continue
        for movie, entries in data.items():
            shows = len(entries)
            seats = sum(e[3] for e in entries)   # total seats
            sold = sum(e[4] for e in entries)   # sold
            venues = {e[1] for e in entries}    # siteId

            agg = movie_agg[movie][date_str]
            agg["shows"] += shows
            agg["seats"] += seats
            agg["sold"] += sold
            agg["venues"].update(venues)

    # Today's date in YYYYMMDD
    today_yyyymmdd = int(get_hk_yyyymmdd())

    os.makedirs("movie/data", exist_ok=True)
    index = []

    for movie, dates in movie_agg.items():
        slug = slugify(movie)
        day_rows = []
        total_tickets = 0
        total_shows = 0
        total_seats = 0
        first_date = None
        last_date = None

        for date_str, stats in sorted(dates.items()):
            date_int = int(date_str)
            if first_date is None:
                first_date = date_int
            last_date = date_int

            sold = stats["sold"]
            shows = stats["shows"]
            seats = stats["seats"]
            venues = len(stats["venues"])

            total_tickets += sold
            total_shows += shows
            total_seats += seats

            # Per-day entry: [date, tickets, shows, seats, venueCount]
            day_rows.append([date_int, sold, shows, seats, venues])

        # Write per‑movie file
        movie_file = os.path.join("movie/data", f"{slug}.json")
        with open(movie_file, "w", encoding="utf-8") as f:
            json.dump(day_rows, f, separators=(',', ':'), ensure_ascii=False)
        print(f"   📄 {movie_file}")

        # Compute d = days between first and last (inclusive)
        if first_date is not None and last_date is not None:
            d = days_between_inclusive(first_date, last_date)
        else:
            d = 0

        # Build index entry
        index.append({
            "name": movie,
            "slug": slug,
            "totalTickets": total_tickets,
            "totalShows": total_shows,
            "totalSeats": total_seats,
            "firstDate": first_date,
            "lastDate": last_date,
            "currentDate": today_yyyymmdd,
            "d": d
        })

    # Write index file with last_updated
    index_file = os.path.join("movie", "index.json")
    output_index = {
        "movies": index,
        "last_updated": ist_timestamp()
    }
    with open(index_file, "w", encoding="utf-8") as f:
        json.dump(output_index, f, separators=(',', ':'), ensure_ascii=False)
    print(f"💾 {index_file} (last_updated: {output_index['last_updated']})")
    print("✅ Movie database updated.\n")

# ========== MAIN ==========
def main():
    print(f"📅 Processing date: {get_hk_date_str()} (Hong Kong Time)")
    try:
        shows_by_movie, movie_lookup = fetch_and_parse()
    except Exception as e:
        print(f"❌ Error fetching data: {e}")
        return

    if not shows_by_movie:
        print("⚠️ No data fetched.")
        return

    filepath = get_daily_filepath()
    merge_and_save(filepath, shows_by_movie)

    # Build movie database
    update_movie_database(movie_lookup)

if __name__ == "__main__":
    main()
