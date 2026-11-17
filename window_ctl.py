"""Shrink the player's app window to a small always-on-top bar, and put it back (Windows only).

The desktop launcher opens the player as an Edge or Chrome app window whose title
ends with the app's name. These helpers find that window and resize it with the
Win32 API through ctypes, so no extra packages are needed.

Edge and Chrome draw their own title bar, so it can't be switched off with window
styles. Instead the window is clipped with a window region to just the web page
area: the title bar is still there, but cut off and invisible. The page draws its
own drag strip, window buttons and resize edges, and asks the server to move,
resize, minimise, maximise or close the window.
"""
import sys

APP_TITLE = "Bonesy's Music Player"
MINI_SIZE = (490, 100)       # visible bar in CSS pixels at 100% scaling
FULL_SIZE = (1400, 900)      # used if the old size wasn't remembered
MIN_SIZE = (720, 480)        # smallest full player, in CSS pixels
MARGIN = 16

_saved = {}  # hwnd -> (rect, was_maximized)

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
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
        "GetWindowRect": [H, ctypes.POINTER(wintypes.RECT)],
        "MonitorFromWindow": [H, wintypes.DWORD],
        "SetWindowPos": [H, H, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT],
        "SetWindowRgn": [H, wintypes.HANDLE, wintypes.BOOL],
        "EnumChildWindows": [H, WNDENUMPROC, wintypes.LPARAM],
    }.items():
        getattr(user32, name).argtypes = args
    user32.MonitorFromWindow.restype = wintypes.HANDLE
    user32.GetForegroundWindow.restype = H
    gdi32.CreateRectRgn.argtypes = [ctypes.c_int] * 4
    gdi32.CreateRectRgn.restype = wintypes.HANDLE


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


def _find_window():
    """The player's app window: an Edge/Chrome window whose title ends with the app name."""
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
    """Pixels between the window's edges and the web page: (left, top, right, bottom)."""
    outer, inner = _rect(hwnd), _content_rect(hwnd)
    if inner:
        return (inner.left - outer.left, inner.top - outer.top,
                outer.right - inner.right, outer.bottom - inner.bottom)
    # Fall back to what the page measured: outer minus inner size, in CSS pixels.
    side = max(0.0, float(page_frame.get("side") or 0)) * scale
    top = max(0.0, float(page_frame.get("top") or 0)) * scale
    return round(side), round(max(0.0, top - side)), round(side), round(side)


def _clip(hwnd, scale, page_frame):
    """Cut the window down to the web page, hiding the browser's title bar and borders."""
    if user32.IsIconic(hwnd):
        return
    user32.SetWindowRgn(hwnd, None, False)  # measure the whole window, not an old clip
    left, top, right, bottom = _frame_offsets(hwnd, scale, page_frame or {})
    r = _rect(hwnd)
    w, h = r.right - r.left, r.bottom - r.top
    user32.SetWindowRgn(hwnd, gdi32.CreateRectRgn(left, top, w - right, h - bottom), True)


def command(op, page_frame=None, dx=0.0, dy=0.0, edge=""):
    """Window commands from the page: clip, move, resize, minimize, maximize, close, mini, full."""
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
        _clip(hwnd, scale, page_frame)
    elif op == "minimize":
        user32.ShowWindow(hwnd, SW_MINIMIZE)
    elif op == "maximize":  # toggles
        user32.ShowWindow(hwnd, SW_RESTORE if user32.IsZoomed(hwnd) else SW_MAXIMIZE)
        _clip(hwnd, scale, page_frame)
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
        _clip(hwnd, scale, page_frame)
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
        user32.SetWindowRgn(hwnd, None, True)  # measure the full window, not an old clip
        left, top, right, bottom = _frame_offsets(hwnd, scale, page_frame or {})
        cw, ch = round(MINI_SIZE[0] * scale), round(MINI_SIZE[1] * scale)
        m = round(MARGIN * scale)
        w, h = left + cw + right, top + ch + bottom
        # Put the visible bar (not the hidden title bar) in the bottom-right corner.
        x, y = work.right - m - cw - left, work.bottom - m - ch - top
        user32.SetWindowPos(hwnd, HWND_TOPMOST, x, y, w, h, SWP_SHOWWINDOW)
        # Show only the page area; the title bar and borders are clipped away.
        user32.SetWindowRgn(hwnd, gdi32.CreateRectRgn(left, top, left + cw, top + ch), True)
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
    _clip(hwnd, scale, page_frame)
    return True
