"""Personal ad-free YouTube song player.

Searches YouTube and streams audio only, via yt-dlp, so no ads are played.
For personal use: skipping ads goes against YouTube's terms of service.
"""
import json
import os
import re
import time
import threading

import requests
import yt_dlp
from flask import Flask, Response, abort, jsonify, redirect, request, send_from_directory

import window_ctl

app = Flask(__name__, static_folder="static")

YDL_OPTS = {"quiet": True, "no_warnings": True, "skip_download": True}
CHUNK = 64 * 1024
STREAM_TTL = 60 * 60  # stream URLs from YouTube expire after a few hours

_stream_cache = {}
_cache_lock = threading.Lock()

# Apple serves the chart in sizes up to 100; fall back to smaller sizes, then the older iTunes feed.
CHART_URLS = [
    "https://rss.applemarketingtools.com/api/v2/au/music/most-played/100/songs.json",
    "https://rss.applemarketingtools.com/api/v2/au/music/most-played/50/songs.json",
    "https://itunes.apple.com/au/rss/topsongs/limit=100/json",
]
# Some feeds turn away requests that don't look like a browser.
HTTP_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
CHART_TTL = 6 * 60 * 60
# Shown if the chart feed can't be reached.
FALLBACK_ARTISTS = [
    "The Rions", "Spacey Jane", "Ocean Alley", "Lime Cordiale", "Royel Otis", "G Flip",
    "Ruel", "Vance Joy", "Tame Impala", "Amyl and the Sniffers", "The Kid LAROI",
    "Troye Sivan", "Dope Lemon", "Budjerah", "Hockey Dad", "King Gizzard & the Lizard Wizard",
]
_chart_cache = {"data": None, "expires": 0}

# Updated by the page's heartbeat; the launcher exits once the window is closed.
last_seen = time.time()
# Set when the page says it's closing; a reload pings again straight after.
closed_at = None


def _thumb(video_id):
    return f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg"


def search_videos(query, limit=20):
    opts = {**YDL_OPTS, "extract_flat": "in_playlist"}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
    results = []
    for entry in info.get("entries") or []:
        # Skip channels and playlists; only 11-character ids are playable videos.
        if not entry or len(entry.get("id") or "") != 11:
            continue
        results.append({
            "id": entry["id"],
            "title": entry.get("title") or "Untitled",
            "channel": entry.get("channel") or entry.get("uploader") or "",
            "duration": entry.get("duration"),
            "thumbnail": _thumb(entry["id"]),
        })
    return results


def resolve_audio(video_id):
    """Return (url, headers, mime) for the best audio-only stream, cached."""
    now = time.time()
    with _cache_lock:
        hit = _stream_cache.get(video_id)
        if hit and hit["expires"] > now:
            return hit["url"], hit["headers"], hit["mime"]

    opts = {**YDL_OPTS, "format": "bestaudio[ext=m4a]/bestaudio/best"}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
    url = info["url"]
    headers = info.get("http_headers") or {}
    ext = info.get("ext") or "m4a"
    mime = {"m4a": "audio/mp4", "webm": "audio/webm", "mp3": "audio/mpeg"}.get(ext, "audio/mp4")

    with _cache_lock:
        _stream_cache[video_id] = {"url": url, "headers": headers, "mime": mime,
                                   "expires": now + STREAM_TTL}
    return url, headers, mime


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/favicon.ico")
def favicon():
    return send_from_directory(app.static_folder, "icon.ico")


@app.post("/api/ping")
def api_ping():
    global last_seen, closed_at
    last_seen = time.time()
    closed_at = None
    return "", 204


@app.post("/api/bye")
def api_bye():
    global closed_at
    closed_at = time.time()
    return "", 204


@app.get("/api/window")
def api_window_info():
    """Whether the server can manage the desktop window (frameless title bar, mini player)."""
    local = request.remote_addr in ("127.0.0.1", "::1")
    return jsonify({"frameless": local and window_ctl.frameless()})


@app.post("/api/window")
def api_window():
    """Window commands from the page: mini/full player, move, resize, minimise, maximise, close."""
    if request.remote_addr not in ("127.0.0.1", "::1"):
        abort(403)
    body = request.get_json(silent=True) or {}
    op = body.get("mode")
    if op not in ("mini", "full", "move", "resize", "clip", "minimize", "maximize", "close", "focus"):
        abort(400)
    try:
        done = window_ctl.command(op, page_frame=body.get("frame") or {},
                                  dx=float(body.get("dx") or 0), dy=float(body.get("dy") or 0),
                                  edge=str(body.get("edge") or ""))
    except Exception as exc:
        print(f"window: {op} failed: {exc!r}", flush=True)
        done = False
    return ("", 204) if done else ("", 501)


# The page's saved queue, favourites and settings, copied here so they survive a change of
# browser engine (the Edge window and the borderless window keep separate storage).
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".player_state.json")
STATE_MAX = 4 * 1024 * 1024


@app.get("/api/state")
def api_state_get():
    if request.remote_addr not in ("127.0.0.1", "::1"):
        abort(403)
    try:
        with open(STATE_FILE) as f:
            return jsonify(json.load(f))
    except (OSError, ValueError):
        return jsonify({})


@app.post("/api/state")
def api_state_put():
    if request.remote_addr not in ("127.0.0.1", "::1"):
        abort(403)
    if (request.content_length or 0) > STATE_MAX:
        abort(413)
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in data.items()):
        abort(400)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, STATE_FILE)
    return "", 204


@app.post("/api/quit")
def api_quit():
    """Lets the launcher stop an older copy of the server so it can update."""
    if request.remote_addr not in ("127.0.0.1", "::1"):
        abort(403)
    threading.Timer(0.3, os._exit, args=(0,)).start()
    return "", 204


def _parse_chart(payload):
    """Songs from either Apple's newer RSS feed or the older iTunes one."""
    if "feed" in payload and "results" in payload["feed"]:
        return [{
            "title": item["name"],
            "artist": item["artistName"],
            "artwork": item.get("artworkUrl100", "").replace("100x100", "300x300"),
        } for item in payload["feed"]["results"]]
    songs = []
    for entry in payload["feed"]["entry"]:
        images = entry.get("im:image") or [{}]
        art = images[-1].get("label", "")
        songs.append({
            "title": entry["im:name"]["label"],
            "artist": entry["im:artist"]["label"],
            "artwork": art.rsplit("/", 1)[0] + "/300x300bb.jpg" if art else "",
        })
    return songs


def top_in_australia():
    """Top songs in Australia (Apple Music's most-played chart) and their artists."""
    if _chart_cache["data"] and _chart_cache["expires"] > time.time():
        return _chart_cache["data"]
    for url in CHART_URLS:
        try:
            resp = requests.get(url, headers=HTTP_HEADERS, timeout=10)
            resp.raise_for_status()
            songs = _parse_chart(resp.json())
            if not songs:
                raise ValueError("empty chart")
        except Exception as exc:
            print(f"chart: {url} failed: {exc!r}", flush=True)
            continue
        artists = list(dict.fromkeys(song["artist"] for song in songs))
        data = {"songs": songs, "artists": artists}
        _chart_cache.update(data=data, expires=time.time() + CHART_TTL)
        return data
    return {"songs": [], "artists": FALLBACK_ARTISTS}


# Artist photos come from Deezer's public search, cached on disk so each artist is looked up once.
ARTIST_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".artist_images.json")
ARTIST_MISS_TTL = 24 * 60 * 60
_artist_lock = threading.Lock()
try:
    with open(ARTIST_FILE) as f:
        _artist_images = json.load(f)
except (OSError, ValueError):
    _artist_images = {}


def _main_artist(name):
    """'Ella Langley & Riley Green' -> 'Ella Langley', 'X feat. Y' -> 'X'."""
    name = re.sub(r"\s*-\s*Topic$|VEVO$|\s+Official$", "", name.strip(), flags=re.I)
    return re.split(r"\s+(?:&|feat\.?|ft\.?|featuring)\s+|,\s*", name, maxsplit=1, flags=re.I)[0].strip() or name


def artist_image(name):
    key = name.strip().lower()
    with _artist_lock:
        hit = _artist_images.get(key)
    if hit and (hit.get("url") or hit.get("checked", 0) + ARTIST_MISS_TTL > time.time()):
        return hit.get("url")
    url = None
    try:
        resp = requests.get("https://api.deezer.com/search/artist",
                            params={"q": _main_artist(name), "limit": 1},
                            headers=HTTP_HEADERS, timeout=8)
        found = (resp.json().get("data") or [None])[0]
        # Deezer's placeholder for artists without a photo has an empty hash in the path.
        if found and found.get("picture_big") and "/artist//" not in found["picture_big"]:
            url = found["picture_big"]
    except Exception as exc:
        print(f"artist image: {name!r} failed: {exc!r}", flush=True)
        return None  # don't remember network errors
    with _artist_lock:
        _artist_images[key] = {"url": url, "checked": time.time()}
        try:
            with open(ARTIST_FILE, "w") as f:
                json.dump(_artist_images, f)
        except OSError:
            pass
    return url


@app.get("/api/artist-image")
def api_artist_image():
    name = (request.args.get("name") or "").strip()
    url = artist_image(name) if name else None
    if not url:
        abort(404)
    resp = redirect(url, code=302)
    resp.headers["Cache-Control"] = "public, max-age=604800"
    return resp


def similar_songs(video_id, limit=10):
    """Songs from YouTube's auto-generated mix (radio) for a video."""
    opts = {**YDL_OPTS, "extract_flat": "in_playlist", "playlistend": limit + 1}
    url = f"https://www.youtube.com/watch?v={video_id}&list=RD{video_id}"
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    results = []
    for entry in info.get("entries") or []:
        if not entry or len(entry.get("id") or "") != 11 or entry["id"] == video_id:
            continue
        results.append({
            "id": entry["id"],
            "title": entry.get("title") or "Untitled",
            "channel": entry.get("channel") or entry.get("uploader") or "",
            "duration": entry.get("duration"),
            "thumbnail": _thumb(entry["id"]),
        })
    return results[:limit]


@app.get("/api/similar/<video_id>")
def api_similar(video_id):
    try:
        return jsonify(similar_songs(video_id))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 502


@app.get("/api/charts")
def api_charts():
    return jsonify(top_in_australia())


@app.get("/api/search")
def api_search():
    query = (request.args.get("q") or "").strip()
    limit = max(1, min(int(request.args.get("n") or 20), 30))
    if not query:
        return jsonify([])
    try:
        return jsonify(search_videos(query, limit))
    except Exception as exc:  # yt-dlp raises many error types
        return jsonify({"error": str(exc)}), 502


@app.get("/api/stream/<video_id>")
def api_stream(video_id):
    if not video_id.replace("-", "").replace("_", "").isalnum() or len(video_id) != 11:
        abort(400)
    try:
        url, headers, mime = resolve_audio(video_id)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 502

    upstream_headers = dict(headers)
    if "Range" in request.headers:
        upstream_headers["Range"] = request.headers["Range"]
    upstream = requests.get(url, headers=upstream_headers, stream=True, timeout=30)
    if upstream.status_code == 403:
        # URL expired or was rejected: drop it so the next request re-resolves.
        with _cache_lock:
            _stream_cache.pop(video_id, None)
        upstream.close()
        abort(502)

    passthrough = {"Accept-Ranges": "bytes", "Content-Type": mime}
    for name in ("Content-Length", "Content-Range"):
        if name in upstream.headers:
            passthrough[name] = upstream.headers[name]

    def generate():
        try:
            for chunk in upstream.iter_content(CHUNK):
                yield chunk
        finally:
            upstream.close()

    return Response(generate(), status=upstream.status_code, headers=passthrough)


if __name__ == "__main__":
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8080"))
    app.run(host=host, port=port, threaded=True)
