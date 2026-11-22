#!/usr/bin/env python3
"""
Game news notifier: Arknights (Global) + Blue Archive (JP)  ->  phone push via ntfy.

- Sends a notification when a new maintenance / update / event announcement appears.
- Sends a reminder a few hours before it starts (REMIND_HOURS, default 3).
- Uses only the Python standard library (no pip install needed).

Usage:
    python notifier.py            check once (for GitHub Actions / Task Scheduler)
    python notifier.py --loop     keep running, check every 15 minutes (for your PC)
    python notifier.py --test     send a test notification
"""
import argparse
import html
import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

# ---------------------------------------------------------------- settings
# Your ntfy topic (pick something long and random, e.g. "doctor-sensei-8f3k2q").
# You can type it here, or set the NTFY_TOPIC environment variable / GitHub secret.
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")
NTFY_SERVER = os.environ.get("NTFY_SERVER", "https://ntfy.sh")
REMIND_HOURS = float(os.environ.get("REMIND_HOURS", "3"))  # reminder this many hours before start
LOOP_MINUTES = 15
STATE_FILE = os.environ.get(
    "STATE_FILE", os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")
)

# ---------------------------------------------------------------- sources
AK_ANNOUNCE = "https://ark-us-static-online.yo-star.com/announce/Android/announcement.meta.json"
AK_NEWS = "https://arknights.global/api/resource/news/list?index=1&size=10&type=latest"
BA_NEWS = "https://api-web.bluearchive.jp/api/news/list?typeId=0&pageNum=16&pageIndex=1"

HKT = timezone(timedelta(hours=8))    # Hong Kong (no daylight saving)
JST = timezone(timedelta(hours=9))    # Blue Archive JP times
AK_TZ = timezone(timedelta(hours=-7))  # Arknights Global writes times in UTC-7

UA = {"User-Agent": "Mozilla/5.0 (personal game-notifier)"}
TAGS = {"Maintenance": "wrench", "Update": "arrow_up", "Event": "tada"}


# ---------------------------------------------------------------- helpers
def get(url, as_json=True):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        data = r.read().decode("utf-8", "replace")
    return json.loads(data) if as_json else data


def strip_html(s):
    s = re.sub(r"(?is)<(script|style).*?</\1>", " ", s or "")
    s = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>", "\n", s)
    s = re.sub(r"<[^>]+>", " ", s)
    return html.unescape(s).replace("\xa0", " ")


def one_line(s):
    return " ".join(str(s).split())


def fmt_hk(dt):
    return dt.astimezone(HKT).strftime("%a %d %b, %H:%M") + " (HK time)"


def fmt_delta(td):
    mins = max(0, int(td.total_seconds() // 60))
    h, m = divmod(mins, 60)
    return f"{h}h {m:02d}m" if h else f"{m}m"


# ---------------------------------------------------------------- time parsing
_MONTHS = ["january", "february", "march", "april", "may", "june", "july",
           "august", "september", "october", "november", "december"]
AK_DT = re.compile(
    r"(" + "|".join(_MONTHS) + r")\s+(\d{1,2}),\s*(\d{4}),?\s+(\d{1,2}):(\d{2})", re.I
)
AK_TZ_RE = re.compile(r"\(UTC\s*([+-]\d{1,2})\)")


def parse_ak_start(text):
    """'November 23, 2026, 08:00-08:10 (UTC-7)' -> datetime (first date in the text)."""
    m = AK_DT.search(text)
    if not m:
        return None, None
    tzm = AK_TZ_RE.search(text, m.start())
    tz = timezone(timedelta(hours=int(tzm.group(1)))) if tzm else AK_TZ
    dt = datetime(int(m.group(3)), _MONTHS.index(m.group(1).lower()) + 1, int(m.group(2)),
                  int(m.group(4)), int(m.group(5)), tzinfo=tz)
    return dt, None


_PAREN = r"(?:\s*[\(（][^\)）]*[\)）])?"   # optional weekday like (水) or （水）
BA_FULL = re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日" + _PAREN + r"\s*(\d{1,2}:\d{2}|メンテナンス(?:終了)?後)?")
BA_SHORT = re.compile(r"(\d{1,2})/(\d{1,2})" + _PAREN + r"\s*(\d{1,2}):(\d{2})")


def make_ba_parser(publish_dt, kind):
    def parse(text):
        # Maintenance titles look like: メンテナンスのお知らせ（11/11 11:00 - 17:00予定）
        if kind == "Maintenance":
            m = BA_SHORT.search(text)
            if m:
                mon, day = int(m.group(1)), int(m.group(2))
                year = publish_dt.year + (1 if mon < publish_dt.month else 0)  # Dec -> Jan
                return datetime(year, mon, day, int(m.group(3)), int(m.group(4)), tzinfo=JST), None
        # Events: 開催期間 2026年11月11日(水) メンテナンス後 ~ 2026年11月25日(水) 10:59
        i = text.find("開催期間")
        m = BA_FULL.search(text, i if i >= 0 else 0) or BA_FULL.search(text)
        if not m or not m.group(4):
            return None, None
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if ":" in m.group(4):
            hh, mm = map(int, m.group(4).split(":"))
            return datetime(y, mo, d, hh, mm, tzinfo=JST), None
        # "after maintenance": maintenance usually begins 11:00 JST
        return datetime(y, mo, d, 11, 0, tzinfo=JST), "starts after that day's maintenance"
    return parse


# ---------------------------------------------------------------- sources -> items
AK_KW = re.compile(r"mainten|update|patch|version", re.I)


def arknights_items():
    items = []
    # 1) In-game announcements: this is where maintenance / update notices go
    meta = get(AK_ANNOUNCE)
    for a in meta.get("announceList", []):
        title = one_line(a.get("title", ""))
        if not AK_KW.search(title):
            continue
        kind = "Maintenance" if re.search("mainten", title, re.I) else "Update"
        url = a.get("webUrl") or ""
        items.append(dict(key=f"ak-ann-{a.get('announceId')}", game="Arknights", kind=kind,
                          title=title.title(), link=url, text=None, detail_url=url,
                          parse=parse_ak_start))
    # 2) Website news: events, with "DURATION: ... (UTC-7)"
    news = get(AK_NEWS)
    for r in news.get("data", {}).get("rows", []):
        if r.get("type") != "event":
            continue
        items.append(dict(key=f"ak-news-{r['id']}", game="Arknights", kind="Event",
                          title=one_line(r.get("title", "")),
                          link=f"https://arknights.global/news/{r['id']}",
                          text=strip_html(r.get("content", "")), detail_url=None,
                          parse=parse_ak_start))
    return items


def bluearchive_items():
    items = []
    data = get(BA_NEWS)
    for r in data.get("data", {}).get("rows", []):
        summary = one_line(r.get("summary", ""))
        t = r.get("typeId")
        if t == 3 or ("メンテナンス" in summary and t != 1):
            kind = "Maintenance"
        elif t == 1:
            kind = "Event"
        elif "アップデート" in summary:
            kind = "Update"
        else:
            continue  # gacha banners, surveys, etc.
        publish = datetime.fromtimestamp(r.get("publishTime", 0) / 1000, JST)
        items.append(dict(key=f"ba-{r['id']}", game="Blue Archive JP", kind=kind, title=summary,
                          link=f"https://bluearchive.jp/news/newsJump/{r['id']}",
                          text=summary + "\n" + strip_html(r.get("content", "")), detail_url=None,
                          parse=make_ba_parser(publish, kind)))
    return items


SOURCES = [("Arknights", arknights_items), ("Blue Archive JP", bluearchive_items)]


# ---------------------------------------------------------------- notifications
def notify(title, message, click=None, tags=None, priority=3):
    print(f"--> {title} | {message.replace(chr(10), ' | ')}")
    if not NTFY_TOPIC:
        print("    (NTFY_TOPIC not set, so nothing was sent)")
        return
    body = {"topic": NTFY_TOPIC, "title": title, "message": message, "priority": priority}
    if click:
        body["click"] = click
    if tags:
        body["tags"] = tags
    req = urllib.request.Request(NTFY_SERVER, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json", **UA}, method="POST")
    urllib.request.urlopen(req, timeout=30).read()


# ---------------------------------------------------------------- state
def load_state():
    if not os.path.exists(STATE_FILE):
        return None
    with open(STATE_FILE, encoding="utf-8") as f:
        return json.load(f)


def save_state(state):
    # keep the newest 500 seen ids so the file doesn't grow forever
    seen = sorted(state["seen"].items(), key=lambda kv: kv[1])[-500:]
    state["seen"] = dict(seen)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STATE_FILE)


# ---------------------------------------------------------------- main logic
def run_once(state, first_run=False, now=None):
    now = now or datetime.now(timezone.utc)
    remind = timedelta(hours=REMIND_HOURS)

    for name, fetch in SOURCES:
        try:
            items = fetch()
        except Exception as e:
            print(f"[warn] could not check {name}: {e}")
            continue
        for it in items:
            if it["key"] in state["seen"]:
                continue
            text = it["text"]
            if text is None:
                try:
                    text = strip_html(get(it["detail_url"], as_json=False))
                except Exception:
                    text = it["title"]
            start, note = it["parse"](text)

            if not first_run:  # first run only records what already exists, no spam
                msg = it["title"]
                if start:
                    msg += f"\nStarts: {fmt_hk(start)}" + (f", {note}" if note else "")
                try:
                    notify(f"[{it['game']}] New {it['kind']}", msg, click=it["link"],
                           tags=[TAGS.get(it["kind"], "bell")])
                except Exception as e:
                    print(f"[warn] notification failed, will retry next run: {e}")
                    continue

            state["seen"][it["key"]] = int(time.time())
            if start and start > now:
                state["reminders"].append(dict(
                    key=it["key"], game=it["game"], kind=it["kind"], title=it["title"],
                    link=it["link"], start=start.isoformat(), note=note,
                    # if it starts soon, the "new" notification already covered it
                    sent=(not first_run) and (start - now <= remind),
                ))

    keep = []
    for r in state["reminders"]:
        start = datetime.fromisoformat(r["start"])
        if start <= now:
            continue  # already started, drop it
        if not r["sent"] and start - now <= remind:
            msg = f"{r['title']}\nStarts: {fmt_hk(start)}" + (f", {r['note']}" if r.get("note") else "")
            try:
                notify(f"[{r['game']}] {r['kind']} in {fmt_delta(start - now)}", msg,
                       click=r["link"], tags=["alarm_clock"], priority=4)
                r["sent"] = True
            except Exception as e:
                print(f"[warn] reminder failed, will retry next run: {e}")
        keep.append(r)
    state["reminders"] = keep
    return state


def check():
    state = load_state()
    first_run = state is None
    if first_run:
        state = {"seen": {}, "reminders": []}
        print("First run: saving current announcements without notifying.")
    run_once(state, first_run=first_run)
    save_state(state)
    pending = [r for r in state["reminders"] if not r["sent"]]
    print(f"Done. Tracking {len(state['seen'])} posts, {len(pending)} upcoming reminder(s).")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--loop", action="store_true", help=f"check every {LOOP_MINUTES} minutes")
    p.add_argument("--test", action="store_true", help="send a test notification")
    a = p.parse_args()

    if a.test:
        if not NTFY_TOPIC:
            sys.exit("Set NTFY_TOPIC first (edit notifier.py or set the environment variable).")
        notify("Game notifier works!", "You'll get Arknights and Blue Archive JP alerts here.",
               tags=["white_check_mark"])
        print("Test notification sent.")
        return

    if a.loop:
        while True:
            try:
                check()
            except Exception as e:
                print(f"[error] {e}")
            time.sleep(LOOP_MINUTES * 60)
    else:
        check()


if __name__ == "__main__":
    main()
