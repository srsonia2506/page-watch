r"""Ping your phone (via ntfy.sh) when an item on a sold-out shop page becomes available.

Reads the page's embedded Next.js data; each item has a `v` field = units left
(0 or negative = sold out). Alerts when a watched item goes from sold out to
available. Python stdlib only.

All target details live in secrets so nothing identifying is in the repo or logs.

Config (environment variables):
  NTFY_TOPIC   required - your secret ntfy topic name
  PAGE_URL     required - the page to read
  BUY_URL      optional - link the notification opens (default: PAGE_URL)
  WATCH        optional - regex of item names to watch (default: all)
  STATE_FILE   optional - default state.json
  VERBOSE      optional - "true" prints item names (only use locally, never in public logs)
"""
import hashlib
import json
import os
import re
import sys
import urllib.request

PAGE_URL = os.environ.get("PAGE_URL", "").strip()
BUY_URL = os.environ.get("BUY_URL", "").strip() or PAGE_URL
WATCH = re.compile(os.environ.get("WATCH") or ".")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()
STATE_FILE = os.environ.get("STATE_FILE", "state.json")
VERBOSE = os.environ.get("VERBOSE") == "true"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")


def fetch_items():
    req = urllib.request.Request(PAGE_URL, headers={"User-Agent": UA, "Accept": "text/html"})
    with urllib.request.urlopen(req, timeout=30) as r:
        html = r.read().decode("utf-8", "replace")
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        raise RuntimeError("data not found on page (blocked or page changed)")
    event = json.loads(m.group(1))["props"]["pageProps"]["event"]
    cat_left = {c["id"]: c.get("v", 0) for c in event.get("categories", []) if "id" in c}
    items = {}
    for t in event["tickets"]:
        if not t.get("active") or not WATCH.search(t["name"]):
            continue
        left = t.get("v", 0)
        if t.get("categoryRef") in cat_left:  # category-wide cap also applies
            left = min(left, cat_left[t["categoryRef"]])
        items[t["name"]] = left
    if not items:
        raise RuntimeError("no items matched WATCH")
    return items


def key(name):
    # state.json is committed to the repo, so store a hash instead of the name
    return hashlib.sha256(name.encode("utf-8")).hexdigest()[:12]


def notify(title, message, priority="urgent", tags="rotating_light"):
    if not NTFY_TOPIC:
        print("NTFY_TOPIC not set, would have sent:", title, "-", message)
        return
    headers = {"Title": title, "Priority": priority, "Tags": tags}
    if BUY_URL:
        headers.update({"Click": BUY_URL, "Actions": f"view, Open, {BUY_URL}"})
    req = urllib.request.Request(f"https://ntfy.sh/{NTFY_TOPIC}",
                                 data=message.encode("utf-8"), headers=headers)
    urllib.request.urlopen(req, timeout=30).read()


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=1)


def main():
    if not PAGE_URL:
        sys.exit("PAGE_URL not set")
    if os.environ.get("TEST_PING") == "true":
        notify("Watcher test", "Notifications work! You'll get a ping like this when a spot opens.",
               priority="default", tags="tada")
        print("test ping sent")
    state = load_state()
    try:
        items = fetch_items()
    except Exception as e:
        print("CHECK FAILED:", type(e).__name__)
        fails = state.get("_fails", 0) + 1
        state["_fails"] = fails
        if fails == 6:  # ~30 min of failures in a row: tell you once
            notify("Watcher can't read the page", str(e)[:200], priority="default", tags="warning")
        save(state)
        sys.exit(1)

    was_open = set(state.get("open", []))
    now_open = {key(n) for n, left in items.items() if left > 0}
    new = sorted(n for n, left in items.items() if left > 0 and key(n) not in was_open)

    print(f"watched: {len(items)}  available: {len(now_open)}  newly available: {len(new)}")
    if VERBOSE:
        for n, left in sorted(items.items()):
            print(f"{'OPEN ' if left > 0 else 'sold '} {left:>5}  {n}")

    if new:
        notify("Spot open!", "\n".join(f"{n}: {items[n]} left" for n in new) + "\nTap to grab it.")
        print("alert sent")
    if state.get("_fails", 0) >= 6:
        notify("Watcher is back", "Reading the page again.", priority="low", tags="white_check_mark")

    save({"open": sorted(now_open)})


if __name__ == "__main__":
    main()
