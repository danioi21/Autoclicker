#!/usr/bin/env python3
"""
HYPER CLICKER — автокликер для Windows с графическим интерфейсом.

Возможности:
  * скорость 1–1000 CPS с точным таймером;
  * режимы Toggle (включил — кликает) и Hold (кликает, пока держишь бинд);
  * два независимых бинда: левая и правая кнопка мыши;
  * переназначение биндов на любую клавишу или Mouse4/Mouse5;
  * оверлей поверх всех окон (не мешает кликам);
  * аварийная остановка курсором в левом верхнем углу;
  * сохранение настроек в settings.json.

Без сторонних библиотек: только стандартный Python + WinAPI.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from ctypes import wintypes
from pathlib import Path

if sys.platform != "win32":
    raise SystemExit("Это приложение работает только на Windows.")

# ---------------------------------------------------------------------------
# WinAPI
# ---------------------------------------------------------------------------

user32 = ctypes.WinDLL("user32", use_last_error=True)
winmm = ctypes.WinDLL("winmm")
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

ERROR_ALREADY_EXISTS = 183
kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
kernel32.CreateMutexW.restype = wintypes.HANDLE
kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
kernel32.CloseHandle.restype = wintypes.BOOL

INPUT_MOUSE = 0

MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010

VK_LBUTTON = 0x01
VK_RBUTTON = 0x02
VK_MBUTTON = 0x04
VK_XBUTTON1 = 0x05
VK_XBUTTON2 = 0x06
VK_ESCAPE = 0x1B
VK_F6 = 0x75
VK_F9 = 0x78
VK_F10 = 0x79

SW_MINIMIZE = 6
SW_RESTORE = 9

BUTTON_FLAGS = {
    "left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
    "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
}

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_FRAMECHANGED = 0x0020
DWMWA_WINDOW_CORNER_PREFERENCE = 33


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", wintypes.DWORD),
        ("mi", MOUSEINPUT),
    ]


user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
user32.SendInput.restype = wintypes.UINT
user32.GetAsyncKeyState.argtypes = (ctypes.c_int,)
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.GetCursorPos.argtypes = (ctypes.POINTER(wintypes.POINT),)
user32.GetCursorPos.restype = wintypes.BOOL
user32.GetParent.argtypes = (wintypes.HWND,)
user32.GetParent.restype = wintypes.HWND
user32.GetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int)
user32.GetWindowLongW.restype = wintypes.LONG
user32.SetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int, wintypes.LONG)
user32.SetWindowLongW.restype = wintypes.LONG
user32.SetWindowPos.argtypes = (
    wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, ctypes.c_int, wintypes.UINT,
)
user32.SetWindowPos.restype = wintypes.BOOL
user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
user32.ShowWindow.restype = wintypes.BOOL
user32.IsIconic.argtypes = (wintypes.HWND,)
user32.IsIconic.restype = wintypes.BOOL
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = (
    wintypes.HWND, ctypes.POINTER(wintypes.DWORD),
)
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.AttachThreadInput.argtypes = (wintypes.DWORD, wintypes.DWORD, wintypes.BOOL)
user32.AttachThreadInput.restype = wintypes.BOOL
user32.BringWindowToTop.argtypes = (wintypes.HWND,)
user32.BringWindowToTop.restype = wintypes.BOOL
user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
user32.SetForegroundWindow.restype = wintypes.BOOL
kernel32.GetCurrentThreadId.restype = wintypes.DWORD


def send_click(button: str) -> None:
    down_flag, up_flag = BUTTON_FLAGS[button]
    events = (INPUT * 2)()

    events[0].type = INPUT_MOUSE
    events[0].mi = MOUSEINPUT(0, 0, 0, down_flag, 0, 0)
    events[1].type = INPUT_MOUSE
    events[1].mi = MOUSEINPUT(0, 0, 0, up_flag, 0, 0)

    if user32.SendInput(2, events, ctypes.sizeof(INPUT)) != 2:
        raise ctypes.WinError(ctypes.get_last_error())


def key_is_down(vk: int) -> bool:
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


def cursor_in_corner() -> bool:
    point = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(point))
    return point.x <= 5 and point.y <= 5


def widget_hwnd(widget: tk.Misc) -> int:
    hwnd = widget.winfo_id()
    parent = user32.GetParent(hwnd)
    return parent or hwnd


def make_rounded(window: tk.Misc) -> None:
    try:
        preference = ctypes.c_int(2)  # DWMWCP_ROUND
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            widget_hwnd(window),
            DWMWA_WINDOW_CORNER_PREFERENCE,
            ctypes.byref(preference),
            ctypes.sizeof(preference),
        )
    except Exception:
        pass


def add_taskbar_button(window: tk.Misc) -> None:
    """Убирает WS_EX_TOOLWINDOW, чтобы окно появилось в панели задач."""
    try:
        hwnd = widget_hwnd(window)
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        style = (style | WS_EX_APPWINDOW) & ~WS_EX_TOOLWINDOW
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
        user32.SetWindowPos(
            hwnd, 0, 0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED,
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Клавиши: виртуальные коды -> подписи
# ---------------------------------------------------------------------------

VK_NAMES: dict[int, str] = {
    0x01: "ЛКМ",
    0x02: "ПКМ",
    0x04: "СКМ",
    0x05: "MOUSE4",
    0x06: "MOUSE5",
    0x08: "BACKSPACE",
    0x09: "TAB",
    0x0D: "ENTER",
    0x10: "SHIFT",
    0x11: "CTRL",
    0x12: "ALT",
    0x13: "PAUSE",
    0x14: "CAPSLOCK",
    0x1B: "ESC",
    0x20: "SPACE",
    0x21: "PGUP",
    0x22: "PGDN",
    0x23: "END",
    0x24: "HOME",
    0x25: "LEFT",
    0x26: "UP",
    0x27: "RIGHT",
    0x28: "DOWN",
    0x2C: "PRTSC",
    0x2D: "INSERT",
    0x2E: "DELETE",
    0x5B: "LWIN",
    0x5C: "RWIN",
    0x6A: "NUM*",
    0x6B: "NUM+",
    0x6D: "NUM-",
    0x6E: "NUM.",
    0x6F: "NUM/",
    0x90: "NUMLOCK",
    0x91: "SCROLL",
    0xBA: ";",
    0xBB: "=",
    0xBC: ",",
    0xBD: "-",
    0xBE: ".",
    0xBF: "/",
    0xC0: "`",
    0xDB: "[",
    0xDC: "\\",
    0xDD: "]",
    0xDE: "'",
}

for _code in range(0x30, 0x3A):  # 0-9
    VK_NAMES[_code] = chr(_code)
for _code in range(0x41, 0x5B):  # A-Z
    VK_NAMES[_code] = chr(_code)
for _index in range(24):  # F1-F24
    VK_NAMES[0x70 + _index] = f"F{_index + 1}"

# Эти клавиши нельзя назначать биндами.
RESERVED_VKS = {VK_LBUTTON, VK_RBUTTON, VK_MBUTTON, VK_F6, VK_F9, VK_F10}


def vk_name(vk: int) -> str:
    return VK_NAMES.get(vk, f"VK 0x{vk:02X}")


# ---------------------------------------------------------------------------
# Настройки
# ---------------------------------------------------------------------------

DEFAULTS = {
    "cps": 12,
    "mode": "toggle",
    "bind_left": 0x77,   # F8
    "bind_right": VK_XBUTTON1,  # MOUSE4
    "overlay": True,
    "always_on_top": True,
    "failsafe": True,
    "hud_corner": "tl",
}

HUD_CORNERS = {
    "tl": "ЛЕВЫЙ ВЕРХ",
    "tr": "ПРАВЫЙ ВЕРХ",
    "bl": "ЛЕВЫЙ НИЗ",
    "br": "ПРАВЫЙ НИЗ",
}

if getattr(sys, "frozen", False):
    # В exe храним настройки в %APPDATA%, чтобы не мусорить рядом с файлом.
    SETTINGS_DIR = Path(
        os.environ.get("APPDATA", str(Path.home()))
    ) / "HyperClicker"
else:
    SETTINGS_DIR = Path(__file__).parent

SETTINGS_PATH = SETTINGS_DIR / "settings.json"


def resource_path(name: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", str(Path(__file__).parent)))
    return base / name


ICON_PATH = resource_path("app.ico")


def load_settings() -> dict:
    settings = dict(DEFAULTS)
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return settings

    if isinstance(data.get("cps"), (int, float)):
        settings["cps"] = max(1, min(1000, int(data["cps"])))
    if data.get("mode") in ("toggle", "hold"):
        settings["mode"] = data["mode"]
    for key in ("bind_left", "bind_right"):
        value = data.get(key)
        if isinstance(value, int) and 1 <= value <= 254 and value not in RESERVED_VKS:
            settings[key] = value
    for key in ("overlay", "always_on_top", "failsafe"):
        if isinstance(data.get(key), bool):
            settings[key] = data[key]
    if data.get("hud_corner") in HUD_CORNERS:
        settings["hud_corner"] = data["hud_corner"]
    if settings["bind_left"] == settings["bind_right"]:
        settings["bind_left"] = DEFAULTS["bind_left"]
        settings["bind_right"] = DEFAULTS["bind_right"]
    return settings


# ---------------------------------------------------------------------------
# Движок кликов
# ---------------------------------------------------------------------------


class ClickEngine:
    def __init__(self, cps: float, failsafe: bool) -> None:
        self.cps = float(cps)
        self.failsafe = failsafe
        self.events = {"left": threading.Event(), "right": threading.Event()}
        self.quit = threading.Event()
        self.counts = {"left": 0, "right": 0}
        self._lock = threading.Lock()
        self._failsafe_hit = False
        self._threads = [
            threading.Thread(target=self._loop, args=(button,), daemon=True)
            for button in ("left", "right")
        ]
        for thread in self._threads:
            thread.start()

    def stop_all(self) -> None:
        for event in self.events.values():
            event.clear()

    def any_active(self) -> bool:
        return any(event.is_set() for event in self.events.values())

    def take_failsafe_hit(self) -> bool:
        hit = self._failsafe_hit
        self._failsafe_hit = False
        return hit

    def _loop(self, button: str) -> None:
        next_tick = time.perf_counter()

        while not self.quit.is_set():
            event = self.events[button]
            if not event.is_set():
                self.quit.wait(0.02)
                next_tick = time.perf_counter()
                continue

            if self.failsafe and cursor_in_corner():
                self.stop_all()
                self._failsafe_hit = True
                continue

            try:
                send_click(button)
            except OSError:
                self.stop_all()
                continue

            with self._lock:
                self.counts[button] += 1

            interval = 1.0 / self.cps
            next_tick += interval
            now = time.perf_counter()
            delay = next_tick - now

            if delay > 0:
                if delay > 0.003:
                    time.sleep(delay - 0.0015)
                while time.perf_counter() < next_tick and not self.quit.is_set():
                    pass
            else:
                next_tick = now

    def shutdown(self) -> None:
        self.stop_all()
        self.quit.set()
        for thread in self._threads:
            thread.join(timeout=0.5)


# ---------------------------------------------------------------------------
# Цвета и шрифты
# ---------------------------------------------------------------------------

BG = "#0e0e12"
BORDER = "#26262f"
CARD = "#16161c"
CARD2 = "#1e1e26"
TEXT = "#f2f2f7"
MUTED = "#8a8a99"
DIM = "#5a5a68"
ACCENT = "#ff3b5c"
ACCENT_DARK = "#2a1016"
GREEN = "#3ddc97"
MAGIC = "#010203"  # цвет-ключ прозрачности оверлея


def round_rect(canvas: tk.Canvas, x1, y1, x2, y2, radius, **kwargs):
    radius = min(radius, (x2 - x1) / 2, (y2 - y1) / 2)
    points = [
        x1 + radius, y1,
        x2 - radius, y1,
        x2, y1,
        x2, y1 + radius,
        x2, y2 - radius,
        x2, y2,
        x2 - radius, y2,
        x1 + radius, y2,
        x1, y2,
        x1, y2 - radius,
        x1, y1 + radius,
        x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, **kwargs)


# ---------------------------------------------------------------------------
# Виджеты
# ---------------------------------------------------------------------------


class RoundedButton(tk.Canvas):
    def __init__(
        self,
        master,
        text: str,
        command=None,
        width: int = 120,
        height: int = 36,
        radius: int = 10,
        fill: str = CARD2,
        hover: str = "#292933",
        text_color: str = TEXT,
        outline: str = BORDER,
        font: tkfont.Font | None = None,
        bg: str | None = None,
    ) -> None:
        parent_bg = bg or master.cget("bg")
        super().__init__(
            master,
            width=width,
            height=height,
            bg=parent_bg,
            highlightthickness=0,
            bd=0,
            cursor="hand2",
        )
        self._command = command
        self._btn_w = width
        self._btn_h = height
        self._radius = radius
        self._fill = fill
        self._hover = hover
        self._text_color = text_color
        self._outline = outline
        self._font = font or tkfont.Font(family="Segoe UI", size=9, weight="bold")
        self._hovered = False
        self._text = text
        self._draw()
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)

    def _draw(self) -> None:
        self.delete("all")
        fill = self._hover if self._hovered else self._fill
        round_rect(
            self, 1, 1, self._btn_w - 1, self._btn_h - 1, self._radius,
            fill=fill, outline=self._outline, width=1,
        )
        self.create_text(
            self._btn_w / 2, self._btn_h / 2,
            text=self._text, fill=self._text_color, font=self._font,
        )

    def _on_enter(self, _event) -> None:
        self._hovered = True
        self._draw()

    def _on_leave(self, _event) -> None:
        self._hovered = False
        self._draw()

    def _on_click(self, _event) -> None:
        if self._command:
            self._command()

    def set_text(self, text: str) -> None:
        self._text = text
        self._draw()

    def set_style(
        self,
        fill: str | None = None,
        hover: str | None = None,
        text_color: str | None = None,
        outline: str | None = None,
    ) -> None:
        if fill is not None:
            self._fill = fill
        if hover is not None:
            self._hover = hover
        if text_color is not None:
            self._text_color = text_color
        if outline is not None:
            self._outline = outline
        self._draw()


class BindChip(RoundedButton):
    def __init__(self, master, command, **kwargs) -> None:
        super().__init__(
            master,
            text="F8",
            command=command,
            width=112,
            height=30,
            radius=8,
            fill=CARD2,
            hover="#262630",
            outline=BORDER,
            **kwargs,
        )
        self._active = False
        self._capturing = False

    def set_key(self, name: str) -> None:
        self._capturing = False
        self.set_text(name)
        self._apply_style()

    def set_active(self, active: bool) -> None:
        if active == self._active:
            return
        self._active = active
        self._apply_style()

    def set_capturing(self, capturing: bool) -> None:
        self._capturing = capturing
        if capturing:
            self.set_text("НАЖМИ...")
        self._apply_style()

    def _apply_style(self) -> None:
        if self._capturing:
            self.set_style(fill=ACCENT_DARK, hover=ACCENT_DARK,
                           text_color=ACCENT, outline=ACCENT)
        elif self._active:
            self.set_style(fill="#20151b", hover="#20151b",
                           text_color=ACCENT, outline="#7a2a3a")
        else:
            self.set_style(fill=CARD2, hover="#262630",
                           text_color=TEXT, outline=BORDER)


class StatusDot(tk.Canvas):
    def __init__(self, master, color: str = MUTED, size: int = 12, bg: str = CARD) -> None:
        super().__init__(
            master, width=size, height=size, bg=bg,
            highlightthickness=0, bd=0,
        )
        self._size = size
        self._color = color
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        pad = 1
        self.create_oval(
            pad, pad, self._size - pad, self._size - pad,
            fill=self._color, outline="",
        )

    def set_color(self, color: str) -> None:
        if color != self._color:
            self._color = color
            self._draw()


class Slider(tk.Canvas):
    MIN = 1
    MAX = 1000

    def __init__(self, master, command=None, width: int = 372) -> None:
        parent_bg = master.cget("bg")
        super().__init__(
            master, width=width, height=32, bg=parent_bg,
            highlightthickness=0, bd=0, cursor="hand2",
        )
        self._slider_w = width
        self._command = command
        self._value = 12
        self._pad = 9
        self._drag = False
        self.bind("<Button-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)
        self._draw()

    def _value_to_x(self, value: float) -> float:
        t = ((value - self.MIN) / (self.MAX - self.MIN)) ** 0.5
        return self._pad + t * (self._slider_w - 2 * self._pad)

    def _x_to_value(self, x: float) -> int:
        t = (x - self._pad) / (self._slider_w - 2 * self._pad)
        t = max(0.0, min(1.0, t))
        value = self.MIN + (self.MAX - self.MIN) * (t ** 2)
        return int(round(value))

    def _draw(self) -> None:
        self.delete("all")
        cy = 16
        x_end = self._value_to_x(self._value)

        round_rect(self, self._pad, cy - 3, self._slider_w - self._pad, cy + 3, 3,
                   fill="#2b2b35", outline="")
        if x_end > self._pad:
            round_rect(self, self._pad, cy - 3, x_end, cy + 3, 3,
                       fill=ACCENT, outline="")
        self.create_oval(
            x_end - 10, cy - 10, x_end + 10, cy + 10,
            fill="#3a1620", outline="",
        )
        self.create_oval(
            x_end - 7, cy - 7, x_end + 7, cy + 7,
            fill=ACCENT, outline="#ffd7de", width=1,
        )

    def set_value(self, value: int, notify: bool = False) -> None:
        value = max(self.MIN, min(self.MAX, int(value)))
        if value == self._value:
            return
        self._value = value
        self._draw()
        if notify and self._command:
            self._command(value)

    def get_value(self) -> int:
        return self._value

    def _on_press(self, event) -> None:
        self._drag = True
        self._update_from_event(event)

    def _on_drag(self, event) -> None:
        if self._drag:
            self._update_from_event(event)

    def _on_release(self, _event) -> None:
        self._drag = False

    def _update_from_event(self, event) -> None:
        value = self._x_to_value(event.x)
        if value != self._value:
            self._value = value
            self._draw()
            if self._command:
                self._command(value)


class ToggleSwitch(tk.Canvas):
    def __init__(self, master, value: bool = True, command=None, bg: str = CARD) -> None:
        super().__init__(
            master, width=42, height=22, bg=bg,
            highlightthickness=0, bd=0, cursor="hand2",
        )
        self._value = value
        self._command = command
        self.bind("<Button-1>", self._toggle)
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        if self._value:
            round_rect(self, 1, 2, 41, 20, 9, fill=ACCENT, outline="")
            knob_x, knob_fill = 30, "#ffffff"
        else:
            round_rect(self, 1, 2, 41, 20, 9, fill="#2b2b35", outline="")
            knob_x, knob_fill = 11, "#777788"
        self.create_oval(
            knob_x - 7, 4, knob_x + 7, 18,
            fill=knob_fill, outline="",
        )

    def _toggle(self, _event) -> None:
        self._value = not self._value
        self._draw()
        if self._command:
            self._command(self._value)

    def set_value(self, value: bool) -> None:
        if self._value != value:
            self._value = value
            self._draw()


class ModeSwitch(tk.Frame):
    def __init__(self, master, value: str, command) -> None:
        bg = master.cget("bg")
        super().__init__(master, bg=bg)
        self._command = command
        self._value = value
        self._buttons: dict[str, RoundedButton] = {}

        font = tkfont.Font(family="Segoe UI", size=9, weight="bold")
        for mode, label in (("toggle", "TOGGLE"), ("hold", "HOLD")):
            button = RoundedButton(
                self, label, command=lambda m=mode: self._choose(m),
                width=178, height=34, radius=9, font=font, bg=bg,
            )
            button.pack(side="left", padx=(0, 8) if mode == "toggle" else (0, 0))
            self._buttons[mode] = button

        self._apply()

    def _choose(self, mode: str) -> None:
        if mode == self._value:
            return
        self._value = mode
        self._apply()
        if self._command:
            self._command(mode)

    def set_value(self, mode: str) -> None:
        if mode != self._value:
            self._value = mode
            self._apply()

    def _apply(self) -> None:
        for mode, button in self._buttons.items():
            if mode == self._value:
                button.set_style(fill=ACCENT, hover=ACCENT,
                                 text_color="#ffffff", outline=ACCENT)
            else:
                button.set_style(fill=CARD2, hover="#262630",
                                 text_color=MUTED, outline=BORDER)


# ---------------------------------------------------------------------------
# Оверлей
# ---------------------------------------------------------------------------


class Overlay(tk.Toplevel):
    WIDTH = 360
    HEIGHT = 38

    def __init__(self, master, corner: str) -> None:
        super().__init__(master)
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(bg=MAGIC)
        try:
            self.attributes("-transparentcolor", MAGIC)
        except tk.TclError:
            self.configure(bg=BG)

        self.canvas = tk.Canvas(
            self, width=self.WIDTH, height=self.HEIGHT,
            bg=MAGIC, highlightthickness=0, bd=0,
        )
        self.canvas.pack()

        font_bold = tkfont.Font(family="Segoe UI", size=9, weight="bold")
        font_small = tkfont.Font(family="Segoe UI", size=9)

        round_rect(
            self.canvas, 1, 1, self.WIDTH - 1, self.HEIGHT - 1, 10,
            fill="#141419", outline="#2c2c36", width=1,
        )
        self._dot = self.canvas.create_oval(
            14, 15, 22, 23, fill=MUTED, outline="",
        )
        self._status = self.canvas.create_text(
            29, 19, text="IDLE", anchor="w", fill=MUTED, font=font_bold,
        )
        self._mode = self.canvas.create_text(
            120, 19, text="MODE: TOGGLE", anchor="w", fill=MUTED, font=font_small,
        )
        self._speed = self.canvas.create_text(
            250, 19, text="SPEED: 12 CPS", anchor="w", fill=TEXT, font=font_small,
        )
        self.snap_to(corner)

        self.after(80, self._make_click_through)

    def snap_to(self, corner: str) -> None:
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        x = 24 if corner in ("tl", "bl") else screen_w - self.WIDTH - 24
        y = 24 if corner in ("tl", "tr") else screen_h - self.HEIGHT - 60
        self.geometry(f"+{x}+{y}")

    def _make_click_through(self) -> None:
        try:
            hwnd = widget_hwnd(self)
            style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            user32.SetWindowLongW(
                hwnd, GWL_EXSTYLE,
                style | WS_EX_LAYERED | WS_EX_TRANSPARENT,
            )
        except Exception:
            pass

    def update_state(self, active: bool, mode: str, cps: int, measured: float) -> None:
        color = ACCENT if active else MUTED
        self.canvas.itemconfigure(self._dot, fill=color)
        self.canvas.itemconfigure(
            self._status, text="ACTIVE" if active else "IDLE", fill=color,
        )
        self.canvas.itemconfigure(self._mode, text=f"MODE: {mode.upper()}")
        self.canvas.itemconfigure(self._speed, text=f"{measured:.1f} / {cps} CPS")


# ---------------------------------------------------------------------------
# Приложение
# ---------------------------------------------------------------------------


class ClickerApp:
    WIDTH = 440

    def __init__(self) -> None:
        self.settings = load_settings()
        self.cps = int(self.settings["cps"])
        self.mode = str(self.settings["mode"])
        self.bind_left = int(self.settings["bind_left"])
        self.bind_right = int(self.settings["bind_right"])
        self.capture: str | None = None
        self.capture_armed = False
        self.capture_prev: set[int] = set()
        self._prev: dict[int, bool] = {}
        self._save_job: str | None = None
        self._last_counts = -1
        self._cps_samples: list[tuple[float, int]] = []

        self.engine = ClickEngine(self.cps, bool(self.settings["failsafe"]))
        self.engine.cps = float(self.cps)

        winmm.timeBeginPeriod(1)
        self.root = tk.Tk()
        self.root.title("HYPER CLICKER")
        try:
            self.root.iconbitmap(default=str(ICON_PATH))
        except tk.TclError:
            pass
        self.root.overrideredirect(True)
        self.root.configure(bg=BORDER)
        self.root.resizable(False, False)
        self.root.withdraw()

        self._build_fonts()
        self._build_ui()
        self._apply_cps(self.cps, save=False, notify_slider=True)
        self._position_window()
        self.root.deiconify()
        self.root.after(60, self._ui_tick)
        self.root.after(15, self._hotkey_tick)
        self.root.after(120, lambda: (make_rounded(self.root), add_taskbar_button(self.root)))

        self.overlay = Overlay(self.root, self.settings["hud_corner"])
        if not self.settings["overlay"]:
            self.overlay.withdraw()

        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.attributes("-topmost", bool(self.settings["always_on_top"]))

    # -- построение интерфейса ------------------------------------------------

    def _build_fonts(self) -> None:
        self.f_title = tkfont.Font(family="Segoe UI", size=12, weight="bold")
        self.f_small = tkfont.Font(family="Segoe UI", size=8)
        self.f_body = tkfont.Font(family="Segoe UI", size=9)
        self.f_body_bold = tkfont.Font(family="Segoe UI", size=9, weight="bold")
        self.f_label = tkfont.Font(family="Segoe UI", size=8, weight="bold")
        self.f_value = tkfont.Font(family="Segoe UI", size=21, weight="bold")
        self.f_status = tkfont.Font(family="Segoe UI", size=10, weight="bold")

    def _card(self, parent) -> tk.Frame:
        card = tk.Frame(
            parent, bg=CARD,
            highlightbackground=BORDER, highlightthickness=1,
        )
        return card

    def _section_label(self, parent, text: str) -> tk.Label:
        return tk.Label(
            parent, text=text, bg=CARD, fg=MUTED,
            font=self.f_label, anchor="w",
        )

    def _build_ui(self) -> None:
        outer = tk.Frame(self.root, bg=BG)
        outer.pack(fill="both", expand=True, padx=1, pady=1)

        content = tk.Frame(outer, bg=BG)
        content.pack(fill="both", expand=True, padx=18, pady=(12, 16))
        self.content = content

        self._build_titlebar(content)
        self._build_status(content)
        self._build_speed(content)
        self._build_mode(content)
        self._build_binds(content)
        self._build_options(content)

        self.exit_button = RoundedButton(
            content, "ВЫХОД   ·   F9", command=self.close,
            width=self.WIDTH - 38, height=40, radius=10,
            fill="#1a1116", hover="#2a1119",
            text_color=ACCENT, outline="#5a2230",
            font=self.f_body_bold,
        )
        self.exit_button.pack(fill="x", pady=(10, 0))

    def _build_titlebar(self, parent) -> None:
        bar = tk.Frame(parent, bg=BG, height=34)
        bar.pack(fill="x")
        bar.pack_propagate(False)

        logo = tk.Canvas(bar, width=26, height=26, bg=BG, highlightthickness=0)
        logo.pack(side="left", pady=2)
        round_rect(logo, 1, 1, 25, 25, 7, fill=ACCENT, outline="")
        logo.create_polygon(
            8, 6, 19, 13, 12, 14, 10, 21, 7, 20,
            fill="#ffffff", outline="",
        )

        title = tk.Label(
            bar, text="HYPER CLICKER", bg=BG, fg=TEXT,
            font=self.f_title, anchor="w",
        )
        title.pack(side="left", padx=(8, 6))

        version = tk.Label(
            bar, text="v2.0", bg=BG, fg=DIM, font=self.f_small, anchor="w",
        )
        version.pack(side="left")

        close = RoundedButton(
            bar, "✕", command=self.close,
            width=28, height=28, radius=8,
            fill=BG, hover="#2a1218", text_color=MUTED,
            outline=BG, font=self.f_body_bold,
        )
        close.pack(side="right")

        minimize = RoundedButton(
            bar, "–", command=self.minimize,
            width=28, height=28, radius=8,
            fill=BG, hover="#23232c", text_color=MUTED,
            outline=BG, font=self.f_body_bold,
        )
        minimize.pack(side="right", padx=(0, 2))

        for widget in (bar, logo, title, version):
            widget.bind("<Button-1>", self._start_drag)
            widget.bind("<B1-Motion>", self._on_drag)

    def _start_drag(self, event) -> None:
        self._drag_offset = (
            event.x_root - self.root.winfo_x(),
            event.y_root - self.root.winfo_y(),
        )

    def _on_drag(self, event) -> None:
        if not hasattr(self, "_drag_offset"):
            return
        x = event.x_root - self._drag_offset[0]
        y = event.y_root - self._drag_offset[1]
        self.root.geometry(f"+{x}+{y}")

    def _build_status(self, parent) -> None:
        card = self._card(parent)
        card.pack(fill="x", pady=(10, 8))

        inner = tk.Frame(card, bg=CARD)
        inner.pack(fill="x", padx=14, pady=(9, 8))

        row1 = tk.Frame(inner, bg=CARD)
        row1.pack(fill="x")

        self.status_dot = StatusDot(row1, color=MUTED, size=12, bg=CARD)
        self.status_dot.pack(side="left")

        self.status_label = tk.Label(
            row1, text="IDLE", bg=CARD, fg=MUTED, font=self.f_status,
        )
        self.status_label.pack(side="left", padx=(7, 0))

        self.speed_badge = tk.Label(
            row1, text="12 CPS", bg=CARD, fg=ACCENT, font=self.f_body_bold,
        )
        self.speed_badge.pack(side="right")

        self.mode_badge = tk.Label(
            row1, text="MODE: TOGGLE", bg=CARD, fg=MUTED, font=self.f_small,
        )
        self.mode_badge.pack(side="right", padx=(0, 14))

        row2 = tk.Frame(inner, bg=CARD)
        row2.pack(fill="x", pady=(5, 0))

        self.clicks_label = tk.Label(
            row2, text="Кликов: 0", bg=CARD, fg=MUTED, font=self.f_small,
        )
        self.clicks_label.pack(side="left")

        self.status_hint = tk.Label(
            row2, text="F6 — оверлей · F9 — выход · F10 — показать",
            bg=CARD, fg=DIM, font=self.f_small,
        )
        self.status_hint.pack(side="right")

        self.mode_badge.configure(text=f"MODE: {self.mode.upper()}")

    def _build_speed(self, parent) -> None:
        card = self._card(parent)
        card.pack(fill="x", pady=(0, 8))

        inner = tk.Frame(card, bg=CARD)
        inner.pack(fill="x", padx=14, pady=(9, 8))

        head = tk.Frame(inner, bg=CARD)
        head.pack(fill="x")

        self._section_label(head, "СКОРОСТЬ КЛИКОВ").pack(side="left", pady=(6, 0))

        unit = tk.Label(
            head, text="CPS", bg=CARD, fg=MUTED, font=self.f_body_bold,
        )
        unit.pack(side="right", pady=(8, 0))

        self.cps_var = tk.StringVar(value=str(self.cps))
        self.cps_entry = tk.Entry(
            head, textvariable=self.cps_var, width=4, justify="right",
            bg=CARD, fg=ACCENT, insertbackground=ACCENT,
            relief="flat", bd=0, font=self.f_value,
            highlightthickness=0,
        )
        self.cps_entry.pack(side="right", padx=(0, 5))
        self.cps_entry.bind("<Return>", self._commit_entry)
        self.cps_entry.bind("<FocusOut>", self._commit_entry)
        self.cps_entry.bind("<Up>", lambda _e: self._nudge(1))
        self.cps_entry.bind("<Down>", lambda _e: self._nudge(-1))

        self.slider = Slider(inner, command=self._on_slider, width=self.WIDTH - 68)
        self.slider.pack(fill="x", pady=(4, 0))

        ranges = tk.Frame(inner, bg=CARD)
        ranges.pack(fill="x")
        tk.Label(ranges, text="1", bg=CARD, fg=DIM, font=self.f_small).pack(side="left")
        tk.Label(ranges, text="1000", bg=CARD, fg=DIM, font=self.f_small).pack(side="right")

        self.slider.set_value(self.cps)

    def _build_mode(self, parent) -> None:
        card = self._card(parent)
        card.pack(fill="x", pady=(0, 8))

        inner = tk.Frame(card, bg=CARD)
        inner.pack(fill="x", padx=14, pady=(9, 10))

        self._section_label(inner, "РЕЖИМ").pack(anchor="w")

        self.mode_switch = ModeSwitch(inner, self.mode, self._on_mode)
        self.mode_switch.pack(anchor="w", pady=(7, 0))

        tk.Label(
            inner,
            text="Toggle — включил и кликает.  Hold — пока держишь бинд.",
            bg=CARD, fg=DIM, font=self.f_small, anchor="w",
        ).pack(anchor="w", pady=(6, 0))

    def _build_binds(self, parent) -> None:
        card = self._card(parent)
        card.pack(fill="x", pady=(0, 8))

        inner = tk.Frame(card, bg=CARD)
        inner.pack(fill="x", padx=14, pady=(9, 9))

        self._section_label(inner, "БИНДЫ").pack(anchor="w")

        self.left_dot, self.left_chip = self._bind_row(inner, "ЛЕВАЯ КНОПКА", "left")
        self.right_dot, self.right_chip = self._bind_row(inner, "ПРАВАЯ КНОПКА", "right")

        self.bind_hint = tk.Label(
            inner, text="Нажми на клавишу, чтобы переназначить",
            bg=CARD, fg=DIM, font=self.f_small, anchor="w",
        )
        self.bind_hint.pack(anchor="w", pady=(5, 0))

        self.left_chip.set_key(vk_name(self.bind_left))
        self.right_chip.set_key(vk_name(self.bind_right))

    def _bind_row(self, parent, label: str, which: str):
        row = tk.Frame(parent, bg=CARD)
        row.pack(fill="x", pady=(6, 0))

        dot = StatusDot(row, color="#3a3a45", size=8, bg=CARD)
        dot.pack(side="left")

        text = tk.Label(
            row, text=label, bg=CARD, fg=TEXT, font=self.f_body,
        )
        text.pack(side="left", padx=(7, 0))

        chip = BindChip(
            row,
            command=lambda w=which: self._start_capture(w),
            bg=CARD, font=self.f_body_bold,
        )
        chip.pack(side="right")

        if which == "left":
            self.left_row_label = text
        else:
            self.right_row_label = text
        return dot, chip

    def _build_options(self, parent) -> None:
        card = self._card(parent)
        card.pack(fill="x")

        inner = tk.Frame(card, bg=CARD)
        inner.pack(fill="x", padx=14, pady=(9, 10))

        self._section_label(inner, "ОПЦИИ").pack(anchor="w")

        self.overlay_switch = self._option_row(
            inner, "Оверлей", bool(self.settings["overlay"]), self._on_overlay,
        )
        self.topmost_switch = self._option_row(
            inner, "Поверх всех окон", bool(self.settings["always_on_top"]),
            self._on_topmost,
        )
        self.failsafe_switch = self._option_row(
            inner, "Стоп в левом верхнем углу", bool(self.settings["failsafe"]),
            self._on_failsafe,
        )
        self.hud_corner_btn = self._corner_row(inner)

    def _corner_row(self, parent) -> RoundedButton:
        row = tk.Frame(parent, bg=CARD)
        row.pack(fill="x", pady=(7, 0))

        tk.Label(
            row, text="Угол оверлея", bg=CARD, fg=TEXT, font=self.f_body, anchor="w",
        ).pack(side="left")

        button = RoundedButton(
            row, HUD_CORNERS[self.settings["hud_corner"]],
            command=self._on_hud_corner,
            width=124, height=26, radius=7, font=self.f_small, bg=CARD,
            fill=CARD2, hover="#262630", text_color=TEXT, outline=BORDER,
        )
        button.pack(side="right")
        return button

    def _on_hud_corner(self) -> None:
        keys = list(HUD_CORNERS)
        corner = keys[(keys.index(self.settings["hud_corner"]) + 1) % len(keys)]
        self.settings["hud_corner"] = corner
        self.hud_corner_btn.set_text(HUD_CORNERS[corner])
        self.overlay.snap_to(corner)
        self._schedule_save()

    def toggle_overlay(self) -> None:
        value = not self.overlay_switch._value
        self.overlay_switch.set_value(value)
        self._on_overlay(value)

    def _option_row(self, parent, label: str, value: bool, command) -> ToggleSwitch:
        row = tk.Frame(parent, bg=CARD)
        row.pack(fill="x", pady=(7, 0))

        tk.Label(
            row, text=label, bg=CARD, fg=TEXT, font=self.f_body, anchor="w",
        ).pack(side="left")

        switch = ToggleSwitch(row, value=value, command=command, bg=CARD)
        switch.pack(side="right")
        return switch

    def _position_window(self) -> None:
        self.root.update_idletasks()
        width = self.WIDTH
        height = self.content.winfo_reqheight() + 2 + 14
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        x = (screen_w - width) // 2
        y = max(20, (screen_h - height) // 2 - 20)
        self.root.geometry(f"{width}x{height}+{x}+{y}")

    # -- действия -------------------------------------------------------------

    def _apply_cps(self, value: int, save: bool = True, notify_slider: bool = False) -> None:
        value = max(1, min(1000, int(value)))
        self.cps = value
        self.engine.cps = float(value)
        self.cps_var.set(str(value))
        if notify_slider:
            self.slider.set_value(value)

        self.speed_badge.configure(text=f"{value} CPS")
        if save:
            self._schedule_save()

    def _on_slider(self, value: int) -> None:
        self._apply_cps(value, save=True, notify_slider=False)

    def _commit_entry(self, _event=None) -> None:
        try:
            value = int(self.cps_var.get())
        except ValueError:
            value = self.cps
        self._apply_cps(value, notify_slider=True)

    def _nudge(self, delta: int) -> str:
        self._apply_cps(self.cps + delta, notify_slider=True)
        return "break"

    def _on_mode(self, mode: str) -> None:
        self.mode = mode
        self.engine.stop_all()
        self.mode_badge.configure(text=f"MODE: {mode.upper()}")
        self._schedule_save()

    def _on_overlay(self, value: bool) -> None:
        self.settings["overlay"] = value
        if value:
            self.overlay.deiconify()
            self.overlay.attributes("-topmost", True)
        else:
            self.overlay.withdraw()
        self._schedule_save()

    def _on_topmost(self, value: bool) -> None:
        self.settings["always_on_top"] = value
        self.root.attributes("-topmost", value)
        self.overlay.attributes("-topmost", value)
        self._schedule_save()

    def _on_failsafe(self, value: bool) -> None:
        self.settings["failsafe"] = value
        self.engine.failsafe = value
        self._schedule_save()

    # -- переназначение биндов -------------------------------------------------

    def _start_capture(self, which: str) -> None:
        self.capture = which
        self.capture_armed = False
        self.capture_prev = set()
        self.engine.stop_all()

        self.bind_hint.configure(
            text="Нажми любую клавишу или Mouse4/Mouse5… (Esc — отмена)",
            fg=ACCENT,
        )
        self.left_chip.set_capturing(which == "left")
        self.right_chip.set_capturing(which == "right")
        if which == "left":
            self.right_chip.set_key(vk_name(self.bind_right))
        else:
            self.left_chip.set_key(vk_name(self.bind_left))

    def _cancel_capture(self) -> None:
        self.capture = None
        self.left_chip.set_key(vk_name(self.bind_left))
        self.right_chip.set_key(vk_name(self.bind_right))
        self.bind_hint.configure(
            text="Нажми на клавишу, чтобы переназначить", fg=DIM,
        )
        self._prev = {
            vk: key_is_down(vk)
            for vk in (self.bind_left, self.bind_right, VK_F6, VK_F9, VK_F10)
        }

    def _finish_capture(self, vk: int) -> None:
        which = self.capture
        self.capture = None

        if which == "left":
            old = self.bind_left
            self.bind_left = vk
            if self.bind_right == vk:
                self.bind_right = old
        else:
            old = self.bind_right
            self.bind_right = vk
            if self.bind_left == vk:
                self.bind_left = old

        self.left_chip.set_key(vk_name(self.bind_left))
        self.right_chip.set_key(vk_name(self.bind_right))
        self.left_chip.set_active(False)
        self.right_chip.set_active(False)
        self.bind_hint.configure(
            text="Нажми на клавишу, чтобы переназначить", fg=DIM,
        )
        self._prev = {
            key: key_is_down(key)
            for key in (self.bind_left, self.bind_right, VK_F6, VK_F9, VK_F10)
        }
        self._schedule_save()

    # -- глобальный опрос клавиш ----------------------------------------------

    def _hotkey_tick(self) -> None:
        try:
            if self.capture:
                self._capture_tick()
            else:
                self._normal_tick()
            self.root.after(15, self._hotkey_tick)
        except tk.TclError:
            pass

    def _normal_tick(self) -> None:
        states = {
            self.bind_left: key_is_down(self.bind_left),
            self.bind_right: key_is_down(self.bind_right),
            VK_F9: key_is_down(VK_F9),
            VK_F10: key_is_down(VK_F10),
            VK_F6: key_is_down(VK_F6),
        }
        # Если бинды совпали (не должно происходить), не дублируем опрос.
        previous = self._prev

        if states[VK_F9] and not previous.get(VK_F9, False):
            self.close()
            return
        if states[VK_F10] and not previous.get(VK_F10, False):
            self.restore_window()
        if states[VK_F6] and not previous.get(VK_F6, False):
            self.toggle_overlay()

        for which, vk in (("left", self.bind_left), ("right", self.bind_right)):
            down = states[vk]
            was = previous.get(vk, False)
            if down and not was:
                self._trigger_bind(which)
            elif self.mode == "hold" and not down:
                self.engine.events[which].clear()

        self._prev = {
            self.bind_left: states[self.bind_left],
            self.bind_right: states[self.bind_right],
            VK_F9: states[VK_F9],
            VK_F10: states[VK_F10],
            VK_F6: states[VK_F6],
        }

    def _trigger_bind(self, which: str) -> None:
        event = self.engine.events[which]
        if self.mode == "toggle":
            if event.is_set():
                event.clear()
            else:
                event.set()
        else:
            event.set()

    def _capture_tick(self) -> None:
        pressed = {vk for vk in range(1, 255) if key_is_down(vk)}
        mouse_held = bool(pressed & {VK_LBUTTON, VK_RBUTTON, VK_MBUTTON})

        if not self.capture_armed:
            if not mouse_held:
                self.capture_armed = True
                self.capture_prev = pressed
            return

        new_keys = pressed - self.capture_prev
        self.capture_prev = pressed

        for vk in sorted(new_keys):
            if vk == VK_ESCAPE:
                self._cancel_capture()
                return
            if vk in RESERVED_VKS:
                continue
            self._finish_capture(vk)
            return

    # -- обновление интерфейса ------------------------------------------------

    def _ui_tick(self) -> None:
        active_left = self.engine.events["left"].is_set()
        active_right = self.engine.events["right"].is_set()
        active = active_left or active_right

        if active:
            self.status_dot.set_color(ACCENT)
            self.status_label.configure(text="ACTIVE", fg=ACCENT)
        else:
            self.status_dot.set_color(MUTED)
            self.status_label.configure(text="IDLE", fg=MUTED)

        self.left_dot.set_color(GREEN if active_left else "#3a3a45")
        self.right_dot.set_color(GREEN if active_right else "#3a3a45")
        self.left_chip.set_active(active_left)
        self.right_chip.set_active(active_right)
        self.left_row_label.configure(fg=TEXT if active_left else MUTED)
        self.right_row_label.configure(fg=TEXT if active_right else MUTED)

        total = sum(self.engine.counts.values())
        if total != self._last_counts:
            self.clicks_label.configure(text=f"Кликов: {total}")
            self._last_counts = total

        if self.engine.take_failsafe_hit():
            self.bind_hint.configure(
                text="Сработала аварийная остановка (курсор в углу).",
                fg=ACCENT,
            )
            self.root.after(
                2500, lambda: self.bind_hint.configure(
                    text="Нажми на клавишу, чтобы переназначить", fg=DIM,
                ),
            )

        now = time.perf_counter()
        self._cps_samples.append((now, total))
        while len(self._cps_samples) > 1 and now - self._cps_samples[0][0] > 1.0:
            self._cps_samples.pop(0)
        start_time, start_total = self._cps_samples[0]
        measured = (total - start_total) / (now - start_time) if now > start_time else 0.0

        self.overlay.update_state(active, self.mode, self.cps, measured)
        self.root.after(60, self._ui_tick)

    # -- сохранение и выход ----------------------------------------------------

    def _schedule_save(self) -> None:
        if self._save_job:
            try:
                self.root.after_cancel(self._save_job)
            except tk.TclError:
                pass
        self._save_job = self.root.after(400, self._save)

    def _save(self) -> None:
        self._save_job = None
        SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
        self.settings.update(
            cps=self.cps,
            mode=self.mode,
            bind_left=self.bind_left,
            bind_right=self.bind_right,
            overlay=bool(self.overlay_switch._value),
            always_on_top=bool(self.topmost_switch._value),
            failsafe=bool(self.failsafe_switch._value),
            hud_corner=self.settings["hud_corner"],
        )
        try:
            SETTINGS_PATH.write_text(
                json.dumps(self.settings, indent=2), encoding="utf-8",
            )
        except OSError:
            pass

    def minimize(self) -> None:
        """Свернуть окно; кликер и оверлей продолжают работать."""
        user32.ShowWindow(widget_hwnd(self.root), SW_MINIMIZE)

    def restore_window(self) -> None:
        hwnd = widget_hwnd(self.root)
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)

        # BringWindowToTop/SetForegroundWindow могут быть заблокированы
        # правилами Windows, если фокус у другого процесса. Временное
        # присоединение к его input-очереди это обходит.
        foreground = user32.GetForegroundWindow()
        current_thread = kernel32.GetCurrentThreadId()
        foreground_thread = 0
        if foreground and foreground != hwnd:
            foreground_thread = user32.GetWindowThreadProcessId(foreground, None)

        attached = False
        if foreground_thread and foreground_thread != current_thread:
            attached = bool(
                user32.AttachThreadInput(foreground_thread, current_thread, True)
            )
        try:
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
        finally:
            if attached:
                user32.AttachThreadInput(foreground_thread, current_thread, False)

    def close(self) -> None:
        if self._save_job:
            try:
                self.root.after_cancel(self._save_job)
            except tk.TclError:
                pass
            self._save_job = None
        self._save()
        self.engine.shutdown()
        try:
            self.overlay.destroy()
        except tk.TclError:
            pass
        self.root.destroy()
        try:
            winmm.timeEndPeriod(1)
        except Exception:
            pass

    def run(self) -> None:
        self.root.mainloop()


# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------


def acquire_single_instance():
    """Не даёт запустить второй экземпляр (иначе клики удвоятся)."""
    handle = kernel32.CreateMutexW(
        None, False, "Local\\HyperClicker_SingleInstance",
    )
    if not handle:
        return 0
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        return 0
    return handle


def main() -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--reset-settings", action="store_true")
    args, _unknown = parser.parse_known_args()

    if args.reset_settings:
        try:
            SETTINGS_PATH.unlink()
        except OSError:
            pass

    mutex = acquire_single_instance()
    if not mutex:
        ctypes.windll.user32.MessageBoxW(
            None,
            "HYPER CLICKER уже запущен.\n\nНажмите F9, чтобы закрыть его.",
            "HYPER CLICKER",
            0x00000040,  # MB_ICONINFORMATION
        )
        return

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass

    app = ClickerApp()
    app.run()


if __name__ == "__main__":
    main()
