"""Shrink the player's app window to a small always-on-top bar, and put it back (Windows only).

The desktop launcher opens the player as an Edge or Chrome app window whose title
ends with the app's name. These helpers find that window and resize it with the
Win32 API through ctypes, so no extra packages are needed.
"""
import sys

APP_TITLE = "Bonesy's Music Player"
MINI_SIZE = (490, 100)       # whole window in CSS pixels at 100% scaling (no title bar)
FULL_SIZE = (1400, 900)      # used if the old size wasn't remembered
MARGIN = 16

_saved = {}  # hwnd -> (rect, was_maximized)

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    HWND_TOPMOST, HWND_NOTOPMOST = -1, -2
    SWP_SHOWWINDOW, SW_RESTORE, SW_MAXIMIZE = 0x0040, 9, 3
    SWP_NOSIZE, SWP_NOZORDER, SWP_NOACTIVATE, SWP_FRAMECHANGED = 0x0001, 0x0004, 0x0010, 0x0020
    GWL_STYLE = -16
    # Title bar, resize border and caption buttons: removed in the mini player so only the bar shows.
    FRAME_STYLES = 0x00C00000 | 0x00040000 | 0x00080000 | 0x00020000 | 0x00010000
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
        "GetWindowLongPtrW": [H, ctypes.c_int], "SetWindowLongPtrW": [H, ctypes.c_int, ctypes.c_ssize_t],
    }.items():
        getattr(user32, name).argtypes = args
    user32.MonitorFromWindow.restype = wintypes.HANDLE
    user32.GetForegroundWindow.restype = H
    user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t


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


def set_mode(mode):
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
            style = user32.GetWindowLongPtrW(hwnd, GWL_STYLE)
            _saved[hwnd] = ((r.left, r.top, r.right, r.bottom), was_max, style)
            user32.SetWindowLongPtrW(hwnd, GWL_STYLE, style & ~FRAME_STYLES)
        w = round(MINI_SIZE[0] * scale)
        h = round(MINI_SIZE[1] * scale)
        m = round(MARGIN * scale)
        user32.SetWindowPos(hwnd, HWND_TOPMOST, work.right - w - m, work.bottom - h - m, w, h,
                            SWP_SHOWWINDOW | SWP_FRAMECHANGED)
        return True

    saved = _saved.pop(hwnd, None)
    if saved:
        (left, top, right, bottom), was_max, style = saved
        user32.SetWindowLongPtrW(hwnd, GWL_STYLE, style)
        x, y, w, h = left, top, right - left, bottom - top
    else:
        was_max = False
        w, h = round(FULL_SIZE[0] * scale), round(FULL_SIZE[1] * scale)
        w, h = min(w, work.right - work.left), min(h, work.bottom - work.top)
        x = work.left + (work.right - work.left - w) // 2
        y = work.top + (work.bottom - work.top - h) // 2
    user32.SetWindowPos(hwnd, HWND_NOTOPMOST, x, y, w, h, SWP_SHOWWINDOW | SWP_FRAMECHANGED)
    if was_max:
        user32.ShowWindow(hwnd, SW_MAXIMIZE)
    return True
