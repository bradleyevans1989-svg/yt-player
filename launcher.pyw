"""Start Bonesy's Music Player without a console window: update, run the server, open the player.

Used by the desktop shortcut that install.ps1 creates. The player opens in its own
borderless window (pywebview, the same engine as Edge) and everything stops when
it's closed. If that can't start, it falls back to an Edge or Chrome app window,
and the server shuts itself down a few minutes after that window is closed.
"""
import io
import json
import os
import shutil
import signal
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
PID_FILE = os.path.join(HERE, ".server.pid")
IDLE_EXIT = 5 * 60
CLOSE_GRACE = 15  # seconds after the window closes, in case it was only reloading
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


def latest_sha():
    """The newest commit on main, or None if GitHub can't be reached."""
    try:
        req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/commits/main",
                                     headers={"Accept": "application/vnd.github.sha"})
        return urllib.request.urlopen(req, timeout=5).read().decode().strip() or None
    except Exception:
        return None


def update(state, latest):
    """Pull the latest code from GitHub, and refresh yt-dlp once a day."""
    try:
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


def stop_running_server():
    """Stop the copy that's already running so a newer version can start. True once the port is free."""
    try:
        urllib.request.urlopen(urllib.request.Request(f"{URL}/api/quit", method="POST"), timeout=3)
    except Exception:
        pass
    for _ in range(10):
        if not already_running():
            return True
        time.sleep(0.3)
    # Older servers have no /api/quit: end the process the launcher recorded.
    try:
        with open(PID_FILE) as f:
            os.kill(int(f.read().strip()), signal.SIGTERM)
    except (OSError, ValueError):
        pass
    for _ in range(10):
        if not already_running():
            return True
        time.sleep(0.3)
    return False


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
            subprocess.Popen([exe, f"--app={URL}", "--window-size=1400,900"])
            return
    import webbrowser
    webbrowser.open(URL)


def run_window(app_module):
    """Show the player in a borderless window and block until it closes. False if that isn't possible."""
    if sys.platform != "win32":
        return False
    # The first time, the Edge window opens instead so it can hand its saved queue and
    # favourites to the server; after that the borderless window restores them from there.
    if not os.path.exists(app_module.STATE_FILE):
        return False
    try:
        import webview
        import window_ctl
    except Exception as exc:
        print(f"borderless window unavailable: {exc!r}", flush=True)
        return False
    window_ctl.borderless = True
    win = webview.create_window(window_ctl.APP_TITLE, URL, width=1400, height=900, frameless=True,
                                easy_drag=False, min_size=(100, 50), background_color="#0d0a22")

    def shown(*_):
        try:
            window_ctl.fixed_hwnd = int(win.native.Handle.ToInt64())
        except Exception as exc:
            print(f"window handle: {exc!r}", flush=True)

    win.events.shown += shown
    try:
        webview.start(private_mode=False, storage_path=os.path.join(HERE, ".webview"))
    except Exception as exc:
        print(f"borderless window failed: {exc!r}", flush=True)
        if window_ctl.fixed_hwnd is None:  # it never opened: fall back to Edge
            window_ctl.borderless = False
            return False
    return True


def focus_running():
    """Bring the running player's window to the front. False if there's no window to focus."""
    try:
        req = urllib.request.Request(f"{URL}/api/window", data=json.dumps({"mode": "focus"}).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        return urllib.request.urlopen(req, timeout=3).status == 204
    except Exception:
        return False


def wait_for_server(timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        if already_running():
            return True
        time.sleep(0.1)
    return False


def watch_idle(app_module):
    """Exit soon after the window closes, or once the heartbeat has stopped for a while."""
    while True:
        time.sleep(5)
        now = time.time()
        closed = app_module.closed_at
        if (closed and now - closed > CLOSE_GRACE) or now - app_module.last_seen > IDLE_EXIT:
            os._exit(0)


def main():
    if sys.stdout is None:  # pythonw has no console; keep a log for troubleshooting
        sys.stdout = sys.stderr = open(os.path.join(HERE, "tunes.log"), "a", buffering=1)
    state = load_state()
    latest = latest_sha()
    if already_running():
        updated = latest and latest != state.get("sha")
        if not updated and focus_running():
            return
        # Reopened while an older version, or a server with no window, is still winding down:
        # restart it so the newest version opens in a fresh window.
        if not stop_running_server():
            open_window()
            return
    update(state, latest)
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))
    sys.path.insert(0, HERE)
    import app as app_module

    app_module.last_seen = time.time()
    threading.Thread(target=lambda: app_module.app.run(host="127.0.0.1", port=PORT, threaded=True),
                     daemon=True).start()
    wait_for_server()
    if run_window(app_module):
        os._exit(0)
    open_window()
    watch_idle(app_module)


if __name__ == "__main__":
    main()
