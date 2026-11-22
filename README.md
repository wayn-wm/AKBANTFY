# Game Notifier: Arknights Global + Blue Archive JP

Sends a push notification to your phone when:
- a new **maintenance**, **update** or **event** is announced
- one of them is **3 hours away** (change `REMIND_HOURS` to adjust)

All times are shown in Hong Kong time.

**Where it checks:**
| Game | Source | What it finds there |
|---|---|---|
| Arknights Global | In-game announcement feed (Yostar) | Maintenance and update notices |
| Arknights Global | arknights.global news | Events, with start times |
| Blue Archive JP | bluearchive.jp news | Maintenance (メンテナンス), events (イベント), updates (アップデート) |

---

## Step 1: Set up ntfy on your phone (2 minutes)

1. Install **ntfy** from the App Store or Google Play (free, no account needed).
2. Tap **+** and subscribe to a topic. Choose a long, random name like `doctor-sensei-8f3k2q`.
   Anyone who knows the name can see your notifications, so don't use something easy to guess.
3. Remember the topic name for the next step.

## Step 2: Choose where it runs

### Option A: GitHub Actions (recommended: free, works while your PC is off)

1. Create a new GitHub repository and make it **public**. Public repos get unlimited
   free Actions minutes. Your topic name stays hidden because it goes in a secret.
2. Upload `notifier.py` and the `.github/workflows/notify.yml` file, keeping the same folder path.
3. In the repo, go to **Settings → Secrets and variables → Actions → New repository secret**.
   Name: `NTFY_TOPIC`, value: your topic name.
4. Go to the **Actions** tab, enable workflows if asked, open **game-notifier**,
   and press **Run workflow** once.

After that it checks every 15 minutes on its own. The first run only saves the
current announcements, so you won't get a burst of old ones. Notifications start
with the next new post.

> **Private repo?** The free plan has a monthly minutes limit, and every 15 minutes can
> go over it. Change the cron line in `notify.yml` to `"*/30 * * * *"`.
>
> If GitHub ever pauses the schedule after a long time with no repo activity,
> turn it back on from the Actions tab.

### Option B: Run it on your PC

You need Python 3.9 or newer. No extra packages are required.

1. Open `notifier.py` and type your topic in `NTFY_TOPIC = "..."` (near the top).
2. Test it: `python notifier.py --test`. Your phone should show a notification.
3. Start it: `python notifier.py --loop`. It checks every 15 minutes while the window stays open.

---

## Settings (at the top of notifier.py)

| Setting | Default | What it does |
|---|---|---|
| `NTFY_TOPIC` | (empty) | Your ntfy topic |
| `REMIND_HOURS` | 3 | How many hours before the start to send a reminder |
| `LOOP_MINUTES` | 15 | Check interval for `--loop` |

## Notes
- Blue Archive events that start "after maintenance" (メンテナンス後) get their reminder
  based on the usual maintenance start, 11:00 JST (10:00 HK).
- Gacha banners and surveys are skipped on purpose. To include them, edit `bluearchive_items()`.
- These are unofficial feeds that the official websites use behind the scenes. If Yostar
  changes its sites, the script prints a warning for that game and keeps checking the other one.
