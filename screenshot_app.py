import os
import sys
import time
from datetime import datetime
from functools import reduce
from pathlib import Path

import keyboard
import mss
import winsound
from PyQt6.QtCore import QObject, QRect, Qt, QStandardPaths, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QApplication, QMenu, QStyle, QSystemTrayIcon, QWidget

HOTKEY_REGION = 'ctrl+shift+s'
HOTKEY_FULL = 'ctrl+shift+x'
# Системный звук Windows. Другие варианты: 'SystemExclamation', 'SystemHand', 'SystemDefault'.
SOUND_ALIAS = 'SystemAsterisk'


def screenshots_dir() -> Path:
    # QStandardPaths учитывает перенаправление папки «Изображения» в OneDrive.
    pictures = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.PicturesLocation)
    folder = Path(pictures) / 'Screenshots'
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def bgra_to_qimage(data: bytes, width: int, height: int) -> QImage:
    # BGRA в памяти совпадает с Format_RGB32 (little-endian). copy() отвязывает изображение от буфера.
    return QImage(data, width, height, width * 4, QImage.Format.Format_RGB32).copy()


def rgb_to_qimage(array) -> QImage:
    height, width = array.shape[:2]
    data = array.tobytes()
    return QImage(data, width, height, width * 3, QImage.Format.Format_RGB888).copy()


class Grabber:
    # Захват кадров. dxcam (Desktop Duplication) для основного монитора, mss для остального.

    def __init__(self) -> None:
        self.sct = mss.mss()  # создаётся и используется только в главном потоке
        self.cam = None
        try:
            import dxcam
            self.cam = dxcam.create(output_color='RGB')
        except Exception:
            self.cam = None  # dxcam не установлен или нет поддержки, пойдём через mss

    def grab_virtual(self) -> QImage:
        # Все мониторы одним кадром (нужно для выбора области).
        shot = self.sct.grab(self.sct.monitors[0])
        return bgra_to_qimage(shot.bgra, shot.width, shot.height)

    def grab_primary(self) -> QImage:
        # Основной монитор. Сначала dxcam, он видит полноэкранные DX/Vulkan-игры.
        if self.cam is not None:
            try:
                for _ in range(10):  # dxcam возвращает None, если кадр не изменился
                    frame = self.cam.grab()
                    if frame is not None:
                        return rgb_to_qimage(frame)
                    time.sleep(0.01)
            except Exception:
                self.cam = None  # dxcam не смог обработать кадр (например, нет cv2), дальше только mss
        shot = self.sct.grab(self.sct.monitors[1])
        return bgra_to_qimage(shot.bgra, shot.width, shot.height)


class Signals(QObject):
    # Глобальные хоткеи приходят из потока keyboard, поэтому передаём их через сигналы Qt.
    region = pyqtSignal()
    full = pyqtSignal()


class RegionOverlay(QWidget):
    # Затемнённый снимок экрана, на котором выделяется прямоугольник.

    def __init__(self, snapshot: QImage, geometry: QRect, on_done) -> None:
        super().__init__(None, Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool)
        self.setGeometry(geometry)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.pixmap = QPixmap.fromImage(snapshot)
        # Снимок в физических пикселях, виджет в логических (масштаб Windows).
        self.scale = snapshot.width() / geometry.width()
        self.on_done = on_done
        self.start = None
        self.end = None

    def _selection(self) -> QRect:
        return QRect(self.start, self.end).normalized()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.drawPixmap(self.rect(), self.pixmap)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 150))  # затемнение
        if self.start is not None and self.end is not None:
            sel = self._selection()
            src = QRect(int(sel.x() * self.scale), int(sel.y() * self.scale),
                        int(sel.width() * self.scale), int(sel.height() * self.scale))
            painter.drawPixmap(sel, self.pixmap, src)  # выделенная область яркая
            painter.setPen(QPen(QColor('#3ddc97'), 2))
            painter.drawRect(sel.adjusted(0, 0, -1, -1))
        painter.end()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.start = self.end = event.position().toPoint()
            self.update()
        elif event.button() == Qt.MouseButton.RightButton:
            self._cancel()

    def mouseMoveEvent(self, event) -> None:
        if self.start is not None and bool(event.buttons() & Qt.MouseButton.LeftButton):
            self.end = event.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton or self.start is None:
            return
        sel = self._selection()
        if sel.width() < 5 or sel.height() < 5:
            self._cancel()
            return
        crop = QRect(int(sel.x() * self.scale), int(sel.y() * self.scale),
                     int(sel.width() * self.scale), int(sel.height() * self.scale))
        image = self.pixmap.toImage().copy(crop)
        self.close()
        self.on_done(image)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self._cancel()

    def _cancel(self) -> None:
        self.close()
        self.on_done(None)


class ScreenshotApp:
    def __init__(self) -> None:
        self.app = QApplication(sys.argv)
        self.app.setQuitOnLastWindowClosed(False)  # приложение живёт в трее

        self.grabber = Grabber()
        self.overlay: RegionOverlay | None = None

        self.signals = Signals()
        self.signals.region.connect(self.start_region)
        self.signals.full.connect(self.capture_full)

        # Хоткеи работают глобально, даже когда окно не в фокусе.
        keyboard.add_hotkey(HOTKEY_REGION, lambda: self.signals.region.emit())
        keyboard.add_hotkey(HOTKEY_FULL, lambda: self.signals.full.emit())

        self._build_tray()

    def _build_tray(self) -> None:
        icon = self.app.style().standardIcon(QStyle.StandardPixmap.SP_DesktopIcon)
        self.tray = QSystemTrayIcon(icon)
        self.tray.setToolTip('Screenshotter')

        menu = QMenu()
        menu.addAction(f'Выбрать область ({HOTKEY_REGION})', self.start_region)
        menu.addAction(f'Весь экран ({HOTKEY_FULL})', self.capture_full)
        menu.addSeparator()
        menu.addAction('Открыть папку со скриншотами', self.open_folder)
        menu.addSeparator()
        menu.addAction('Выход', self.quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _on_tray_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.open_folder()

    def start_region(self) -> None:
        if self.overlay is not None:
            return
        snapshot = self.grabber.grab_virtual()
        # Область всех мониторов в логических координатах Qt.
        geometry = reduce(lambda a, b: a.united(b),
                          (screen.geometry() for screen in self.app.screens()))
        self.overlay = RegionOverlay(snapshot, geometry, self._on_region_done)
        self.overlay.show()
        self.overlay.activateWindow()
        self.overlay.setFocus()
        self.overlay.raise_()

    def _on_region_done(self, image: QImage | None) -> None:
        self.overlay = None
        if image is not None:
            self.deliver(image)

    def capture_full(self) -> None:
        self.deliver(self.grabber.grab_primary())

    def deliver(self, image: QImage) -> None:
        # Буфер обмена: сразу можно вставить через Ctrl+V.
        QApplication.clipboard().setImage(image)

        folder = screenshots_dir()
        path = folder / f'Screen_{datetime.now():%Y-%m-%d_%H-%M-%S}.png'
        counter = 1
        while path.exists():  # если два снимка в одну секунду
            path = folder / f'Screen_{datetime.now():%Y-%m-%d_%H-%M-%S}_{counter}.png'
            counter += 1
        image.save(str(path), 'PNG')

        winsound.PlaySound(SOUND_ALIAS, winsound.SND_ALIAS | winsound.SND_ASYNC)
        self.tray.showMessage('Скриншот сохранён', path.name,
                              QSystemTrayIcon.MessageIcon.NoIcon, 1500)

    def open_folder(self) -> None:
        os.startfile(str(screenshots_dir()))

    def quit(self) -> None:
        keyboard.unhook_all()
        self.tray.hide()
        self.app.quit()

    def run(self) -> int:
        return self.app.exec()


def main() -> None:
    def log_error(exc_type, exc, tb) -> None:
        # PyQt завершает процесс на необработанном исключении в слоте. Пишем в лог и продолжаем работу.
        import traceback
        log = Path(os.environ.get('TEMP', '.')) / 'Screenshotter_error.log'
        with open(log, 'a', encoding='utf-8') as f:
            f.write(''.join(traceback.format_exception(exc_type, exc, tb)) + '\n')

    sys.excepthook = log_error
    sys.exit(ScreenshotApp().run())


if __name__ == '__main__':
    main()
