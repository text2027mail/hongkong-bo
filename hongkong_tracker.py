import requests
import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo
from collections import defaultdict

URL = "https://www.cinema.com.hk/en/movie/ticketing"
HEADERS = {"User-Agent": "Mozilla/5.0"}

IST_TZ = ZoneInfo("Asia/Kolkata")
RUN_TIME = datetime.now(IST_TZ).strftime("%Y-%m-%d %I:%M:%S %p IST")
print("Run Time:", RUN_TIME)


def fetch_page():
    r = requests.get(URL, headers=HEADERS)
    return r.text


def extract_shows_array(html):
    """
    Find the JSON array for 'shows' using bracket counting.
    Searches for the literal "shows": in the HTML.
    """
    # Look for "shows": (with quotes) in the HTML
    # In the raw HTML, it is \"shows\":, but the backslash is an escape character in JSON,
    # so in the HTML text it's actually a backslash and a quote. We search for the sequence.
    # Let's search for the literal substring: "shows": (double quote, shows, double quote, colon)
    # But since the HTML has backslashes, we need to search for the raw bytes? No.
    # In Python, when we read the HTML, the backslashes are present. So we can search for:
    # start = html.find('"shows":')
    # But because it's escaped in the JSON string, it's actually '"shows":'? Actually the HTML
    # contains the characters: backslash, double quote, shows, double quote, colon.
    # So the literal substring is \ "shows": but with a backslash.
    # We'll search for both possibilities: with and without backslash.
    start = html.find('"shows":')
    if start == -1:
        # Try with backslash
        start = html.find('\\"shows\\":')
        if start == -1:
            return None

    # We found the key, now find the colon and then the '[' that starts the array
    colon = html.find(':', start)
    if colon == -1:
        return None

    # Find the '[' after the colon
    bracket_start = html.find('[', colon)
    if bracket_start == -1:
        return None

    # Now extract the array using bracket counting
    stack = 0
    i = bracket_start
    in_string = False
    escape = False
    while i < len(html):
        ch = html[i]
        if escape:
            escape = False
        elif ch == '\\':
            escape = True
        elif ch == '"':
            in_string = not in_string
        elif not in_string:
            if ch == '[':
                stack += 1
            elif ch == ']':
                stack -= 1
                if stack == 0:
                    end = i + 1
                    break
        i += 1
    else:
        return None

    array_str = html[bracket_start:end]
    try:
        return json.loads(array_str)
    except json.JSONDecodeError:
        # The array might have escaped characters, but json.loads should handle it if it's proper JSON.
        # If not, maybe we need to unescape, but let's just return None.
        return None


def parse_shows_from_array(shows_array):
    shows = []
    for show in shows_array:
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
    shows_array = extract_shows_array(html)
    if shows_array is None:
        print("Could not find shows array. Check HTML structure.")
        return

    shows = parse_shows_from_array(shows_array)
    print("Shows scraped:", len(shows))

    if shows:
        save_daily(shows)
        save_logs(shows)
        generate_monthly()
    else:
        print("No shows found.")


if __name__ == "__main__":
    main()
