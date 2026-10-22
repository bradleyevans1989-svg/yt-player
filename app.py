"""Personal ad-free YouTube song player.

Searches YouTube and streams audio only, via yt-dlp, so no ads are played.
For personal use: skipping ads goes against YouTube's terms of service.
"""
import os
import time
import threading

import requests
import yt_dlp
from flask import Flask, Response, abort, jsonify, request, send_from_directory

app = Flask(__name__, static_folder="static")

YDL_OPTS = {"quiet": True, "no_warnings": True, "skip_download": True}
CHUNK = 64 * 1024
STREAM_TTL = 60 * 60  # stream URLs from YouTube expire after a few hours

_stream_cache = {}
_cache_lock = threading.Lock()

CHART_URL = "https://rss.applemarketingtools.com/api/v2/au/music/most-played/50/songs.json"
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
    global last_seen
    last_seen = time.time()
    return "", 204


def top_in_australia():
    """Top songs in Australia (Apple Music's most-played chart) and their artists."""
    if _chart_cache["data"] and _chart_cache["expires"] > time.time():
        return _chart_cache["data"]
    try:
        feed = requests.get(CHART_URL, timeout=10).json()["feed"]["results"]
        songs = [{
            "title": item["name"],
            "artist": item["artistName"],
            "artwork": item.get("artworkUrl100", "").replace("100x100", "300x300"),
        } for item in feed]
        artists = list(dict.fromkeys(song["artist"] for song in songs))
        data = {"songs": songs, "artists": artists}
        _chart_cache.update(data=data, expires=time.time() + CHART_TTL)
        return data
    except Exception:
        return {"songs": [], "artists": FALLBACK_ARTISTS}


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
