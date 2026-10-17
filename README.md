# page-watch

Checks a page every ~5 min and sends a push notification (via [ntfy](https://ntfy.sh)) when a watched item goes from sold out to available.

All target details (page URL, item filter, notification topic) are repository **secrets**, so nothing identifying shows up in the code, the run logs, or `state.json`, which stores only hashes.

## Setup

1. Install the free **ntfy** app on your phone and subscribe to your secret topic name.
2. Create a **public** GitHub repo (public repos get unlimited free Actions minutes). Push this folder to it.
3. Go to Settings → Secrets and variables → Actions → **New repository secret** and add:
   | Name | Value |
   |---|---|
   | `NTFY_TOPIC` | your topic name |
   | `PAGE_URL` | the page to read |
   | `BUY_URL` | *(optional)* the link the notification opens |
   | `WATCH` | *(optional)* regex of item names to watch |
4. Go to Actions → **check** → **Run workflow**. You should get a test push within a minute. After that it runs on its own.

To stop it, go to Actions → **check** → `···` → **Disable workflow**.

## Notes

- You get one ping per item that becomes available. If it sells out and comes back, you get pinged again.
- GitHub's scheduler can lag 5–15 min when busy.
- After ~30 min of failed checks you get a "can't read the page" ping.
- To test locally with item names printed, set `VERBOSE=true`.
