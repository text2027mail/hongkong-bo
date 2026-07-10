import requests
import json
import os
from datetime import datetime, timedelta, timezone
from collections import defaultdict

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

IST_TZ = timezone(timedelta(hours=5, minutes=30))
HK_TZ = timezone(timedelta(hours=8))
RUN_TIME = datetime.now(IST_TZ).strftime("%Y-%m-%d %I:%M:%S %p IST")
print("Run Time:", RUN_TIME)


def fetch_page():
    r = requests.get(URL, headers=HEADERS)
    return r.text


def extract_all_flight_strings(html):
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


def parse_payloads(payload_strings):
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


def resolve_reference(ref, chunks, root_data=None):
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


def find_key(data, target_key):
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


def build_movie_lookup(chunks):
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


def build_site_lookup(chunks):
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


def parse_shows_from_array(shows_array, chunks, movie_lookup, site_lookup):
    shows = []
    for show in shows_array:
        try:
            show_id = show["id"]
            # Convert UTC to HK time
            time_str = show["time"]
            utc_dt = datetime.fromisoformat(time_str.replace('Z', '+00:00'))
            hk_dt = utc_dt.astimezone(HK_TZ)
            date = hk_dt.strftime('%Y-%m-%d')
            time_hm = hk_dt.strftime('%I:%M %p').lstrip('0')

            price = show["price"]
            seats = show["seats"]
            sold = show["sold"]

            # Debug for the problematic show
            if show_id == 124082:
                print(f"DEBUG show {show_id}: sold={sold}, avaliable={show.get('avaliable')}")

            # Movie name
            movie_obj = show.get("movie")
            if isinstance(movie_obj, str) and movie_obj.startswith('$'):
                movie_obj = resolve_reference(movie_obj, chunks)
            if not isinstance(movie_obj, dict):
                print(f"Warning: movie_obj is not a dict for show {show_id}: {movie_obj}")
                continue
            movie_name = movie_obj.get("name")
            if not movie_name:
                movie_id = movie_obj.get("id")
                if movie_id is not None and movie_id in movie_lookup:
                    movie_name = movie_lookup[movie_id].get("name", f"Movie {movie_id}")
                else:
                    movie_name = f"Movie {movie_id if movie_id is not None else 'unknown'}"

            # Venue name
            site_obj = show.get("site", {})
            site_id = site_obj.get("id")
            venue = site_lookup.get(site_id, f"Site {site_id}") if site_id is not None else "Cinema.com.hk"

            shows.append({
                "perfIx": show_id,
                "movie": movie_name,
                "venue": venue,
                "date": date,
                "time": time_hm,
                "total": seats,
                "available": seats - sold,       # compute from sold
                "blocked": 0,
                "sold": sold,
                "gross": sold * price,
                "price": price,
                "last_updated": RUN_TIME
            })
        except KeyError as e:
            print(f"Skipping show due to missing key: {e}")
            continue
    return shows


def save_daily(shows):
    grouped = defaultdict(list)
    for s in shows:
        grouped[s["date"]].append(s)

    for date, data in grouped.items():
        year = date[:4]
        mmdd = date[5:]
        path = f"Hongkong Data/{year}"
        os.makedirs(path, exist_ok=True)

        file = f"{path}/{mmdd}.json"
        # Overwrite to avoid stale data
        with open(file, "w") as f:
            json.dump(data, f, indent=2)
        print("Saved:", file)


def save_logs(shows):
    grouped = defaultdict(list)
    for s in shows:
        grouped[s["date"]].append(s)

    for date, data in grouped.items():
        year = date[:4]
        mmdd = date[5:]
        path = f"Hongkong Data/{year}"
        log_file = f"{path}/{mmdd}_logs.json"

        total_shows = len(data)
        sold = sum(x["sold"] for x in data)
        capacity = sum(x["total"] for x in data)
        gross = sum(x["gross"] for x in data)

        log = {
            "time": RUN_TIME,
            "date": date,
            "total_shows": total_shows,
            "tickets_sold": sold,
            "total_gross_hkd": gross,
            "avg_occupancy": round((sold / capacity) * 100 if capacity else 0, 2),
            "unique_movies": len(set(x["movie"] for x in data))
        }

        if os.path.exists(log_file):
            with open(log_file, "r") as f:
                logs = json.load(f)
        else:
            logs = []

        logs.append(log)
        with open(log_file, "w") as f:
            json.dump(logs, f, indent=2)
        print("Log updated:", log_file)


def generate_monthly():
    monthly = defaultdict(lambda: defaultdict(lambda: {
        "shows": 0,
        "seats": 0,
        "sold": 0,
        "gross": 0,
        "dates": defaultdict(lambda: {
            "shows": 0,
            "seats": 0,
            "sold": 0,
            "gross": 0
        })
    }))

    for root, _, files in os.walk("Hongkong Data"):
        for f in files:
            if not f.endswith(".json") or "_logs" in f:
                continue
            with open(os.path.join(root, f), "r") as fp:
                data = json.load(fp)
            for d in data:
                date = d["date"]
                ym = date[:7]
                movie = d["movie"]
                x = monthly[ym][movie]
                x["shows"] += 1
                x["seats"] += d["total"]
                x["sold"] += d["sold"]
                x["gross"] += d["gross"]

                dd = x["dates"][date]
                dd["shows"] += 1
                dd["seats"] += d["total"]
                dd["sold"] += d["sold"]
                dd["gross"] += d["gross"]

    os.makedirs("Hongkong Summary", exist_ok=True)
    for ym, data in monthly.items():
        out = f"Hongkong Summary/{ym}.json"
        with open(out, "w") as f:
            json.dump(data, f, indent=2)
        print("Monthly summary:", out)


def main():
    html = fetch_page()
    print(f"HTML length: {len(html)}")
    print("Contains self.__next_f.push:", "self.__next_f.push" in html)

    all_strings = extract_all_flight_strings(html)
    print(f"Found {len(all_strings)} flight strings.")

    if not all_strings:
        print("No flight strings extracted.")
        return

    chunks = parse_payloads(all_strings)
    print(f"Parsed {len(chunks)} chunks.")

    movie_lookup = build_movie_lookup(chunks)
    print(f"Found {len(movie_lookup)} unique movies.")
    site_lookup = build_site_lookup(chunks)
    print(f"Found {len(site_lookup)} unique venues.")

    # Find the shows array
    shows_array = None
    used_chunk = None
    for chunk_id, data in chunks.items():
        shows = find_key(data, 'shows')
        if shows is not None:
            shows_array = shows
            used_chunk = chunk_id
            break

    if shows_array is None:
        print("Could not find 'shows' array in any chunk.")
        return

    print(f"Using shows array from chunk {used_chunk} with {len(shows_array)} entries.")
    # Debug: print sold for show 124082 if present
    for s in shows_array:
        if s.get("id") == 124082:
            print(f"DEBUG from array: id=124082 sold={s.get('sold')}, avaliable={s.get('avaliable')}")

    shows = parse_shows_from_array(shows_array, chunks, movie_lookup, site_lookup)
    print("Shows scraped:", len(shows))

    if shows:
        save_daily(shows)
        save_logs(shows)
        generate_monthly()
    else:
        print("No valid shows found.")


if __name__ == "__main__":
    main()
