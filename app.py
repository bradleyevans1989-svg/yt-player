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


@app.get("/api/search")
def api_search():
    query = (request.args.get("q") or "").strip()
    if not query:
        return jsonify([])
    try:
        return jsonify(search_videos(query))
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
