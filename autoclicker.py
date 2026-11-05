#!/usr/bin/env python3
"""
Автокликер для Windows на чистом Python (без сторонних библиотек).

Управление:
  F6  — старт / пауза
  F7  — выход
  Курсор в левом верхнем углу экрана — аварийная остановка кликов.

Примеры:
  python autoclicker.py                     # 10 кликов/сек, левая кнопка
  python autoclicker.py --cps 25            # 25 кликов/сек
  python autoclicker.py --cps 5 --button right
  python autoclicker.py --start             # начать кликать сразу после запуска
"""

from __future__ import annotations

import argparse
import ctypes
import sys
import threading
import time
from ctypes import wintypes

if sys.platform != "win32":
    raise SystemExit("Этот скрипт работает только на Windows.")

user32 = ctypes.WinDLL("user32", use_last_error=True)

# --- Константы WinAPI ---------------------------------------------------------

INPUT_MOUSE = 0

MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040

VK_F6 = 0x75
VK_F7 = 0x76

BUTTONS = {
    "left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
    "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
    "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
}


# --- Структуры WinAPI ---------------------------------------------------------


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),  # ULONG_PTR
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


# --- Работа с мышью и клавиатурой ---------------------------------------------


def send_click(down_flag: int, up_flag: int) -> None:
    """Отправляет одно нажатие и отпускание кнопки мыши через SendInput."""
    events = (INPUT * 2)()

    events[0].type = INPUT_MOUSE
    events[0].mi = MOUSEINPUT(0, 0, 0, down_flag, 0, 0)

    events[1].type = INPUT_MOUSE
    events[1].mi = MOUSEINPUT(0, 0, 0, up_flag, 0, 0)

    sent = user32.SendInput(2, events, ctypes.sizeof(INPUT))
    if sent != 2:
        raise ctypes.WinError(ctypes.get_last_error())


def key_is_down(vk: int) -> bool:
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


def cursor_position() -> tuple[int, int]:
    point = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(point))
    return point.x, point.y


# --- Автокликер -----------------------------------------------------------------


class AutoClicker:
    def __init__(self, cps: float, button: str, start_immediately: bool) -> None:
        self.cps = cps
        self.interval = 1.0 / cps
        self.button_name = button
        self.down_flag, self.up_flag = BUTTONS[button]

        self.running = threading.Event()
        self.quit = threading.Event()
        self.clicks = 0
        self._lock = threading.Lock()

        if start_immediately:
            self.running.set()

    def toggle(self) -> None:
        if self.running.is_set():
            self.running.clear()
            print(f"\n[ПАУЗА] Всего кликов: {self.clicks}")
        else:
            self.running.set()
            print(
                f"\n[СТАРТ] {self.cps:g} кликов/сек, "
                f"кнопка: {self.button_name}"
            )
        print("F6 — старт/пауза, F7 — выход.")

    def click_loop(self) -> None:
        next_tick = time.perf_counter()

        while not self.quit.is_set():
            if not self.running.is_set():
                self.quit.wait(0.02)
                next_tick = time.perf_counter()
                continue

            # Аварийная остановка: курсор в левом верхнем углу.
            x, y = cursor_position()
            if x <= 5 and y <= 5:
                self.running.clear()
                print("\n[FAILSAFE] Курсор в левом верхнем углу — клики остановлены.")
                print("F6 — старт/пауза, F7 — выход.")
                continue

            try:
                send_click(self.down_flag, self.up_flag)
            except OSError as exc:
                self.running.clear()
                print(f"\n[ОШИБКА] Не удалось отправить клик: {exc}")
                print("F6 — старт/пауза, F7 — выход.")
                continue

            with self._lock:
                self.clicks += 1

            next_tick += self.interval
            now = time.perf_counter()
            if next_tick > now:
                self.quit.wait(next_tick - now)
            else:
                # Не накапливаем отставание при нехватке производительности.
                next_tick = now

    def hotkey_loop(self) -> None:
        """Опрашивает глобальные клавиши F6/F7 без хуков и прав администратора."""
        prev_f6 = False
        prev_f7 = False

        try:
            while not self.quit.is_set():
                f6 = key_is_down(VK_F6)
                f7 = key_is_down(VK_F7)

                if f6 and not prev_f6:
                    self.toggle()
                if f7 and not prev_f7:
                    print("\n[ВЫХОД]")
                    self.quit.set()
                    break

                prev_f6, prev_f7 = f6, f7
                time.sleep(0.02)
        except KeyboardInterrupt:
            self.quit.set()

    def run(self) -> None:
        print("=" * 52)
        print("  Автокликер")
        print("=" * 52)
        print(f"  Скорость: {self.cps:g} кликов/сек")
        print(f"  Кнопка:   {self.button_name}")
        print("  F6 — старт/пауза, F7 — выход")
        print("  Аварийная остановка: курсор в левый верхний угол")
        print("=" * 52)
        if self.running.is_set():
            print("[СТАРТ] Клики уже идут.")
        else:
            print("Нажмите F6, чтобы начать.")

        click_thread = threading.Thread(target=self.click_loop, daemon=True)
        click_thread.start()

        self.hotkey_loop()

        click_thread.join(timeout=1.0)
        print(f"Итого кликов: {self.clicks}")


# --- Точка входа ----------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Автокликер для Windows (без сторонних библиотек).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--cps",
        type=float,
        default=10.0,
        metavar="N",
        help="кликов в секунду",
    )
    parser.add_argument(
        "--button",
        choices=tuple(BUTTONS),
        default="left",
        help="кнопка мыши",
    )
    parser.add_argument(
        "--start",
        action="store_true",
        help="начать кликать сразу после запуска",
    )
    args = parser.parse_args()

    if args.cps <= 0:
        parser.error("--cps должно быть больше нуля")

    return args


def main() -> None:
    args = parse_args()
    clicker = AutoClicker(args.cps, args.button, args.start)

    try:
        clicker.run()
    except KeyboardInterrupt:
        clicker.quit.set()
        print(f"\nИтого кликов: {clicker.clicks}")


if __name__ == "__main__":
    main()
