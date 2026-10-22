# Bonesy's Song Player: ad-free YouTube songs

A small personal web app. You search YouTube, build a queue, and it plays the audio stream
directly with [yt-dlp](https://github.com/yt-dlp/yt-dlp), so no ads are played and no video is downloaded.
Lock-screen and media-key controls work on phones and laptops.

> For personal use only. Skipping ads goes against YouTube's terms of service.

## Run it

**Windows, easiest:** open PowerShell and paste:

```powershell
irm https://raw.githubusercontent.com/bradleyevans1989-svg/yt-player/main/install.ps1 | iex
```

It installs Python if needed, puts it in `%LOCALAPPDATA%\BonesySongPlayer`, and adds a **Bonesy's Song Player** icon to your desktop
and Start menu. The icon opens the player in its own window with no console. Each launch pulls the latest
version from GitHub and updates yt-dlp once a day. The server stops a few minutes after you close the window.
If something goes wrong, check `%LOCALAPPDATA%\BonesySongPlayer\tunes.log`.

You can also double-click `run.bat` in a downloaded copy.

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
- `GET /api/charts` returns Australia's top 50 songs (Apple Music's most-played chart) and their artists for the home screen.
- `GET /api/similar/<id>` returns songs from YouTube's mix for a video; the player uses it to keep going when the queue runs out.
- `static/index.html` is the whole UI: home screen, search, queue (saved in your browser), autoplay, next/previous, shuffle.
