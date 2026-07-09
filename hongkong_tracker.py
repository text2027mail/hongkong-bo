import requests
import json
import os
import re
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


def extract_flight_string(html):
    """
    Extract the string argument from self.__next_f.push([1, " ... "])
    Returns the raw string content (including all escapes) or None.
    """
    # Pattern to match self.__next_f.push([1, " ... "])
    # The string can contain escaped quotes, so we capture everything between the quotes,
    # using a non-greedy match that stops at the first unescaped quote.
    # We'll use a more robust approach: find start, then scan manually to handle escapes.
    start = html.find('self.__next_f.push([1, "')
    if start == -1:
        print("Could not find self.__next_f.push([1, ...")
        return None
    start += len('self.__next_f.push([1, "')
    # Now find the closing quote, respecting escapes
    i = start
    while i < len(html):
        if html[i] == '\\':
            i += 2  # skip escaped character
            continue
        if html[i] == '"':
            # potential closing quote
            # check if it's followed by ]) or just ])
            # Actually the push ends with ]) so we can just take until the quote.
            # But we need to make sure we don't stop at a quote inside the string.
            # Since we are inside the string, any quote preceded by backslash is escaped,
            # and we already skip backslashes. So the first unescaped quote is the closing one.
            end = i
            break
        i += 1
    else:
        print("Could not find closing quote for flight string.")
        return None
    raw = html[start:end]
    # The raw string contains escapes like \" and \/ etc.
    # We need to decode them to actual JSON.
    # We can use json.loads on a string that wraps it in quotes.
    # But we must handle the fact that the raw string might contain newlines? It doesn't.
    # We'll parse it as a JSON string.
    try:
        # Unescape the string: treat raw as a JSON string literal.
        decoded = json.loads('"' + raw + '"')
        return decoded
    except json.JSONDecodeError as e:
        print(f"Failed to decode flight string: {e}")
        # Fallback: try to manually unescape by replacing known sequences
        # But we'll rely on json.loads
        return None


def parse_flight_payload(payload_str):
    """
    The payload_str is a concatenation of chunks like "5:...6:...".
    We extract the first chunk (or all, but we just need the one containing "shows").
    Returns the parsed JSON data from the chunk.
    """
    # Find the first colon to separate chunk ID from data.
    colon_idx = payload_str.find(':')
    if colon_idx == -1:
        print("No colon found in flight payload")
        return None
    data_str = payload_str[colon_idx+1:]
    # The data_str is a JSON value (array or object) with escaped quotes.
    # json.loads can parse it directly because it contains valid JSON with escaped quotes.
    try:
        return json.loads(data_str)
    except json.JSONDecodeError as e:
        print(f"Failed to parse flight data: {e}")
        print("Data snippet:", data_str[:200])
        return None


def find_shows(data):
    """
    Recursively search for a key "shows" in the parsed data.
    Returns the shows array (list) if found, else None.
    """
    if isinstance(data, dict):
        if "shows" in data:
            return data["shows"]
        for v in data.values():
            res = find_shows(v)
            if res is not None:
                return res
    elif isinstance(data, list):
        for item in data:
            res = find_shows(item)
            if res is not None:
                return res
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
    print(f"HTML length: {len(html)}")
    print("Contains self.__next_f.push:", "self.__next_f.push" in html)

    # Extract the raw flight string
    flight_str = extract_flight_string(html)
    if flight_str is None:
        print("Could not extract flight string.")
        # Optionally save debug HTML
        with open("debug.html", "w", encoding="utf-8") as f:
            f.write(html)
        print("Saved debug.html for inspection.")
        return

    # Parse the flight payload
    parsed_data = parse_flight_payload(flight_str)
    if parsed_data is None:
        print("Failed to parse flight data.")
        return

    # Find the shows array
    shows_array = find_shows(parsed_data)
    if shows_array is None:
        print("Could not find 'shows' key in parsed data.")
        # If you want to inspect the parsed data, save it
        with open("parsed_data.json", "w", encoding="utf-8") as f:
            json.dump(parsed_data, f, indent=2)
        print("Saved parsed_data.json for inspection.")
        return

    print(f"Found shows array with {len(shows_array)} entries.")
    shows = parse_shows_from_array(shows_array)
    print("Shows scraped:", len(shows))

    if shows:
        save_daily(shows)
        save_logs(shows)
        generate_monthly()
    else:
        print("No valid shows found.")


if __name__ == "__main__":
    main()
