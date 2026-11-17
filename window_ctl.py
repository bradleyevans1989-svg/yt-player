"""Shrink the player's app window to a small always-on-top bar, and put it back (Windows only).

The desktop launcher opens the player as an Edge or Chrome app window whose title
ends with the app's name. These helpers find that window and resize it with the
Win32 API through ctypes, so no extra packages are needed.
"""
import sys

APP_TITLE = "Bonesy's Music Player"
MINI_SIZE = (400, 100)       # client area in CSS pixels at 100% scaling
FULL_SIZE = (1400, 900)      # used if the old size wasn't remembered
MARGIN = 16

_saved = {}  # hwnd -> (rect, was_maximized)

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    HWND_TOPMOST, HWND_NOTOPMOST = -1, -2
    SWP_SHOWWINDOW, SW_RESTORE, SW_MAXIMIZE = 0x0040, 9, 3
    MONITOR_DEFAULTTONEAREST = 2
    DPI_AWARENESS_PER_MONITOR_V2 = ctypes.c_void_p(-4)

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, wintypes.UINT]
    H = wintypes.HWND
    for name, args in {
        "GetWindowTextLengthW": [H], "GetWindowTextW": [H, wintypes.LPWSTR, ctypes.c_int],
        "GetClassNameW": [H, wintypes.LPWSTR, ctypes.c_int], "IsWindowVisible": [H],
        "IsZoomed": [H], "ShowWindow": [H, ctypes.c_int], "GetDpiForWindow": [H],
        "GetWindowRect": [H, ctypes.POINTER(wintypes.RECT)], "GetClientRect": [H, ctypes.POINTER(wintypes.RECT)],
        "MonitorFromWindow": [H, wintypes.DWORD],
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


def _rect(hwnd, client=False):
    r = wintypes.RECT()
    (user32.GetClientRect if client else user32.GetWindowRect)(hwnd, ctypes.byref(r))
    return r


def _work_area(hwnd):
    info = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
    user32.GetMonitorInfoW(user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST), ctypes.byref(info))
    return info.rcWork


def set_mode(mode):
    """mode is 'mini' or 'full'. Returns True if the window was found and resized."""
    if not supported():
        return False
    try:  # work in real pixels, whatever the display scaling
        user32.SetThreadDpiAwarenessContext(DPI_AWARENESS_PER_MONITOR_V2)
    except AttributeError:
        pass
    hwnd = _find_window()
    if not hwnd:
        return False
    try:
        scale = user32.GetDpiForWindow(hwnd) / 96
    except AttributeError:
        scale = 1.0
    work = _work_area(hwnd)

    if mode == "mini":
        if hwnd not in _saved:
            was_max = bool(user32.IsZoomed(hwnd))
            if was_max:
                user32.ShowWindow(hwnd, SW_RESTORE)
            r = _rect(hwnd)
            _saved[hwnd] = ((r.left, r.top, r.right, r.bottom), was_max)
        outer, inner = _rect(hwnd), _rect(hwnd, client=True)
        frame_w = (outer.right - outer.left) - (inner.right - inner.left)
        frame_h = (outer.bottom - outer.top) - (inner.bottom - inner.top)
        w = round(MINI_SIZE[0] * scale) + frame_w
        h = round(MINI_SIZE[1] * scale) + frame_h
        m = round(MARGIN * scale)
        user32.SetWindowPos(hwnd, HWND_TOPMOST, work.right - w - m, work.bottom - h - m, w, h, SWP_SHOWWINDOW)
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
