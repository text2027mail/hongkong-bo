import requests
import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo
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

IST_TZ = ZoneInfo("Asia/Kolkata")
RUN_TIME = datetime.now(IST_TZ).strftime("%Y-%m-%d %I:%M:%S %p IST")
print("Run Time:", RUN_TIME)


def fetch_page():
    r = requests.get(URL, headers=HEADERS)
    return r.text


def extract_json_object(html):
    # Find the shows array
    pos = html.find('"shows":')
    if pos == -1:
        pos = html.find('\\"shows\\":')
        if pos == -1:
            return None

    # Find the opening '{' of the object
    start = pos
    while start >= 0 and html[start] != '{':
        start -= 1
    if start < 0:
        return None

    # Find matching closing '}'
    stack = 0
    i = start
    in_string = False
    escape = False
    while i < len(html):
        ch = html[i]
        if escape:
            escape = False
        elif ch == '\\':
            escape = True
        elif ch == '"' and not escape:
            in_string = not in_string
        elif not in_string:
            if ch == '{':
                stack += 1
            elif ch == '}':
                stack -= 1
                if stack == 0:
                    end = i + 1
                    break
        i += 1
    else:
        return None

    obj_str = html[start:end]

    # Unescape the escaped JSON by treating it as a JSON string
    try:
        inner = json.loads('"' + obj_str + '"')
        return json.loads(inner)
    except json.JSONDecodeError:
        return None


def parse_shows(data):
    shows = []
    if not data:
        return shows

    def search_shows(obj):
        if isinstance(obj, dict):
            for key, value in obj.items():
                if key == "shows" and isinstance(value, list):
                    for show in value:
                        try:
                            show_id = show["id"]
                            date = show["date"][:10]
                            time = show["time"][11:16] if len(show["time"]) >= 16 else show["time"]
                            price = show["price"]
                            seats = show["seats"]
                            sold = show["sold"]
                            movie = show["movie"]["name"]
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
                            continue
                else:
                    search_shows(value)
        elif isinstance(obj, list):
            for item in obj:
                search_shows(item)

    search_shows(data)
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
    data = extract_json_object(html)
    if data is None:
        print("Failed to extract JSON object. Check HTML.")
        return

    shows = parse_shows(data)
    print("Shows scraped:", len(shows))

    if shows:
        save_daily(shows)
        save_logs(shows)
        generate_monthly()
    else:
        print("No shows found.")


if __name__ == "__main__":
    main()
