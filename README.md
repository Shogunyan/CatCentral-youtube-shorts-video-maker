# CatCentral — YouTube Shorts Maker

Automatically finds viral cat videos, compiles them into ranked "Top 5" videos with AI voiceover, and uploads them to your YouTube Shorts channel — 3 times a day, hands-free.

---

## What it does

Every time it runs, the app:

1. **Scrapes** viral cat videos from YouTube Shorts, TikTok, and Instagram (looking for high view counts)
2. **Downloads** the best clips it finds (3–5)
3. **Generates AI voiceover** using a free Reddit-narrator-style voice (Microsoft Edge TTS) — lines like *"These are the 5 funniest cat videos on the internet — ranked"* and *"Coming in at number five…"*
4. **Edits the video** — resizes all clips to vertical (9:16 for Shorts), adds big rank number overlays (#5 → #1), title text at the top, and a moving `@CatCentral` watermark that rotates between corners
5. **Uploads to your channel** with a randomly generated title, description, and hashtags

You set it up once and it runs 3 times a day automatically (default: 9am, 2pm, 7pm).

---

## Requirements — what you need before starting

- A computer running **Windows**, **Mac**, or **Linux**
- **Python 3.10 or newer**
- **ffmpeg** installed (free video tool the app uses behind the scenes)
- A **YouTube channel** (your CatCentral channel)
- A **Google account** that owns the channel (for uploading)
- An **internet connection** — required every time the app runs (for scraping, downloading, TTS, and uploading)

> **Note on Instagram:** Adding your Instagram login is optional and gives the app one more source for finding cat videos. However, Instagram actively limits automated access — if you use it heavily, Instagram may temporarily lock your account. Use a secondary account.

---

## Setup — Windows (WSL)

Windows users run this app through **WSL** (Windows Subsystem for Linux). This gives you a real Linux environment that the app needs.

### Step 1 — Install WSL

Open **PowerShell as Administrator** (right-click the Start button → "Terminal (Admin)") and run:

```powershell
wsl --install
```

**Restart your PC** when it asks. After restart, the **Ubuntu** app will open automatically. Set a username and password when prompted (remember this password).

### Step 2 — Install system dependencies

In your Ubuntu terminal:

```bash
sudo apt update && sudo apt install -y python3 python3-pip python3-venv ffmpeg git
```

### Step 3 — Clone and install

```bash
git clone https://github.com/shogunyan/catcentral-youtube-shorts-video-maker.git
cd catcentral-youtube-shorts-video-maker
git checkout claude/cat-video-ranking-system-bht7M
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pip install --upgrade cryptography cffi
```

### Step 4 — Set up Google credentials

1. Go to [console.cloud.google.com](https://console.cloud.google.com) in your Windows browser
2. Create a new project (name it anything, e.g. "CatCentral")
3. Go to **APIs & Services → Library** → search **YouTube Data API v3** → click **Enable**
4. Go to **APIs & Services → OAuth consent screen** (or "Google Auth Platform → Overview"):
   - Choose **External** → Create
   - Fill in App name (e.g. "CatCentral") and your email for both email fields
   - Click **Save and Continue** through all screens
5. Go to **Audience** (left sidebar) → **Add users** → add your own Gmail address → Save
6. Go to **Clients** (left sidebar) → **+ Create Client** → choose **Desktop app** → Create
7. Copy the **Client ID** and **Client Secret** from the popup

### Step 5 — Run the app

```bash
source venv/bin/activate
python3 main.py run
```

The first time it runs, it will ask you to authorize YouTube access:
- A URL will be printed in the terminal (starts with `https://accounts.google.com/...`)
- If a browser doesn't open automatically, **copy the URL** and paste it into your Windows browser
- Log in with the Google account that owns your YouTube channel
- You'll see a warning *"CatCentral has not completed the Google verification process"* — this is normal. Click **Continue** to proceed
- Click **Allow** when asked for permissions
- The terminal will show "success" — you're done. This only needs to happen once.

> If you closed the terminal and need to come back later, always run these first:
> ```bash
> cd ~/catcentral-youtube-shorts-video-maker
> source venv/bin/activate
> ```

---

## Setup — Mac

### Step 1 — Install dependencies

```bash
# Install Homebrew if you don't have it
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Install Python and ffmpeg
brew install python ffmpeg git
```

### Step 2 — Clone and install

```bash
git clone https://github.com/shogunyan/catcentral-youtube-shorts-video-maker.git
cd catcentral-youtube-shorts-video-maker
git checkout claude/cat-video-ranking-system-bht7M
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Step 3 — Google credentials

Follow **Step 4** from the Windows section above (it's the same process).

### Step 4 — Run

```bash
source venv/bin/activate
python3 main.py run
```

---

## Setup — Linux

### Step 1 — Install dependencies

```bash
sudo apt update && sudo apt install -y python3 python3-pip python3-venv ffmpeg git
```

### Step 2 — Clone and install

```bash
git clone https://github.com/shogunyan/catcentral-youtube-shorts-video-maker.git
cd catcentral-youtube-shorts-video-maker
git checkout claude/cat-video-ranking-system-bht7M
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Step 3 — Google credentials

Follow **Step 4** from the Windows section above.

### Step 4 — Run

```bash
source venv/bin/activate
python3 main.py run
```

---

## Usage

### Run once right now (recommended for first test)

```bash
python3 main.py run
```

This runs the full pipeline in your terminal with plain text output. You'll see every step happening in real time. Best way to verify everything works.

### Run on a schedule (3 times a day)

```bash
python3 main.py schedule
```

Runs the pipeline automatically at your configured times (default: 9:00, 14:00, 19:00). Leave the terminal open. Press `Ctrl+C` to stop.

### Interactive dashboard (TUI)

```bash
python3 main.py
```

Opens a graphical terminal interface with progress bar, live log, and buttons. Press `R` to run, `S` to start the scheduler, `Q` to quit.

### Other commands

```bash
python3 main.py test      # Dry run — scrape + edit but skip upload
python3 main.py auth      # Re-run YouTube OAuth (if token expired)
```

### Keyboard shortcuts (TUI only)

| Key | Action |
|-----|--------|
| `R` | Run pipeline once now |
| `S` | Start/stop the daily scheduler |
| `T` | Open settings |
| `Q` | Quit |

---

## How to keep it running 24/7

If you want uploads to happen even when you close your terminal:

**Mac / Linux:**
```bash
nohup python3 main.py schedule > logs/scheduler.log 2>&1 &
```
To stop: `pkill -f "main.py schedule"`

**Windows (WSL):**
```bash
nohup python3 main.py schedule > logs/scheduler.log 2>&1 &
```
Note: WSL stays running in the background as long as you don't shut down Windows. To stop: `pkill -f "main.py schedule"`

---

## Configuration

All settings live in the `.env` file in the project root. Edit it with any text editor:

```bash
nano .env
```

| Setting | Default | Description |
|---------|---------|-------------|
| `GOOGLE_CLIENT_ID` | (required) | From Google Cloud Console |
| `GOOGLE_CLIENT_SECRET` | (required) | From Google Cloud Console |
| `INSTAGRAM_USERNAME` | (blank) | Optional — for scraping Instagram reels |
| `INSTAGRAM_PASSWORD` | (blank) | Optional — use a burner account |
| `UPLOAD_TIMES` | `09:00,14:00,19:00` | Daily upload schedule (24h format) |
| `CLIPS_PER_VIDEO` | `5` | Target clips per video (will proceed with 3+ if not enough found) |
| `CLIP_DURATION` | `10` | Seconds per clip |
| `WATERMARK_TEXT` | `@CatCentral` | Text watermark on videos |
| `TTS_ENABLED` | `true` | Enable AI voiceover |
| `TTS_VOICE` | `en-US-GuyNeural` | Microsoft Edge TTS voice |
| `LOG_LEVEL` | `INFO` | Logging detail level |

### Available voices

```
en-US-GuyNeural          # Male narrator (default, Reddit-style)
en-US-EricNeural         # Slightly different male
en-GB-RyanNeural         # British male narrator
en-US-JennyNeural        # Female voice
en-US-AriaNeural         # Another female option
```

To disable voiceover: `TTS_ENABLED=false`

---

## Troubleshooting

**"No candidates found" during scraping**
The scraper couldn't find cat videos. Check your internet connection. TikTok sometimes blocks scrapers — YouTube Shorts is the most reliable source and usually finds enough on its own.

**"Got 4/5 clips — proceeding with fewer"**
Normal. The app needs at least 3 clips. If it only finds 4, it makes a "Top 4" video instead. Only fails if fewer than 3 are found.

**Browser doesn't open during YouTube authorization (WSL)**
Copy the URL from the terminal and paste it into your Windows browser manually. The URL starts with `https://accounts.google.com/...`

**"CatCentral has not completed the Google verification process"**
Go to Google Cloud Console → OAuth consent screen → **Audience** → **Add users** → add your Gmail address. Then try again.

**"This app isn't verified" warning from Google**
Click **Continue** (or **Advanced → Go to CatCentral**). It's your own app — it's safe.

**Upload fails with a 403 error**
Your OAuth token may have expired. Run `python3 main.py auth` to re-authorize.

**Video is silent (no voiceover)**
Make sure `edge-tts` installed: `pip install edge-tts`. Check that `TTS_ENABLED=true` in `.env`.

**ffmpeg not found**
Install it: `sudo apt install ffmpeg` (Linux/WSL) or `brew install ffmpeg` (Mac).

**"externally-managed-environment" error from pip**
You forgot to activate the virtual environment. Run `source venv/bin/activate` first.

---

## File structure

```
catcentral-youtube-shorts-video-maker/
├── main.py              ← run this to start
├── tui.py               ← graphical terminal UI
├── config.py            ← reads settings from .env
├── .env                 ← your credentials and settings
├── requirements.txt     ← Python package list
├── src/
│   ├── scraper.py       ← finds viral cat videos
│   ├── downloader.py    ← downloads video files
│   ├── video_editor.py  ← compiles the ranking video
│   ├── tts.py           ← generates AI voiceover
│   ├── caption_gen.py   ← generates titles/descriptions/hashtags
│   ├── uploader.py      ← uploads to YouTube via API
│   ├── video_tracker.py ← tracks uploads and re-upload cycles
│   └── scheduler.py     ← runs the full pipeline
├── data/
│   ├── downloaded/      ← raw downloaded clips
│   ├── processed/       ← finished videos
│   └── used_videos.json ← tracks which clips have been used
└── logs/
    └── catcentral.log   ← detailed log
```

---

## Notes

- The app tracks every video it uses (`data/used_videos.json`) so it avoids repeating clips
- Clips can be reused up to 2 times across different videos, and their counters reset after 14 days
- After 2.5 weeks, the app checks if any uploaded videos hit 100k views and can re-upload older videos
- Downloaded clips are kept in `data/downloaded/` — delete to free space; they'll be re-downloaded if needed
- Your credentials are in `.env` — never share this file or commit it to git
