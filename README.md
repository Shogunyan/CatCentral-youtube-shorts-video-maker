# 🐱 CatCentral — YouTube Shorts Maker

Automatically finds viral cat videos, compiles them into ranked "Top 5" videos with AI voiceover, and uploads them to your YouTube Shorts channel — 3 times a day, hands-free.

---

## What it does

Every time it runs, the app:

1. **Scrapes** viral cat videos from YouTube Shorts, TikTok, and Instagram (looking for high view counts)
2. **Downloads** the 5 best clips it finds
3. **Generates AI voiceover** using a free Reddit-narrator-style voice (Microsoft Edge TTS) — lines like *"These are the 5 funniest cat videos on the internet — ranked"* and *"Coming in at number five…"*
4. **Edits the video** — resizes all clips to vertical (9:16 for Shorts), adds big rank number overlays (#5 → #1), title text at the top, and a moving `@CatCentral` watermark that rotates between corners
5. **Uploads to your channel** with a randomly generated title, description, and hashtags

You set it up once and it runs 3 times a day automatically (default: 9am, 2pm, 7pm).

---

## Requirements — what you need before starting

- A computer running **Windows, Mac, or Linux**
- **Python 3.10 or newer** installed
- **ffmpeg** installed (free video tool the app uses behind the scenes — Step 2 below)
- A **YouTube channel** (your CatCentral channel)
- A **Google account** that owns the channel (for uploading — Step 5 below)
- An **internet connection** — required every time the app runs (for scraping, downloading clips, generating the AI voiceover, and uploading to YouTube)

> **Note on Instagram:** Adding your Instagram login is optional and gives the app one more source for finding cat videos. However, Instagram actively limits automated access — if you use it heavily, Instagram may temporarily lock your account. It's safer to use a secondary Instagram account rather than your main one.

---

## Step 1 — Install Python

> Skip this step if you already have Python 3.10+ installed.  
> To check: open a terminal and type `python --version` or `python3 --version`

**Windows:**
1. Go to [python.org/downloads](https://www.python.org/downloads/)
2. Click the big yellow **Download Python** button
3. Run the installer — **check the box that says "Add Python to PATH"** before clicking Install
4. Click Install Now

**Mac:**
1. Open Terminal (press `Cmd + Space`, type `Terminal`, hit Enter)
2. Install Homebrew first if you don't have it — paste this and press Enter:
   ```
   /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
   ```
3. Then install Python:
   ```
   brew install python
   ```

**Linux (Ubuntu/Debian):**
```bash
sudo apt update && sudo apt install python3 python3-pip python3-venv
```

---

## Step 2 — Install ffmpeg

ffmpeg is a free tool that handles all the video processing. The app can't edit videos without it.

**Windows:**
1. Go to [ffmpeg.org/download.html](https://ffmpeg.org/download.html)
2. Under "Get packages & executable files", click **Windows**
3. Click the **gyan.dev** link → download the **ffmpeg-release-essentials.zip**
4. Extract the zip somewhere permanent like `C:\ffmpeg\`
5. Add ffmpeg to your PATH:
   - Press `Win + S`, search **"Edit the system environment variables"**, click it
   - Click **Environment Variables** at the bottom
   - Under "System variables", find **Path**, click it, click **Edit**
   - Click **New** and paste `C:\ffmpeg\bin` (or wherever you extracted it)
   - Click OK on all windows
6. Open a new terminal and test: `ffmpeg -version` — you should see version info

**Mac:**
```bash
brew install ffmpeg
```
(This takes a few minutes, just let it run)

**Linux:**
```bash
sudo apt install ffmpeg
```

---

## Step 3 — Download the app

Open a terminal and run:

```bash
git clone https://github.com/shogunyan/catcentral-youtube-shorts-video-maker
cd catcentral-youtube-shorts-video-maker
git checkout claude/cat-video-ranking-system-bht7M
```

> **Don't have git?**
> - Windows: download from [git-scm.com](https://git-scm.com/download/win)
> - Mac: `brew install git`
> - Linux: `sudo apt install git`

---

## Step 4 — Install the app's dependencies

Still in the terminal inside the `catcentral-youtube-shorts-video-maker` folder:

```bash
# Create a virtual environment (keeps everything tidy)
python -m venv venv

# Activate it
source venv/bin/activate       # Mac / Linux
venv\Scripts\activate          # Windows

# Install everything the app needs
pip install -r requirements.txt
```

This downloads about 15 packages including the video scraper, TTS voice, and YouTube uploader. Takes 1–3 minutes.

---

## Step 5 — Set up Google credentials (one-time, ~5 minutes)

YouTube requires proper API access to upload videos automatically. This is a one-time setup.

### 5a. Create a Google Cloud project

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. At the very top of the page, click the **project dropdown** (next to "Google Cloud")
3. Click **New Project**
4. Name it anything — e.g. `CatCentral` — then click **Create**
5. Wait a few seconds for it to create, then make sure it's selected in the dropdown

### 5b. Enable the YouTube API

1. In the left sidebar, click **APIs & Services** → **Library**
2. In the search box, type `YouTube Data API v3`
3. Click on it, then click the blue **Enable** button
4. Wait for it to enable (takes about 10 seconds)

### 5c. Create OAuth credentials

1. In the left sidebar, click **APIs & Services** → **Credentials**
2. Click **+ Create Credentials** at the top → choose **OAuth client ID**
3. If it asks you to configure a consent screen first, click **Configure Consent Screen**:
   - Choose **External** → click Create
   - Fill in **App name** (e.g. `CatCentral`) and your **email** for both support and developer email fields
   - Click **Save and Continue** through all the screens (defaults are fine)
   - On the last screen click **Back to Dashboard**
   - Then go back to **Credentials** → **+ Create Credentials** → **OAuth client ID**
4. For **Application type**, choose **Desktop app**
5. Name it anything (e.g. `CatCentral Desktop`) → click **Create**
6. A popup appears with your **Client ID** and **Client Secret** — copy both, you'll need them in the next step

---

## Step 6 — Run the setup wizard

Now launch the app:

```bash
python main.py
```

The app opens in your terminal with a nice UI. Since it's your first time, it shows the **Setup Screen**:

```
🐱  CatCentral — Setup
─────────────────────────────────────────────────────
Google Client ID
[ paste your client ID here                        ]

Google Client Secret
[ paste your client secret here (hidden)           ]

Upload Schedule  (24h times, comma-separated)
[ 09:00,14:00,19:00                                ]

Instagram Username  (optional)
[ leave blank to skip                              ]

[ ▶  Authorize YouTube & Save ]
```

1. **Paste your Client ID** into the first box (click it, then paste with `Ctrl+V` / `Cmd+V`)
2. **Paste your Client Secret** into the second box
3. Leave the upload schedule as-is, or change it to your preferred times
4. Instagram is optional — if you add credentials it gives the app more video sources
5. Click **▶ Authorize YouTube & Save**

A browser window opens automatically. Log in with the **Google account that owns your YouTube channel**, then click **Allow** when it asks for permission.

> ⚠️ You might see a warning saying *"This app isn't verified"*. This is normal for personal projects. Click **Advanced** at the bottom left, then **Go to CatCentral (unsafe)** to continue. It's your own app — it's safe.

Once you click Allow, the browser will show a success message and the app switches to the main dashboard. **You're done with setup — this never needs to be repeated** unless you delete the saved token.

---

## Using the app

After setup, running `python main.py` goes straight to the dashboard:

```
🐱  CatCentral — YouTube Shorts Maker
═══════════════════════════════════════════════════
┌─────────────────────────────────────────────────┐
│  ◉  Idle — press R or click Run Now to start   │
└─────────────────────────────────────────────────┘
  ░░░░░░░░░░░░░░░░░░░░░░░░░░░░  0%

◈  Live Log
┌─────────────────────────────────────────────────┐
│ [09:00:00]  CatCentral ready.                   │
└─────────────────────────────────────────────────┘

[ ▶ Run Now ]  [ 📅 Start Scheduler ]  [ ⚙ Settings ]
  Next upload: 14:00
```

### To make and upload a video right now
Press **R** on your keyboard, or click **▶ Run Now**.

The action label updates live as it works through each stage:
- 🔍 Scraping viral cat videos…
- ⬇ Downloading clips (1/5)…
- 🎙 Generating AI voiceover…
- 🎬 Building ranking video…
- 📤 Uploading to YouTube…
- ✅ Done! Video is live on YouTube.

The log box below shows exactly what's happening at every step.

### To run automatically 3 times a day
Click **📅 Start Scheduler**. The app keeps running and automatically makes + uploads a video at each of your scheduled times (default: 9am, 2pm, 7pm). Leave the window open and it handles everything.

### Keyboard shortcuts

| Key | Action |
|-----|--------|
| `R` | Run pipeline once now |
| `S` | Start/stop the daily scheduler |
| `T` | Open settings (change credentials, schedule, etc.) |
| `Q` | Quit |

### Headless / no UI mode (advanced)

If you want to run it without the graphical interface — for example on a server:

```bash
python main.py run        # one video, right now
python main.py schedule   # starts 3x/day scheduler (no UI, logs to file)
python main.py test       # full dry run — no upload, good for testing
```

---

## How to keep it running 24/7 without leaving a window open

If you want uploads to happen automatically even when you close your terminal:

**Mac / Linux** — run it in the background:
```bash
nohup python main.py schedule > logs/scheduler.log 2>&1 &
```
To stop it later: `pkill -f "main.py schedule"`

**Windows** — run it as a background task:
```bash
start /B pythonw main.py schedule
```

---

## Timing — how long does each step take?

These are rough estimates. Speed depends on your internet connection and CPU.

| Step | Time |
|------|------|
| **Scraping** (searching for videos) | 10–30 seconds |
| **Downloading 5 clips** | 30 seconds – 2 minutes |
| **Generating AI voiceover** | 10–20 seconds |
| **Video editing** (resize, overlay, render) | 1–3 minutes |
| **Uploading to YouTube** | 30 seconds – 2 minutes |
| **Total per video** | **~3 to 8 minutes** |

The video editing step is the slowest because it's doing a lot of CPU work — re-encoding 5 clips, adding overlays, mixing audio, and rendering the final MP4. A faster computer (or one with a newer CPU) will be noticeably quicker here.

---

## What the finished video looks like

Each video is a vertical 9:16 Short, roughly 50–55 seconds long:

- **Title card (2–4 sec):** Black screen with the ranking title and a narrator voiceover — *"These are the 5 funniest cat videos on the internet — ranked."*
- **Clip #5 (10 sec):** Viral cat clip with a large `#5` overlay for the first 2.5 seconds, title text at the top, and the voice saying *"Kicking things off at number five…"*
- **Clips #4 through #2** — same structure, counting down
- **Clip #1 (10 sec):** The "best" clip with the voice saying *"And the number one funniest cat video is…"*
- **Throughout the whole video:** A small `@CatCentral` watermark that quietly rotates between the four corners every 12 seconds

Each run randomises the script lines so the videos don't all sound identical.

---

## Changing the AI voice

The default voice (`en-US-GuyNeural`) is the male narrator style you hear on Reddit reading channels. To switch voices, add this to your `.env` file:

```
TTS_VOICE=en-US-EricNeural         # slightly different male voice
TTS_VOICE=en-GB-RyanNeural         # British male narrator
TTS_VOICE=en-US-JennyNeural        # female voice
TTS_VOICE=en-US-AriaNeural         # another female option
```

To disable voiceover entirely:
```
TTS_ENABLED=false
```

---

## Changing the upload schedule

Open your `.env` file (in the app folder) and edit this line:

```
UPLOAD_TIMES=09:00,14:00,19:00
```

Times are in 24-hour format. You can have as many or as few as you want — just separate them with commas. Examples:

```
UPLOAD_TIMES=08:00,20:00           # twice a day
UPLOAD_TIMES=10:00,14:00,18:00,22:00   # four times a day
```

---

## Troubleshooting

**"ffmpeg is not installed" error**
→ Follow Step 2 above. On Windows make sure you added ffmpeg to your PATH and opened a fresh terminal after doing so.

**"No candidates found" during scraping**
→ The scraper couldn't reach the platforms. Check your internet connection and try again. TikTok occasionally blocks scrapers — YouTube Shorts is the most reliable fallback.

**Browser doesn't open during YouTube authorization**
→ Copy the URL printed in the terminal and paste it into your browser manually.

**"This app isn't verified" warning from Google**
→ This is expected. Click **Advanced** → **Go to CatCentral (unsafe)**. It's your own app registered under your own Google account — it's completely safe.

**Upload fails with a 403 error**
→ Your OAuth token may have been revoked. Run `python main.py auth` to re-authorize.

**Video is silent (no voiceover)**
→ Make sure `edge-tts` installed correctly: `pip install edge-tts`. Also check that `TTS_ENABLED=true` in your `.env` file.

---

## File structure

```
catcentral-youtube-shorts-video-maker/
├── main.py              ← run this to start the app
├── tui.py               ← the graphical terminal UI
├── config.py            ← reads settings from .env
├── setup_wizard.py      ← handles first-time credential setup
├── .env                 ← YOUR credentials and settings (auto-created)
├── .env.example         ← template showing all available settings
├── requirements.txt     ← Python package list
├── src/
│   ├── scraper.py       ← finds viral cat videos on YouTube/TikTok/Instagram
│   ├── downloader.py    ← downloads the video files
│   ├── video_editor.py  ← compiles and renders the ranking video
│   ├── tts.py           ← generates the AI voiceover
│   ├── caption_gen.py   ← generates titles, descriptions, hashtags
│   ├── uploader.py      ← uploads to YouTube via the API
│   └── scheduler.py     ← runs the full pipeline on a schedule
├── data/
│   ├── downloaded/      ← raw downloaded clips (auto-managed)
│   ├── processed/       ← finished ranking videos before upload
│   └── used_videos.json ← tracks which clips have already been used
└── logs/
    └── app.log          ← detailed log of everything the app does
```

---

## Notes

- The app tracks every video it has already used (`data/used_videos.json`) so it never repeats the same clip twice
- Downloaded clips are kept in `data/downloaded/` — you can delete these to free up disk space at any time; they'll be re-downloaded if needed
- Finished videos are saved in `data/processed/` — feel free to delete old ones after they've been uploaded
- Your credentials are stored in `.env` — never share this file or commit it to git (it's in `.gitignore` by default)
