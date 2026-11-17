"""Shrink the player's app window to a small always-on-top bar, and put it back (Windows only).

The desktop launcher opens the player as an Edge or Chrome app window whose title
ends with the app's name. These helpers find that window and resize it with the
Win32 API through ctypes, so no extra packages are needed.

When the launcher hosts the player in its own borderless window (pywebview), it
sets borderless and the page draws its own drag strip, window buttons and resize
edges, asking the server to move, resize, minimise, maximise or close the window.
Otherwise the player is an Edge/Chrome app window, found by its title; that keeps
the browser's own title bar, and only the mini player resizing applies.
"""
import os
import sys

APP_TITLE = "Bonesy's Music Player"
MINI_SIZE = (490, 100)       # visible bar in CSS pixels at 100% scaling
FULL_SIZE = (1400, 900)      # used if the old size wasn't remembered
MIN_SIZE = (720, 480)        # smallest full player, in CSS pixels
MARGIN = 16

_saved = {}      # hwnd -> (rect, was_maximized) before the mini player
_maximized = {}  # hwnd -> rect before our own maximise (borderless window)
borderless = False  # set by the launcher when it hosts the player in its own borderless window
fixed_hwnd = None   # that window's handle, once it has been shown

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    HWND_TOPMOST, HWND_NOTOPMOST = -1, -2
    SWP_SHOWWINDOW, SW_RESTORE, SW_MAXIMIZE, SW_MINIMIZE = 0x0040, 9, 3, 6
    WM_CLOSE = 0x0010
    SWP_NOSIZE, SWP_NOZORDER, SWP_NOACTIVATE = 0x0001, 0x0004, 0x0010
    MONITOR_DEFAULTTONEAREST = 2
    DPI_AWARENESS_PER_MONITOR_V2 = ctypes.c_void_p(-4)
    CONTENT_CLASS = "Chrome_RenderWidgetHostHWND"  # the child window that holds the web page

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    H = wintypes.HWND
    for name, args in {
        "GetWindowTextLengthW": [H], "GetWindowTextW": [H, wintypes.LPWSTR, ctypes.c_int],
        "GetClassNameW": [H, wintypes.LPWSTR, ctypes.c_int], "IsWindowVisible": [H],
        "IsZoomed": [H], "IsIconic": [H], "ShowWindow": [H, ctypes.c_int], "GetDpiForWindow": [H],
        "PostMessageW": [H, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM],
        "IsWindow": [H], "SetForegroundWindow": [H],
        "GetWindowRect": [H, ctypes.POINTER(wintypes.RECT)],
        "MonitorFromWindow": [H, wintypes.DWORD],
        "SetWindowPos": [H, H, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT],
        "EnumChildWindows": [H, WNDENUMPROC, wintypes.LPARAM],
        "GetWindowThreadProcessId": [H, ctypes.POINTER(wintypes.DWORD)],
    }.items():
        getattr(user32, name).argtypes = args
    user32.MonitorFromWindow.restype = wintypes.HANDLE
    user32.GetForegroundWindow.restype = H


def supported():
    return sys.platform == "win32"


def _title(hwnd):
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def _class(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def frameless():
    return bool(supported() and borderless)


def _own_window():
    """Our borderless window: the handle the launcher gave us, else our process's window with the app's title."""
    global fixed_hwnd
    if fixed_hwnd and user32.IsWindow(fixed_hwnd):
        return fixed_hwnd
    found = []

    def check(hwnd, _):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == os.getpid() and user32.IsWindowVisible(hwnd) and _title(hwnd) == APP_TITLE:
            found.append(hwnd)
        return True

    user32.EnumWindows(WNDENUMPROC(check), 0)
    fixed_hwnd = found[0] if found else None
    return fixed_hwnd


def _find_window():
    """The player's window: our borderless one, else an Edge/Chrome app window by title."""
    if frameless():
        return _own_window()
    found = []

    def check(hwnd, _):
        if (user32.IsWindowVisible(hwnd) and _title(hwnd).endswith(APP_TITLE)
                and _class(hwnd).startswith("Chrome_WidgetWin")):
            found.append(hwnd)
        return True

    user32.EnumWindows(WNDENUMPROC(check), 0)
    front = user32.GetForegroundWindow()
    return front if front in found else (found[0] if found else None)


def _rect(hwnd):
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r


def _content_rect(hwnd):
    """Screen rectangle of the web page inside the window, or None if it can't be found."""
    best = []

    def check(child, _):
        if _class(child) == CONTENT_CLASS and user32.IsWindowVisible(child):
            r = _rect(child)
            area = (r.right - r.left) * (r.bottom - r.top)
            if area > 0 and (not best or area > best[0][0]):
                best[:] = [(area, r)]
        return True

    user32.EnumChildWindows(hwnd, WNDENUMPROC(check), 0)
    return best[0][1] if best else None


def _work_area(hwnd):
    info = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
    user32.GetMonitorInfoW(user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST), ctypes.byref(info))
    return info.rcWork


def _window_and_scale():
    """The player window and its display scale, working in real pixels."""
    try:
        user32.SetThreadDpiAwarenessContext(DPI_AWARENESS_PER_MONITOR_V2)
    except AttributeError:
        pass
    hwnd = _find_window()
    if not hwnd:
        return None, 1.0
    try:
        return hwnd, user32.GetDpiForWindow(hwnd) / 96
    except AttributeError:
        return hwnd, 1.0


def move_by(dx, dy):
    """Move the window by (dx, dy) CSS pixels: dragging the mini player, which has no title bar."""
    if not supported():
        return False
    hwnd, scale = _window_and_scale()
    if not hwnd:
        return False
    r = _rect(hwnd)
    user32.SetWindowPos(hwnd, None, r.left + round(dx * scale), r.top + round(dy * scale), 0, 0,
                        SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)
    return True


def _frame_offsets(hwnd, scale, page_frame):
    """Pixels between the window's edges and the web page: (left, top, right, bottom).

    The page reports its own size in real pixels (innerWidth/innerHeight x devicePixelRatio).
    The browser's hidden borders are the same width on the left, right and bottom, so the
    page sits centred horizontally on the bottom border, with the title bar above it.
    """
    outer = _rect(hwnd)
    w, h = outer.right - outer.left, outer.bottom - outer.top
    iw, ih = float(page_frame.get("iw") or 0), float(page_frame.get("ih") or 0)
    if 0 < iw <= w and 0 < ih <= h:
        side = max(0, round((w - iw) / 2))
        return side, max(0, h - side - round(ih)), side, side
    inner = _content_rect(hwnd)
    if inner:
        return (inner.left - outer.left, inner.top - outer.top,
                outer.right - inner.right, outer.bottom - inner.bottom)
    return 0, 0, 0, 0


def command(op, page_frame=None, dx=0.0, dy=0.0, edge=""):
    """Window commands from the page: move, resize, minimize, maximize, close, focus, mini, full."""
    if not supported():
        return False
    if op in ("mini", "full"):
        return set_mode(op, page_frame)
    if op == "move":
        return move_by(dx, dy)
    hwnd, scale = _window_and_scale()
    if not hwnd:
        return False
    if op == "clip":
        pass  # nothing to clip any more; kept so older pages don't get errors
    elif op == "focus":
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)
        user32.SetForegroundWindow(hwnd)
    elif op == "minimize":
        user32.ShowWindow(hwnd, SW_MINIMIZE)
    elif op == "maximize":  # toggles
        if frameless():
            # A borderless window maximised by Windows would cover the taskbar: fill the work area instead.
            saved = _maximized.pop(hwnd, None)
            if saved:
                x, y, w, h = saved
            else:
                r, work = _rect(hwnd), _work_area(hwnd)
                _maximized[hwnd] = (r.left, r.top, r.right - r.left, r.bottom - r.top)
                x, y, w, h = work.left, work.top, work.right - work.left, work.bottom - work.top
            user32.SetWindowPos(hwnd, None, x, y, w, h, SWP_NOZORDER)
        else:
            user32.ShowWindow(hwnd, SW_RESTORE if user32.IsZoomed(hwnd) else SW_MAXIMIZE)
    elif op == "close":
        user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
    elif op == "resize":
        if user32.IsZoomed(hwnd):
            return True
        r = _rect(hwnd)
        left, top, right, bottom = r.left, r.top, r.right, r.bottom
        ddx, ddy = round(dx * scale), round(dy * scale)
        min_w, min_h = round(MIN_SIZE[0] * scale), round(MIN_SIZE[1] * scale)
        if "l" in edge:
            left = min(left + ddx, right - min_w)
        if "r" in edge:
            right = max(right + ddx, left + min_w)
        if "t" in edge:
            top = min(top + ddy, bottom - min_h)
        if "b" in edge:
            bottom = max(bottom + ddy, top + min_h)
        user32.SetWindowPos(hwnd, None, left, top, right - left, bottom - top, SWP_NOZORDER | SWP_NOACTIVATE)
        _maximized.pop(hwnd, None)
    else:
        return False
    return True


def set_mode(mode, page_frame=None):
    """mode is 'mini' or 'full'. Returns True if the window was found and resized."""
    if not supported():
        return False
    hwnd, scale = _window_and_scale()
    if not hwnd:
        return False
    work = _work_area(hwnd)

    if mode == "mini":
        if hwnd not in _saved:
            was_max = bool(user32.IsZoomed(hwnd))
            if was_max:
                user32.ShowWindow(hwnd, SW_RESTORE)
            r = _rect(hwnd)
            _saved[hwnd] = ((r.left, r.top, r.right, r.bottom), was_max)
        left, top, right, bottom = _frame_offsets(hwnd, scale, page_frame or {})
        cw, ch = round(MINI_SIZE[0] * scale), round(MINI_SIZE[1] * scale)
        m = round(MARGIN * scale)
        w, h = left + cw + right, top + ch + bottom
        # Put the visible bar (not the hidden title bar) in the bottom-right corner.
        x, y = work.right - m - cw - left, work.bottom - m - ch - top
        user32.SetWindowPos(hwnd, HWND_TOPMOST, x, y, w, h, SWP_SHOWWINDOW)
        return True

    saved = _saved.pop(hwnd, None)
    if saved:
        (left, top, right, bottom), was_max = saved
        x, y, w, h = left, top, right - left, bottom - top
    else:
        was_max = False
        w, h = round(FULL_SIZE[0] * scale), round(FULL_SIZE[1] * scale)
        w, h = min(w, work.right - work.left), min(h, work.bottom - work.top)
        x = work.left + (work.right - work.left - w) // 2
        y = work.top + (work.bottom - work.top - h) // 2
    user32.SetWindowPos(hwnd, HWND_NOTOPMOST, x, y, w, h, SWP_SHOWWINDOW)
    if was_max:
        user32.ShowWindow(hwnd, SW_MAXIMIZE)
    return True
