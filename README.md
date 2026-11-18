# GTA VI Clip Bot

Raw gameplay goes in. 9:16 Shorts come out, with titles, uploaded to YouTube as **private** drafts and sent to Telegram one by one. Everything runs on GitHub's free runners, so your PC can be off.

```
footage ──► GitHub Actions ──► detect highlights (audio spikes)
                              ├► cut 1080×1920 MP4 (ffmpeg)
                              ├► caption (GitHub Models, free)
                              ├► upload to YouTube as private
                              ├► send each clip to Telegram
                              └► commit manifests/<run>.json ──► dashboard
```

## What's in the repo

| Path | What it does |
|---|---|
| `.github/workflows/clip-pipeline.yml` | The workflow. Starts on a push to `inbox/`, a published Release, or a manual/dashboard run |
| `clipper/` | The Python pipeline: `detect.py`, `cut.py`, `captions.py`, `youtube.py`, `telegram.py`, orchestrated by `run.py` |
| `tools/get_youtube_refresh_token.py` | Run once on your PC to get the YouTube token for Actions |
| `docs/index.html` | The dashboard. Host it with GitHub Pages |
| `manifests/` | One JSON file per run (titles, YouTube ids, thumbnails); the dashboard reads these |
| `inbox/` | Drop videos under 100 MB here |

## One-time setup (about 20 minutes)

### 1. Create the repo

Create a new GitHub repo and push this folder to `main`.

**Public or private?** A **public** repo gets unlimited free Actions minutes and free GitHub Pages. Your secrets stay secret either way. The footage you push and the manifests (titles, private video ids) become visible, which matters little for clips you're about to post publicly. A **private** repo gets 2,000 minutes a month (a typical run takes 3–6 minutes, so roughly 300+ runs), but GitHub Pages on a private repo needs a paid plan. In that case put `docs/index.html` in a second, public repo just for the dashboard.

### 2. YouTube (Google Cloud, free, no card needed)

1. Go to [console.cloud.google.com](https://console.cloud.google.com), create a project, and enable **YouTube Data API v3**.
2. Under **OAuth consent screen**, choose External, add yourself as a test user, then click **Publish app** so the status is **In production**. If you leave it in Testing, Google expires the refresh token after 7 days and uploads stop.
3. Under **Credentials**, create an OAuth client ID of type **Desktop app**. Then, on your PC:
   ```
   python tools/get_youtube_refresh_token.py <client id> <client secret>
   ```
   Approve in the browser. The "unverified app" warning is expected because it's your own app (choose Advanced → Continue). Save the three printed values as repository secrets.
4. For the dashboard, create a second OAuth client ID, this time of type **Web application**. Add `https://<you>.github.io` as an Authorized JavaScript origin and `https://<you>.github.io/<repo>/` as an Authorized redirect URI. The dashboard shows the exact values in its YouTube bar.

> **Important: the API audit.** YouTube locks videos uploaded through an **unaudited** API project as private, and you can't make them public until the project passes YouTube's free compliance audit. Apply through the [YouTube API Services audit form](https://support.google.com/youtube/contact/yt_api_form) as soon as you create the project; approval takes a while. Until then, Telegram still gets every clip as a playable MP4, so you can post the good ones from your phone by hand.

> **Quota.** The default quota is 10,000 units a day and each upload costs about 1,600, so about **6 uploads a day**. The workflow stops at `max_uploads` (default 6). After that, or if the quota runs out, clips still go to Telegram, marked "not uploaded". The audit form is also where you ask for more quota.

### 3. Telegram

1. Message [@BotFather](https://t.me/BotFather), run `/newbot`, and copy the token.
2. Send your new bot any message (bots can't message you first).
3. Get your chat ID from [@userinfobot](https://t.me/userinfobot).

### 4. Repository secrets and variables

**Settings → Secrets and variables → Actions → Secrets**

| Secret | Value |
|---|---|
| `YT_CLIENT_ID`, `YT_CLIENT_SECRET`, `YT_REFRESH_TOKEN` | From step 2 |
| `TG_BOT_TOKEN`, `TG_CHAT_ID` | From step 3 |
| `LLM_API_KEY` | Optional, see Captions below |

| `YTDLP_COOKIES` | Optional: a cookies.txt export, so stream links download from GitHub's servers |

**Variables** (all optional):

| Variable | Default | What it does |
|---|---|---|
| `PUBLISH_MODE` | `schedule` | `schedule` = YouTube posts each video at its own time slot, `now` = public at once, `private` = drafts you post yourself |
| `SHORTS_PER_DAY` | `6` | Shorts slots per day |
| `POST_START_HOUR` / `POST_END_HOUR` | `10` / `22` | Posting window; Shorts are spread evenly across it |
| `LONG_POST_HOUR` | `19` | When the daily best-of goes out |
| `POST_TZ` | `Africa/Lagos` | Time zone for the hours above |
| `CREDIT_LINE` | none | Added to every description, e.g. `Streamed live by @yourfriend` |
| `CLIPS_PER_VIDEO` | `5` | Shorts per run (dashboard runs can ask for up to 20) |
| `LONG_MINUTES` | `0` | Length of the best-of video (0 = off) |
| `CLIP_SECONDS`, `LAYOUT`, `MAX_UPLOADS`, `CAPTION_MODEL`, `KEEP_INBOX` | 30, crop, 6, gpt-4o-mini, false | Clip length, `crop`/`blur`, upload cap per run, caption model, keep processed inbox files |

Taken slots are recorded in `manifests/schedule.json`, so one run never books a time another run already took. When a day is full, posts roll to the next day.

These apply to runs started by a push or a Release. Runs started from the dashboard use the dashboard's settings.

### 5. Dashboard on GitHub Pages

**Settings → Pages →** Deploy from branch, `main`, folder `/docs`. The dashboard is at `https://<you>.github.io/<repo>/`.

Open it, click **☁ GitHub**, and enter `owner/repo` plus a [fine-grained token](https://github.com/settings/personal-access-tokens/new) scoped to this repo only, with **Actions: read and write** and **Contents: read and write**. Tokens and keys stay in your browser (session only, unless you tick "Remember keys on this device").

## Chat with your bot (Telegram)

Text your Telegram bot and it starts runs for you. It checks for messages every 5 minutes, so replies can take up to about 10 minutes when GitHub is busy. It only obeys your own chat (`TG_CHAT_ID`).

| You send | It does |
|---|---|
| `https://youtube.com/watch?v=...` | Clips it with your default settings |
| `clip <link> 20 shorts 30 min` | 20 Shorts plus a 30-minute best-of |
| `clip <link> 10 shorts no long post now` | 10 Shorts, no long video, public right away |
| `clip <link> private` | Uploads as drafts for you to post |
| `clip <link> blur` | Keeps the whole frame (HUD/minimap) over a blurred fill |
| `status` | Your latest runs |
| `schedule` | What's going public next |
| `help` | The list above |

Bots can only download files up to 20 MB from Telegram, so send links, not video files.

## Daily use

**Getting footage in.** Use whichever fits the file:

- **Over 100 MB (most recordings):** create a Release on GitHub (Releases → Draft new release), drag the video in (up to 2 GB per file), and publish. The run starts by itself. This works from a phone too.
- **Under 75 MB:** load it in the dashboard and press **Send loaded video**.
- **Under 100 MB, from a computer:** commit it to `inbox/` and push.
- **Already in Google Drive or Dropbox:** paste the share link in the dashboard and press **Run on GitHub**. A Drive link must be shared as "Anyone with the link".

**Stream links.** Paste a finished YouTube or Twitch stream link (after the stream ends, not while it's live). Pick 20 Shorts and a 30-minute best-of. The best-of is the strongest one-minute moments in order, in 16:9 with fades and chapters, plus a thumbnail made from the loudest frame with a big text hook. Get the streamer's permission first, and set `CREDIT_LINE` to credit them.

**Overnight.** The run detects highlights, cuts them, writes titles, descriptions, hashtags and search tags, and uploads each video with its publish time. YouTube then posts them through the day on its own. Every clip also arrives in Telegram as a playable video showing when it goes public. A summary message follows at the end.

**Morning.** Watch the clips in Telegram. Then do one of two things:

- Open YouTube Studio and publish the ones you like, or
- Open the dashboard, where last night's clips appear under **Cloud**. Press ✓ on the good ones. Each one gets the next free slot (your clips-per-day spread across the posting window). In **Auto-publish** mode the dashboard sets YouTube's own publish time, and YouTube makes the video public at that slot even with every device off. In **Remind me** mode the video stays private and Telegram pings you at the slot while a dashboard tab is open.

## Captions without an Anthropic key

On Actions, captions come from **GitHub Models** using the workflow's own `GITHUB_TOKEN`: free, with nothing to sign up for. The model sees three stills from the clip, so titles describe what's on screen. The default model is `openai/gpt-4o-mini`; set the `CAPTION_MODEL` variable to change it. The free tier allows roughly 150 requests a day, plenty for this.

To use another free provider instead, set `LLM_BASE_URL`, `LLM_MODEL` (variables) and `LLM_API_KEY` (secret) for any OpenAI-compatible API, for example Groq (`https://api.groq.com/openai/v1`) or Google Gemini (`https://generativelanguage.googleapis.com/v1beta/openai`). If every provider fails, built-in title templates are used and the run carries on.

In the dashboard, captions come from Claude only when the page is opened as the claude.ai artifact (see below); otherwise it uses templates.

## The dashboard inside claude.ai

claude.ai artifacts can't make network requests to other sites, so the artifact copy can't reach GitHub, Telegram, Google or the ffmpeg download. It shows a banner when that happens. Its Claude captions and the MediaRecorder fallback cutter still work. Use the GitHub Pages copy for everything else.

## Cutting in the browser

The dashboard's local pipeline uses ffmpeg.wasm (single-threaded `@ffmpeg/core` 0.12, no SharedArrayBuffer needed). It downloads about 31 MB once, which the browser then caches. Compared with the old MediaRecorder approach:

- Clips are **MP4/H.264 with audio**. MediaRecorder on a canvas recorded silent WebM.
- It keeps working when the tab is in the background. MediaRecorder relied on `requestAnimationFrame`, which pauses there.
- Highlight detection reads the audio straight from the file in slices, so there's **no 200 MB limit** any more.
- Speed: it is **slower** than real time. In testing, a 20 s clip at 720p took about 70 s (single-threaded WebAssembly, headless browser); 1080p is slower again. Treat in-browser cutting as a fallback for a clip or two. Batches belong on GitHub Actions, where native ffmpeg cuts a 30 s clip in about 10–20 s.

If ffmpeg.wasm can't load, the old MediaRecorder cutter is used automatically.

## Run it locally

```
pip install -r requirements.txt
echo path/to/video.mp4 > inputs.txt
python -m clipper.run --inputs inputs.txt --clips 5 --upload false
```

Clips land in `work/clips/` and the manifest in `manifests/`. Set the same environment variables as the secrets to test uploads and Telegram. If ffmpeg isn't on your PATH, point `FFMPEG` at the binary.
