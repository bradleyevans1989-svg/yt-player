# Tunes: ad-free YouTube song player

A small personal web app. You search YouTube, build a queue, and it plays the audio stream
directly with [yt-dlp](https://github.com/yt-dlp/yt-dlp), so no ads are played and no video is downloaded.
Lock-screen and media-key controls work on phones and laptops.

> For personal use only. Skipping ads goes against YouTube's terms of service.

## Run it

**Windows, easiest:** install Python from python.org, then double-click `run.bat`. It installs what's needed and opens the player in your browser.

**Any OS:**

Requires Python 3.10+.

```bash
pip install -r requirements.txt
python app.py
```

Open http://localhost:8080 on the same machine. To use it from your phone, open
`http://<your-computer's-LAN-IP>:8080` while both are on the same Wi-Fi.
Set `PORT` or `HOST` env vars to change where it listens.

## If playback stops working

YouTube changes often, and yt-dlp ships fixes quickly. Update it first:

```bash
pip install -U yt-dlp
```

## How it works

- `GET /api/search?q=...` runs a yt-dlp search and returns titles, channels and thumbnails.
- `GET /api/stream/<id>` resolves the best audio-only format and proxies it to the browser
  (with seek/Range support). Stream URLs are cached for an hour.
- `static/index.html` is the whole UI: search, queue (saved in your browser), next/previous, shuffle.
