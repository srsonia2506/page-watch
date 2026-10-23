r"""Ping your phone (via ntfy.sh) when an item on a sold-out shop page becomes available.

Reads the page's embedded Next.js data; each item has a `v` field = units left
(0 or negative = sold out). Alerts when a watched item goes from sold out to
available. Python stdlib only.

All target details live in secrets so nothing identifying is in the repo or logs.

Config (environment variables):
  NTFY_TOPIC   required - your secret ntfy topic name
  PAGE_URL     required - page(s) to read; put several on separate lines or separate with spaces
  BUY_URL      optional - link the notification opens (default: that item's event page)
  WATCH        optional - regex of item names to watch (default: all)
  STATE_FILE   optional - default state.json
  VERBOSE      optional - "true" prints item names (only use locally, never in public logs)
"""
import hashlib
import json
import os
import re
import sys
import urllib.parse
import urllib.request

PAGE_URLS = os.environ.get("PAGE_URL", "").split()
BUY_URL = os.environ.get("BUY_URL", "").strip()
WATCH = re.compile(os.environ.get("WATCH") or ".")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()
STATE_FILE = os.environ.get("STATE_FILE", "state.json")
VERBOSE = os.environ.get("VERBOSE") == "true"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")


def fetch_items(page_url):
    req = urllib.request.Request(page_url, headers={"User-Agent": UA, "Accept": "text/html"})
    with urllib.request.urlopen(req, timeout=30) as r:
        html = r.read().decode("utf-8", "replace")
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        raise RuntimeError("data not found on page (blocked or page changed)")
    event = json.loads(m.group(1))["props"]["pageProps"]["event"]
    cat_left = {c["id"]: c.get("v", 0) for c in event.get("categories", []) if "id" in c}
    items, labels = {}, {}
    for t in event["tickets"]:
        if not t.get("active") or not WATCH.search(t["name"]):
            continue
        left = t.get("v", 0)
        if t.get("categoryRef") in cat_left:  # category-wide cap also applies
            left = min(left, cat_left[t["categoryRef"]])
        items[t["name"]] = left
        labels[t["name"]] = division(t)
    if not items:
        raise RuntimeError("no items matched WATCH")
    origin = "{0.scheme}://{0.netloc}".format(urllib.parse.urlsplit(page_url))
    event_link = f"{origin}/event/{event['url']}" if event.get("url") else page_url
    return event.get("name", ""), event_link, items, labels


def division(ticket):
    """Division label from the ticket's class code, e.g. SOLO_OPEN_W -> OPEN, SOLO_PRO_W -> PRO."""
    code = (ticket.get("meta") or {}).get("competition_class_matching_key", "")
    return " ".join(p for p in code.upper().split("_") if p not in ("SOLO", "M", "W", "X", "MX"))


def key(page_url, name):
    # state.json is committed to the repo, so store a hash instead of the name
    return hashlib.sha256(f"{page_url}|{name}".encode("utf-8")).hexdigest()[:12]


def notify(title, message, priority="urgent", tags="rotating_light", link=""):
    if not NTFY_TOPIC:
        print("NTFY_TOPIC not set, would have sent:", title, "-", message)
        return
    headers = {"Title": title, "Priority": priority, "Tags": tags}
    link = BUY_URL or link
    if link:
        headers.update({"Click": link, "Actions": f"view, Open, {link}"})
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
    if not PAGE_URLS:
        sys.exit("PAGE_URL not set")
    if os.environ.get("TEST_PING") == "true":
        notify("Watcher test", "Notifications work! You'll get a ping like this when a spot opens.",
               priority="default", tags="tada")
        print("test ping sent")
    state = load_state()
    was_open = set(state.get("open", []))
    now_open, failed = set(), []

    for i, page_url in enumerate(PAGE_URLS, 1):
        try:
            event_name, event_link, items, labels = fetch_items(page_url)
        except Exception as e:
            print(f"page {i}: CHECK FAILED:", type(e).__name__)
            failed.append(f"page {i}: {str(e)[:150]}")
            # keep this page's previous state so a blip doesn't cause a repeat ping
            now_open |= {k for k in was_open if k.startswith(f"{i}:")}
            continue
        opened = {f"{i}:{key(page_url, n)}" for n, left in items.items() if left > 0}
        new = sorted(n for n, left in items.items()
                     if left > 0 and f"{i}:{key(page_url, n)}" not in was_open)
        now_open |= opened
        print(f"page {i}: watched: {len(items)}  available: {len(opened)}  newly available: {len(new)}")
        if VERBOSE:
            for n, left in sorted(items.items()):
                print(f"  {'OPEN ' if left > 0 else 'sold '} {left:>5}  [{labels[n]}] {n}")
        if new:
            new.sort(key=lambda n: labels[n] != "OPEN")  # Open is the priority, list it first
            body = "\n".join(f"{labels[n] + ' - ' if labels[n] else ''}{n}: {items[n]} left" for n in new)
            kinds = " + ".join(dict.fromkeys(labels[n] for n in new if labels[n]))
            title = f"{kinds} spot open! {event_name}" if kinds else f"Spot open! {event_name}"
            notify(title.strip(), body + "\nTap to grab it.", link=event_link)
            print(f"page {i}: alert sent")

    fails = state.get("_fails", 0) + 1 if failed else 0
    if fails == 6:  # ~30 min of failures in a row: tell you once
        notify("Watcher can't read the page", "\n".join(failed), priority="default", tags="warning")
    if not failed and state.get("_fails", 0) >= 6:
        notify("Watcher is back", "Reading the page again.", priority="low", tags="white_check_mark")
    save({"open": sorted(now_open), **({"_fails": fails} if fails else {})})
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
