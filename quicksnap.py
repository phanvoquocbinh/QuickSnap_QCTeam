# -*- coding: utf-8 -*-
"""
QuickSnap - Công cụ chụp màn hình & quay video nhanh cho Windows (giống Snagit cơ bản)

Tính năng:
  * Chụp vùng / chụp cửa sổ (click) / chụp toàn màn hình
  * Chụp cuộn: cuộn chuột đến đâu, ảnh được nối dài đến đó (có chế độ tự cuộn)
  * Quay video màn hình (không âm thanh) ra file .mp4
  * Trình chỉnh sửa: khoanh tròn, chữ nhật, mũi tên, bút vẽ, tô sáng, chữ, cắt ảnh
  * Nhấn PrtSc ở bất cứ đâu -> menu Chụp ảnh / Quay video
"""
import os
import sys
import io
import copy
import math
import time
import queue
import ctypes
import datetime
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog, colorchooser

IS_WIN = sys.platform.startswith("win")
APP_NAME = "QuickSnap"            # tên thư mục lưu trữ/dữ liệu (giữ nguyên để không mất lịch sử)
DISPLAY_NAME = "QuickSnap_QCTeam"  # tên mặc định (đổi được qua file ten_ung_dung.txt)
NAME_FILE = "ten_ung_dung.txt"


def _load_display_name():
    """Đọc tên app từ ten_ung_dung.txt (dòng đầu tiên không bắt đầu bằng #) nằm cạnh quicksnap.py."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    try:
        with open(os.path.join(base, NAME_FILE), encoding="utf-8-sig") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#"):
                    return line[:80]
    except Exception:
        pass
    return DISPLAY_NAME


DISPLAY_NAME = _load_display_name()
# tên dùng trong tên file: bỏ các ký tự Windows không cho phép
FILE_PREFIX = "".join("_" if ch in '\\/:*?"<>|' else ch for ch in DISPLAY_NAME).strip(" .") or "QuickSnap"


# ----------------------------------------------------------------------------
# DPI awareness (phải gọi trước khi tạo cửa sổ để toạ độ = pixel thật)
# ----------------------------------------------------------------------------
def _set_dpi_awareness():
    if not IS_WIN:
        return
    try:
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


_set_dpi_awareness()

try:
    import numpy as np
    import mss
    import cv2
    from PIL import Image, ImageTk, ImageDraw, ImageFont, ImageColor
except ImportError as _e:  # pragma: no cover
    _r = tk.Tk()
    _r.withdraw()
    messagebox.showerror(
        APP_NAME,
        "Thiếu thư viện: %s\n\nHãy chạy:\n  pip install -r requirements.txt" % _e,
    )
    sys.exit(1)


# ----------------------------------------------------------------------------
# Win32 helpers (dùng instance DLL riêng để không ảnh hưởng thư viện khác)
# ----------------------------------------------------------------------------
if IS_WIN:
    from ctypes import wintypes

    _u32 = ctypes.WinDLL("user32", use_last_error=True)
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    try:
        _dwm = ctypes.WinDLL("dwmapi")
    except OSError:
        _dwm = None

    _u32.IsWindowVisible.argtypes = [wintypes.HWND]
    _u32.IsIconic.argtypes = [wintypes.HWND]
    _u32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    _u32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    _u32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    _u32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _u32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    _u32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
    _u32.GetParent.argtypes = [wintypes.HWND]
    _u32.GetParent.restype = wintypes.HWND
    _u32.OpenClipboard.argtypes = [wintypes.HWND]
    _u32.SetClipboardData.argtypes = [wintypes.UINT, ctypes.c_void_p]
    _u32.SetClipboardData.restype = ctypes.c_void_p
    _u32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
    _u32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
    _u32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
    _u32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    _k32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    _k32.GlobalAlloc.restype = ctypes.c_void_p
    _k32.GlobalLock.argtypes = [ctypes.c_void_p]
    _k32.GlobalLock.restype = ctypes.c_void_p
    _k32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    _k32.GlobalFree.argtypes = [ctypes.c_void_p]
    if _dwm is not None:
        _dwm.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]


def known_folder(kind):
    """Trả về thư mục Pictures / Videos của người dùng (hỗ trợ OneDrive)."""
    guids = {
        "Pictures": "33E28130-4E1E-4676-835A-98395C3BC3BB",
        "Videos": "18989B1D-99B5-455B-841C-AB7C74E4DDFC",
    }
    path = None
    if IS_WIN:
        try:
            import uuid

            class GUID(ctypes.Structure):
                _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                            ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]

            u = uuid.UUID(guids[kind])
            g = GUID(u.time_low, u.time_mid, u.time_hi_version,
                     (ctypes.c_ubyte * 8)(*u.bytes[8:]))
            p = ctypes.c_wchar_p()
            shell32 = ctypes.WinDLL("shell32")
            ole32 = ctypes.WinDLL("ole32")
            if shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(p)) == 0:
                path = p.value
                ole32.CoTaskMemFree(p)
        except Exception:
            path = None
    if not path:
        path = os.path.join(os.path.expanduser("~"), kind)
    d = os.path.join(path, APP_NAME)
    os.makedirs(d, exist_ok=True)
    return d


def get_hwnd(win):
    win.update_idletasks()
    try:
        return int(win.wm_frame(), 16)
    except Exception:
        try:
            return _u32.GetParent(win.winfo_id())
        except Exception:
            return None


def exclude_from_capture(win):
    """Ẩn cửa sổ khỏi ảnh chụp/video (Windows 10 2004+)."""
    if not IS_WIN:
        return
    try:
        hwnd = get_hwnd(win)
        if hwnd:
            _u32.SetWindowDisplayAffinity(hwnd, 0x11)  # WDA_EXCLUDEFROMCAPTURE
    except Exception:
        pass


def get_cursor_pos(root=None):
    if IS_WIN:
        pt = wintypes.POINT()
        _u32.GetCursorPos(ctypes.byref(pt))
        return pt.x, pt.y
    if root is not None:
        return root.winfo_pointerxy()
    return 0, 0


def set_cursor_pos(x, y):
    if IS_WIN:
        _u32.SetCursorPos(int(x), int(y))


def send_wheel(delta=-120):
    if IS_WIN:
        _u32.mouse_event(0x0800, 0, 0, ctypes.c_int(delta), 0)  # MOUSEEVENTF_WHEEL


def list_windows():
    """Danh sách (x, y, w, h) các cửa sổ hiển thị, theo thứ tự trên -> dưới."""
    if not IS_WIN:
        return []
    res = []
    pid = os.getpid()
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(hwnd, _lp):
        try:
            if not _u32.IsWindowVisible(hwnd) or _u32.IsIconic(hwnd):
                return True
            if _u32.GetWindowTextLengthW(hwnd) == 0:
                return True
            if _u32.GetWindowLongW(hwnd, -20) & 0x80:  # WS_EX_TOOLWINDOW
                return True
            if _dwm is not None:
                cloaked = ctypes.c_int(0)
                _dwm.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked), 4)
                if cloaked.value:
                    return True
            wpid = wintypes.DWORD()
            _u32.GetWindowThreadProcessId(hwnd, ctypes.byref(wpid))
            if wpid.value == pid:
                return True
            cls = ctypes.create_unicode_buffer(64)
            _u32.GetClassNameW(hwnd, cls, 64)
            if cls.value in ("Progman", "WorkerW", "Shell_TrayWnd"):
                return True
            r = wintypes.RECT()
            ok = False
            if _dwm is not None:
                ok = _dwm.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(r), ctypes.sizeof(r)) == 0
            if not ok:
                _u32.GetWindowRect(hwnd, ctypes.byref(r))
            w, h = r.right - r.left, r.bottom - r.top
            if w >= 80 and h >= 50:
                res.append((r.left, r.top, w, h))
        except Exception:
            pass
        return True

    _u32.EnumWindows(WNDENUMPROC(cb), 0)
    return res


def copy_image_to_clipboard(img):
    """Copy ảnh PIL vào clipboard Windows (CF_DIB)."""
    if not IS_WIN:
        return False
    out = io.BytesIO()
    img.convert("RGB").save(out, "BMP")
    data = out.getvalue()[14:]
    for _ in range(20):
        if _u32.OpenClipboard(None):
            break
        time.sleep(0.05)
    else:
        return False
    try:
        _u32.EmptyClipboard()
        h = _k32.GlobalAlloc(0x0002, len(data))  # GMEM_MOVEABLE
        if not h:
            return False
        p = _k32.GlobalLock(h)
        ctypes.memmove(p, data, len(data))
        _k32.GlobalUnlock(h)
        if not _u32.SetClipboardData(8, h):  # CF_DIB
            _k32.GlobalFree(h)
            return False
        return True
    finally:
        _u32.CloseClipboard()


def open_path(path):
    try:
        if IS_WIN:
            os.startfile(path)
        else:
            import subprocess
            subprocess.Popen(["xdg-open", path])
    except Exception as e:
        messagebox.showerror(DISPLAY_NAME, str(e))


def timestamp():
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S")


def shot_to_pil(shot):
    return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")


def monitor_at(sct, x, y):
    for m in sct.monitors[1:]:
        if m["left"] <= x < m["left"] + m["width"] and m["top"] <= y < m["top"] + m["height"]:
            return m
    return sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]


# ----------------------------------------------------------------------------
# Phím tắt toàn cục (RegisterHotKey)
# ----------------------------------------------------------------------------
# ----------------------------------------------------------------------------
# Phím tắt chụp (mặc định PrtSc, người dùng tự đổi được)
# ----------------------------------------------------------------------------
DEFAULT_HOTKEY = {"vk": 0x2C, "mods": []}
MOD_ORDER = ("ctrl", "alt", "shift", "win")
MOD_LABEL = {"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "win": "Win"}
MODIFIER_VKS = {0x10, 0x11, 0x12, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0x5B, 0x5C}
MOUSE_VKS = {0x05: "Chuột nút phụ 1", 0x06: "Chuột nút phụ 2"}
_VK_NAMES = {
    0x2C: "PrtSc", 0x13: "Pause", 0x91: "Scroll Lock", 0x2D: "Insert", 0x2E: "Delete", 0x24: "Home",
    0x23: "End", 0x21: "Page Up", 0x22: "Page Down", 0x25: "←", 0x26: "↑", 0x27: "→", 0x28: "↓",
    0x20: "Space", 0x0D: "Enter", 0x09: "Tab", 0x08: "Backspace", 0x14: "Caps Lock", 0x90: "Num Lock",
    0x5D: "Menu", 0xC0: "`", 0xBD: "-", 0xBB: "=", 0xDB: "[", 0xDD: "]", 0xDC: "\\", 0xBA: ";",
    0xDE: "'", 0xBC: ",", 0xBE: ".", 0xBF: "/", 0x6A: "Num *", 0x6B: "Num +", 0x6D: "Num -",
    0x6E: "Num .", 0x6F: "Num /", 0xAD: "Tắt tiếng", 0xAE: "Giảm âm lượng", 0xAF: "Tăng âm lượng",
    0xB0: "Bài tiếp", 0xB1: "Bài trước", 0xB3: "Phát/Dừng",
}
# Phím dùng để gõ chữ -> bắt buộc kèm Ctrl/Alt/Win, nếu không sẽ không gõ được phím đó nữa
_TYPING_VKS = (set(range(0x30, 0x3A)) | set(range(0x41, 0x5B)) | set(range(0x60, 0x6A))
               | {0x20, 0x0D, 0x09, 0x08, 0x1B, 0x2E, 0x25, 0x26, 0x27, 0x28, 0x14, 0x90,
                  0xC0, 0xBD, 0xBB, 0xDB, 0xDD, 0xDC, 0xBA, 0xDE, 0xBC, 0xBE, 0xBF,
                  0x6A, 0x6B, 0x6D, 0x6E, 0x6F})


def vk_name(vk):
    vk = int(vk)
    if vk in MOUSE_VKS:
        return MOUSE_VKS[vk]
    if vk in _VK_NAMES:
        return _VK_NAMES[vk]
    if 0x41 <= vk <= 0x5A or 0x30 <= vk <= 0x39:
        return chr(vk)
    if 0x70 <= vk <= 0x87:
        return "F%d" % (vk - 0x6F)
    if 0x60 <= vk <= 0x69:
        return "Num %d" % (vk - 0x60)
    return "Phím %d" % vk


def normalize_hotkey(hk):
    try:
        vk = int(hk.get("vk"))
        mods = [m for m in MOD_ORDER if m in (hk.get("mods") or [])]
        if vk <= 0 or vk > 0xFE or vk in MODIFIER_VKS:
            raise ValueError
        return {"vk": vk, "mods": mods}
    except Exception:
        return dict(DEFAULT_HOTKEY)


def hotkey_name(hk):
    hk = normalize_hotkey(hk)
    return " + ".join([MOD_LABEL[m] for m in hk["mods"]] + [vk_name(hk["vk"])])


def hotkey_problem(hk):
    """Trả về câu báo lỗi nếu phím tắt không nên dùng, None nếu được."""
    hk = normalize_hotkey(hk)
    hard = [m for m in hk["mods"] if m != "shift"]
    if hk["vk"] in _TYPING_VKS and not hard:
        return ("Phím \"%s\" dùng để gõ chữ – dùng làm phím tắt sẽ không gõ được nữa.\n"
                "Hãy kèm Ctrl / Alt / Win (vd: Ctrl + Shift + S) hoặc chọn phím F1–F12, PrtSc, Pause..."
                % hotkey_name(hk))
    return None


HOTKEY_NAME = "PrtSc"  # tên phím tắt hiện tại (để hiện trên giao diện)


class PrintScreenHook(threading.Thread):
    """Bắt phím tắt chụp toàn hệ thống (mặc định PrtSc, chặn luôn Snipping Tool).

    Có chế độ "ghi phím": phím / tổ hợp / nút chuột phụ kế tiếp được báo về qua
    q.put(("hotkey_recorded", {"vk":..,"mods":[..]})); Esc = huỷ (("hotkey_recorded", None))."""
    WM_KEYDOWN, WM_SYSKEYDOWN, WM_HOTKEY, WM_QUIT = 0x0100, 0x0104, 0x0312, 0x0012
    WM_XBUTTONDOWN, WM_XBUTTONUP = 0x020B, 0x020C
    WM_APP_UPDATE = 0x8001
    VK_SNAPSHOT = 0x2C

    def __init__(self, q, hotkey=None):
        super().__init__(daemon=True)
        self.q = q
        self.tid = None
        self.hotkey = normalize_hotkey(hotkey or DEFAULT_HOTKEY)
        self.recording = False
        self.swallow_up = set()   # phím đã nuốt lúc nhấn -> nuốt luôn lúc nhả
        self.mods_fn = self._win_mods
        self._proc = None
        self._mproc = None
        self.mouse_hook = None
        self.kb_hook = None
        self.registered = False

    # ----- gọi từ luồng giao diện
    def set_hotkey(self, hk):
        self.hotkey = normalize_hotkey(hk)
        self._poke()

    def start_record(self):
        self.recording = True
        self._poke()

    def cancel_record(self):
        self.recording = False
        self._poke()

    def _poke(self):
        if IS_WIN and self.tid:
            _u32.PostThreadMessageW(self.tid, self.WM_APP_UPDATE, 0, 0)

    # ----- logic (tách riêng để test được)
    @staticmethod
    def _win_mods():
        g = _u32.GetAsyncKeyState
        mods = []
        if g(0x11) & 0x8000:
            mods.append("ctrl")
        if g(0x12) & 0x8000:
            mods.append("alt")
        if g(0x10) & 0x8000:
            mods.append("shift")
        if (g(0x5B) & 0x8000) or (g(0x5C) & 0x8000):
            mods.append("win")
        return mods

    def handle(self, vk, down, mods):
        """Xử lý 1 sự kiện phím/nút. Trả về True nếu nuốt (không cho chương trình khác nhận)."""
        if vk in MODIFIER_VKS:
            return False
        if not down:
            if vk in self.swallow_up:
                self.swallow_up.discard(vk)
                return True
            return False
        if self.recording:
            self.recording = False
            self.swallow_up.add(vk)
            if vk == 0x1B and not mods:  # Esc = huỷ
                self.q.put(("hotkey_recorded", None))
            else:
                self.q.put(("hotkey_recorded", {"vk": vk, "mods": [m for m in MOD_ORDER if m in mods]}))
            self._release_trick(mods)
            return True
        hk = self.hotkey
        if vk == hk["vk"] and set(mods) == set(hk["mods"]):
            if vk not in self.swallow_up:  # giữ phím lâu (auto-repeat) chỉ tính 1 lần
                self.q.put(("prtsc",))
            self.swallow_up.add(vk)
            self._release_trick(mods)
            return True
        if vk == self.VK_SNAPSHOT and hk["vk"] == self.VK_SNAPSHOT and not hk["mods"] and not mods:
            return True
        return False

    def _release_trick(self, mods):
        """Nuốt phím khi đang giữ Win/Alt thì lúc nhả Win sẽ mở Start, nhả Alt sẽ nhảy menu ->
        bấm giả 1 phím 'không tên' để Windows hiểu là đã dùng tổ hợp."""
        if IS_WIN and ("win" in mods or "alt" in mods):
            try:
                _u32.keybd_event(0xE8, 0, 0, 0)
                _u32.keybd_event(0xE8, 0, 2, 0)
            except Exception:
                pass

    def _need_mouse(self):
        return self.recording or self.hotkey["vk"] in MOUSE_VKS

    # ----- luồng hook
    def run(self):
        if not IS_WIN:
            return
        self.tid = _k32.GetCurrentThreadId()

        class KBDLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD),
                        ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                        ("dwExtraInfo", ctypes.c_size_t)]

        class MSLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [("pt", wintypes.POINT), ("mouseData", wintypes.DWORD),
                        ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                        ("dwExtraInfo", ctypes.c_size_t)]

        LRESULT = ctypes.c_ssize_t
        HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
        _u32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, ctypes.c_void_p, wintypes.DWORD]
        _u32.SetWindowsHookExW.restype = ctypes.c_void_p
        _u32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
        _u32.CallNextHookEx.restype = LRESULT
        _u32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
        _k32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        _k32.GetModuleHandleW.restype = ctypes.c_void_p
        hmod = _k32.GetModuleHandleW(None)

        def proc(n, wp, lp):
            try:
                if n == 0:
                    kb = ctypes.cast(lp, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                    if not (kb.flags & 0x10):  # bỏ qua phím giả lập
                        down = wp in (self.WM_KEYDOWN, self.WM_SYSKEYDOWN)
                        if self.handle(int(kb.vkCode), down, self.mods_fn()):
                            return 1  # nuốt phím
            except Exception:
                pass
            return _u32.CallNextHookEx(None, n, wp, lp)

        def mproc(n, wp, lp):
            try:
                if n == 0 and wp in (self.WM_XBUTTONDOWN, self.WM_XBUTTONUP):
                    ms = ctypes.cast(lp, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                    if not (ms.flags & 0x1):  # bỏ qua chuột giả lập
                        btn = (ms.mouseData >> 16) & 0xFFFF
                        vk = 0x05 if btn == 1 else 0x06
                        if self.handle(vk, wp == self.WM_XBUTTONDOWN, self.mods_fn()):
                            return 1
            except Exception:
                pass
            return _u32.CallNextHookEx(None, n, wp, lp)

        self._proc = HOOKPROC(proc)
        self._mproc = HOOKPROC(mproc)
        self.kb_hook = _u32.SetWindowsHookExW(13, self._proc, hmod, 0)  # WH_KEYBOARD_LL

        def mods_flag(hk):
            return 0x4000 | sum({"alt": 1, "ctrl": 2, "shift": 4, "win": 8}[m] for m in hk["mods"])

        def sync():
            # hook chuột chỉ bật khi cần (đỡ làm chậm chuột)
            if self._need_mouse() and not self.mouse_hook:
                self.mouse_hook = _u32.SetWindowsHookExW(14, self._mproc, hmod, 0)  # WH_MOUSE_LL
            elif not self._need_mouse() and self.mouse_hook:
                _u32.UnhookWindowsHookEx(self.mouse_hook)
                self.mouse_hook = None
            if not self.kb_hook:  # dự phòng khi không đặt được hook bàn phím
                if self.registered:
                    _u32.UnregisterHotKey(None, 1)
                    self.registered = False
                if self.hotkey["vk"] not in MOUSE_VKS:
                    self.registered = bool(_u32.RegisterHotKey(None, 1, mods_flag(self.hotkey),
                                                               self.hotkey["vk"]))
                if not self.registered and self.hotkey["vk"] not in MOUSE_VKS:
                    self.q.put(("hotkey_fail", [hotkey_name(self.hotkey)]))

        sync()
        msg = wintypes.MSG()
        while _u32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == self.WM_HOTKEY:
                self.q.put(("prtsc",))
            elif msg.message == self.WM_APP_UPDATE:
                sync()
        if self.kb_hook:
            _u32.UnhookWindowsHookEx(self.kb_hook)
        if self.mouse_hook:
            _u32.UnhookWindowsHookEx(self.mouse_hook)
        if self.registered:
            _u32.UnregisterHotKey(None, 1)

    def stop(self):
        if IS_WIN and self.tid:
            _u32.PostThreadMessageW(self.tid, self.WM_QUIT, 0, 0)


def ensure_single_instance():
    """Trả về False nếu QuickSnap đã chạy."""
    if not IS_WIN:
        return True
    try:
        _k32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        _k32.CreateMutexW.restype = ctypes.c_void_p
        global _MUTEX
        _MUTEX = _k32.CreateMutexW(None, False, "QuickSnap_SingleInstance_Mutex")
        return ctypes.get_last_error() != 183  # ERROR_ALREADY_EXISTS
    except Exception:
        return True


def startup_command():
    if getattr(sys, "frozen", False):
        return '"%s" --tray' % sys.executable
    exe = sys.executable
    pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if os.path.exists(pyw):
        exe = pyw
    return '"%s" "%s" --tray' % (exe, os.path.abspath(__file__))


_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def get_autostart():
    if not IS_WIN:
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as k:
            winreg.QueryValueEx(k, APP_NAME)
            return True
    except Exception:
        return False


def set_autostart(on):
    if not IS_WIN:
        return
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, startup_command())
        else:
            try:
                winreg.DeleteValue(k, APP_NAME)
            except FileNotFoundError:
                pass


_WNDPROC_REFS = {}


def keep_size_on_dpi_change(win, fixed=None, on_exit=None, status=None):
    """Không cho Windows đổi kích thước cửa sổ khi kéo sang màn hình có Scale (DPI) khác.

    Chặn ngay ở WM_WINDOWPOSCHANGING (trước khi cửa sổ thay đổi) nên không bị giật:
      - fixed=True  : cửa sổ không viền (menu, thanh điều khiển) -> luôn giữ nguyên size
      - fixed=False : cửa sổ thường -> giữ size trong lúc đang KÉO DI CHUYỂN,
                      vẫn cho phép người dùng kéo viền để đổi size / phóng to.
    """
    if not IS_WIN:
        return
    try:
        hwnd = get_hwnd(win)
        if not hwnd or hwnd in _WNDPROC_REFS:
            return
        if fixed is None:
            fixed = bool(win.overrideredirect())
        LRESULT = ctypes.c_ssize_t
        WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
        setlong = getattr(_u32, "SetWindowLongPtrW", None) or _u32.SetWindowLongW
        setlong.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
        setlong.restype = ctypes.c_void_p
        _u32.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT,
                                         wintypes.WPARAM, wintypes.LPARAM]
        _u32.CallWindowProcW.restype = LRESULT

        class SIZE(ctypes.Structure):
            _fields_ = [("cx", wintypes.LONG), ("cy", wintypes.LONG)]

        class WINDOWPOS(ctypes.Structure):
            _fields_ = [("hwnd", wintypes.HWND), ("hwndInsertAfter", wintypes.HWND),
                        ("x", ctypes.c_int), ("y", ctypes.c_int), ("cx", ctypes.c_int),
                        ("cy", ctypes.c_int), ("flags", wintypes.UINT)]

        def cur_size(h):
            r = wintypes.RECT()
            _u32.GetWindowRect(h, ctypes.byref(r))
            return r.right - r.left, r.bottom - r.top

        st = status if status is not None else {}
        st.update({"old": None, "moving": False, "sizing": False,
                   "size": cur_size(hwnd) if fixed else None})

        def proc(h, msg, wp, lp):
            try:
                if msg == 0x0231:      # WM_ENTERSIZEMOVE: bắt đầu kéo
                    st["moving"], st["sizing"] = True, False
                    if not fixed:
                        st["size"] = cur_size(h)
                elif msg == 0x0214:    # WM_SIZING: người dùng đang kéo viền -> cho phép
                    st["sizing"] = True
                elif msg == 0x0232:    # WM_EXITSIZEMOVE: thả chuột
                    st["moving"] = False
                    if on_exit is not None:
                        on_exit()
                elif msg == 0x0046:    # WM_WINDOWPOSCHANGING: chặn đổi size TRƯỚC khi xảy ra
                    pos = ctypes.cast(lp, ctypes.POINTER(WINDOWPOS)).contents
                    lock = fixed or (st["moving"] and not st["sizing"])
                    if lock and st["size"] and not (pos.flags & 0x0001):  # không có SWP_NOSIZE
                        pos.cx, pos.cy = st["size"]
                elif msg == 0x02E4:    # WM_GETDPISCALEDSIZE: báo Windows giữ nguyên size
                    sz = ctypes.cast(lp, ctypes.POINTER(SIZE)).contents
                    sz.cx, sz.cy = cur_size(h)
                    return 1
                elif msg == 0x02E0:    # WM_DPICHANGED: bỏ qua, không đổi size/vị trí
                    return 0
            except Exception:
                pass
            return _u32.CallWindowProcW(st["old"], h, msg, wp, lp)

        cb = WNDPROC(proc)
        st["old"] = setlong(hwnd, -4, ctypes.cast(cb, ctypes.c_void_p))  # GWLP_WNDPROC
        if st["old"]:
            _WNDPROC_REFS[hwnd] = cb  # giữ tham chiếu để callback không bị thu hồi
    except Exception:
        pass


def lock_size(win):
    """Giữ nguyên kích thước cửa sổ khi kéo sang màn hình có Scale khác."""
    win.update_idletasks()
    size = (win.winfo_reqwidth(), win.winfo_reqheight())
    state = {"job": None}
    win_state = {"moving": False}  # được cập nhật bởi keep_size_on_dpi_change

    def fix():
        state["job"] = None
        if win_state.get("moving"):
            return  # đang kéo -> KHÔNG đụng vào (tránh giật), đợi thả chuột
        try:
            if (win.winfo_width(), win.winfo_height()) != size:
                win.geometry("%dx%d+%d+%d" % (size[0], size[1], win.winfo_x(), win.winfo_y()))
        except Exception:
            pass

    def schedule(delay=30):
        if state["job"] is None:
            state["job"] = win.after(delay, fix)

    def on_configure(e):
        if e.widget is win and (e.width, e.height) != size and not win_state.get("moving"):
            schedule()

    win.bind("<Configure>", on_configure, add="+")
    win.geometry("%dx%d" % size)
    keep_size_on_dpi_change(win, on_exit=lambda: schedule(10), status=win_state)


def dark_button(parent, text, cmd, bg="#3a3a3a", big=False, scale=1.0):
    return tk.Button(parent, text=text, command=cmd, bg=bg, fg="white",
                     activebackground="#555", activeforeground="white", relief="flat", bd=0,
                     padx=int((16 if big else 12) * scale), pady=int((10 if big else 5) * scale),
                     cursor="hand2", font=("Segoe UI", int(round((12 if big else 10) * scale)), "bold"))


class CaptureMenu:
    """Menu nhỏ hiện ra khi nhấn PrtSc: Chụp ảnh / Quay video."""

    def __init__(self, app):
        self.app = app
        top = self.top = tk.Toplevel(app.root)
        top.overrideredirect(True)
        top.attributes("-topmost", True)
        top.configure(bg="#ff3b30")
        f = tk.Frame(top, bg="#1f1f1f", padx=14, pady=12)
        f.pack(padx=2, pady=2)
        head = tk.Frame(f, bg="#1f1f1f")
        head.pack(fill="x", pady=(0, 10))
        tk.Label(head, text=DISPLAY_NAME, bg="#1f1f1f", fg="white",
                 font=("Segoe UI", 11, "bold")).pack(side="left")
        tk.Button(head, text="✕", command=self.close, bg="#1f1f1f", fg="#aaa", bd=0, relief="flat",
                  activebackground="#333", activeforeground="white", cursor="hand2",
                  font=("Segoe UI", 10)).pack(side="right")
        dark_button(f, "📷  Chụp ảnh", lambda: self.pick("photo"), bg="#0a84ff", big=True).pack(
            side="left", padx=(0, 8))
        dark_button(f, "🎥  Quay video", lambda: self.pick("video"), bg="#d70015", big=True).pack(side="left")
        lock_size(top)
        cx, cy = get_cursor_pos(app.root)
        m = monitor_at(app.sct, cx, cy)
        w, h = top.winfo_reqwidth(), top.winfo_reqheight()
        top.geometry("+%d+%d" % (m["left"] + (m["width"] - w) // 2, m["top"] + (m["height"] - h) // 3))
        exclude_from_capture(top)
        top.bind("<Escape>", lambda e: self.close())
        top.bind("<Key-1>", lambda e: self.pick("photo"))
        top.bind("<Key-2>", lambda e: self.pick("video"))
        top.bind("<FocusOut>", self._focus_out)
        top.after(30, self._focus)
        self.alive = True

    def _focus(self):
        try:
            self.top.lift()
            self.top.focus_force()
        except Exception:
            pass

    def _focus_out(self, _e):
        self.top.after(150, self._check_focus)

    def _check_focus(self):
        if not self.alive:
            return
        try:
            if self.top.focus_get() is None:
                self.close()
        except Exception:
            self.close()

    def close(self):
        if not self.alive:
            return
        self.alive = False
        self.top.destroy()
        self.app.menu = None

    def pick(self, what):
        self.close()
        self.app.start_capture(what)

# ----------------------------------------------------------------------------
# Thuật toán nối ảnh khi chụp cuộn
# ----------------------------------------------------------------------------
def dynamic_rows(f1, f2, pad=8):
    """Các dòng đang 'tự chạy' (banner, video, ảnh động): khác nhau giữa 2 lần chụp CÙNG vị trí."""
    h, w = f1.shape[:2]
    d = cv2.absdiff(cv2.cvtColor(np.ascontiguousarray(f1), cv2.COLOR_BGR2GRAY),
                    cv2.cvtColor(np.ascontiguousarray(f2), cv2.COLOR_BGR2GRAY)) > 20
    rows = d.sum(axis=1) > max(2, int(w * 0.004))
    if pad and rows.any():  # nới rộng vài dòng cho chắc
        rows = np.convolve(rows.astype(np.int32), np.ones(2 * pad + 1, np.int32), "same") > 0
    return rows


def find_scroll_offset(a, b, dyn_a=None, dyn_b=None, edges_a=None, strict=False):
    """So sánh khung trước (a) và khung sau (b) - mảng HxWx3 uint8.

    dyn_a / dyn_b: mảng bool theo dòng - các dòng đang tự chạy (banner...) sẽ được bỏ qua.
    Trả về (dy, top, bot):
      dy  > 0 : nội dung cuộn xuống dy pixel
      dy == 0 : không cuộn
      dy  < 0 : cuộn ngược lên
      top/bot : số dòng cố định (header/footer dính) ở trên/dưới
    Trả về None nếu không xác định được.
    """
    h, w = a.shape[:2]
    A = cv2.cvtColor(np.ascontiguousarray(a), cv2.COLOR_BGR2GRAY)
    B = cv2.cvtColor(np.ascontiguousarray(b), cv2.COLOR_BGR2GRAY)
    neq = A != B
    rows = np.flatnonzero(neq.any(axis=1))
    if rows.size == 0:
        return 0, 0, 0
    cap = int(h * 0.4)
    top = min(int(rows[0]), cap)
    bot = min(int(h - 1 - rows[-1]), cap)
    cols = np.flatnonzero(neq.any(axis=0))
    left, right = int(cols[0]), int(cols[-1]) + 1
    if right - left < 40:
        right = min(w, max(right, left + 40))
        left = max(0, right - 40)

    # chỉ dùng tối đa ~400 cột (lấy cách đều) để so khớp -> nhanh hơn nhiều mà vẫn chính xác
    stride = max(1, (right - left) // 400)
    ga = np.ascontiguousarray(A[top:h - bot, left:right:stride])
    gb = np.ascontiguousarray(B[top:h - bot, left:right:stride])
    hc, wc = ga.shape
    if hc < 40:
        return None
    da = np.zeros(hc, bool) if dyn_a is None else np.asarray(dyn_a[top:h - bot], bool)
    db_known = dyn_b is not None
    db = np.asarray(dyn_b[top:h - bot], bool) if db_known else np.zeros(hc, bool)

    # --- dò nhanh trên ảnh thu nhỏ 1/2
    ga2 = cv2.resize(ga, (max(8, wc // 2), hc // 2), interpolation=cv2.INTER_AREA)
    gb2 = cv2.resize(gb, (max(8, wc // 2), hc // 2), interpolation=cv2.INTER_AREA)
    hc2 = ga2.shape[0]
    th = max(10, min(50, hc2 // 6))
    starts, best = [], (-1.0, None)
    step = max(3, th // 3)
    for yy in range(hc2 - th, -1, -step):
        if any(abs(yy - s0) < th for s0 in starts):
            continue
        if da[yy * 2:(yy + th) * 2].any():  # mẫu không được nằm trên vùng tự chạy
            continue
        sd = float(ga2[yy:yy + th].std())
        if sd > best[0]:
            best = (sd, yy)
        if sd > 10:
            starts.append(yy)
            if len(starts) >= 4:
                break
    if not starts and best[1] is not None and best[0] >= 2.0:
        starts = [best[1]]

    votes = {0: 0}
    for y0 in starts:
        res = cv2.matchTemplate(gb2, ga2[y0:y0 + th], cv2.TM_CCOEFF_NORMED).ravel()
        taken = []
        for yb in np.argsort(res)[::-1][:200]:
            if res[yb] < 0.6 or len(taken) >= 8:
                break
            if any(abs(int(yb) - t) <= 4 for t in taken):  # bỏ các đỉnh sát nhau
                continue
            taken.append(int(yb))
            d2 = (y0 - int(yb)) * 2
            for dy in (d2 - 1, d2, d2 + 1):  # tinh chỉnh lại ở độ phân giải gốc
                votes[dy] = votes.get(dy, 0) + 1

    # --- chiều ngược lại: mẫu ở phần trên khung MỚI tìm trong khung CŨ (hữu ích khi phần dưới
    #     khung cũ đang có banner chạy)
    rstarts = []
    for yy in range(0, hc2 - th, step):
        if any(abs(yy - s0) < th for s0 in rstarts):
            continue
        if db[yy * 2:(yy + th) * 2].any():
            continue
        if float(gb2[yy:yy + th].std()) > 10:
            rstarts.append(yy)
            if len(rstarts) >= 3:
                break
    for y1 in rstarts:
        res = cv2.matchTemplate(ga2, gb2[y1:y1 + th], cv2.TM_CCOEFF_NORMED).ravel()
        taken = []
        for ya in np.argsort(res)[::-1][:200]:
            if res[ya] < 0.6 or len(taken) >= 8:
                break
            if any(abs(int(ya) - t) <= 4 for t in taken):
                continue
            taken.append(int(ya))
            d2 = (int(ya) - y1) * 2
            for dy in (d2 - 1, d2, d2 + 1):
                votes[dy] = votes.get(dy, 0) + 1

    # --- mép trên/dưới của vùng tự chạy (banner) cũng dịch đúng bằng khoảng cuộn -> dùng làm mốc
    ea = eb = np.zeros(0, int)
    ma_meas = da if edges_a is None else np.asarray(edges_a[top:h - bot], bool)
    if dyn_a is not None and db_known:
        ea = np.flatnonzero(np.diff(ma_meas.astype(np.int8)))  # chỉ mép ĐO TRỰC TIẾP mới làm mốc
        eb = np.flatnonzero(np.diff(db.astype(np.int8)))
        for x in ea[:20]:
            for y in eb[:20]:
                votes[int(x - y)] = votes.get(int(x - y), 0) + 1

    # --- chấm điểm ở độ phân giải gốc, bỏ qua các dòng tự chạy
    px_tol = max(2, int(wc * 0.004))
    scored = []
    for dy, v in votes.items():
        if abs(dy) >= hc - 30:
            continue
        if dy >= 0:
            ov_a, ov_b = ga[dy:], gb[:hc - dy]
            ma, mb = da[dy:], db[:hc - dy]
        else:
            ov_a, ov_b = ga[:hc + dy], gb[-dy:]
            ma, mb = da[:hc + dy], db[-dy:]
        if not db_known:
            mb = ma  # chưa biết vùng tự chạy của khung mới -> giả định giống khung cũ
        static = ~(ma | mb)                     # dòng đứng yên ở CẢ 2 khung
        both_dyn = int((ma & mb).sum()) if (dyn_a is not None and db_known) else 0
        rowbad = (cv2.absdiff(ov_a, ov_b) > 20).sum(axis=1) > px_tol
        n_static = int(static.sum())
        bad_rows = int((rowbad & static).sum())
        good_static = n_static - bad_rows
        # dòng bị đánh dấu "tự chạy" nhưng thực tế vẫn khớp hoàn hảo (và có chi tiết) -> vẫn là bằng chứng
        soft = ~static & ~rowbad
        if soft.any():
            soft &= ov_a.std(axis=1) > 8
            n_soft = int(soft.sum())
            good_static += n_soft
            n_static += n_soft
        # banner chạy ở cả 2 khung: chỉ tính là bằng chứng khi MÉP banner khớp chính xác (±1px)
        edge_hits = 0
        if both_dyn and ea.size and eb.size:
            edge_hits = int(sum(1 for x in ea[:20] if np.any(np.abs(eb - (x - dy)) <= 1)))
        if edge_hits == 0:
            both_dyn = 0
        evidence = good_static + 0.5 * both_dyn
        if evidence < 30:
            continue
        bad = bad_rows / max(1, n_static + both_dyn)
        # ít dòng làm bằng chứng thì kém tin cậy -> cộng điểm phạt
        scored.append((bad + 3.0 / evidence - 0.005 * min(v, 4) - 0.02 * min(edge_hits, 3),
                       bad, evidence, dy))
    if not scored:
        return None
    scored.sort()
    _score, bad, good_rows, dy = scored[0]

    def rows_at(d):
        if d >= 0:
            return ga[d:], gb[:hc - d], da[d:], (db[:hc - d] if db_known else da[d:])
        return ga[:hc + d], gb[-d:], da[:hc + d], (db[-d:] if db_known else da[:hc + d])

    def refine(dy0):
        """Tinh chỉnh ±4px bằng tổng chênh lệch điểm ảnh (bắt được lệch 1px ở vùng khối màu đặc).
        Chỉ dùng các dòng đã khớp tốt ở dy0, và chỉ đổi khi chênh lệch giảm rõ rệt."""
        oa, ob, ma0, mb0 = rows_at(dy0)
        good = ~(ma0 | mb0) & ~((cv2.absdiff(oa, ob) > 20).sum(axis=1) > px_tol)
        base_idx = np.flatnonzero(good)  # vị trí dòng (theo khung mới khi dy>=0)
        if base_idx.size < 20:
            return dy0

        def sad(d):
            if abs(d) >= hc - 30:
                return None
            # cùng các dòng của khung MỚI, so với khung cũ dịch d
            rb = base_idx + (0 if dy0 >= 0 else -dy0)
            ra = rb + d
            ok = (ra >= 0) & (ra < hc) & (rb >= 0) & (rb < hc)
            if ok.sum() < 20:
                return None
            return float(cv2.absdiff(ga[ra[ok]], gb[rb[ok]]).mean())

        v0 = sad(dy0)
        if v0 is None:
            return dy0
        best_d, best_v = dy0, v0
        for d in range(dy0 - 4, dy0 + 5):
            v = sad(d)
            if v is not None and v < best_v:
                best_d, best_v = d, v
        return best_d if best_v < v0 * 0.98 else dy0

    if strict:
        # vừa mất dấu xong: chỉ nối lại khi khớp RẤT chắc chắn và vượt trội (tránh bắt nhầm chỗ giống nhau)
        others = [q for q in scored[1:] if abs(q[3] - dy) > 3]
        if bad <= 0.2 and good_rows >= 80 and (not others or others[0][0] - _score >= 0.25):
            return refine(dy), top, bot
        return None
    if bad <= 0.45 and good_rows >= 30:
        return refine(dy), top, bot
    # dự phòng: banner đổi slide đúng lúc cuộn làm nhiều dòng lệch -> vẫn nhận nếu vị trí tốt nhất
    # có đủ bằng chứng VÀ vượt trội rõ ràng so với vị trí khác (không lẫn với ±3px của chính nó)
    others = [q for q in scored[1:] if abs(q[3] - dy) > 3]
    if bad <= 0.55 and good_rows >= 120 and (not others or others[0][0] - _score >= 0.25):
        return refine(dy), top, bot
    return None


class Stitcher:
    """Ghép các khung hình cuộn thành 1 ảnh dài (mảng BGR)."""

    def __init__(self, first, dyn=None):
        self.parts = [first]
        self.height = first.shape[0]
        self.last = first
        self.last_dyn = dyn      # vùng bỏ qua khi so khớp (đo được + tự học)
        self.last_meas = dyn     # vùng tự chạy ĐO TRỰC TIẾP (dùng làm mốc mép banner)
        self.fail_run = 0        # số lần lệch liên tiếp (>=3 -> cần 2 khung xác nhận mới nối lại)
        self.pending = None

    def _trim_end(self, n):
        while n > 0 and self.parts:
            p = self.parts[-1]
            if p.shape[0] <= n:
                n -= p.shape[0]
                self.height -= p.shape[0]
                self.parts.pop()
            else:
                self.parts[-1] = p[:p.shape[0] - n]
                self.height -= n
                n = 0

    def add(self, cur, dyn=None):
        """Trả về 'added' | 'same' | 'up' | 'fail'."""
        r = find_scroll_offset(self.last, cur, self.last_dyn, dyn, self.last_meas)
        if r is None:
            self.fail_run += 1
            return "fail"
        if self.fail_run >= 3:
            # vừa mất dấu: chỉ tin vị trí nối lại khi 2 khung liên tiếp xác nhận NHẤT QUÁN
            # (khớp nhầm vào chỗ trông giống nhau gần như không lặp lại y hệt ở khung sau)
            if self.pending is None:
                self.pending = (cur, r)
                return "fail"
            pc, pr = self.pending
            r12 = find_scroll_offset(pc, cur)
            self.pending = (cur, r)
            if r12 is None or abs((r[0] - pr[0]) - r12[0]) > 2:
                return "fail"
        self.pending = None
        self.fail_run = 0
        dy, top, bot = r
        if dy == 0:
            # cùng vị trí -> học luôn vùng nào đang tự chạy để lần sau bỏ qua
            moving = dynamic_rows(self.last, cur)
            if self.last_dyn is not None:
                moving = moving | self.last_dyn
            if dyn is not None:
                moving = moving | dyn
            self.last, self.last_dyn, self.last_meas = cur, moving, moving
            return "same"
        if dy < 0:
            return "up"
        h = cur.shape[0]
        self._trim_end(bot)
        new = cur[h - bot - dy:]
        self.parts.append(new)
        self.height += new.shape[0]
        # (không "tự học" vùng banner từ phép căn khớp nữa - dễ lẫn nút chat/khung cố định)
        self.last, self.last_dyn, self.last_meas = cur, dyn, dyn
        return "added"

    def reset(self, frame, dyn=None):
        """Lấy khung này làm gốc mới (dùng khi chưa nối được khung nào)."""
        self.parts = [frame]
        self.height = frame.shape[0]
        self.last, self.last_dyn, self.last_meas = frame, dyn, dyn
        self.fail_run = 0
        self.pending = None

    def result(self):
        return np.vstack(self.parts)



# ----------------------------------------------------------------------------
# Các cửa sổ phụ: viền vùng chụp, thanh điều khiển
# ----------------------------------------------------------------------------
class BorderFrame:
    def __init__(self, root, rect, color="#ff3b30", t=3):
        x, y, w, h = rect
        specs = [(x - t, y - t, w + 2 * t, t), (x - t, y + h, w + 2 * t, t),
                 (x - t, y, t, h), (x + w, y, t, h)]
        self.wins = []
        for bx, by, bw, bh in specs:
            win = tk.Toplevel(root)
            win.overrideredirect(True)
            win.attributes("-topmost", True)
            win.configure(bg=color)
            win.geometry("%dx%d+%d+%d" % (max(1, bw), max(1, bh), bx, by))
            keep_size_on_dpi_change(win)
            exclude_from_capture(win)
            self.wins.append(win)

    def destroy(self):
        for w in self.wins:
            try:
                w.destroy()
            except Exception:
                pass


def make_control_bar(root, rect, sct):
    """Tạo thanh điều khiển nổi gần vùng chụp (không bị chụp vào ảnh)."""
    bar = tk.Toplevel(root)
    bar.overrideredirect(True)
    bar.attributes("-topmost", True)
    bar.configure(bg="#1f1f1f")
    frame = tk.Frame(bar, bg="#1f1f1f", padx=8, pady=6)
    frame.pack(fill="both", expand=True)

    def place():
        bar.update_idletasks()
        bw, bh = bar.winfo_reqwidth(), bar.winfo_reqheight()
        x, y, w, h = rect
        mon = monitor_at(sct, x + w // 2, y + h // 2)
        mx, my = mon["left"], mon["top"]
        mw, mh = mon["width"], mon["height"]
        bx = min(max(x + w - bw, mx), mx + mw - bw)
        if y - bh - 8 >= my:
            by = y - bh - 8
        elif y + h + bh + 8 <= my + mh:
            by = y + h + 8
        else:
            by = y + 8
            bx = min(max(x + (w - bw) // 2, mx), mx + mw - bw)
        bar.geometry("+%d+%d" % (bx, by))
        keep_size_on_dpi_change(bar)
        exclude_from_capture(bar)

    return bar, frame, place


def bar_button(parent, text, cmd, bg="#3a3a3a"):
    return tk.Button(parent, text=text, command=cmd, bg=bg, fg="white",
                     activebackground="#555", activeforeground="white",
                     relief="flat", padx=10, pady=3, font=("Segoe UI", 9, "bold"),
                     cursor="hand2", bd=0)


# ----------------------------------------------------------------------------
# Chọn vùng
# ----------------------------------------------------------------------------
class RegionSelector:
    """Overlay toàn màn hình để chọn vùng.
    callback(rect, image): rect = (x, y, w, h) toạ độ màn hình, image = ảnh PIL đóng băng.
    Huỷ -> callback(None, None)."""

    def __init__(self, app, callback, title="Kéo chuột để khoanh vùng", actions=None):
        self.app = app
        self.cb = callback
        self.actions = actions  # [(key, label, color)] -> hiện nút sau khi chọn
        self.selected = None
        self.action_win = None
        sct = app.sct
        mon = sct.monitors[0]
        self.vx, self.vy, self.vw, self.vh = mon["left"], mon["top"], mon["width"], mon["height"]
        self.cursor_at_shot = get_cursor_pos(app.root)  # vị trí chuột lúc chụp (để vẽ vào ảnh nếu bật)
        self.full = shot_to_pil(sct.grab(mon))
        dim = Image.blend(self.full, Image.new("RGB", self.full.size, (0, 0, 0)), 0.45)
        self.windows = list_windows()
        self.start = None
        self.dragging = False
        self.hover = None
        self._pending = None
        self._scheduled = False
        self.done = False

        top = self.top = tk.Toplevel(app.root)
        top.overrideredirect(True)
        top.attributes("-topmost", True)
        top.geometry("%dx%d+%d+%d" % (self.vw, self.vh, self.vx, self.vy))
        top.configure(cursor="crosshair", bg="black")
        c = self.canvas = tk.Canvas(top, width=self.vw, height=self.vh,
                                    highlightthickness=0, bd=0, bg="black")
        c.pack(fill="both", expand=True)
        # Vị trí THẬT của cửa sổ phủ trên màn hình (phòng khi Windows dịch cửa sổ)
        top.update()
        keep_size_on_dpi_change(top, fixed=True)
        self.ox, self.oy = top.winfo_rootx(), top.winfo_rooty()
        self.dx, self.dy = self.ox - self.vx, self.oy - self.vy  # canvas -> toạ độ trong ảnh
        self.dim_photo = ImageTk.PhotoImage(dim)
        c.create_image(-self.dx, -self.dy, image=self.dim_photo, anchor="nw")
        self.crop_photo = None
        self.crop_item = c.create_image(0, 0, anchor="nw", state="hidden")
        self.rect_item = c.create_rectangle(0, 0, 0, 0, outline="#ff3b30", width=2, state="hidden")
        self.hline = c.create_line(0, 0, 0, 0, fill="#00e5ff", dash=(4, 4))
        self.vline = c.create_line(0, 0, 0, 0, fill="#00e5ff", dash=(4, 4))
        self.lbl_bg = c.create_rectangle(0, 0, 0, 0, fill="#000000", outline="", state="hidden")
        self.lbl = c.create_text(0, 0, text="", fill="white", anchor="nw",
                                 font=("Segoe UI", 10, "bold"), state="hidden")

        # Hướng dẫn
        cx, cy = get_cursor_pos(app.root)
        m = monitor_at(sct, cx, cy)
        hx = m["left"] - self.ox + m["width"] // 2
        hy = m["top"] - self.oy + 40
        hint = ("%s  •  Click: chọn cửa sổ  •  Enter: toàn màn hình  •  Esc: huỷ\n"
                "Khoanh xong vẫn kéo được các góc/cạnh để chỉnh to nhỏ, kéo bên trong để di chuyển" % title)
        t = c.create_text(hx, hy + 10, text=hint, fill="white", font=("Segoe UI", 11, "bold"),
                          justify="center")
        bb = c.bbox(t)
        hb = c.create_rectangle(bb[0] - 14, bb[1] - 8, bb[2] + 14, bb[3] + 8, fill="#202020", outline="#ff3b30")
        c.tag_raise(t, hb)
        self.hint_items = (t, hb)

        c.bind("<ButtonPress-1>", self.on_press)
        c.bind("<B1-Motion>", self.on_drag)
        c.bind("<ButtonRelease-1>", self.on_release)
        c.bind("<Motion>", self.on_motion)
        c.bind("<ButtonPress-3>", lambda e: self.cancel())
        top.bind("<Escape>", lambda e: self.cancel())
        top.bind("<Return>", lambda e: self._on_enter())
        top.after(30, self._focus)

    def _focus(self):
        try:
            self.top.lift()
            self.top.focus_force()
            self.canvas.focus_set()
        except Exception:
            pass

    # -- hiển thị
    def _show(self, r):
        self._pending = r
        if not self._scheduled:
            self._scheduled = True
            self.top.after(12, self._apply)

    def _apply(self):
        self._scheduled = False
        if self.done:
            return
        c = self.canvas
        r = self._pending
        if r is None:
            for it in (self.crop_item, self.rect_item, self.lbl, self.lbl_bg):
                c.itemconfigure(it, state="hidden")
            return
        x0, y0, x1, y1 = r
        if x1 - x0 >= 1 and y1 - y0 >= 1:
            self.crop_photo = ImageTk.PhotoImage(self.full.crop(self._img_box(r)))
            c.itemconfigure(self.crop_item, image=self.crop_photo, state="normal")
            c.coords(self.crop_item, x0, y0)
        c.coords(self.rect_item, x0, y0, x1, y1)
        c.itemconfigure(self.rect_item, state="normal")
        c.itemconfigure(self.lbl, text=" %d × %d " % (x1 - x0, y1 - y0), state="normal")
        mtop = self._monitor_box(x0, y0)[1]
        ly = y0 - 24 if y0 - 24 >= mtop else y0 + 4
        c.coords(self.lbl, x0 + 2, ly)
        bb = c.bbox(self.lbl)
        c.coords(self.lbl_bg, bb[0] - 2, bb[1] - 1, bb[2] + 2, bb[3] + 1)
        c.itemconfigure(self.lbl_bg, state="normal")
        c.tag_raise(self.rect_item)
        c.tag_raise(self.lbl_bg)
        c.tag_raise(self.lbl)
        self._draw_handles()

    HANDLE_CURSORS = {"nw": "size_nw_se", "se": "size_nw_se", "ne": "size_ne_sw", "sw": "size_ne_sw",
                      "n": "sb_v_double_arrow", "s": "sb_v_double_arrow",
                      "e": "sb_h_double_arrow", "w": "sb_h_double_arrow", "move": "fleur"}

    def _handle_points(self, r):
        x0, y0, x1, y1 = r
        mx, my = (x0 + x1) // 2, (y0 + y1) // 2
        return {"nw": (x0, y0), "n": (mx, y0), "ne": (x1, y0), "e": (x1, my),
                "se": (x1, y1), "s": (mx, y1), "sw": (x0, y1), "w": (x0, my)}

    def _draw_handles(self):
        c = self.canvas
        c.delete("handle")
        if self.selected is None or self.done:
            return
        for (hx, hy) in self._handle_points(self.selected).values():
            c.create_rectangle(hx - 5, hy - 5, hx + 5, hy + 5, fill="white", outline="#ff3b30",
                               width=2, tags=("handle",))

    def _hit_handle(self, x, y):
        """Trả về tên ô nắm dưới chuột ('nw', 'e', ...), 'move' nếu ở trong vùng, None nếu ở ngoài."""
        if self.selected is None:
            return None
        for name, (hx, hy) in self._handle_points(self.selected).items():
            if abs(x - hx) <= 9 and abs(y - hy) <= 9:
                return name
        x0, y0, x1, y1 = self.selected
        tol = 5
        if abs(x - x0) <= tol and y0 <= y <= y1:
            return "w"
        if abs(x - x1) <= tol and y0 <= y <= y1:
            return "e"
        if abs(y - y0) <= tol and x0 <= x <= x1:
            return "n"
        if abs(y - y1) <= tol and x0 <= x <= x1:
            return "s"
        if x0 < x < x1 and y0 < y < y1:
            return "move"
        return None

    def _crop_photo(self, r):
        """Cắt vùng chọn từ ảnh đóng băng; vẽ thêm con trỏ chuột nếu bật tuỳ chọn."""
        img = self.full.crop(self._img_box(r))
        var = getattr(self.app, "photo_cursor_var", None)
        if var is not None and var.get():
            try:
                scale = (_u32.GetDpiForSystem() / 96.0) if IS_WIN else 1.0
            except Exception:
                scale = 1.0
            x0, y0 = self._img_box(r)[:2]
            arr = np.array(img.convert("RGB"))
            draw_cursor(arr, self.vx + x0, self.vy + y0, scale, pos=self.cursor_at_shot)
            img = Image.fromarray(arr)
        return img

    def _img_box(self, r):
        return (r[0] + self.dx, r[1] + self.dy, r[2] + self.dx, r[3] + self.dy)

    def _monitor_box(self, x, y):
        """Khung màn hình (toạ độ canvas) chứa điểm canvas (x, y)."""
        m = monitor_at(self.app.sct, x + self.ox, y + self.oy)
        return (m["left"] - self.ox, m["top"] - self.oy,
                m["left"] - self.ox + m["width"], m["top"] - self.oy + m["height"])

    def _crosshair(self, x, y):
        c = self.canvas
        c.coords(self.hline, 0, y, self.vw, y)
        c.coords(self.vline, x, 0, x, self.vh)
        c.tag_raise(self.hline)
        c.tag_raise(self.vline)

    def _window_at(self, x, y):
        sx, sy = x + self.ox, y + self.oy
        for (wx, wy, ww, wh) in self.windows:
            if wx <= sx < wx + ww and wy <= sy < wy + wh:
                x0 = max(wx - self.ox, 0)
                y0 = max(wy - self.oy, 0)
                x1 = min(wx + ww - self.ox, self.vw)
                y1 = min(wy + wh - self.oy, self.vh)
                if x1 - x0 > 10 and y1 - y0 > 10:
                    return (x0, y0, x1, y1)
        return None

    # -- sự kiện
    def on_motion(self, e):
        if self.selected is not None:
            h = self._hit_handle(e.x, e.y)
            self.canvas.configure(cursor=self.HANDLE_CURSORS.get(h, "crosshair"))
            return
        self._crosshair(e.x, e.y)
        r = self._window_at(e.x, e.y)
        if r != self.hover:
            self.hover = r
            self._show(r)

    def on_press(self, e):
        if self.selected is not None:
            h = self._hit_handle(e.x, e.y)
            if h is not None:  # kéo ô nắm / di chuyển vùng đã khoanh
                self.edit_mode = h
                self.edit_start = (e.x, e.y)
                self.edit_orig = self.selected
                self._hide_actions()
                return
        self.edit_mode = None
        if self.selected is not None:  # bấm ra ngoài -> khoanh lại vùng mới
            self.selected = None
            self.canvas.delete("handle")
            self.canvas.configure(cursor="crosshair")
            self._hide_actions()
            for it in (self.hline, self.vline):
                self.canvas.itemconfigure(it, state="normal")
        self.start = (e.x, e.y)
        self.dragging = False
        for it in self.hint_items:
            self.canvas.itemconfigure(it, state="hidden")

    def on_drag(self, e):
        if getattr(self, "edit_mode", None):
            self._edit_drag(e.x, e.y)
            return
        if not self.start:
            return
        self._crosshair(e.x, e.y)
        x0, y0 = self.start
        if not self.dragging and (abs(e.x - x0) > 4 or abs(e.y - y0) > 4):
            self.dragging = True
        if self.dragging:
            x1 = min(max(e.x, 0), self.vw)
            y1 = min(max(e.y, 0), self.vh)
            self._show((min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)))

    def _edit_drag(self, x, y):
        dx, dy = x - self.edit_start[0], y - self.edit_start[1]
        x0, y0, x1, y1 = self.edit_orig
        h = self.edit_mode
        if h == "move":
            w, hh = x1 - x0, y1 - y0
            nx0 = min(max(x0 + dx, 0), self.vw - w)
            ny0 = min(max(y0 + dy, 0), self.vh - hh)
            r = (nx0, ny0, nx0 + w, ny0 + hh)
        else:
            if "w" in h:
                x0 += dx
            if "e" in h:
                x1 += dx
            if "n" in h:
                y0 += dy
            if "s" in h:
                y1 += dy
            x0, x1 = sorted((min(max(x0, 0), self.vw), min(max(x1, 0), self.vw)))
            y0, y1 = sorted((min(max(y0, 0), self.vh), min(max(y1, 0), self.vh)))
            if x1 - x0 < 5:
                x1 = x0 + 5
            if y1 - y0 < 5:
                y1 = y0 + 5
            r = (x0, y0, x1, y1)
        self.selected = r
        self._show(r)

    def on_release(self, e):
        if getattr(self, "edit_mode", None):
            self.edit_mode = None
            if self.selected is not None:
                self._show_actions(self.selected)
            return
        if not self.start:
            return
        if self.dragging:
            x0, y0 = self.start
            x1 = min(max(e.x, 0), self.vw)
            y1 = min(max(e.y, 0), self.vh)
            r = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
            if r[2] - r[0] >= 5 and r[3] - r[1] >= 5:
                self.finish(r)
                return
            self.start = None
            self.dragging = False
            self._show(None)
            return
        r = self._window_at(e.x, e.y)
        if r:
            self.finish(r)
        else:
            self.choose_monitor()

    def choose_monitor(self):
        cx, cy = get_cursor_pos(self.app.root)
        m = monitor_at(self.app.sct, cx, cy)
        x0 = m["left"] - self.ox
        y0 = m["top"] - self.oy
        self.finish((x0, y0, x0 + m["width"], y0 + m["height"]))

    def _on_enter(self):
        if self.selected is not None and self.actions:
            self.do_action(self.actions[0][0])
        else:
            self.choose_monitor()

    def finish(self, r):
        if self.done:
            return
        if self.actions:
            self.selected = r
            self.start = None
            self._show(r)
            for it in (self.hline, self.vline):
                self.canvas.itemconfigure(it, state="hidden")
            self._show_actions(r)
            return
        self.done = True
        img = self._crop_photo(r)
        rect = (r[0] + self.ox, r[1] + self.oy, r[2] - r[0], r[3] - r[1])
        self.top.destroy()
        self.cb(rect, img)

    def _show_actions(self, r):
        self._hide_actions()
        c = self.canvas
        fr = tk.Frame(c, bg="#1f1f1f", padx=8, pady=8)
        for key, label, color in self.actions:  # nút to hơn ~30% cho dễ đọc
            dark_button(fr, label, lambda k=key: self.do_action(k), bg=color, scale=1.3).pack(side="left", padx=4)
        self.action_frame = fr
        fr.update_idletasks()
        bw, bh = fr.winfo_reqwidth(), fr.winfo_reqheight()
        x0, y0, x1, y1 = r
        mx0, my0, mx1, my1 = self._monitor_box(max(x0, x1 - 2), max(y0, y1 - 2))
        # phần vùng chọn đang hiển thị trên màn hình đó
        vx1, vy1 = min(x1, mx1), min(y1, my1)
        vx0, vy0 = max(x0, mx0), max(y0, my0)
        if vx1 - vx0 >= bw + 16 and vy1 - vy0 >= bh + 16:
            bx, by = vx1 - bw - 8, vy1 - bh - 8          # bên trong, góc dưới phải
        else:
            bx = vx1 - bw                                 # vùng nhỏ: đặt ngay dưới / trên
            by = vy1 + 8 if vy1 + bh + 8 <= my1 else vy0 - bh - 8
        bx = min(max(bx, mx0 + 4), mx1 - bw - 4)
        by = min(max(by, my0 + 4), my1 - bh - 4)
        self.action_win = c.create_window(bx, by, window=fr, anchor="nw")

    def _hide_actions(self):
        if self.action_win is not None:
            self.canvas.delete(self.action_win)
            self.action_frame.destroy()
            self.action_win = None

    def do_action(self, key):
        if self.done or self.selected is None:
            return
        self.done = True
        r = self.selected
        img = self._crop_photo(r) if key in ("photo", "confirm") else self.full.crop(self._img_box(r))
        rect = (r[0] + self.ox, r[1] + self.oy, r[2] - r[0], r[3] - r[1])
        self.top.destroy()
        if key == "cancel":
            self.cb(None, None, None)
        else:
            self.cb(key, rect, img)

    def cancel(self):
        if self.done:
            return
        self.done = True
        self.top.destroy()
        if self.actions:
            self.cb(None, None, None)
        else:
            self.cb(None, None)


# ----------------------------------------------------------------------------
# Chụp cuộn
# ----------------------------------------------------------------------------
class ScrollCapture:
    MAX_HEIGHT = 60000

    def __init__(self, app, rect):
        self.app = app
        x, y, w, h = rect
        self.rect = rect
        self.mon = {"left": x, "top": y, "width": w, "height": h}
        self.running = True
        self.auto = False
        self.same_count = 0
        self.fail_count = 0
        self.warn_until = 0
        self.stitcher = None

        self.border = BorderFrame(app.root, rect)
        self.bar, f, place = make_control_bar(app.root, rect, app.sct)
        tk.Label(f, text="⇕ CHỤP CUỘN", bg="#1f1f1f", fg="#ff9f0a",
                 font=("Segoe UI", 9, "bold")).pack(side="left", padx=(0, 8))
        self.status = tk.Label(f, text="Hãy cuộn chuột xuống từ từ...", bg="#1f1f1f", fg="white",
                               font=("Segoe UI", 9), width=34, anchor="w")
        self.status.pack(side="left")
        self.btn_auto = bar_button(f, "Tự cuộn", self.toggle_auto)
        self.btn_auto.pack(side="left", padx=3)
        bar_button(f, "✔ OK", self.finish, bg="#0a84ff").pack(side="left", padx=3)
        bar_button(f, "✖ Tắt", self.cancel, bg="#8e2a2a").pack(side="left", padx=3)
        place()
        self.app.root.after(200, self._start)

    # ================= luồng nền: chụp + nối liên tục =================
    def _start(self):
        self.lock = threading.Lock()
        self.last_result = None
        self.fail_streak = 0
        self.hit_max = False
        self.worker_error = None
        self.worker = threading.Thread(target=self._worker, daemon=True)
        self.worker.start()
        self.app.root.after(150, self._ui_tick)

    def _worker(self):
        try:
            with mss.mss() as sct:
                def grab():
                    return np.ascontiguousarray(np.asarray(sct.grab(self.mon))[:, :, :3])

                f0 = grab()
                time.sleep(0.15)
                f1 = grab()
                st = Stitcher(f1, dynamic_rows(f0, f1))
                self.stitcher = st
                last_small = f1[::12, ::12].copy()
                while self.running:
                    f = grab()
                    small = f[::12, ::12]
                    if np.array_equal(small, last_small):  # màn hình đứng yên -> nghỉ chút
                        time.sleep(0.01)
                        continue
                    last_small = small.copy()
                    with self.lock:
                        r = st.add(f)
                        if r == "fail" and len(st.parts) == 1:
                            # chưa nối được gì (vd: banner đang chuyển slide lúc mới bắt đầu)
                            # -> lấy khung mới nhất làm gốc thay vì kẹt mãi
                            st.reset(f)
                            r = "same"
                    self.last_result = r
                    self.fail_streak = self.fail_streak + 1 if r == "fail" else 0
                    if st.height >= self.MAX_HEIGHT:
                        self.hit_max = True
                        break
        except Exception as e:  # pragma: no cover
            self.worker_error = str(e)

    def _ui_tick(self):
        """Cập nhật thanh trạng thái (chạy trên luồng giao diện)."""
        if not self.running:
            return
        if self.worker_error:
            self.status.configure(text="Lỗi: %s" % self.worker_error, fg="#ff453a")
        elif self.hit_max:
            self.finish()
            return
        elif self.stitcher is not None and time.time() >= self.warn_until:
            if self.fail_streak >= 6:
                self.status.configure(text="⚠ Cuộn nhanh quá – cuộn LÊN lại tới khi hết cảnh báo", fg="#ffd60a")
            else:
                extra = "– đang tự cuộn..." if self.auto else ""
                self.status.configure(text="Chiều cao: %d px %s" % (self.stitcher.height, extra), fg="white")
        self.app.root.after(100, self._ui_tick)

    def _update_status(self, extra=""):
        pass  # trạng thái do _ui_tick cập nhật

    # ================= Tự cuộn =================
    AUTO_STEP_MS = 280     # mỗi bước cuộn cách nhau ~0.3 giây
    AUTO_END_SAME = 4      # 4 bước liền mà ảnh không dài thêm -> đã tới cuối trang

    def _auto_step(self):
        if not self.running or not self.auto:
            return
        x, y, w, h = self.rect
        notches = max(1, min(5, int(h * 0.45 / 100)))  # mỗi bước ~45% chiều cao vùng chụp
        set_cursor_pos(x + w // 2, y + h // 2)
        send_wheel(-120 * notches)
        self._auto_h = self.stitcher.height if self.stitcher else 0
        self.app.root.after(self.AUTO_STEP_MS, self._auto_check)

    def _auto_check(self):
        if not self.running or not self.auto:
            return
        h_now = self.stitcher.height if self.stitcher else 0
        if h_now > self._auto_h:
            self.same_count = 0
        elif self.fail_streak >= 25:  # lệch quá lâu
            self.toggle_auto()
            self.warn_until = time.time() + 30
            self.status.configure(text="⚠ Không nối tiếp được – cuộn lên 1 chút rồi bấm Tự cuộn lại",
                                  fg="#ffd60a")
            return
        elif self.last_result in ("same", "up", None) or self.fail_streak == 0:
            self.same_count += 1
            if self.same_count >= self.AUTO_END_SAME:
                self.toggle_auto()
                self.warn_until = time.time() + 30
                self.status.configure(text="✓ Đã tới cuối trang (%d px) – bấm OK" % h_now, fg="#30d158")
                return
        self._auto_step()

    def toggle_auto(self):
        self.auto = not self.auto
        self.same_count = 0
        self.btn_auto.configure(text="Dừng cuộn" if self.auto else "Tự cuộn",
                                bg="#ff9f0a" if self.auto else "#3a3a3a")
        if self.auto:
            self.warn_until = 0
            self._auto_step()

    def _close(self):
        self.running = False
        self.auto = False
        w = getattr(self, "worker", None)
        if w is not None and w.is_alive() and w is not threading.current_thread():
            w.join(timeout=3)
        self.border.destroy()
        try:
            self.bar.destroy()
        except Exception:
            pass

    def finish(self):
        if not self.running:
            return
        self._close()
        if self.stitcher is None:
            self.app.capture_done(None)
            return
        arr = self.stitcher.result()
        img = Image.fromarray(np.ascontiguousarray(arr[:, :, ::-1]))
        self.app.capture_done(img)

    def cancel(self):
        if not self.running:
            return
        self._close()
        self.app.capture_done(None)


# ----------------------------------------------------------------------------
# Quay video
# ----------------------------------------------------------------------------
_CURSOR_SHAPE = np.array([[0, 0], [0, 17], [4, 13], [7, 20], [10, 19], [7, 12], [12, 12]], np.float32)


def draw_cursor(frame, ox, oy, scale=1.0, pos=None):
    cx, cy = pos if pos is not None else get_cursor_pos()
    px, py = cx - ox, cy - oy
    h, w = frame.shape[:2]
    if px < -30 or py < -30 or px > w + 30 or py > h + 30:
        return
    pts = (_CURSOR_SHAPE * scale + (px, py)).astype(np.int32)
    cv2.fillPoly(frame, [pts], (255, 255, 255, 255), lineType=cv2.LINE_AA)
    cv2.polylines(frame, [pts], True, (0, 0, 0, 255), max(1, int(scale)), cv2.LINE_AA)


class _FfmpegWriter:
    def __init__(self, path, w, h, fps):
        import imageio_ffmpeg
        self.gen = imageio_ffmpeg.write_frames(
            path, (w, h), pix_fmt_in="bgra", pix_fmt_out="yuv420p", fps=fps,
            codec="libx264", quality=None, macro_block_size=1, ffmpeg_log_level="error",
            output_params=["-preset", "veryfast", "-crf", "23", "-movflags", "+faststart"])
        self.gen.send(None)

    def write(self, frame_bgra):
        self.gen.send(frame_bgra)

    def close(self):
        self.gen.close()


class _CvWriter:
    def __init__(self, path, w, h, fps):
        self.vw = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
        if not self.vw.isOpened():
            raise RuntimeError("Không mở được VideoWriter")

    def write(self, frame_bgra):
        self.vw.write(cv2.cvtColor(frame_bgra, cv2.COLOR_BGRA2BGR))

    def close(self):
        self.vw.release()


def open_video_writer(path, w, h, fps):
    try:
        return _FfmpegWriter(path, w, h, fps)
    except Exception:
        return _CvWriter(path, w, h, fps)


class VideoRecorder:
    def __init__(self, app, rect, fps=30, show_cursor=True):
        self.app = app
        x, y, w, h = rect
        w -= w % 2
        h -= h % 2
        self.rect = (x, y, w, h)
        self.mon = {"left": x, "top": y, "width": w, "height": h}
        self.fps = fps
        self.show_cursor = show_cursor
        self.path = os.path.join(app.video_dir, "%s_%s.mp4" % (FILE_PREFIX, timestamp()))
        self.stop_evt = threading.Event()
        self.paused = False
        self.elapsed = 0.0
        self.error = None
        self.active = True

        self.border = BorderFrame(app.root, self.rect, color="#ff3b30")
        bar = self.bar = tk.Toplevel(app.root)
        bar.overrideredirect(True)
        bar.attributes("-topmost", True)
        bar.configure(bg="#d70015")
        f = tk.Frame(bar, bg="#1f1f1f", padx=8, pady=6)
        f.pack(padx=2, pady=2)
        grip = tk.Label(f, text="⠿", bg="#1f1f1f", fg="#888", font=("Segoe UI", 12), cursor="fleur")
        grip.pack(side="left", padx=(0, 6))
        self.lbl = tk.Label(f, text="● REC 00:00", bg="#1f1f1f", fg="#ff453a",
                            font=("Consolas", 12, "bold"), width=12, anchor="w", cursor="fleur")
        self.lbl.pack(side="left", padx=(0, 8))
        self.btn_pause = bar_button(f, "❚❚ Tạm dừng", self.toggle_pause)
        self.btn_pause.pack(side="left", padx=3)
        dark_button(f, "■  DỪNG & LƯU", self.stop, bg="#d70015").pack(side="left", padx=3)
        for wdg in (grip, self.lbl):
            wdg.bind("<ButtonPress-1>", self._drag_start)
            wdg.bind("<B1-Motion>", self._drag_move)
        lock_size(bar)
        m = monitor_at(app.sct, x + w // 2, y + h // 2)
        bw = bar.winfo_reqwidth()
        bar.geometry("+%d+%d" % (m["left"] + m["width"] - bw - 16, m["top"] + 12))
        exclude_from_capture(bar)

        try:
            self.scale = (_u32.GetDpiForSystem() / 96.0) if IS_WIN else 1.0
        except Exception:
            self.scale = 1.0
        self.thread = threading.Thread(target=self.run, daemon=True)
        app.root.after(250, self.thread.start)
        self._tick()

    def run(self):
        writer = None
        try:
            writer = open_video_writer(self.path, self.mon["width"], self.mon["height"], self.fps)
            interval = 1.0 / self.fps
            with mss.mss() as sct:
                start = time.perf_counter()
                paused_total = 0.0
                pause_start = None
                n = 0
                while n == 0 or not self.stop_evt.is_set():  # luôn ghi ít nhất 1 khung
                    now = time.perf_counter()
                    if self.paused:
                        if pause_start is None:
                            pause_start = now
                        time.sleep(0.05)
                        continue
                    if pause_start is not None:
                        paused_total += now - pause_start
                        pause_start = None
                    frame = np.array(sct.grab(self.mon), dtype=np.uint8)
                    if self.show_cursor:
                        draw_cursor(frame, self.mon["left"], self.mon["top"], self.scale)
                    elapsed = time.perf_counter() - start - paused_total
                    self.elapsed = elapsed
                    due = int(elapsed * self.fps) + 1
                    burst = 0
                    while n < due and burst < self.fps * 2:
                        writer.write(frame)
                        n += 1
                        burst += 1
                    n = max(n, due)
                    wait = start + paused_total + n * interval - time.perf_counter()
                    if wait > 0:
                        time.sleep(wait)
        except Exception as e:
            self.error = str(e)
        finally:
            if writer is not None:
                try:
                    writer.close()
                except Exception as e:
                    self.error = self.error or str(e)
            self.app.q.put(("video_done", self.path, self.error))

    def _tick(self):
        if not self.active:
            return
        s = int(self.elapsed)
        prefix = "❚❚ " if self.paused else "● REC "
        self.lbl.configure(text="%s%02d:%02d" % (prefix, s // 60, s % 60),
                           fg="#ffd60a" if self.paused else "#ff453a")
        self.app.root.after(250, self._tick)

    def _drag_start(self, e):
        self._dx = e.x_root - self.bar.winfo_x()
        self._dy = e.y_root - self.bar.winfo_y()

    def _drag_move(self, e):
        self.bar.geometry("+%d+%d" % (e.x_root - self._dx, e.y_root - self._dy))

    def toggle_pause(self):
        self.paused = not self.paused
        self.btn_pause.configure(text="▶ Tiếp tục" if self.paused else "❚❚ Tạm dừng")

    def stop(self):
        if not self.active:
            return
        self.active = False
        self.stop_evt.set()
        self.border.destroy()
        try:
            self.bar.destroy()
        except Exception:
            pass


# ----------------------------------------------------------------------------
# Vẽ chú thích lên ảnh (dùng khi xuất file) - khử răng cưa bằng supersampling
# ----------------------------------------------------------------------------
_FONT_CACHE = {}


def get_font(size):
    size = max(6, int(size))
    if size in _FONT_CACHE:
        return _FONT_CACHE[size]
    font = None
    for name in ("segoeuib.ttf", "arialbd.ttf", "seguisb.ttf", "arial.ttf",
                 "DejaVuSans-Bold.ttf", "DejaVuSans.ttf"):
        try:
            font = ImageFont.truetype(name, size)
            break
        except Exception:
            continue
    if font is None:
        try:
            font = ImageFont.load_default(size)
        except Exception:
            font = ImageFont.load_default()
    _FONT_CACHE[size] = font
    return font


def arrow_geometry(p0, p1, width):
    L = max(14.0, width * 4.0)
    hw = L * 0.55
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    d = math.hypot(dx, dy) or 1.0
    ux, uy = dx / d, dy / d
    L = min(L, d)
    bx, by = p1[0] - ux * L, p1[1] - uy * L
    nx, ny = -uy, ux
    head = [p1, (bx + nx * hw, by + ny * hw), (bx - nx * hw, by - ny * hw)]
    return (bx + ux * 1, by + uy * 1), head, L, hw


TEXT_PAD = 6


def wrap_text(text, font, max_w):
    """Tách chữ thành các dòng vừa bề rộng max_w (giữ các lần Enter xuống dòng)."""
    out = []
    for para in text.split("\n"):
        if not para:
            out.append("")
            continue
        line = ""
        for word in para.split(" "):
            cand = word if not line else line + " " + word
            if font.getlength(cand) <= max_w:
                line = cand
                continue
            if line:
                out.append(line)
                line = ""
            while len(word) > 1 and font.getlength(word) > max_w:  # từ quá dài -> cắt theo ký tự
                k = len(word)
                while k > 1 and font.getlength(word[:k]) > max_w:
                    k -= 1
                out.append(word[:k])
                word = word[k:]
            line = word
        out.append(line)
    return out


def textbox_layout(sh):
    """-> (x0, y0, x1, y1, các dòng, chiều cao 1 dòng). Khung tự cao thêm nếu chữ dài."""
    font = get_font(sh["size"])
    (ax, ay), (bx, by) = sh["pts"][0], sh["pts"][1]
    x0, y0, x1, y1 = min(ax, bx), min(ay, by), max(ax, bx), max(ay, by)
    lines = wrap_text(sh.get("text", ""), font, max(10, x1 - x0 - 2 * TEXT_PAD))
    asc, desc = font.getmetrics()
    lh = asc + desc
    h = 2 * TEXT_PAD + max(1, len(lines)) * lh
    return x0, y0, x1, max(y1, y0 + h), lines, lh


def shape_bbox(sh):
    t = sh["type"]
    if t == "textbox":
        x0, y0, x1, y1, _l, _h = textbox_layout(sh)
        return (x0, y0, x1, y1)
    if t == "text":
        x, y = sh["pts"][0]
        font = get_font(sh["size"])
        try:
            b = font.getbbox(sh["text"], anchor="la")
            return (x + b[0], y + b[1], x + b[2], y + b[3])
        except Exception:
            return (x, y, x + sh["size"] * len(sh["text"]) * 0.6, y + sh["size"] * 1.2)
    xs = [p[0] for p in sh["pts"]]
    ys = [p[1] for p in sh["pts"]]
    pad = sh.get("width", 2) * (3 if t == "arrow" else 1) + 2
    if t == "arrow":
        pad = max(pad, 16)
    return (min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad)


def draw_shape_pil(base, sh, ss=3):
    """Vẽ 1 hình lên ảnh RGBA `base` (tại chỗ)."""
    t = sh["type"]
    col = ImageColor.getrgb(sh["color"])[:3]
    if t == "text":
        d = ImageDraw.Draw(base)
        x, y = sh["pts"][0]
        d.text((x, y), sh["text"], fill=col + (255,), font=get_font(sh["size"]), anchor="la",
               stroke_width=max(1, int(sh["size"] / 14)),
               stroke_fill=(255, 255, 255, 255) if sum(col) < 380 else (0, 0, 0, 255))
        return base
    if t == "textbox":
        d = ImageDraw.Draw(base)
        x0, y0, _x1, _y1, lines, lh = textbox_layout(sh)
        font = get_font(sh["size"])
        for i, line in enumerate(lines):
            d.text((x0 + TEXT_PAD, y0 + TEXT_PAD + i * lh), line, fill=col + (255,), font=font,
                   stroke_width=max(1, int(sh["size"] / 14)),
                   stroke_fill=(255, 255, 255, 255) if sum(col) < 380 else (0, 0, 0, 255))
        return base
    if t == "redact":  # tô đen kín, không khử răng cưa để che hoàn toàn
        (ax, ay), (bx, by) = sh["pts"][0], sh["pts"][1]
        ImageDraw.Draw(base).rectangle([min(ax, bx), min(ay, by), max(ax, bx), max(ay, by)],
                                       fill=col + (255,))
        return base
    x0, y0, x1, y1 = shape_bbox(sh)
    x0, y0 = max(0, int(math.floor(x0))), max(0, int(math.floor(y0)))
    x1, y1 = min(base.width, int(math.ceil(x1))), min(base.height, int(math.ceil(y1)))
    if x1 - x0 < 1 or y1 - y0 < 1:
        return base
    bw, bh = x1 - x0, y1 - y0
    layer = Image.new("RGBA", (bw * ss, bh * ss), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    w = max(1, int(round(sh["width"] * ss)))

    def P(p):
        return ((p[0] - x0) * ss, (p[1] - y0) * ss)

    if t in ("ellipse", "rect", "highlight"):
        (ax, ay), (bx, by) = P(sh["pts"][0]), P(sh["pts"][1])
        box = [min(ax, bx), min(ay, by), max(ax, bx), max(ay, by)]
        if t == "ellipse":
            d.ellipse(box, outline=col + (255,), width=w)
        elif t == "rect":
            d.rectangle(box, outline=col + (255,), width=w)
        else:
            d.rectangle(box, fill=col + (110,))
    elif t == "arrow":
        p0, p1 = sh["pts"][0], sh["pts"][1]
        neck, head, _L, _hw = arrow_geometry(p0, p1, sh["width"])
        d.line([P(p0), P(neck)], fill=col + (255,), width=w)
        r = w / 2
        a = P(p0)
        d.ellipse([a[0] - r, a[1] - r, a[0] + r, a[1] + r], fill=col + (255,))
        d.polygon([P(p) for p in head], fill=col + (255,))
    elif t == "pen":
        pts = [P(p) for p in sh["pts"]]
        if len(pts) > 1:
            d.line(pts, fill=col + (255,), width=w, joint="curve")
        r = w / 2
        for a in (pts[0], pts[-1]):
            d.ellipse([a[0] - r, a[1] - r, a[0] + r, a[1] + r], fill=col + (255,))
    layer = layer.resize((bw, bh), Image.LANCZOS)
    base.alpha_composite(layer, (x0, y0))
    return base


def render_annotated(image, shapes):
    base = image.convert("RGBA")
    for sh in shapes:
        draw_shape_pil(base, sh)
    return base.convert("RGB")


# ----------------------------------------------------------------------------
# Trình chỉnh sửa ảnh
# ----------------------------------------------------------------------------
def app_data_dir():
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    d = os.path.join(base, APP_NAME)
    os.makedirs(d, exist_ok=True)
    return d


def hide_path(path):
    if IS_WIN:
        try:
            ctypes.windll.kernel32.SetFileAttributesW(path, 0x2)  # FILE_ATTRIBUTE_HIDDEN
        except Exception:
            pass


def new_capture_path(folder, ext):
    base = os.path.join(folder, "%s_%s" % (FILE_PREFIX, timestamp()))
    path, i = base + ext, 2
    while os.path.exists(path):
        path, i = "%s_%d%s" % (base, i, ext), i + 1
    return path


def copy_file_to_clipboard(path):
    """Copy file (vd: video) vào clipboard để Ctrl+V dán vào Zalo, Explorer, Teams..."""
    if not IS_WIN:
        return False
    import struct
    data = struct.pack("<IiiII", 20, 0, 0, 0, 1) + (os.path.abspath(path) + "\0\0").encode("utf-16-le")
    for _ in range(20):
        if _u32.OpenClipboard(None):
            break
        time.sleep(0.05)
    else:
        return False
    try:
        _u32.EmptyClipboard()
        h = _k32.GlobalAlloc(0x0042, len(data))  # GMEM_MOVEABLE | GMEM_ZEROINIT
        if not h:
            return False
        p = _k32.GlobalLock(h)
        ctypes.memmove(p, data, len(data))
        _k32.GlobalUnlock(h)
        if not _u32.SetClipboardData(15, h):  # CF_HDROP
            _k32.GlobalFree(h)
            return False
        return True
    finally:
        _u32.CloseClipboard()


def delete_to_recycle_bin(path):
    """Xoá file vào Thùng rác (khôi phục được nếu lỡ tay). Không được thì xoá hẳn."""
    if not os.path.exists(path):
        return True
    if IS_WIN:
        try:
            class SHFILEOPSTRUCTW(ctypes.Structure):
                _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT),
                            ("pFrom", wintypes.LPCWSTR), ("pTo", wintypes.LPCWSTR),
                            ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL),
                            ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]
            op = SHFILEOPSTRUCTW()
            op.wFunc = 3  # FO_DELETE
            op.pFrom = os.path.abspath(path) + "\0"
            # FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI
            op.fFlags = 0x0040 | 0x0010 | 0x0004 | 0x0400
            if ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op)) == 0 and not os.path.exists(path):
                return True
        except Exception:
            pass
    try:
        os.remove(path)
        return True
    except Exception:
        return False


def delete_many_to_recycle_bin(paths):
    """Xoá nhiều file 1 lần vào Thùng rác. Trả về danh sách file đã xoá được."""
    paths = [os.path.abspath(p) for p in paths if os.path.exists(p)]
    if not paths:
        return []
    if IS_WIN:
        try:
            class SHFILEOPSTRUCTW(ctypes.Structure):
                _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT),
                            ("pFrom", wintypes.LPCWSTR), ("pTo", wintypes.LPCWSTR),
                            ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL),
                            ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]
            for i in range(0, len(paths), 200):
                op = SHFILEOPSTRUCTW()
                op.wFunc = 3  # FO_DELETE
                op.pFrom = "\0".join(paths[i:i + 200]) + "\0"
                op.fFlags = 0x0040 | 0x0010 | 0x0004 | 0x0400
                ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
        except Exception:
            pass
    for p in paths:  # cái nào chưa xoá được thì xoá từng cái
        if os.path.exists(p):
            delete_to_recycle_bin(p)
    return [p for p in paths if not os.path.exists(p)]


# Tự xoá ảnh/video cũ
CLEANUP_CHOICES = [("Không tự xoá", 0), ("7 ngày", 7), ("14 ngày", 14), ("30 ngày", 30),
                   ("60 ngày", 60), ("90 ngày", 90)]
CLEANUP_DEFAULT_DAYS = 30
CLEANUP_EXTS = (".png", ".jpg", ".jpeg", ".mp4")


def find_old_captures(folders, days, skip=(), now=None):
    """Tìm ảnh/video do app tạo (tên bắt đầu bằng tên app_) cũ hơn `days` ngày.

    Trả về danh sách (file chính, [file phụ trong .edit]). Chỉ đụng tới file của app,
    không đụng file khác người dùng tự bỏ vào thư mục."""
    if days <= 0:
        return []
    prefixes = tuple({(FILE_PREFIX + "_").lower(), (APP_NAME + "_").lower()})
    limit = (now or time.time()) - days * 86400
    skip = {os.path.normcase(os.path.abspath(p)) for p in skip if p}
    out = []
    for folder in folders:
        try:
            names = os.listdir(folder)
        except Exception:
            continue
        for name in names:
            low = name.lower()
            if not low.startswith(prefixes) or not low.endswith(CLEANUP_EXTS):
                continue
            p = os.path.join(folder, name)
            try:
                if not os.path.isfile(p) or os.path.getmtime(p) >= limit:
                    continue
            except Exception:
                continue
            if os.path.normcase(os.path.abspath(p)) in skip:
                continue
            d = os.path.join(folder, ".edit")
            extra = [q for q in (os.path.join(d, name), os.path.join(d, name + ".json")) if os.path.exists(q)]
            out.append((p, extra))
    return out


def reveal_in_folder(path):
    if IS_WIN:
        import subprocess
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    else:
        open_path(os.path.dirname(path))


def load_settings():
    import json
    try:
        with open(os.path.join(app_data_dir(), "settings.json"), encoding="utf-8") as fh:
            d = json.load(fh)
            return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save_settings(d):
    import json
    try:
        with open(os.path.join(app_data_dir(), "settings.json"), "w", encoding="utf-8") as fh:
            json.dump(d, fh, ensure_ascii=False, indent=1)
    except Exception:
        pass


class History:
    """Lịch sử ảnh/video đã chụp (lưu trong %APPDATA%\\QuickSnap\\history.json)."""
    SHOW = 10

    def __init__(self):
        self.file = os.path.join(app_data_dir(), "history.json")
        self.entries = []
        try:
            import json
            with open(self.file, encoding="utf-8") as fh:
                self.entries = [e for e in json.load(fh) if isinstance(e, dict) and "path" in e]
        except Exception:
            self.entries = []

    def _save(self):
        import json
        try:
            with open(self.file, "w", encoding="utf-8") as fh:
                json.dump(self.entries[:200], fh, ensure_ascii=False, indent=1)
        except Exception:
            pass

    def add(self, path, kind):
        self.entries = [e for e in self.entries if e["path"] != path]
        entry = {"path": path, "kind": kind, "time": time.time()}
        self.entries.insert(0, entry)
        self._save()
        return entry

    def remove(self, path):
        self.entries = [e for e in self.entries if e["path"] != path]
        self._save()

    def items(self):
        return [e for e in self.entries if os.path.exists(e["path"])][:self.SHOW]


TOOLS = [
    ("rect", "▭ Chữ nhật", "R"),
    ("ellipse", "◯ Khoanh tròn", "E"),
    ("pen", "✎ Bút vẽ", "P"),
    ("arrow", "➜ Mũi tên", "A"),
    ("redact", "■ Tô đen", "B"),
    ("highlight", "▮ Tô sáng", "H"),
    ("textbox", "T Thêm chữ", "T"),
    ("crop", "⌗ Cắt ảnh", "K"),
]
PRESET_COLORS = ["#ff3b30", "#ff9500", "#ffcc00", "#34c759", "#007aff", "#af52de", "#000000", "#ffffff"]
ZOOMS = ["Vừa cửa sổ", "Vừa chiều ngang", "25%", "50%", "75%", "100%", "150%", "200%"]


# Bảng màu giao diện (tông sáng, mềm như Snagit)
UI_CANVAS = "#e9ebef"   # nền khung xem ảnh
UI_SIDE = "#fafbfc"     # cột công cụ
UI_LINE = "#d5d9df"     # đường kẻ phân cách
UI_STRIP = "#eef0f3"    # thanh "Gần đây"
UI_TEXT_SUB = "#7a818c"
IMG_PAD = 28            # khoảng đệm quanh ảnh


def make_shadow_frame(W, H, pad=IMG_PAD, bg=UI_CANVAS):
    """Nền có bóng đổ mềm + viền mảnh quanh ảnh kích thước W x H."""
    from PIL import ImageFilter
    fw, fh = W + 2 * pad, H + 2 * pad
    frame = Image.new("RGB", (fw, fh), bg)
    sh = Image.new("L", (fw, fh), 0)
    ImageDraw.Draw(sh).rectangle([pad - 1, pad + 3, pad + W + 1, pad + H + 5], fill=95)
    sh = sh.filter(ImageFilter.GaussianBlur(9))
    frame.paste(Image.new("RGB", (fw, fh), (60, 66, 75)), (0, 0), sh)
    ImageDraw.Draw(frame).rectangle([pad - 1, pad - 1, pad + W, pad + H], outline=(200, 205, 212))
    return frame


class Editor:
    THUMB = (120, 68)

    def __init__(self, app):
        self.app = app
        self.entry = None
        self.mode = None
        self.image = Image.new("RGB", (10, 10), "#5b5f66")
        self.path = None
        self.dirty = False
        self.alive = True
        self._thumbs = {}
        self.cap = None
        self.playing = False
        self._play_job = None
        self._seek_internal = False
        self.ox = self.oy = 0
        self._disp_base = None
        self._photo_plain = None
        self.img_item = None
        self.disp_size = (1, 1)
        self.shapes = []
        self.undo_stack = []
        self.redo_stack = []
        self.next_id = 1
        self.zoom = 1.0
        self.zoom_mode = "auto"
        self.sel = None
        self.cur = None
        self.drag_last = None
        self.drag_mode = None
        self._text_edit = None
        self._editing_shape = None
        self.moved = False
        self.photo = None
        self.clip_synced = False
        self._clip_job = None

        self.tool = tk.StringVar(value="ellipse")
        self.color = "#ff3b30"
        self.width = tk.IntVar(value=4)
        self.font_size = tk.IntVar(value=28)
        self.zoom_var = tk.StringVar(value="Vừa cửa sổ")

        win = self.win = tk.Toplevel(app.root)
        self._update_title()
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        ww, wh = int(sw * 0.8), int(sh * 0.85)
        win.geometry("%dx%d+%d+%d" % (ww, wh, (sw - ww) // 2, max(0, (sh - wh) // 2 - 20)))
        win.minsize(760, 420)
        win.protocol("WM_DELETE_WINDOW", self.close)
        win.after(10, lambda: keep_size_on_dpi_change(win))

        self.status = ttk.Label(win, text="", anchor="w", padding=(8, 3))
        self.status.pack(side="bottom", fill="x")
        self.recent = tk.Frame(win, bg=UI_STRIP, height=self.THUMB[1] + 40)
        tk.Frame(win, bg=UI_LINE, height=1).pack(side="bottom", fill="x")
        self.recent.pack(side="bottom", fill="x")
        self.main_area = ttk.Frame(win)
        self.main_area.pack(fill="both", expand=True)
        self.tools_frame = tk.Frame(self.main_area, bg=UI_SIDE, highlightthickness=1,
                                    highlightbackground=UI_LINE, highlightcolor=UI_LINE)
        self.tools_frame.pack(side="left", fill="y")
        self._build_toolbar()
        self.video_bar = ttk.Frame(self.main_area, padding=(6, 6))
        self._build_video_bar()
        self.body_holder = ttk.Frame(self.main_area)
        self.body_holder.pack(fill="both", expand=True)
        body = self.img_frame = ttk.Frame(self.body_holder)
        body.pack(fill="both", expand=True)
        self.player_frame = tk.Frame(self.body_holder, bg="#111")
        self.vcanvas = tk.Canvas(self.player_frame, bg="#111", highlightthickness=0)
        self.vcanvas.pack(fill="both", expand=True)
        self.vcanvas.bind("<Configure>", lambda e: self._redraw_video_frame())
        self.vcanvas.bind("<Button-1>", lambda e: self.toggle_play())
        self.canvas = tk.Canvas(body, bg=UI_CANVAS, highlightthickness=0, cursor="crosshair")
        vs = ttk.Scrollbar(body, orient="vertical", command=self.canvas.yview)
        hs = ttk.Scrollbar(body, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        vs.grid(row=0, column=1, sticky="ns")
        hs.grid(row=1, column=0, sticky="ew")
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)

        c = self.canvas
        c.bind("<ButtonPress-1>", self.on_press)
        c.bind("<B1-Motion>", self.on_drag)
        c.bind("<ButtonRelease-1>", self.on_release)
        c.bind("<Double-Button-1>", self.on_double)
        c.bind("<Motion>", self.on_hover)
        c.bind("<MouseWheel>", self.on_wheel)
        c.bind("<Button-4>", lambda e: c.yview_scroll(-3, "units"))
        c.bind("<Button-5>", lambda e: c.yview_scroll(3, "units"))
        c.bind("<Configure>", self.on_resize)
        win.bind("<Control-z>", lambda e: None if self._typing() else self.undo())
        win.bind("<Control-y>", lambda e: None if self._typing() else self.redo())
        win.bind("<Control-c>", lambda e: None if self._typing() else self.copy())
        win.bind("<Left>", lambda e: None if self._typing() else self.step_recent(1))
        win.bind("<Right>", lambda e: None if self._typing() else self.step_recent(-1))
        win.bind("<space>", lambda e: self.toggle_play() if self.mode == "video" and not self._typing() else None)
        win.bind("<Control-s>", lambda e: self.save())
        win.bind("<Control-S>", lambda e: self.save_as())
        win.bind("<Control-Shift-s>", lambda e: self.save_as())
        win.bind("<Delete>", lambda e: None if self._typing() else self.delete_selected())
        win.bind("<Escape>", lambda e: None if self._typing() else self.deselect())
        for key, _label, sc in [(t[0], t[1], t[2]) for t in TOOLS]:
            win.bind("<Key-%s>" % sc.lower(),
                     lambda e, k=key: None if self._typing() else self.tool.set(k))
        self.tool.trace_add("write", lambda *a: self.on_tool_change())
        self.width.trace_add("write", lambda *a: self.on_style_change("width"))
        self.font_size.trace_add("write", lambda *a: self.on_style_change("size"))

        self.refresh_recent()
        win.update_idletasks()
        side_w = self.tools_frame.winfo_reqwidth()
        side_h = self.tools_frame.winfo_reqheight()
        win.minsize(side_w + 480, side_h + self.THUMB[1] + 90)

    def bring_to_front(self):
        win = self.win
        if win.state() == "iconic":
            win.deiconify()
        win.attributes("-topmost", True)
        win.lift()
        win.focus_force()
        win.after(400, lambda: win.attributes("-topmost", False))

    # ---------- lịch sử / chuyển ảnh
    def load_entry(self, entry):
        if self.entry is not None and entry["path"] == self.entry["path"]:
            return
        self.commit()
        self._stop_video()
        self.entry = entry
        self.path = entry["path"]
        self.sel = None
        self.cur = None
        self.undo_stack.clear()
        self.redo_stack.clear()
        if entry["kind"] == "video":
            self._show_mode("video")
            self._open_video(entry["path"])
        else:
            self._show_mode("image")
            base_p, json_p = self._edit_paths(entry["path"])
            src = base_p if os.path.exists(base_p) else entry["path"]
            try:
                with Image.open(src) as im:
                    self.image = im.convert("RGB")
            except Exception as e:
                self.image = Image.new("RGB", (400, 200), "#5b5f66")
                self._set_status("Không mở được ảnh: %s" % e)
            self.shapes = []
            if os.path.exists(json_p):
                try:
                    import json
                    with open(json_p, encoding="utf-8") as fh:
                        self.shapes = json.load(fh)
                    for sh in self.shapes:
                        sh["pts"] = [tuple(p) for p in sh["pts"]]
                except Exception:
                    self.shapes = []
            self.dirty = False
            self.clip_synced = bool(entry.get("_copied"))
            entry.pop("_copied", None)
            self.refresh()                       # hiện ngay ảnh mới
            self.win.after(30, self._initial_fit)  # rồi canh vừa cửa sổ
        self._update_title()
        self.refresh_recent()

    def _edit_paths(self, path):
        d = os.path.join(os.path.dirname(path), ".edit")
        name = os.path.basename(path)
        return os.path.join(d, name), os.path.join(d, name + ".json")

    def commit(self):
        self._finish_text_edit()
        """Tự lưu ảnh đang chỉnh (ảnh đã vẽ -> file chính; ảnh gốc + nét vẽ -> thư mục .edit)."""
        if self.mode != "image" or self.entry is None or not self.dirty:
            return
        try:
            import json
            base_p, json_p = self._edit_paths(self.entry["path"])
            d = os.path.dirname(base_p)
            if not os.path.isdir(d):
                os.makedirs(d, exist_ok=True)
                hide_path(d)
            self.image.save(base_p, compress_level=1)
            with open(json_p, "w", encoding="utf-8") as fh:
                json.dump(self.shapes, fh, ensure_ascii=False)
            self.rendered().save(self.entry["path"], compress_level=3)
            self.dirty = False
            self._update_title()
        except Exception as e:
            self._set_status("Tự lưu lỗi: %s" % e)

    def step_recent(self, d):
        items = self.app.history.items()
        paths = [e["path"] for e in items]
        if not self.entry or self.entry["path"] not in paths:
            return
        i = paths.index(self.entry["path"]) + d
        if 0 <= i < len(items):
            self.load_entry(items[i])

    def _show_mode(self, mode):
        if mode == self.mode:
            return
        self.mode = mode
        if mode == "video":
            self.tools_frame.pack_forget()
            self.img_frame.pack_forget()
            self.video_bar.pack(side="top", fill="x", before=self.body_holder)
            self.player_frame.pack(fill="both", expand=True)
        else:
            self.video_bar.pack_forget()
            self.player_frame.pack_forget()
            self.tools_frame.pack(side="left", fill="y", before=self.body_holder)
            self.img_frame.pack(fill="both", expand=True)

    def _thumb(self, entry):
        path = entry["path"]
        try:
            key = (path, os.path.getmtime(path))
        except OSError:
            key = (path, 0)
        if key in self._thumbs:
            return self._thumbs[key]
        tw, th = self.THUMB
        canvas = Image.new("RGB", (tw, th), "#1e1f22")
        try:
            if entry["kind"] == "video":
                cap = cv2.VideoCapture(path)
                ok, fr = cap.read()
                cap.release()
                im = Image.fromarray(cv2.cvtColor(fr, cv2.COLOR_BGR2RGB)) if ok else Image.new("RGB", (16, 9), "#333")
            else:
                im = Image.open(path)
                im.draft("RGB", (tw * 2, th * 2))
                im = im.convert("RGB")
            im.thumbnail((tw, th), Image.LANCZOS)
            canvas.paste(im, ((tw - im.width) // 2, (th - im.height) // 2))
        except Exception:
            pass
        if entry["kind"] == "video":
            d = ImageDraw.Draw(canvas, "RGBA")
            cx, cy, r = tw // 2, th // 2, 16
            d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(0, 0, 0, 150))
            d.polygon([(cx - 5, cy - 9), (cx - 5, cy + 9), (cx + 10, cy)], fill=(255, 255, 255, 255))
            d.rectangle([0, th - 16, 44, th], fill=(215, 0, 21, 230))
            d.text((5, th - 14), "VIDEO", fill=(255, 255, 255, 255), font=get_font(10))
        photo = ImageTk.PhotoImage(canvas)
        self._thumbs = {k: v for k, v in self._thumbs.items() if k[0] != path}
        self._thumbs[key] = photo
        return photo

    def refresh_recent(self):
        bar = self.recent
        for w in bar.winfo_children():
            w.destroy()
        tk.Label(bar, text="GẦN\nĐÂY", bg=UI_STRIP, fg=UI_TEXT_SUB,
                 font=("Segoe UI", 9, "bold")).pack(side="left", padx=(12, 6))
        right = tk.Frame(bar, bg=UI_STRIP)
        right.pack(side="right", padx=10)
        ttk.Button(right, text="📂 Thư mục ảnh", command=lambda: open_path(self.app.pic_dir)).pack(pady=2, fill="x")
        ttk.Button(right, text="📂 Thư mục video", command=lambda: open_path(self.app.video_dir)).pack(pady=2, fill="x")
        # vùng thumbnail cuộn ngang được (lăn chuột) nếu cửa sổ hẹp
        strip_h = self.THUMB[1] + 34
        mid = tk.Frame(bar, bg=UI_STRIP)
        mid.pack(side="left", fill="x", expand=True)
        cv = tk.Canvas(mid, bg=UI_STRIP, height=strip_h, highlightthickness=0)
        hs = ttk.Scrollbar(mid, orient="horizontal", command=cv.xview)
        cv.configure(xscrollcommand=hs.set)
        cv.pack(side="top", fill="x", expand=True)
        hs.pack(side="top", fill="x")
        holder = tk.Frame(cv, bg=UI_STRIP)
        cv.create_window(0, 0, window=holder, anchor="nw")
        holder.bind("<Configure>", lambda e: cv.configure(scrollregion=(0, 0, e.width, strip_h)))

        def wheel(ev):
            cv.xview_scroll(-1 if (ev.delta > 0 or getattr(ev, "num", 0) == 4) else 1, "units")
        items = self.app.history.items()
        if not items:
            tk.Label(holder, text="Chưa có ảnh/video nào", bg=UI_STRIP, fg=UI_TEXT_SUB,
                     font=("Segoe UI", 9)).pack(side="left", pady=30)
        for e in items:
            selected = self.entry is not None and e["path"] == self.entry["path"]
            border = "#0a84ff" if selected else "#ffffff"
            f = tk.Frame(holder, bg=border, padx=3, pady=3, cursor="hand2",
                         highlightthickness=1, highlightbackground="#0a84ff" if selected else UI_LINE)
            f.pack(side="left", padx=3, pady=6)
            img_lbl = tk.Label(f, image=self._thumb(e), bg="#1e1f22", bd=0)
            img_lbl.pack()
            # nút ✕ ở góc trên phải mỗi ảnh -> xoá ảnh
            x_btn = tk.Label(f, text="✕", bg="#c62828", fg="white", font=("Segoe UI", 9, "bold"),
                             padx=5, pady=0, cursor="hand2")
            x_btn.place(relx=1.0, x=-3, y=3, anchor="ne")
            x_btn.bind("<Button-1>", lambda ev, en=e: self.delete_entry(en))
            x_btn.bind("<Enter>", lambda ev, b=x_btn: b.configure(bg="#ff1744"))
            x_btn.bind("<Leave>", lambda ev, b=x_btn: b.configure(bg="#c62828"))
            t = datetime.datetime.fromtimestamp(e.get("time", 0)).strftime("%H:%M:%S  %d/%m")
            cap_lbl = tk.Label(f, text=t, bg=border, fg="white" if selected else "#4a5260",
                               font=("Segoe UI", 8))
            cap_lbl.pack(fill="x")
            for w in (f, img_lbl, cap_lbl):
                w.bind("<Button-1>", lambda ev, en=e: self.load_entry(en))
                w.bind("<Button-3>", lambda ev, en=e: self._recent_menu(ev, en))
                w.bind("<MouseWheel>", wheel)
        cv.bind("<MouseWheel>", wheel)

    def _recent_menu(self, ev, entry):
        m = tk.Menu(self.win, tearoff=0)
        m.add_command(label="Mở", command=lambda: self.load_entry(entry))
        m.add_command(label="Mở thư mục chứa file", command=lambda: reveal_in_folder(entry["path"]))
        m.add_separator()
        m.add_command(label="Ẩn khỏi danh sách (file vẫn giữ)", command=lambda: self._hide_entry(entry))
        m.add_command(label="Xoá ảnh/video này", command=lambda: self.delete_entry(entry))
        m.tk_popup(ev.x_root, ev.y_root)

    def _hide_entry(self, entry, delete=False):
        is_current = self.entry is not None and self.entry["path"] == entry["path"]
        if is_current:
            if not delete:
                self.commit()
            self._stop_video()
            self.entry = None
            self.dirty = False
        self.app.history.remove(entry["path"])
        if delete:
            ok = delete_to_recycle_bin(entry["path"])
            base_p, json_p = self._edit_paths(entry["path"])
            for p in (base_p, json_p):
                delete_to_recycle_bin(p)
            self._thumbs = {k: v for k, v in self._thumbs.items() if k[0] != entry["path"]}
            self._set_status("✓ Đã xoá %s (nằm trong Thùng rác)" % os.path.basename(entry["path"]) if ok
                             else "Không xoá được file (có thể đang mở ở chương trình khác)")
        if is_current:
            items = self.app.history.items()
            if items:
                self.load_entry(items[0])
                return
            self._show_empty()
        self.refresh_recent()

    def delete_entry(self, entry):
        """Xoá hẳn ảnh/video khỏi danh sách và khỏi ổ đĩa (vào Thùng rác)."""
        self._hide_entry(entry, delete=True)

    def _show_empty(self):
        """Không còn ảnh nào -> để trống khung hiển thị."""
        self._show_mode("image")
        self.path = None
        self.image = Image.new("RGB", (480, 270), "#ffffff")
        self.shapes = []
        self.sel = None
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.clip_synced = True  # không copy ảnh trống
        self.refresh()
        self.canvas.create_text(self.canvas.winfo_width() // 2, self.canvas.winfo_height() // 2,
                                text="Chưa có ảnh – nhấn %s để chụp" % HOTKEY_NAME, fill="#8a919c",
                                font=("Segoe UI", 14))
        self._update_title()
        self._set_status("")

    # ---------- trình phát video
    def _build_video_bar(self):
        b = self.video_bar
        self.btn_play = dark_button(b, "▶  Phát", self.toggle_play, bg="#0a84ff")
        self.btn_play.pack(side="left")
        self.vpos = tk.DoubleVar(value=0)
        self.vscale = ttk.Scale(b, from_=0, to=1, variable=self.vpos, command=self._on_seek)
        self.vscale.pack(side="left", fill="x", expand=True, padx=10)
        self.vtime = ttk.Label(b, text="00:00 / 00:00", width=14)
        self.vtime.pack(side="left")
        dark_button(b, "📋 Copy file video", self.copy).pack(side="left", padx=(10, 3))
        dark_button(b, "Mở bằng trình phát", lambda: open_path(self.path)).pack(side="left", padx=3)
        dark_button(b, "Mở thư mục", lambda: reveal_in_folder(self.path)).pack(side="left", padx=3)

    def _open_video(self, path):
        self.cap = cv2.VideoCapture(path)
        self.vfps = self.cap.get(cv2.CAP_PROP_FPS) or 15.0
        self.vn = max(1, int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT)))
        self.vscale.configure(to=max(1, self.vn - 1))
        self.vframe = None
        self.btn_play.configure(text="▶  Phát")
        self._read_show()
        self._set_status("Video: %s" % path)

    def _fmt(self, sec):
        sec = int(sec)
        return "%02d:%02d" % (sec // 60, sec % 60)

    def _read_show(self):
        if self.cap is None:
            return False
        ok, fr = self.cap.read()
        if not ok:
            return False
        self.vframe = fr
        idx = int(self.cap.get(cv2.CAP_PROP_POS_FRAMES))
        self._seek_internal = True
        self.vpos.set(idx)
        self._seek_internal = False
        self.vtime.configure(text="%s / %s" % (self._fmt(idx / self.vfps), self._fmt(self.vn / self.vfps)))
        self._redraw_video_frame()
        return True

    def _redraw_video_frame(self):
        fr = getattr(self, "vframe", None)
        if fr is None or self.mode != "video":
            return
        cw, ch = max(10, self.vcanvas.winfo_width()), max(10, self.vcanvas.winfo_height())
        h, w = fr.shape[:2]
        s = min(cw / w, ch / h)
        nw, nh = max(1, int(w * s)), max(1, int(h * s))
        rgb = cv2.cvtColor(cv2.resize(fr, (nw, nh), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2RGB)
        self.vphoto = ImageTk.PhotoImage(Image.fromarray(rgb))
        self.vcanvas.delete("all")
        self.vcanvas.create_image(cw // 2, ch // 2, image=self.vphoto)
        if not self.playing:
            self.vcanvas.create_oval(cw // 2 - 34, ch // 2 - 34, cw // 2 + 34, ch // 2 + 34,
                                     fill="#000000", outline="", stipple="gray50")
            self.vcanvas.create_polygon(cw // 2 - 10, ch // 2 - 18, cw // 2 - 10, ch // 2 + 18,
                                        cw // 2 + 20, ch // 2, fill="white")

    def toggle_play(self):
        if self.cap is None:
            return
        if self.playing:
            self.playing = False
            self.btn_play.configure(text="▶  Phát")
            self._redraw_video_frame()
            return
        if int(self.cap.get(cv2.CAP_PROP_POS_FRAMES)) >= self.vn - 1:
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
        self.playing = True
        self.btn_play.configure(text="❚❚  Dừng")
        self._play_loop()

    def _play_loop(self):
        self._play_job = None
        if not self.playing or not self.alive:
            return
        t0 = time.perf_counter()
        if not self._read_show():
            self.playing = False
            self.btn_play.configure(text="▶  Phát")
            self._redraw_video_frame()
            return
        spent = (time.perf_counter() - t0) * 1000
        self._play_job = self.win.after(max(1, int(1000 / self.vfps - spent)), self._play_loop)

    def _on_seek(self, v):
        if self._seek_internal or self.cap is None:
            return
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, int(float(v)))
        self._read_show()

    def _stop_video(self):
        self.playing = False
        if self._play_job:
            self.win.after_cancel(self._play_job)
            self._play_job = None
        if self.cap is not None:
            self.cap.release()
            self.cap = None
        self.vframe = None

    # ---------- UI
    def _build_toolbar(self):
        """Cột công cụ dọc bên trái."""
        BG = UI_SIDE
        side = self.tools_frame
        st = ttk.Style(self.win)
        st.configure("Side.Toolbutton", anchor="w", padding=(8, 5), font=("Segoe UI", 10))

        def title(text):
            tk.Label(side, text=text, bg=BG, fg="#8a8f98", font=("Segoe UI", 8, "bold"),
                     anchor="w").pack(fill="x", padx=12, pady=(10, 3))

        title("CÔNG CỤ")
        grid = tk.Frame(side, bg=BG)
        grid.pack(fill="x", padx=8)
        for i, (key, label, sc) in enumerate(TOOLS):
            ttk.Radiobutton(grid, text=label, value=key, variable=self.tool,
                            style="Side.Toolbutton").grid(row=i // 2, column=i % 2, padx=1, pady=1, sticky="ew")
        # 2 cột rộng bằng nhau, tự giãn theo chữ dài nhất -> không bao giờ bị cắt chữ
        grid.columnconfigure(0, weight=1, uniform="tools")
        grid.columnconfigure(1, weight=1, uniform="tools")

        title("MÀU")
        crow = tk.Frame(side, bg=BG)
        crow.pack(fill="x", padx=12)
        self.color_btn = tk.Button(crow, width=4, height=2, bg=self.color, activebackground=self.color,
                                   relief="groove", command=self.pick_color, cursor="hand2")
        self.color_btn.pack(side="left", padx=(0, 8))
        pal = tk.Frame(crow, bg=BG)
        pal.pack(side="left")
        for i, col in enumerate(PRESET_COLORS):
            tk.Button(pal, bg=col, activebackground=col, width=2, relief="flat", bd=1,
                      command=lambda c=col: self.set_color(c), cursor="hand2").grid(
                row=i // 4, column=i % 4, padx=1, pady=1)

        title("NÉT VẼ  /  CỠ CHỮ")
        srow = tk.Frame(side, bg=BG)
        srow.pack(fill="x", padx=12)
        tk.Label(srow, text="Nét", bg=BG).pack(side="left")
        ttk.Spinbox(srow, from_=1, to=30, width=3, textvariable=self.width).pack(side="left", padx=(4, 12))
        tk.Label(srow, text="Chữ", bg=BG).pack(side="left")
        ttk.Spinbox(srow, from_=8, to=200, width=4, textvariable=self.font_size).pack(side="left", padx=4)

        title("XEM")
        vrow = tk.Frame(side, bg=BG)
        vrow.pack(fill="x", padx=12)
        zc = ttk.Combobox(vrow, values=ZOOMS, textvariable=self.zoom_var, width=16, state="readonly")
        zc.pack(side="left", fill="x", expand=True)
        zc.bind("<<ComboboxSelected>>", lambda e: self.set_zoom_choice(self.zoom_var.get()))
        urow = tk.Frame(side, bg=BG)
        urow.pack(fill="x", padx=12, pady=(6, 0))
        ttk.Button(urow, text="↶ Hoàn tác", command=self.undo).pack(side="left", fill="x", expand=True)
        ttk.Button(urow, text="↷ Làm lại", command=self.redo).pack(side="left", fill="x", expand=True, padx=(4, 0))

        # Nút hành động ở đáy cột
        act = tk.Frame(side, bg=BG)
        act.pack(side="bottom", fill="x", padx=10, pady=10)
        dark_button(act, "📋  Copy (Ctrl+C)", self.copy, bg="#0a84ff", big=True).pack(side="bottom", fill="x", pady=(4, 0))
        ttk.Button(act, text="Lưu thành file...", command=self.save_as).pack(side="bottom", fill="x")

    def _update_title(self):
        if not self.path or self.entry is None:
            self.win.title(DISPLAY_NAME)
            return
        kind = "Video quay" if self.entry.get("kind") == "video" else "Ảnh chụp"
        when = datetime.datetime.fromtimestamp(self.entry.get("time", time.time())).strftime("%H:%M:%S – %d/%m/%Y")
        self.win.title("%s  •  %s lúc %s" % (DISPLAY_NAME, kind, when))

    def _set_status(self, msg=""):
        if self.mode == "video":
            self.status.configure(text=msg)
            return
        base = "%d × %d px   |   Zoom %d%%" % (self.image.width, self.image.height, round(self.zoom * 100))
        self.status.configure(text=base + ("   |   " + msg if msg else ""))

    # ---------- zoom / hiển thị
    def _fit_scale(self, mode):
        cw = max(50, self.canvas.winfo_width() - 2 * IMG_PAD - 4)
        ch = max(50, self.canvas.winfo_height() - 2 * IMG_PAD - 4)
        iw, ih = self.image.size
        if mode == "fitw":
            return min(1.0, cw / iw)
        return min(1.0, cw / iw, ch / ih)

    def _initial_fit(self):
        iw, ih = self.image.size
        if ih > iw * 2.2:
            self.set_zoom_choice("Vừa chiều ngang")
        else:
            self.set_zoom_choice("Vừa cửa sổ")

    def set_zoom_choice(self, choice):
        self.zoom_var.set(choice)
        if choice == "Vừa cửa sổ":
            self.zoom_mode = "fit"
            self.zoom = self._fit_scale("fit")
        elif choice == "Vừa chiều ngang":
            self.zoom_mode = "fitw"
            self.zoom = self._fit_scale("fitw")
        else:
            self.zoom_mode = None
            self.zoom = int(choice.rstrip("%")) / 100.0
        self.refresh()

    def on_resize(self, _e):
        if self.zoom_mode in ("fit", "fitw"):
            z = self._fit_scale(self.zoom_mode)
            if abs(z - self.zoom) > 0.005:
                self.zoom = z
                if getattr(self, "_rs_job", None):
                    self.win.after_cancel(self._rs_job)
                self._rs_job = self.win.after(120, self.refresh)
                return
        self._place()  # chỉ cần căn lại giữa

    def _place(self):
        """Căn ảnh vào giữa vùng hiển thị (khi ảnh nhỏ hơn khung)."""
        c = self.canvas
        if self.img_item is None:
            return
        W, H = self.disp_size
        cw, ch = max(1, c.winfo_width()), max(1, c.winfo_height())
        ox, oy = max(IMG_PAD, (cw - W) // 2), max(IMG_PAD, (ch - H) // 2)
        dx, dy = ox - self.ox, oy - self.oy
        if dx or dy:
            c.move("all", dx, dy)
            self.ox, self.oy = ox, oy
        c.configure(scrollregion=(0, 0, max(W + 2 * IMG_PAD, cw), max(H + 2 * IMG_PAD, ch)))

    def on_wheel(self, e):
        if e.state & 0x0004:  # Ctrl -> zoom
            f = 1.25 if e.delta > 0 else 0.8
            self.zoom = min(4.0, max(0.05, self.zoom * f))
            self.zoom_mode = None
            self.zoom_var.set("%d%%" % round(self.zoom * 100))
            self.refresh()
        elif e.state & 0x0001:  # Shift -> ngang
            self.canvas.xview_scroll(int(-e.delta / 120) * 3, "units")
        else:
            self.canvas.yview_scroll(int(-e.delta / 120) * 3, "units")

    def refresh(self):
        s = self.zoom
        iw, ih = self.image.size
        W, H = max(1, int(round(iw * s))), max(1, int(round(ih * s)))
        if (W, H) == (iw, ih):
            disp = self.image
        elif s < 1:
            disp = self.image.resize((W, H), Image.LANCZOS, reducing_gap=2.0)
        else:
            disp = self.image.resize((W, H), Image.BICUBIC)  # phóng to mượt hơn
        self._disp_base = disp.convert("RGB")
        self._frame_bg = make_shadow_frame(W, H)          # bóng đổ + viền (tính 1 lần)
        self._photo_plain = None
        c = self.canvas
        c.delete("all")
        self.disp_size = (W, H)
        cw, ch = max(1, c.winfo_width()), max(1, c.winfo_height())
        self.ox = max(IMG_PAD, (cw - W) // 2)
        self.oy = max(IMG_PAD, (ch - H) // 2)
        self.img_item = c.create_image(self.ox - IMG_PAD, self.oy - IMG_PAD, anchor="nw")
        c.configure(scrollregion=(0, 0, max(W + 2 * IMG_PAD, cw), max(H + 2 * IMG_PAD, ch)))
        self.draw_shapes()
        self._set_status("✓ Ảnh đã nằm trong clipboard – Ctrl+V để dán" if self.clip_synced else "")

    def _schedule_clip(self):
        if self.mode != "image" or self.clip_synced or not self.app.autocopy_var.get():
            return
        if self._clip_job:
            self.win.after_cancel(self._clip_job)
        self._clip_job = self.win.after(500, self._auto_copy)

    def _auto_copy(self):
        self._clip_job = None
        if copy_image_to_clipboard(self.rendered()):
            self.clip_synced = True
            self._set_status("✓ Ảnh đã nằm trong clipboard – Ctrl+V để dán")

    def _compose_photo(self, with_shapes=True):
        """Ảnh hiển thị = nền bóng đổ + ảnh + (nét vẽ khử răng cưa, vẽ bằng PIL)."""
        if getattr(self, "_disp_base", None) is None:
            return None
        img = self._disp_base
        if with_shapes and self.shapes:
            s = self.zoom
            base = img.convert("RGBA")
            for sh in self.shapes:
                if sh is self._editing_shape:
                    continue
                z = dict(sh)
                z["pts"] = [(p[0] * s, p[1] * s) for p in sh["pts"]]
                z["width"] = max(1.0, sh.get("width", 1) * s)
                if "size" in sh:
                    z["size"] = max(4, int(round(sh["size"] * s)))
                try:
                    draw_shape_pil(base, z, ss=2 if s < 1.5 else 1)
                except Exception:
                    pass
            img = base.convert("RGB")
        frame = self._frame_bg.copy()
        frame.paste(img, (IMG_PAD, IMG_PAD))
        return ImageTk.PhotoImage(frame)

    def draw_shapes(self, interactive=False):
        """interactive=True (đang kéo/chỉnh hình): vẽ nhanh bằng canvas; xong thì vẽ mượt bằng PIL."""
        self._schedule_clip()
        c = self.canvas
        c.delete("shape")
        c.delete("selbox")
        if getattr(self, "img_item", None) is not None and getattr(self, "_disp_base", None) is not None:
            if interactive:
                if self._photo_plain is None:
                    self._photo_plain = self._compose_photo(with_shapes=False)
                self.photo = self._photo_plain
            else:
                self.photo = self._compose_photo(with_shapes=True)
            c.itemconfigure(self.img_item, image=self.photo)
        if interactive:
            for sh in self.shapes:
                if sh is self._editing_shape:
                    continue  # đang gõ chữ trong khung này
                self._draw_canvas_shape(sh, ("shape",))
        if self.sel is not None and self.sel in self.shapes:
            x0, y0, x1, y1 = shape_bbox(self.sel)
            s = self.zoom
            c.create_rectangle(x0 * s - 3 + self.ox, y0 * s - 3 + self.oy,
                               x1 * s + 3 + self.ox, y1 * s + 3 + self.oy,
                               outline="#00a2ff", dash=(4, 3), width=1, tags=("selbox",))
            for (hx, hy) in self._sel_handles().values():
                cx, cy = hx * s + self.ox, hy * s + self.oy
                c.create_rectangle(cx - 5, cy - 5, cx + 5, cy + 5, fill="white", outline="#00a2ff",
                                   width=2, tags=("selbox",))
        self._draw_canvas_handles()

    # ----- khung ảnh: kéo ô nắm ở góc/cạnh ảnh để cắt bớt (kéo vào) hoặc nới thêm (kéo ra)
    CANVAS_CURSORS = {"nw": "size_nw_se", "se": "size_nw_se", "ne": "size_ne_sw", "sw": "size_ne_sw",
                      "n": "sb_v_double_arrow", "s": "sb_v_double_arrow",
                      "e": "sb_h_double_arrow", "w": "sb_h_double_arrow"}

    def _canvas_handle_pts(self, box=None):
        x0, y0, x1, y1 = box if box is not None else (0, 0, self.image.width, self.image.height)
        mx, my = (x0 + x1) / 2, (y0 + y1) / 2
        return {"nw": (x0, y0), "n": (mx, y0), "ne": (x1, y0), "e": (x1, my),
                "se": (x1, y1), "s": (mx, y1), "sw": (x0, y1), "w": (x0, my)}

    def _draw_canvas_handles(self, box=None):
        c = self.canvas
        c.delete("canvh")
        if self.entry is None or self.mode != "image":
            return
        s = self.zoom
        for (hx, hy) in self._canvas_handle_pts(box).values():
            X, Y = hx * s + self.ox, hy * s + self.oy
            c.create_rectangle(X - 4, Y - 4, X + 4, Y + 4, fill="white", outline="#3c4048",
                               width=1, tags=("canvh",))

    def _hit_canvas_handle(self, e):
        if self.entry is None or self.mode != "image":
            return None
        cx, cy = self.canvas.canvasx(e.x), self.canvas.canvasy(e.y)
        s = self.zoom
        for name, (hx, hy) in self._canvas_handle_pts().items():
            if abs(cx - (hx * s + self.ox)) <= 8 and abs(cy - (hy * s + self.oy)) <= 8:
                return name
        return None

    def _canvas_drag(self, p):
        dx, dy = p[0] - self.drag_start[0], p[1] - self.drag_start[1]
        x0, y0, x1, y1 = 0.0, 0.0, float(self.image.width), float(self.image.height)
        h = self.drag_mode.split(":", 1)[1]
        if "w" in h:
            x0 += dx
        if "e" in h:
            x1 += dx
        if "n" in h:
            y0 += dy
        if "s" in h:
            y1 += dy
        if x1 - x0 < 10:
            x0, x1 = (x0, x0 + 10) if "e" in h else (x1 - 10, x1)
        if y1 - y0 < 10:
            y0, y1 = (y0, y0 + 10) if "s" in h else (y1 - 10, y1)
        box = (int(round(x0)), int(round(y0)), int(round(x1)), int(round(y1)))
        self.canvas_box = box
        c, s = self.canvas, self.zoom
        c.delete("preview")
        X0, Y0, X1, Y1 = (box[0] * s + self.ox, box[1] * s + self.oy, box[2] * s + self.ox, box[3] * s + self.oy)
        c.create_rectangle(X0, Y0, X1, Y1, outline="#0a84ff", dash=(6, 4), width=2, tags=("preview",))
        label = " %d × %d px " % (box[2] - box[0], box[3] - box[1])
        t = c.create_text(X1 - 4, Y1 + 6, text=label, anchor="ne", fill="white",
                          font=("Segoe UI", 9, "bold"), tags=("preview",))
        bb = c.bbox(t)
        c.tag_lower(c.create_rectangle(bb[0] - 2, bb[1] - 1, bb[2] + 2, bb[3] + 1, fill="#0a84ff",
                                       outline="", tags=("preview",)), t)
        self._draw_canvas_handles(box)
        self.status.configure(text="Khung ảnh: %d × %d px  (kéo vào = cắt bớt, kéo ra = nới thêm nền trắng)"
                              % (box[2] - box[0], box[3] - box[1]))

    def _apply_canvas(self, box):
        x0, y0, x1, y1 = box
        if box == (0, 0, self.image.width, self.image.height):
            self._draw_canvas_handles()
            return
        self.push_undo()
        new = Image.new("RGB", (x1 - x0, y1 - y0), "white")  # phần nới thêm là nền trắng
        new.paste(self.image, (-x0, -y0))
        self.image = new
        for sh in self.shapes:  # nét vẽ giữ nguyên vị trí so với nội dung ảnh
            sh["pts"] = [(q[0] - x0, q[1] - y0) for q in sh["pts"]]
        self.sel = None
        if self.zoom_mode in ("fit", "fitw"):
            self.zoom = self._fit_scale(self.zoom_mode)
        self.refresh()

    def _draw_canvas_shape(self, sh, tags):
        item = self._draw_canvas_shape_raw(sh, tags)
        if item is not None and (self.ox or self.oy):
            self.canvas.move(item, self.ox, self.oy)
        return item

    def _draw_canvas_shape_raw(self, sh, tags):
        c = self.canvas
        s = self.zoom
        t = sh["type"]
        col = sh["color"]
        if t == "text":
            x, y = sh["pts"][0]
            size = max(4, int(round(sh["size"] * s)))
            return c.create_text(x * s, y * s, text=sh["text"], fill=col, anchor="nw",
                                 font=("Segoe UI", -size, "bold"), tags=tags)
        if t == "textbox":
            x0, y0, x1, y1, lines, _lh = textbox_layout(sh)
            if not sh.get("text"):
                return c.create_rectangle(x0 * s, y0 * s, x1 * s, y1 * s, outline="#0a84ff",
                                          dash=(5, 3), width=2, tags=tags)
            size = max(4, int(round(sh["size"] * s)))
            return c.create_text((x0 + TEXT_PAD) * s, (y0 + TEXT_PAD) * s, text="\n".join(lines),
                                 fill=col, anchor="nw", font=("Segoe UI", -size, "bold"), tags=tags)
        if t == "redact":
            (ax, ay), (bx, by) = sh["pts"]
            return c.create_rectangle(ax * s, ay * s, bx * s, by * s, fill=col, outline="", tags=tags)
        w = max(1, sh["width"] * s)
        if t == "crop":
            (ax, ay), (bx, by) = sh["pts"]
            return c.create_rectangle(ax * s, ay * s, bx * s, by * s, outline="#ffffff",
                                      dash=(6, 4), width=2, tags=tags)
        if t in ("ellipse", "rect", "highlight"):
            (ax, ay), (bx, by) = sh["pts"]
            if t == "ellipse":
                return c.create_oval(ax * s, ay * s, bx * s, by * s, outline=col, width=w, tags=tags)
            if t == "rect":
                return c.create_rectangle(ax * s, ay * s, bx * s, by * s, outline=col, width=w, tags=tags)
            return c.create_rectangle(ax * s, ay * s, bx * s, by * s, fill=col, outline="",
                                      stipple="gray50", tags=tags)
        if t == "arrow":
            p0, p1 = sh["pts"]
            _neck, _head, L, hw = arrow_geometry(p0, p1, sh["width"])
            return c.create_line(p0[0] * s, p0[1] * s, p1[0] * s, p1[1] * s, fill=col, width=w,
                                 arrow="last", arrowshape=(L * s, L * s, max(1, (hw - sh["width"] / 2) * s)),
                                 capstyle="round", tags=tags)
        if t == "pen":
            flat = []
            for p in sh["pts"]:
                flat += [p[0] * s, p[1] * s]
            if len(flat) == 2:
                flat += flat
            return c.create_line(*flat, fill=col, width=w, capstyle="round", joinstyle="round",
                                 smooth=True, tags=tags)

    # ---------- undo / redo
    def push_undo(self):
        self.undo_stack.append((self.image, copy.deepcopy(self.shapes)))
        if len(self.undo_stack) > 60:
            self.undo_stack.pop(0)
        self.redo_stack.clear()
        self._mark_dirty()

    def _mark_dirty(self):
        self.clip_synced = False
        self.dirty = True

    def undo(self):
        if not self.undo_stack:
            return
        self.redo_stack.append((self.image, copy.deepcopy(self.shapes)))
        img, shapes = self.undo_stack.pop()
        self._restore(img, shapes)

    def redo(self):
        if not self.redo_stack:
            return
        self.undo_stack.append((self.image, copy.deepcopy(self.shapes)))
        img, shapes = self.redo_stack.pop()
        self._restore(img, shapes)

    def _restore(self, img, shapes):
        size_changed = img.size != self.image.size
        self.image = img
        self.shapes = shapes
        self.sel = None
        self._mark_dirty()
        if size_changed and self.zoom_mode in ("fit", "fitw"):
            self.zoom = self._fit_scale(self.zoom_mode)
        self.refresh()

    # ---------- style
    def set_color(self, col):
        self.color = col
        self.color_btn.configure(bg=col, activebackground=col)
        if self.sel is not None:
            self.push_undo()
            self.sel["color"] = col
            self.draw_shapes()

    def pick_color(self):
        r = colorchooser.askcolor(color=self.color, parent=self.win, title="Chọn màu")
        if r and r[1]:
            self.set_color(r[1])

    def _safe_int(self, var, default):
        try:
            return int(var.get())
        except Exception:
            return default

    def on_style_change(self, what):
        if self.sel is None:
            return
        if what == "width" and self.sel["type"] not in ("text", "textbox"):
            if self.sel["width"] == self._safe_int(self.width, 4):
                return
            self.push_undo()
            self.sel["width"] = self._safe_int(self.width, 4)
        elif what == "size" and self.sel["type"] in ("text", "textbox"):
            if self.sel["size"] == self._safe_int(self.font_size, 28):
                return
            self.push_undo()
            self.sel["size"] = self._safe_int(self.font_size, 28)
        else:
            return
        self.draw_shapes()

    def _set_color_quiet(self, col):
        """Đổi màu đang dùng (không đổi màu hình đang chọn)."""
        self.color = col
        self.color_btn.configure(bg=col, activebackground=col)

    def on_tool_change(self):
        self._finish_text_edit()
        t = self.tool.get()
        # Tô đen: tự chuyển sang màu đen (vẫn chọn màu khác được); rời công cụ thì trả lại màu cũ
        prev = getattr(self, "_prev_tool", None)
        if t == "redact" and prev != "redact":
            self._color_before_redact = self.color
            self._set_color_quiet("#000000")
        elif prev == "redact" and t != "redact" and getattr(self, "_color_before_redact", None):
            self._set_color_quiet(self._color_before_redact)
        self._prev_tool = t
        self.canvas.configure(cursor="arrow" if t == "select" else ("xterm" if t == "text" else "crosshair"))
        if t == "crop":
            self.deselect()

    def deselect(self):
        self.sel = None
        self.draw_shapes()

    def delete_selected(self):
        if self.sel is not None and self.sel in self.shapes:
            self.push_undo()
            self.shapes.remove(self.sel)
            self.sel = None
            self.draw_shapes()

    # ---------- chuột
    def to_img(self, e):
        return ((self.canvas.canvasx(e.x) - self.ox) / self.zoom,
                (self.canvas.canvasy(e.y) - self.oy) / self.zoom)

    # ----- chọn / di chuyển / chỉnh kích cỡ hình đã vẽ (dùng được ở mọi công cụ)
    BOX_TYPES = ("ellipse", "rect", "highlight", "redact", "textbox")
    HANDLE_CURSORS = {"nw": "size_nw_se", "se": "size_nw_se", "ne": "size_ne_sw", "sw": "size_ne_sw",
                      "n": "sb_v_double_arrow", "s": "sb_v_double_arrow",
                      "e": "sb_h_double_arrow", "w": "sb_h_double_arrow", "p0": "crosshair", "p1": "crosshair"}

    @staticmethod
    def _seg_dist(p, a, b):
        ax, ay = a
        bx, by = b
        dx, dy = bx - ax, by - ay
        L = dx * dx + dy * dy
        t = 0 if L == 0 else max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / L))
        return math.hypot(p[0] - (ax + t * dx), p[1] - (ay + t * dy))

    def _shape_dist(self, sh, p):
        """Khoảng cách từ điểm p tới NÉT của hình (0 nếu nằm trong hình đặc)."""
        t = sh["type"]
        if t in ("text", "textbox", "highlight", "redact"):
            x0, y0, x1, y1 = shape_bbox(sh) if t in ("text", "textbox") else self._box(sh)
            dx = max(x0 - p[0], 0, p[0] - x1)
            dy = max(y0 - p[1], 0, p[1] - y1)
            return math.hypot(dx, dy)
        if t == "rect":
            x0, y0, x1, y1 = self._box(sh)
            inside = x0 <= p[0] <= x1 and y0 <= p[1] <= y1
            d_edge = min(abs(p[0] - x0), abs(p[0] - x1), abs(p[1] - y0), abs(p[1] - y1))
            if inside:
                return d_edge
            return min(self._seg_dist(p, a, b) for a, b in (((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)),
                                                            ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))))
        if t == "ellipse":
            x0, y0, x1, y1 = self._box(sh)
            a, b = max((x1 - x0) / 2, 1), max((y1 - y0) / 2, 1)
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            d = math.hypot((p[0] - cx) / a, (p[1] - cy) / b)
            return abs(d - 1) * min(a, b)
        pts = sh["pts"]
        if len(pts) == 1:
            return math.hypot(p[0] - pts[0][0], p[1] - pts[0][1])
        return min(self._seg_dist(p, pts[k], pts[k + 1]) for k in range(len(pts) - 1))

    @staticmethod
    def _box(sh):
        (ax, ay), (bx, by) = sh["pts"][0], sh["pts"][1]
        return min(ax, bx), min(ay, by), max(ax, bx), max(ay, by)

    def hit_test(self, p):
        for sh in reversed(self.shapes):
            tol = 7 / self.zoom + sh.get("width", 2) / 2
            if self._shape_dist(sh, p) <= tol:
                return sh
        return None

    def _sel_handles(self):
        sh = self.sel
        if sh is None:
            return {}
        if sh["type"] in self.BOX_TYPES:
            x0, y0, x1, y1 = shape_bbox(sh) if sh["type"] == "textbox" else self._box(sh)
            mx, my = (x0 + x1) / 2, (y0 + y1) / 2
            return {"nw": (x0, y0), "n": (mx, y0), "ne": (x1, y0), "e": (x1, my),
                    "se": (x1, y1), "s": (mx, y1), "sw": (x0, y1), "w": (x0, my)}
        if sh["type"] == "arrow":
            return {"p0": sh["pts"][0], "p1": sh["pts"][1]}
        return {}  # bút vẽ, chữ: chỉ di chuyển

    def _hit_sel_handle(self, p):
        tol = 8 / self.zoom
        for name, (hx, hy) in self._sel_handles().items():
            if abs(p[0] - hx) <= tol and abs(p[1] - hy) <= tol:
                return name
        return None

    def _select(self, sh):
        self.sel = sh
        if sh is not None:
            if sh["type"] in ("text", "textbox"):
                self.font_size.set(sh["size"])
            else:
                self.width.set(sh["width"])
        self.draw_shapes()

    def on_hover(self, e):
        if self.mode != "image" or self.cur is not None:
            return
        p = self.to_img(e)
        t = self.tool.get()
        h = self._hit_sel_handle(p) if self.sel is not None else None
        ch = None if h else self._hit_canvas_handle(e)
        if ch:
            cur = self.CANVAS_CURSORS[ch]
        elif h:
            cur = self.HANDLE_CURSORS[h]
        elif t != "crop" and self.hit_test(p) is not None:
            cur = "fleur"
        else:
            cur = "arrow" if t == "select" else ("xterm" if t == "text" else "crosshair")
        if self.canvas.cget("cursor") != cur:
            self.canvas.configure(cursor=cur)

    def on_press(self, e):
        if self._text_edit is not None:  # đang gõ chữ -> bấm ra ngoài là xong
            self._finish_text_edit()
            self.canvas.focus_set()
            return
        self.canvas.focus_set()
        if self.entry is None:
            return
        p = self.to_img(e)
        t = self.tool.get()
        self.drag_mode = None
        self.moved = False
        self.drag_last = p
        sel_h = self._hit_sel_handle(p) if self.sel is not None else None
        if not sel_h:
            ch = self._hit_canvas_handle(e)
            if ch:  # ô nắm khung ảnh
                self.drag_mode = "canvas:" + ch
                self.drag_start = p
                self.canvas_box = None
                return
        if t != "crop":
            # 1) đang chọn 1 hình và bấm trúng ô nắm -> chỉnh kích cỡ
            h = self._hit_sel_handle(p) if self.sel is not None else None
            if h:
                self.drag_mode = h
                self.drag_orig = list(self.sel["pts"])
                if self.sel["type"] == "textbox":  # chỉnh theo khung đang hiển thị
                    bx0, by0, bx1, by1 = shape_bbox(self.sel)
                    self.drag_orig = [(bx0, by0), (bx1, by1)]
                self.drag_start = p
                return
            # 2) bấm trúng 1 hình đã vẽ -> chọn & sẵn sàng kéo di chuyển
            hit = self.hit_test(p)
            if hit is not None:
                self._select(hit)
                self.drag_mode = "move"
                return
        # 3) bấm vào chỗ trống
        if self.sel is not None:
            self._select(None)
        if t == "select":
            return
        self.cur = {"type": t, "pts": [p, p], "color": self.color,
                    "width": self._safe_int(self.width, 4)}
        if t == "textbox":
            self.cur.update({"text": "", "size": self._safe_int(self.font_size, 28), "width": 1})
        if t == "pen":
            self.cur["pts"] = [p]

    def _resize_sel(self, p, shift=False):
        dx, dy = p[0] - self.drag_start[0], p[1] - self.drag_start[1]
        h = self.drag_mode
        sh = self.sel
        if h in ("p0", "p1"):
            k = 0 if h == "p0" else 1
            pts = list(self.drag_orig)
            pts[k] = (self.drag_orig[k][0] + dx, self.drag_orig[k][1] + dy)
            sh["pts"] = pts
            return
        (ax, ay), (bx, by) = self.drag_orig[0], self.drag_orig[1]
        x0, y0, x1, y1 = min(ax, bx), min(ay, by), max(ax, bx), max(ay, by)
        if "w" in h:
            x0 += dx
        if "e" in h:
            x1 += dx
        if "n" in h:
            y0 += dy
        if "s" in h:
            y1 += dy
        if shift and len(h) == 2:  # giữ Shift: giữ hình tròn/vuông
            side = max(abs(x1 - x0), abs(y1 - y0))
            if "w" in h:
                x0 = x1 - side
            else:
                x1 = x0 + side
            if "n" in h:
                y0 = y1 - side
            else:
                y1 = y0 + side
        sh["pts"] = [(x0, y0), (x1, y1)]

    def on_drag(self, e):
        p = self.to_img(e)
        t = self.tool.get()
        if isinstance(self.drag_mode, str) and self.drag_mode.startswith("canvas:"):
            self._canvas_drag(p)
            return
        if self.drag_mode is not None and self.sel is not None:
            if not self.moved:
                if abs(p[0] - self.drag_last[0]) * self.zoom < 2 and abs(p[1] - self.drag_last[1]) * self.zoom < 2:
                    return  # rung tay nhẹ -> chưa tính là kéo
                self.push_undo()
                self.moved = True
            if self.drag_mode == "move":
                dx, dy = p[0] - self.drag_last[0], p[1] - self.drag_last[1]
                self.sel["pts"] = [(q[0] + dx, q[1] + dy) for q in self.sel["pts"]]
                self.drag_last = p
            else:
                self._resize_sel(p, shift=bool(e.state & 0x0001))
            self.draw_shapes(interactive=True)
            return
        if self.cur is None:
            return
        if t == "pen":
            self.cur["pts"].append(p)
        else:
            a = self.cur["pts"][0]
            if (e.state & 0x0001) and t in ("ellipse", "rect", "highlight", "crop"):
                d = max(abs(p[0] - a[0]), abs(p[1] - a[1]))
                p = (a[0] + math.copysign(d, p[0] - a[0]), a[1] + math.copysign(d, p[1] - a[1]))
            self.cur["pts"][1] = p
        self.canvas.delete("preview")
        self._draw_canvas_shape(self.cur, ("preview",))

    def on_release(self, e):
        if isinstance(self.drag_mode, str) and self.drag_mode.startswith("canvas:"):
            self.drag_mode = None
            self.canvas.delete("preview")
            if self.canvas_box is not None:
                self._apply_canvas(self.canvas_box)
            self._set_status()
            return
        if self.drag_mode is not None:
            if self.sel is not None and self.sel["type"] in self.BOX_TYPES and self.moved:
                x0, y0, x1, y1 = self._box(self.sel)  # chuẩn hoá toạ độ sau khi kéo ngược
                self.sel["pts"] = [(x0, y0), (x1, y1)]
            self.drag_mode = None
            self.draw_shapes()
            return
        if self.cur is None:
            return
        sh = self.cur
        self.cur = None
        self.canvas.delete("preview")
        if sh["type"] == "pen":
            if len(sh["pts"]) < 2:
                sh["pts"].append((sh["pts"][0][0] + 0.5, sh["pts"][0][1] + 0.5))
        elif sh["type"] == "textbox":
            a, b = sh["pts"]
            if abs(a[0] - b[0]) * self.zoom < 20:  # chỉ bấm 1 cái -> khung mặc định rộng 320px
                x0, y0 = min(a[0], b[0]), min(a[1], b[1])
                sh["pts"] = [(x0, y0), (x0 + 320 / self.zoom, y0)]
            self._open_text_editor(sh, is_new=True)
            return
        else:
            a, b = sh["pts"]
            if abs(a[0] - b[0]) * self.zoom < 4 and abs(a[1] - b[1]) * self.zoom < 4:
                return
        if sh["type"] == "crop":
            self.apply_crop(sh["pts"])
            return
        self.push_undo()
        self.shapes.append(sh)
        self.draw_shapes()

    def on_double(self, e):
        sh = self.hit_test(self.to_img(e))
        if sh is not None and sh["type"] == "textbox":
            self.drag_mode = None
            self._open_text_editor(sh, is_new=False)
            return
        if sh is not None and sh["type"] == "text":
            txt = simpledialog.askstring("Sửa chữ", "Nội dung:", initialvalue=sh["text"], parent=self.win)
            if txt:
                self.push_undo()
                sh["text"] = txt
                self.draw_shapes()

    def _open_text_editor(self, sh, is_new):
        """Mở ô gõ chữ nhiều dòng nằm ngay trên khung (Enter = xuống dòng)."""
        if self._text_edit is not None:
            self._finish_text_edit()
        s = self.zoom
        x0, y0, x1, y1, _lines, lh = textbox_layout(sh)
        size = max(6, int(round(sh["size"] * s)))
        dark = sum(ImageColor.getrgb(sh["color"])[:3]) < 600
        txt = tk.Text(self.canvas, font=("Segoe UI", -size, "bold"), fg=sh["color"],
                      bg="#ffffff" if dark else "#3a3a3a", insertbackground=sh["color"],
                      wrap="word", bd=0, highlightthickness=2, highlightbackground="#0a84ff",
                      highlightcolor="#0a84ff", padx=max(1, int(TEXT_PAD * s) - 2),
                      pady=max(1, int(TEXT_PAD * s) - 2), undo=True)
        txt.bindtags((str(txt), "Text", "all"))  # phím gõ không kích hoạt phím tắt của cửa sổ
        txt.insert("1.0", sh.get("text", ""))
        cw = max(60, int((x1 - x0) * s))
        ch = max(int(lh * s + 2 * TEXT_PAD * s) + 4, int((y1 - y0) * s))
        wid = self.canvas.create_window(x0 * s + self.ox, y0 * s + self.oy, window=txt, anchor="nw",
                                        width=cw, height=ch)
        self._text_edit = {"txt": txt, "win": wid, "shape": sh, "new": is_new, "min_h": ch,
                           "old": sh.get("text", "")}
        self._editing_shape = sh
        self.draw_shapes()

        def grow(_e=None):
            try:
                n = txt.count("1.0", "end", "displaylines")
                n = n[0] if isinstance(n, tuple) else int(n or 1)
            except Exception:
                n = int(txt.index("end-1c").split(".")[0])
            need = int(max(1, n) * lh * s + 2 * TEXT_PAD * s) + 6
            self.canvas.itemconfigure(wid, height=max(self._text_edit["min_h"], need))

        txt.bind("<KeyRelease>", grow)
        txt.bind("<Escape>", lambda e: (self._finish_text_edit(), "break")[1])
        txt.bind("<Control-Return>", lambda e: (self._finish_text_edit(), "break")[1])
        grow()
        txt.focus_set()
        txt.mark_set("insert", "end-1c")
        self._set_status("Gõ chữ trong khung • Enter: xuống dòng • Esc / Ctrl+Enter / bấm ra ngoài: xong")

    def _finish_text_edit(self):
        ed = self._text_edit
        if ed is None:
            return
        self._text_edit = None
        self._editing_shape = None
        txt, sh = ed["txt"], ed["shape"]
        text = txt.get("1.0", "end-1c").rstrip()
        self.canvas.delete(ed["win"])
        txt.destroy()
        if not text.strip():
            if not ed["new"] and sh in self.shapes:  # xoá hết chữ -> bỏ khung
                self.push_undo()
                self.shapes.remove(sh)
            self.sel = None
        elif ed["new"] or text != ed["old"]:
            self.push_undo()
            sh["text"] = text
            if ed["new"]:
                self.shapes.append(sh)
            self.sel = sh
        self.draw_shapes()
        self._set_status("")

    def _typing(self):
        """Đang gõ trong ô nhập (khung chữ, ô số...) -> không chạy phím tắt."""
        w = self.win.focus_get()
        return isinstance(w, (tk.Text, tk.Entry, ttk.Entry, ttk.Spinbox, ttk.Combobox))

    def add_text(self, p):
        txt = simpledialog.askstring("Thêm chữ", "Nội dung:", parent=self.win)
        if not txt:
            return
        self.push_undo()
        self.shapes.append({"type": "text", "pts": [p], "color": self.color, "text": txt,
                            "size": self._safe_int(self.font_size, 28), "width": 1})
        self.draw_shapes()

    def apply_crop(self, pts):
        (ax, ay), (bx, by) = pts
        x0 = int(max(0, min(ax, bx)))
        y0 = int(max(0, min(ay, by)))
        x1 = int(min(self.image.width, max(ax, bx)))
        y1 = int(min(self.image.height, max(ay, by)))
        if x1 - x0 < 5 or y1 - y0 < 5:
            return
        self.push_undo()
        self.image = self.image.crop((x0, y0, x1, y1))
        for sh in self.shapes:
            sh["pts"] = [(q[0] - x0, q[1] - y0) for q in sh["pts"]]
        self.sel = None
        if self.zoom_mode in ("fit", "fitw"):
            self.zoom = self._fit_scale(self.zoom_mode)
        self.refresh()
        self.tool.set("ellipse")

    # ---------- xuất
    def rendered(self):
        return render_annotated(self.image, self.shapes)

    def copy(self):
        if self.entry is None:
            return False
        if self.mode == "video":
            ok = bool(self.path) and copy_file_to_clipboard(self.path)
            self._set_status("✓ Đã copy file video – Ctrl+V để dán vào Zalo / Teams / thư mục" if ok
                             else "Không copy được file video")
            return ok
        if self.mode != "image":
            return False
        ok = copy_image_to_clipboard(self.rendered())
        if ok:
            self.clip_synced = True
        self._set_status("✓ Đã copy – Ctrl+V để dán ở bất cứ đâu" if ok else "Không copy được clipboard")
        return ok

    def done(self):
        """Copy ảnh vào clipboard rồi đóng cửa sổ."""
        if self._clip_job:
            self.win.after_cancel(self._clip_job)
            self._clip_job = None
        self.copy()
        self.close()

    def _write(self, path):
        img = self.rendered()
        ext = os.path.splitext(path)[1].lower()
        if ext in (".jpg", ".jpeg"):
            img.save(path, quality=92)
        elif ext == ".bmp":
            img.save(path)
        else:
            img.save(path, optimize=True)
        self._set_status("Đã xuất file: %s" % path)

    def save(self):
        self.commit()
        self._set_status("✓ Đã lưu: %s" % self.path)
        return True

    def save_as(self):
        if self.mode != "image":
            return False
        init = os.path.basename(self.path) if self.path else "%s_%s.png" % (FILE_PREFIX, timestamp())
        path = filedialog.asksaveasfilename(
            parent=self.win, initialdir=os.path.dirname(self.path) if self.path else self.app.pic_dir,
            initialfile=init, defaultextension=".png",
            filetypes=[("PNG", "*.png"), ("JPEG", "*.jpg;*.jpeg"), ("BMP", "*.bmp")])
        if not path:
            return False
        try:
            self._write(path)
            return True
        except Exception as e:
            messagebox.showerror(DISPLAY_NAME, "Lưu thất bại: %s" % e, parent=self.win)
            return False

    def close(self):
        if self._clip_job:  # còn thay đổi chưa copy -> copy luôn
            self.win.after_cancel(self._clip_job)
            self._auto_copy()
        self.commit()  # tự lưu, không cần hỏi
        self._stop_video()
        self.alive = False
        self.win.destroy()
        if self.app.editor is self:
            self.app.editor = None


# ----------------------------------------------------------------------------
# Cửa sổ chính
# ----------------------------------------------------------------------------
def resource_path(name):
    """Đường dẫn file đi kèm (chạy từ mã nguồn hoặc từ file .exe đóng gói)."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def show_splash(root, on_done, ms=3000):
    """Hiện ảnh QC Team ~3 giây (hiện dần / mờ dần) rồi gọi on_done()."""
    # Ảnh chào: mọi file ảnh nằm cạnh quicksnap.py (thư mục app). Không có ảnh -> bỏ qua.
    import random
    folder = resource_path("")
    try:
        files = [os.path.join(folder, f) for f in os.listdir(folder)
                 if f.lower().endswith((".png", ".jpg", ".jpeg", ".bmp", ".webp"))]
    except Exception:
        files = []
    random.shuffle(files)
    img = None
    for path in files:  # nhiều ảnh -> chọn ngẫu nhiên 1 tấm (bỏ qua file hỏng)
        try:
            img = Image.open(path).convert("RGB")
            break
        except Exception:
            continue
    if img is None:
        on_done()
        return
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    k = min(sh * 0.62 / img.height, sw * 0.5 / img.width, 1.0)
    img = img.resize((max(1, int(img.width * k)), max(1, int(img.height * k))), Image.LANCZOS)
    top = tk.Toplevel(root)
    top.overrideredirect(True)
    top.attributes("-topmost", True)
    top.configure(bg="#c9a227")
    photo = ImageTk.PhotoImage(img)
    top._photo = photo  # giữ tham chiếu ảnh
    tk.Label(top, image=photo, bd=0).pack(padx=3, pady=(3, 0))
    # --- chân ảnh: tên app + thanh tiến trình "đang tải"
    foot = tk.Frame(top, bg="#1f1f1f", padx=14, pady=8)
    foot.pack(fill="x", padx=3, pady=(0, 3))
    row = tk.Frame(foot, bg="#1f1f1f")
    row.pack(fill="x")
    tk.Label(row, text=DISPLAY_NAME, bg="#1f1f1f", fg="#f5d76e",
             font=("Segoe UI", 11, "bold")).pack(side="left")
    pct_lbl = tk.Label(row, text="0%", bg="#1f1f1f", fg="#f5d76e", font=("Segoe UI", 10, "bold"))
    pct_lbl.pack(side="right")
    msg_lbl = tk.Label(foot, text="Đang khởi động...", bg="#1f1f1f", fg="#c9ccd1",
                       font=("Segoe UI", 9), anchor="w")
    msg_lbl.pack(fill="x", pady=(2, 5))
    bar_w = max(120, img.width - 34)
    bar = tk.Canvas(foot, width=bar_w, height=10, bg="#1f1f1f", highlightthickness=0)
    bar.pack(fill="x")
    bar.create_rectangle(0, 0, bar_w, 10, fill="#3a3d42", outline="")
    fill = bar.create_rectangle(0, 0, 0, 10, fill="#f5c542", outline="")
    shine = bar.create_rectangle(0, 0, 0, 4, fill="#ffe08a", outline="")
    stages = [(0.0, "Đang khởi động..."), (0.2, "Đang tải thư viện..."),
              (0.5, "Đang chuẩn bị công cụ chụp màn hình..."), (0.8, "Đang mở giao diện..."),
              (0.98, "Sẵn sàng!")]
    t_start = [None]

    def tick():
        if not top.winfo_exists():
            return
        if t_start[0] is None:
            t_start[0] = time.time()
        f = min(1.0, (time.time() - t_start[0]) * 1000 / ms)
        f_eased = 1 - (1 - f) ** 2  # chạy nhanh lúc đầu, chậm dần về cuối cho tự nhiên
        x = int(bar_w * f_eased)
        bar.coords(fill, 0, 0, x, 10)
        bar.coords(shine, 0, 0, x, 4)
        pct_lbl.configure(text="%d%%" % round(f_eased * 100))
        msg = [m for (th, m) in stages if f_eased >= th][-1]
        if msg_lbl.cget("text") != msg:
            msg_lbl.configure(text=msg)
        if f < 1.0:
            top.after(30, tick)
    top.update_idletasks()
    w, h = top.winfo_reqwidth(), top.winfo_reqheight()
    geo = "%dx%d+%d+%d" % (w, h, (sw - w) // 2, (sh - h) // 2)
    top.geometry(geo)
    top.update()
    top.geometry(geo)  # đặt lại sau khi cửa sổ đã hiện để chắc chắn nằm giữa màn hình
    can_fade = True
    try:
        top.attributes("-alpha", 0.0)
    except Exception:
        can_fade = False

    def fade(a, step, then):
        try:
            top.attributes("-alpha", max(0.0, min(1.0, a)))
        except Exception:
            pass
        if (step > 0 and a >= 1.0) or (step < 0 and a <= 0.0):
            then()
        else:
            top.after(25, lambda: fade(a + step, step, then))

    def finish():
        try:
            top.destroy()
        finally:
            on_done()

    def hold():
        tick()  # thanh tiến trình chạy trong lúc hiển thị
        top.after(ms + 150, lambda: fade(1.0, -0.12, finish) if can_fade else finish())

    top.lift()
    if can_fade:
        fade(0.0, 0.12, hold)
    else:
        hold()


def make_tray_image():
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([4, 14, 60, 54], radius=10, fill=(10, 132, 255, 255))
    d.rectangle([22, 8, 42, 16], fill=(10, 132, 255, 255))
    d.ellipse([20, 22, 44, 46], fill=(255, 255, 255, 255))
    d.ellipse([26, 28, 38, 40], fill=(10, 132, 255, 255))
    return img


class App:
    def __init__(self, start_hidden=False):
        self.root = tk.Tk()
        root = self.root
        root.withdraw()  # ẩn trong lúc hiện màn hình chào
        root.title(DISPLAY_NAME)
        root.resizable(False, False)
        self.sct = mss.mss()
        self.q = queue.Queue()
        self.pic_dir = known_folder("Pictures")
        self.video_dir = known_folder("Videos")
        self.busy = False
        self.menu = None
        self.scroller = None
        self.recorder = None
        self.prev_state = "normal"
        self.tray = None
        self.history = History()
        self.editor = None

        st = load_settings()
        global HOTKEY_NAME
        self.hotkey = normalize_hotkey(st.get("hotkey", DEFAULT_HOTKEY))
        HOTKEY_NAME = hotkey_name(self.hotkey)
        self.hotkey_var = tk.StringVar(value=HOTKEY_NAME)
        self._recording_hotkey = False
        self.fps_var = tk.StringVar(value="30")  # mỗi lần mở app: FPS mặc định 30
        # mỗi lần mở app: các tuỳ chọn đều bật sẵn (trừ "Khởi động cùng Windows")
        self.cursor_var = tk.BooleanVar(value=True)
        self.autocopy_var = tk.BooleanVar(value=True)
        self.minimize_editor_var = tk.BooleanVar(value=True)
        self.photo_cursor_var = tk.BooleanVar(value=True)
        self.autostart_var = tk.BooleanVar(value=get_autostart())
        try:
            days = int(st.get("autodelete_days", CLEANUP_DEFAULT_DAYS))
        except Exception:
            days = CLEANUP_DEFAULT_DAYS
        lbl = next((t for t, d in CLEANUP_CHOICES if d == days), None)
        if lbl is None:
            lbl = next(t for t, d in CLEANUP_CHOICES if d == CLEANUP_DEFAULT_DAYS)
        self.cleanup_var = tk.StringVar(value=lbl)
        self._cleanup_running = False
        self.cleanup_var.trace_add("write", lambda *a: (self._save_settings(),
                                                        self.root.after(800, self.run_cleanup)))
        for v in (self.fps_var, self.cursor_var, self.autocopy_var, self.minimize_editor_var,
                  self.photo_cursor_var):
            v.trace_add("write", lambda *a: self._save_settings())
        self._editor_hidden_by_capture = False

        self._style()
        self._build()
        self._size_locked = False
        root.protocol("WM_DELETE_WINDOW", self.hide_to_tray)
        self._start_tray()
        hidden = start_hidden and self.tray is not None
        # màn hình chào QC Team (chỉ khi mở app; app chạy dưới khay nên không hiện lại tới khi thoát)
        show_splash(root, lambda: None if hidden else self.show_main())

        self.hook = PrintScreenHook(self.q, self.hotkey)
        self.hook.start()
        root.after(50, self.poll)
        root.after(8000, self._cleanup_timer)  # dọn ảnh/video cũ: lúc mở app + mỗi 3 tiếng

    def _style(self):
        st = ttk.Style(self.root)
        try:
            if IS_WIN:
                st.theme_use("vista")
        except Exception:
            pass
        st.configure("Big.TButton", font=("Segoe UI", 12, "bold"), padding=(10, 14))
        st.configure("Title.TLabel", font=("Segoe UI", 16, "bold"))
        st.configure("Sub.TLabel", font=("Segoe UI", 9), foreground="#666")
        st.configure("Key.TLabel", font=("Segoe UI", 10, "bold"), foreground="#d70015")

    def _build(self):
        f = ttk.Frame(self.root, padding=14)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text=DISPLAY_NAME, style="Title.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        self.lbl_key = ttk.Label(f, style="Key.TLabel")
        self.lbl_key.grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 10))
        self.btn_capture = ttk.Button(f, style="Big.TButton", command=lambda: self.start_capture("select"))
        self.btn_capture.grid(row=2, column=0, columnspan=2, padx=4, pady=4, sticky="nsew")
        ttk.Button(f, text="🕘  Ảnh / video gần đây", command=self.show_recent).grid(
            row=3, column=0, sticky="ew", padx=4, pady=(4, 8))
        ttk.Button(f, text="📂  Mở ảnh để chỉnh sửa...", command=self.open_image).grid(
            row=3, column=1, sticky="ew", padx=4, pady=(4, 8))

        opt = ttk.LabelFrame(f, text=" Tuỳ chọn ", padding=(10, 6))
        opt.grid(row=4, column=0, columnspan=2, sticky="ew", padx=4)
        ttk.Label(opt, text="FPS video:").grid(row=0, column=0, sticky="w")
        ttk.Combobox(opt, values=["10", "15", "20", "25", "30"], textvariable=self.fps_var, width=5,
                     state="readonly").grid(row=0, column=1, sticky="w", padx=6, pady=2)
        cur = ttk.Frame(opt)
        cur.grid(row=1, column=0, columnspan=2, sticky="w", pady=2)
        ttk.Checkbutton(cur, text="Hiện con trỏ chuột trong video", variable=self.cursor_var).pack(side="left")
        ttk.Checkbutton(cur, text="Hiện con trỏ chuột trong ảnh", variable=self.photo_cursor_var).pack(
            side="left", padx=(16, 0))
        ttk.Checkbutton(opt, text="Tự copy ảnh vào clipboard (Ctrl+V để dán)", variable=self.autocopy_var).grid(
            row=2, column=0, columnspan=2, sticky="w", pady=2)
        ttk.Checkbutton(opt, text="Khởi động cùng Windows (chạy ẩn dưới khay)", variable=self.autostart_var,
                        command=self._toggle_autostart).grid(row=3, column=0, columnspan=2, sticky="w", pady=2)
        self.chk_minimize = ttk.Checkbutton(opt, variable=self.minimize_editor_var)
        self.chk_minimize.grid(row=4, column=0, columnspan=2, sticky="w", pady=2)
        hk = ttk.Frame(opt)
        hk.grid(row=6, column=0, columnspan=2, sticky="w", pady=2)
        ttk.Label(hk, text="Phím tắt chụp:").pack(side="left")
        self.lbl_hotkey = tk.Label(hk, textvariable=self.hotkey_var, font=("Segoe UI", 10, "bold"),
                                   bg="white", fg="#d70015", relief="solid", bd=1, padx=8, pady=1)
        self.lbl_hotkey.pack(side="left", padx=6)
        self.btn_hotkey = ttk.Button(hk, text="Đổi phím...", command=self.record_hotkey)
        self.btn_hotkey.pack(side="left")
        ttk.Button(hk, text="Về PrtSc", command=lambda: self.set_hotkey(DEFAULT_HOTKEY)).pack(side="left", padx=4)
        cl = ttk.Frame(opt)
        cl.grid(row=5, column=0, columnspan=2, sticky="w", pady=2)
        ttk.Label(cl, text="Tự xoá ảnh/video cũ hơn:").pack(side="left")
        ttk.Combobox(cl, values=[t for t, _d in CLEANUP_CHOICES], textvariable=self.cleanup_var, width=12,
                     state="readonly").pack(side="left", padx=6)
        ttk.Label(cl, text="(vào Thùng rác)", style="Sub.TLabel").pack(side="left")
        fr = ttk.Frame(opt)
        fr.grid(row=7, column=0, columnspan=2, sticky="w", pady=(4, 0))
        ttk.Button(fr, text="Thư mục ảnh", command=lambda: open_path(self.pic_dir)).pack(side="left")
        ttk.Button(fr, text="Thư mục video", command=lambda: open_path(self.video_dir)).pack(side="left", padx=6)
        ttk.Button(fr, text="Thoát hẳn", command=self.quit).pack(side="left")

        self.status = ttk.Label(f, text="Sẵn sàng – đóng cửa sổ này app vẫn chạy dưới khay hệ thống",
                                style="Sub.TLabel")
        self.status.grid(row=5, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self._update_hotkey_texts()

    # ---------- phím tắt chụp
    def _update_hotkey_texts(self):
        n = HOTKEY_NAME
        self.hotkey_var.set(n)
        self.lbl_key.configure(text="Nhấn  %s  ở bất cứ đâu để chụp ảnh / quay video" % n)
        # tên phím ngắn thì ghi kèm trên nút, dài thì thôi (đã có dòng chữ đỏ phía trên)
        self.btn_capture.configure(text="✂  Khoanh vùng để chụp ảnh / quay video" + ("   (%s)" % n if len(n) <= 8 else ""))
        self.chk_minimize.configure(text="Tự thu nhỏ cửa sổ chỉnh sửa khi nhấn %s" % n)
        if self.tray is not None:
            try:
                self.tray.title = DISPLAY_NAME + " – nhấn " + n
                self.tray.update_menu()
            except Exception:
                pass

    def record_hotkey(self):
        if self._recording_hotkey:  # bấm lần nữa = huỷ
            self.hook.cancel_record()
            self._end_record("Đã huỷ đổi phím tắt")
            return
        self._recording_hotkey = True
        self.hook.start_record()
        self.btn_hotkey.configure(text="Huỷ")
        self.hotkey_var.set("Nhấn phím mới...")
        self.lbl_hotkey.configure(bg="#fff4cc")
        self.status.configure(text="Nhấn phím / tổ hợp phím / nút chuột phụ muốn dùng (Esc = huỷ)")
        self._record_timeout = self.root.after(15000, lambda: self._recording_hotkey and (
            self.hook.cancel_record(), self._end_record("Hết giờ – giữ nguyên phím cũ")))
        if not IS_WIN:  # môi trường thử nghiệm: lấy phím từ cửa sổ app
            self.root.bind_all("<KeyPress>", self._tk_record_key)

    def _tk_record_key(self, e):
        self.root.unbind_all("<KeyPress>")
        if not self._recording_hotkey:
            return
        mods = [m for m, bit in (("ctrl", 0x4), ("alt", 0x8), ("shift", 0x1)) if e.state & bit]
        vk = {"Escape": 0x1B, "Print": 0x2C}.get(e.keysym)
        if vk is None:
            if len(e.keysym) == 1:
                vk = ord(e.keysym.upper())
            elif e.keysym.startswith("F") and e.keysym[1:].isdigit():
                vk = 0x6F + int(e.keysym[1:])
            else:
                vk = 0x13
        self.hook.recording = False
        self.q.put(("hotkey_recorded", None if vk == 0x1B and not mods else {"vk": vk, "mods": mods}))

    def _end_record(self, msg=None):
        self._recording_hotkey = False
        try:
            self.root.after_cancel(self._record_timeout)
        except Exception:
            pass
        self.btn_hotkey.configure(text="Đổi phím...")
        self.lbl_hotkey.configure(bg="white")
        self.hotkey_var.set(HOTKEY_NAME)
        if msg:
            self.status.configure(text=msg)

    def _hotkey_recorded(self, hk):
        if not self._recording_hotkey:
            return
        if hk is None:
            self._end_record("Đã huỷ – giữ nguyên phím %s" % HOTKEY_NAME)
            return
        problem = hotkey_problem(hk)
        if problem:
            self._end_record()
            messagebox.showwarning(DISPLAY_NAME, problem)
            return
        self._end_record()
        self.set_hotkey(hk)

    def set_hotkey(self, hk):
        global HOTKEY_NAME
        self.hotkey = normalize_hotkey(hk)
        HOTKEY_NAME = hotkey_name(self.hotkey)
        self.hook.set_hotkey(self.hotkey)
        self._update_hotkey_texts()
        self._save_settings()
        extra = ""
        if self.hotkey["vk"] != 0x2C:
            extra = " (phím PrtSc trở lại bình thường)"
        self.status.configure(text="✓ Phím tắt chụp: %s%s" % (HOTKEY_NAME, extra))

    # ---------- khay hệ thống
    def _start_tray(self):
        try:
            import pystray
        except Exception:
            return
        q = self.q
        menu = pystray.Menu(
            pystray.MenuItem(lambda it: "Khoanh vùng chụp / quay (%s)" % HOTKEY_NAME,
                             lambda i, it: q.put(("cmd", "photo")),
                             default=True),
            pystray.MenuItem("Ảnh / video gần đây", lambda i, it: q.put(("cmd", "recent"))),
            pystray.MenuItem("Mở " + DISPLAY_NAME, lambda i, it: q.put(("cmd", "show"))),
            pystray.MenuItem("Thoát", lambda i, it: q.put(("cmd", "quit"))),
        )
        try:
            self.tray = pystray.Icon(APP_NAME, make_tray_image(), DISPLAY_NAME + " – nhấn " + HOTKEY_NAME, menu)
            threading.Thread(target=self.tray.run, daemon=True).start()
        except Exception:
            self.tray = None

    def hide_to_tray(self):
        if self.tray is None:
            self.quit()
            return
        self.root.withdraw()
        try:
            self.tray.notify(DISPLAY_NAME + " vẫn đang chạy. Nhấn %s để chụp." % HOTKEY_NAME, DISPLAY_NAME)
        except Exception:
            pass

    def _lock_main_size(self):
        if not self._size_locked:
            self._size_locked = True
            lock_size(self.root)

    def show_main(self):
        self.root.deiconify()
        self._lock_main_size()
        self.root.lift()
        self.root.focus_force()

    def _toggle_autostart(self):
        try:
            set_autostart(self.autostart_var.get())
        except Exception as e:
            messagebox.showerror(DISPLAY_NAME, "Không đặt được khởi động cùng Windows: %s" % e)
            self.autostart_var.set(get_autostart())

    # ---------- sự kiện từ thread khác
    def poll(self):
        try:
            while True:
                msg = self.q.get_nowait()
                kind = msg[0]
                if kind == "prtsc":
                    self.on_prtsc()
                elif kind == "cmd":
                    if msg[1] in ("photo", "video"):
                        self.start_capture(msg[1])
                    elif msg[1] == "show":
                        self.show_main()
                    elif msg[1] == "recent":
                        self.show_recent()
                    elif msg[1] == "quit":
                        self.quit()
                        return
                elif kind == "hotkey_fail":
                    self.status.configure(text="⚠ Không bắt được phím %s – có thể app khác đang dùng, "
                                               "hãy đổi phím khác" % msg[1][0])
                elif kind == "hotkey_recorded":
                    self._hotkey_recorded(msg[1])
                elif kind == "video_done":
                    self._video_done(msg[1], msg[2])
                elif kind == "cleanup_done":
                    self._cleanup_done(msg[1], msg[2])
        except queue.Empty:
            pass
        self.root.after(50, self.poll)

    def on_prtsc(self):
        if self.recorder is not None:      # đang quay -> PrtSc = dừng & lưu
            self.recorder.stop()
        elif self.scroller is not None:    # đang cuộn -> PrtSc = OK
            self.scroller.finish()
        elif self.busy:
            return
        else:
            self.start_capture("select")  # vào thẳng chế độ khoanh vùng

    # ---------- chụp
    def _hide_main(self):
        self.prev_state = self.root.state()
        self.root.withdraw()

    def _restore_main(self):
        if self.prev_state == "normal":
            self.root.deiconify()
            self._lock_main_size()
        elif self.prev_state == "iconic":
            self.root.iconify()

    def start_capture(self, mode):
        if self.busy:
            return
        if self.menu is not None:
            self.menu.close()
        self.busy = True
        self._hide_main()
        delay = 250
        self._editor_hidden_by_capture = False
        ed = self.editor
        if self.minimize_editor_var.get() and ed is not None and ed.alive and ed.win.state() == "normal":
            ed.win.iconify()  # thu nhỏ cửa sổ chỉnh sửa cho khỏi che màn hình
            self._editor_hidden_by_capture = True
            delay = 400       # đợi hiệu ứng thu nhỏ của Windows xong
        self.root.after(delay, lambda: self._do_capture("select"))

    def _do_capture(self, mode):
        try:
            RegionSelector(self, self._select_action, title="Kéo chuột để khoanh vùng",
                           actions=[("photo", "📷  Chụp ảnh", "#0a84ff"),
                                    ("video", "🎥  Quay video", "#d70015"),
                                    ("scroll", "⇕  Cuộn chuột", "#ff9500"),
                                    ("cancel", "✖  Tắt", "#555555")])
        except Exception as e:
            self.busy = False
            self._restore_main()
            messagebox.showerror(DISPLAY_NAME, "Lỗi khi chụp: %s" % e)

    def _select_action(self, action, rect, img):
        if action == "video":
            self._video_action("record", rect, img)
        else:
            self._photo_action(action, rect, img)

    def _photo_action(self, action, rect, img):
        if action in ("confirm", "photo"):
            self.capture_done(img)
        elif action == "scroll":
            self.scroller = ScrollCapture(self, rect)
        else:
            self.capture_done(None)

    def _video_action(self, action, rect, _img):
        if action != "record":
            self.capture_done(None)
            return
        try:
            fps = int(self.fps_var.get())
        except Exception:
            fps = 30
        self.recorder = VideoRecorder(self, rect, fps=fps, show_cursor=self.cursor_var.get())
        self.status.configure(text="Đang quay... bấm DỪNG & LƯU ở góc màn hình (hoặc %s)" % HOTKEY_NAME)
        self.busy = True

    def _save_settings(self):
        save_settings({"fps": self.fps_var.get(), "cursor": self.cursor_var.get(),
                       "autocopy": self.autocopy_var.get(),
                       "minimize_editor": self.minimize_editor_var.get(),
                       "photo_cursor": self.photo_cursor_var.get(),
                       "hotkey": getattr(self, "hotkey", DEFAULT_HOTKEY),
                       "autodelete_days": self.cleanup_days()})

    # ---------- tự xoá ảnh/video cũ
    def cleanup_days(self):
        v = self.cleanup_var.get() if hasattr(self, "cleanup_var") else ""
        return next((d for t, d in CLEANUP_CHOICES if t == v), CLEANUP_DEFAULT_DAYS)

    def _cleanup_timer(self):
        self.run_cleanup()
        self.root.after(3 * 3600 * 1000, self._cleanup_timer)

    def run_cleanup(self):
        days = self.cleanup_days()
        if days <= 0 or self._cleanup_running:
            return
        skip = []
        ed = self.editor
        if ed is not None and ed.alive and ed.entry is not None:
            skip.append(ed.entry["path"])  # không xoá ảnh đang mở
        if self.recorder is not None:
            skip.append(getattr(self.recorder, "path", None))
        folders = [self.pic_dir, self.video_dir]
        self._cleanup_running = True

        def work():
            removed = []
            try:
                found = find_old_captures(folders, days, skip)
                if found:
                    removed = delete_many_to_recycle_bin([p for p, _x in found])
                    gone = set(removed)
                    extras = [x for p, xs in found if os.path.abspath(p) in gone for x in xs]
                    delete_many_to_recycle_bin(extras)
            except Exception:
                pass
            self.q.put(("cleanup_done", removed, days))

        threading.Thread(target=work, daemon=True).start()

    def _cleanup_done(self, removed, days):
        self._cleanup_running = False
        if not removed:
            return
        gone = {os.path.normcase(p) for p in removed}
        for e in list(self.history.entries):
            if os.path.normcase(os.path.abspath(e["path"])) in gone:
                self.history.remove(e["path"])
        ed = self.editor
        if ed is not None and ed.alive:
            ed._thumbs = {k: v for k, v in ed._thumbs.items()
                          if os.path.normcase(os.path.abspath(k[0])) not in gone}
            try:
                ed.refresh_recent()
            except Exception:
                pass
        msg = "Đã tự xoá %d ảnh/video cũ hơn %d ngày (nằm trong Thùng rác)" % (len(removed), days)
        self.status.configure(text="🧹 " + msg)
        if self.tray is not None:
            try:
                self.tray.notify(msg, DISPLAY_NAME)
            except Exception:
                pass

    def capture_done(self, img):
        self.busy = False
        self.scroller = None
        self._restore_main()
        if img is None:
            # huỷ -> trả cửa sổ chỉnh sửa về như cũ
            ed = self.editor
            if self._editor_hidden_by_capture and ed is not None and ed.alive:
                ed.win.deiconify()
            self._editor_hidden_by_capture = False
            return
        self._editor_hidden_by_capture = False
        copied = False
        if self.autocopy_var.get() and img.width * img.height <= 40_000_000:
            copied = copy_image_to_clipboard(img)
        path = new_capture_path(self.pic_dir, ".png")
        try:
            img.save(path, compress_level=3)  # tự lưu ngay
        except Exception as e:
            messagebox.showerror(DISPLAY_NAME, "Không lưu được ảnh: %s" % e)
            return
        entry = self.history.add(path, "image")
        entry["_copied"] = copied
        self.status.configure(text="Đã chụp %d × %d px – đã lưu %s" % (img.width, img.height,
                                                                          os.path.basename(path)))
        self.open_in_editor(entry)

    def open_in_editor(self, entry):
        if self.editor is None or not self.editor.alive:
            self.editor = Editor(self)
        self.editor.load_entry(entry)
        self.editor.bring_to_front()

    def show_recent(self):
        items = self.history.items()
        if items:
            if self.editor is not None and self.editor.alive and self.editor.entry is not None:
                self.editor.refresh_recent()
                self.editor.bring_to_front()
            else:
                self.open_in_editor(items[0])
        else:
            messagebox.showinfo(DISPLAY_NAME, "Chưa có ảnh/video nào. Nhấn %s để chụp." % HOTKEY_NAME)

    def _video_done(self, path, error):
        self.recorder = None
        self.busy = False
        self._restore_main()
        if error:
            messagebox.showerror(DISPLAY_NAME, "Quay video lỗi:\n%s" % error)
            self.status.configure(text="Quay video lỗi")
            return
        self.status.configure(text="Đã lưu video: %s" % os.path.basename(path))
        self.open_in_editor(self.history.add(path, "video"))

    def open_image(self):
        path = filedialog.askopenfilename(
            parent=self.root, initialdir=self.pic_dir,
            filetypes=[("Ảnh", "*.png;*.jpg;*.jpeg;*.bmp;*.gif;*.webp"), ("Tất cả", "*.*")])
        if path:
            try:  # chép vào thư mục QuickSnap để không sửa đè lên file gốc
                with Image.open(path) as im:
                    img = im.convert("RGB")
                dst = new_capture_path(self.pic_dir, ".png")
                img.save(dst, compress_level=3)
                self.open_in_editor(self.history.add(dst, "image"))
            except Exception as e:
                messagebox.showerror(DISPLAY_NAME, "Không mở được ảnh: %s" % e)

    def quit(self):
        if self.editor is not None and self.editor.alive:
            self.editor.commit()
        if self.recorder is not None:
            self.recorder.stop()
            if self.recorder.thread.is_alive():
                self.recorder.thread.join(timeout=10)
        self.hook.stop()
        if self.tray is not None:
            try:
                self.tray.stop()
            except Exception:
                pass
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    if not ensure_single_instance():
        _r = tk.Tk()
        _r.withdraw()
        messagebox.showinfo(DISPLAY_NAME, DISPLAY_NAME + " đang chạy rồi (xem biểu tượng dưới khay hệ thống).\n"
                                      "Nhấn %s để chụp." % hotkey_name(load_settings().get("hotkey", DEFAULT_HOTKEY)))
        sys.exit(0)
    App(start_hidden="--tray" in sys.argv).run()
