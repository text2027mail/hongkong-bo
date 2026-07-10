#!/usr/bin/env python3
"""
Hong Kong Box Office & Movie Database Tracker (with Gross Revenue)

- Fetches all show data from cinema.com.hk (multiple dates)
- Stores daily files: Hongkong Data/{year}/{month}-{day}.json (minified)
  Entry: [perfIx, siteId, time, totalSeats, sold, price]
- Builds/updates a movie database (minified where possible):
  - movie/{slug}.json  -> full movie metadata (Recommended Schema, minified)
  - movie/data/{slug}.json -> daily stats: [date, tickets, shows, seats, venues, gross]
  - movie/index.json -> minified index: name, slug, totalTickets, totalShows,
                         totalSeats, totalGross, firstDate, lastDate, currentDate, d

All dates are in Hong Kong Time (UTC+8). last_updated is in IST (UTC+5:30).
Retries on network errors. Handles legacy daily files (no price) gracefully.
"""

import requests
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from typing import Dict, List, Any, Optional, Tuple
import pytz

# ========== CONFIGURATION ==========
URL = "https://www.cinema.com.hk/en/movie/ticketing"
MAX_RETRIES = 3
RETRY_DELAY = 2  # seconds

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

HK_TZ = timezone(timedelta(hours=8))       # Hong Kong Time
IST = pytz.timezone("Asia/Kolkata")        # Indian Standard Time

# ========== HELPERS ==========
def hk_now() -> datetime:
    return datetime.now(HK_TZ)

def get_hk_yyyymmdd() -> str:
    return hk_now().strftime("%Y%m%d")

def ist_timestamp() -> str:
    return datetime.now(IST).strftime("%Y-%m-%d %H:%M IST")

def slugify(title: str) -> str:
    slug = re.sub(r'[^a-zA-Z0-9\s]', '', title).strip().lower()
    slug = re.sub(r'\s+', '-', slug)
    return slug

def date_to_datetime(date_int: int) -> datetime:
    return datetime.strptime(str(date_int), "%Y%m%d")

def days_between_inclusive(first: int, last: int) -> int:
    d1 = date_to_datetime(first)
    d2 = date_to_datetime(last)
    return (d2 - d1).days + 1

def parse_lang_field(field: Any) -> dict:
    if isinstance(field, str):
        try:
            return json.loads(field)
        except:
            return {}
    return field if isinstance(field, dict) else {}

def fetch_html_with_retry() -> str:
    """Fetch HTML with exponential backoff retries."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = requests.get(URL, headers=HEADERS, timeout=30)
            r.raise_for_status()
            return r.text
        except Exception as e:
            print(f"⚠️ Fetch attempt {attempt}/{MAX_RETRIES} failed: {e}")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY * attempt)
            else:
                raise

# ========== FETCH & PARSE ==========
def extract_all_flight_strings(html: str) -> List[str]:
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
    for chunk_id, data in chunks.items():
        shows = find_key(data, 'shows')
        if shows is not None:
            return shows
    return None

def get_movie_name_from_show(show: dict, chunks: Dict[str, Any],
                             movie_lookup: Dict[int, dict]) -> str:
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

def fetch_and_parse() -> Tuple[Dict[str, Dict[str, List[Tuple[int, int, str, int, int, int]]]], Dict[int, dict]]:
    """
    Fetch and parse all shows.
    Returns:
      - shows_by_date_movie: dict date (YYYY-MM-DD) -> dict movie_name -> list of (perfIx, siteId, time, total, sold, price)
      - movie_lookup: dict movie_id -> full movie object
    """
    html = fetch_html_with_retry()
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

    # Group by date, then by movie
    shows_by_date_movie = defaultdict(lambda: defaultdict(list))

    for show in shows_array:
        try:
            movie_name = get_movie_name_from_show(show, chunks, movie_lookup)

            time_str = show["time"]
            utc_dt = datetime.fromisoformat(time_str.replace('Z', '+00:00'))
            hk_dt = utc_dt.astimezone(HK_TZ)
            local_date = hk_dt.strftime('%Y-%m-%d')
            local_time = hk_dt.strftime('%H:%M:%S')

            show_id = show["id"]
            total = show["seats"]
            sold = show["sold"]
            price = show["price"]
            site_obj = show.get("site", {})
            site_id = site_obj.get("id")

            shows_by_date_movie[local_date][movie_name].append(
                (show_id, site_id, local_time, total, sold, price)
            )
        except KeyError as e:
            print(f"Skipping show due to missing key: {e}")
            continue

    return dict(shows_by_date_movie), movie_lookup

# ========== DAILY FILE I/O ==========
def get_daily_filepath(date_str: str) -> str:
    year, month_day = date_str.split('-')[0], date_str[5:]
    dir_path = os.path.join("Hongkong Data", year)
    os.makedirs(dir_path, exist_ok=True)
    return os.path.join(dir_path, f"{month_day}.json")

def load_existing_data(filepath: str) -> Dict[str, List[List[Any]]]:
    """Load existing daily data. Converts legacy entries (length 5 -> 6 with price=0)."""
    if not os.path.exists(filepath):
        return {}
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = json.load(f)
        if isinstance(content, dict) and "data" in content:
            data = content["data"]
        else:
            data = content
        # Ensure all entries have 6 elements (pad price=0 if missing)
        for movie, entries in data.items():
            fixed = []
            for e in entries:
                if len(e) == 5:
                    # old format: [perfIx, siteId, time, total, sold]
                    fixed.append(e + [0])  # add price=0
                else:
                    fixed.append(e)
            data[movie] = fixed
        return data
    except:
        return {}

def merge_and_save_daily(filepath: str, new_data: Dict[str, List[Tuple[int, int, str, int, int, int]]]):
    existing = load_existing_data(filepath)

    for movie, entries in new_data.items():
        if movie not in existing:
            existing[movie] = []
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
    Scan all daily files, aggregate per movie per date,
    write full metadata (minified), daily stats (minified), and minified index.
    """
    print("\n📊 Building/updating movie database...")
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

    # Aggregate per movie per date
    movie_agg: Dict[str, Dict[str, Dict[str, Any]]] = defaultdict(lambda: defaultdict(lambda: {
        "shows": 0,
        "seats": 0,
        "sold": 0,
        "gross": 0,
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
            gross = sum(e[4] * e[5] for e in entries)  # sold * price
            venues = {e[1] for e in entries}

            agg = movie_agg[movie][date_str]
            agg["shows"] += shows
            agg["seats"] += seats
            agg["sold"] += sold
            agg["gross"] += gross
            agg["venues"].update(venues)

    today_yyyymmdd = int(get_hk_yyyymmdd())

    os.makedirs("movie", exist_ok=True)
    os.makedirs("movie/data", exist_ok=True)

    index = []

    for movie, dates in movie_agg.items():
        slug = slugify(movie)

        # --- Write full metadata (movie/{slug}.json) - minified ---
        movie_id = None
        for mid, mobj in movie_lookup.items():
            if mobj.get("name") == movie:
                movie_id = mid
                break

        if movie_id is not None:
            movie_obj = movie_lookup[movie_id]
            enriched = {
                "id": movie_obj.get("id"),
                "movieId": movie_obj.get("filmId"),
                "masterId": movie_obj.get("masterId"),
                "extId": movie_obj.get("extId"),
                "slug": slug,
                "title": movie_obj.get("name"),
                "title_lang": parse_lang_field(movie_obj.get("name_lang")),
                "synopsis": movie_obj.get("description"),
                "synopsis_lang": parse_lang_field(movie_obj.get("description_lang")),
                "category": movie_obj.get("category"),
                "duration": movie_obj.get("duration"),
                "releaseDate": movie_obj.get("openingDate"),
                "genres": [g.get("name") for g in movie_obj.get("movieTypes", []) if isinstance(g, dict)],
                "director": movie_obj.get("director"),
                "director_lang": parse_lang_field(movie_obj.get("director_lang")),
                "cast": movie_obj.get("cast"),
                "cast_lang": parse_lang_field(movie_obj.get("cast_lang")),
                "dialect": movie_obj.get("dialect"),
                "dialect_lang": parse_lang_field(movie_obj.get("dialect_lang")),
                "subtitle": movie_obj.get("subtitle"),
                "subtitle_lang": parse_lang_field(movie_obj.get("subtitle_lang")),
                "poster": movie_obj.get("images", [""])[0] if movie_obj.get("images") else "",
                "landscapeImages": movie_obj.get("landscapeImages", []),
                "trailer": movie_obj.get("trailer"),
                "website": movie_obj.get("website"),
                "featured": movie_obj.get("featured"),
                "active": movie_obj.get("active"),
                "numSchedules": movie_obj.get("numSchedules", 0),
                "maxTime": movie_obj.get("maxTime"),
                "tags": movie_obj.get("tags", []),
                "hkta": movie_obj.get("hkta"),
                "createdAt": movie_obj.get("createTime"),
                "updatedAt": movie_obj.get("updateTime")
            }
            meta_file = os.path.join("movie", f"{slug}.json")
            with open(meta_file, "w", encoding="utf-8") as f:
                json.dump(enriched, f, separators=(',', ':'), ensure_ascii=False)
            print(f"   📄 {meta_file} (minified)")

        # --- Write daily stats (movie/data/{slug}.json) - minified ---
        day_rows = []
        total_tickets = 0
        total_shows = 0
        total_seats = 0
        total_gross = 0
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
            gross = stats["gross"]
            venues = len(stats["venues"])

            total_tickets += sold
            total_shows += shows
            total_seats += seats
            total_gross += gross

            day_rows.append([date_int, sold, shows, seats, venues, gross])

        stats_file = os.path.join("movie/data", f"{slug}.json")
        with open(stats_file, "w", encoding="utf-8") as f:
            json.dump(day_rows, f, separators=(',', ':'), ensure_ascii=False)
        print(f"   📄 {stats_file} (minified)")

        # --- Index entry ---
        if first_date is not None and last_date is not None:
            d = days_between_inclusive(first_date, last_date)
        else:
            d = 0

        index.append({
            "name": movie,
            "slug": slug,
            "totalTickets": total_tickets,
            "totalShows": total_shows,
            "totalSeats": total_seats,
            "totalGross": total_gross,
            "firstDate": first_date,
            "lastDate": last_date,
            "currentDate": today_yyyymmdd,
            "d": d
        })

    # Write minified index
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
    print(f"📅 Processing all dates (based on shows found)")
    try:
        shows_by_date_movie, movie_lookup = fetch_and_parse()
    except Exception as e:
        print(f"❌ Error fetching data: {e}")
        return

    if not shows_by_date_movie:
        print("⚠️ No data fetched.")
        return

    # Save daily files for each date found
    for date_str, movie_data in shows_by_date_movie.items():
        print(f"\n📆 Processing date: {date_str}")
        filepath = get_daily_filepath(date_str)
        merge_and_save_daily(filepath, movie_data)

    # Update movie database
    update_movie_database(movie_lookup)

if __name__ == "__main__":
    main()
