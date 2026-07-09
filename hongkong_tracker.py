import requests
import re
import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo
from collections import defaultdict

URL = "https://www.cinema.com.hk/en/movie/ticketing"
HEADERS = {"User-Agent": "Mozilla/5.0"}

HK_TZ = ZoneInfo("Asia/Hong_Kong")
IST_TZ = ZoneInfo("Asia/Kolkata")
RUN_TIME = datetime.now(IST_TZ).strftime("%Y-%m-%d %I:%M:%S %p IST")
print("Run Time:", RUN_TIME)


def fetch_page():
    r = requests.get(URL, headers=HEADERS)
    return r.text


def extract_chunks(html):
    """
    Extract JSON strings from self.__next_f.push([1, "..."]).
    Returns a list of parsed Python objects (dict/list).
    """
    chunks = []
    # regex to capture the double-quoted JSON string (handles escaped quotes)
    pattern = r'self\.__next_f\.push\(\[1,\s*"((?:[^"\\]|\\.)*)"\)'
    for match in re.findall(pattern, html, re.S):
        try:
            data = json.loads(match)
            chunks.append(data)
        except json.JSONDecodeError:
            pass
    return chunks


def parse_shows(chunks):
    shows = []

    def search_shows(obj):
        """Recursively find all 'shows' lists inside obj."""
        if isinstance(obj, dict):
            for key, value in obj.items():
                if key == "shows" and isinstance(value, list):
                    for show in value:
                        try:
                            # Extract required fields
                            show_id = show["id"]
                            date = show["date"][:10]  # YYYY-MM-DD
                            time = show["time"][11:16] if len(show["time"]) >= 16 else show["time"]
                            price = show["price"]
                            seats = show["seats"]
                            sold = show["sold"]
                            movie = show["movie"]["name"]
                            # venue: use site.name if present, otherwise fallback
                            venue = show.get("site", {}).get("name", "Cinema.com.hk")
                            shows.append({
                                "perfIx": show_id,
                                "movie": movie,
                                "venue": venue,
                                "date": date,
                                "time": time,
                                "total": seats,
                                "available": seats - sold,
                                "blocked": 0,
                                "sold": sold,
                                "gross": sold * price,
                                "price": price,
                                "last_updated": RUN_TIME
                            })
                        except KeyError:
                            # Skip if any required field is missing
                            pass
                else:
                    search_shows(value)
        elif isinstance(obj, list):
            for item in obj:
                search_shows(item)

    for chunk in chunks:
        search_shows(chunk)

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
        if os.path.exists(file):
            with open(file, "r") as f:
                old = json.load(f)
        else:
            old = []

        # Merge by perfIx (preserve latest data)
        index = {d["perfIx"]: d for d in old}
        for s in data:
            index[s["perfIx"]] = s

        merged = list(index.values())
        with open(file, "w") as f:
            json.dump(merged, f, indent=2)
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
    chunks = extract_chunks(html)
    shows = parse_shows(chunks)
    print("Shows scraped:", len(shows))
    if shows:
        save_daily(shows)
        save_logs(shows)
        generate_monthly()
    else:
        print("No shows found. Check extraction.")


if __name__ == "__main__":
    main()
