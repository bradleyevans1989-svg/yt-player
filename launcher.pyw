"""Start Bonesy's Music Player without a console window: update, run the server, open the player.

Used by the desktop shortcut that install.ps1 creates. The server shuts itself
down a few minutes after the player window is closed.
"""
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import zipfile

REPO = "bradleyevans1989-svg/yt-player"
HERE = os.path.dirname(os.path.abspath(__file__))
PORT = 8080
URL = f"http://localhost:{PORT}"
STATE = os.path.join(HERE, ".state.json")
IDLE_EXIT = 5 * 60
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def load_state():
    try:
        with open(STATE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(state):
    with open(STATE, "w") as f:
        json.dump(state, f)


def pip_install(*args):
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", *args],
                   cwd=HERE, creationflags=NO_WINDOW, check=False)


def update(state):
    """Pull the latest code from GitHub, and refresh yt-dlp once a day."""
    try:
        req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/commits/main",
                                     headers={"Accept": "application/vnd.github.sha"})
        latest = urllib.request.urlopen(req, timeout=5).read().decode().strip()
        if latest and latest != state.get("sha"):
            data = urllib.request.urlopen(
                f"https://github.com/{REPO}/archive/refs/heads/main.zip", timeout=30).read()
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                for member in zf.infolist():
                    rel = member.filename.split("/", 1)[1] if "/" in member.filename else ""
                    if not rel or member.is_dir():
                        continue
                    dest = os.path.join(HERE, rel)
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    with zf.open(member) as src, open(dest, "wb") as out:
                        shutil.copyfileobj(src, out)
            pip_install("-r", "requirements.txt")
            state["sha"] = latest
    except Exception:
        pass  # offline or GitHub unreachable: run what we have

    if time.time() - state.get("ytdlp_checked", 0) > 24 * 60 * 60:
        pip_install("-U", "yt-dlp")
        state["ytdlp_checked"] = time.time()
    save_state(state)


def already_running():
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", PORT)) == 0


def open_window():
    """Open as a standalone app window in Edge or Chrome, else a normal tab."""
    candidates = [
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
    ]
    for exe in candidates:
        if os.path.exists(exe):
            subprocess.Popen([exe, f"--app={URL}", "--window-size=1000,800"])
            return
    import webbrowser
    webbrowser.open(URL)


def watch_idle(app_module):
    while True:
        time.sleep(30)
        if time.time() - app_module.last_seen > IDLE_EXIT:
            os._exit(0)


def main():
    if sys.stdout is None:  # pythonw has no console; keep a log for troubleshooting
        sys.stdout = sys.stderr = open(os.path.join(HERE, "tunes.log"), "a", buffering=1)
    if already_running():
        open_window()
        return
    update(load_state())
    sys.path.insert(0, HERE)
    import app as app_module

    app_module.last_seen = time.time()
    threading.Thread(target=watch_idle, args=(app_module,), daemon=True).start()
    threading.Timer(1.0, open_window).start()
    app_module.app.run(host="127.0.0.1", port=PORT, threaded=True)


if __name__ == "__main__":
    main()
