import os
import re
import sys
import json
from datetime import datetime
from PyQt5.QtWidgets import (
    QApplication,
    QWidget,
    QLabel,
    QVBoxLayout,
    QSizePolicy,
    QMenu,
    QAction,
    QMessageBox,
    QCheckBox,
    QDesktopWidget,
    QInputDialog,
    QScrollArea,
    QFrame,
)
from PyQt5.QtGui import QPixmap, QImage, QPainter, QColor, QPen
from PyQt5.QtCore import (
    Qt,
    QPoint,
    QTimer,
    QRect,
    pyqtSignal,
    QThread,
    QMutex,
    QWaitCondition,
)
from PIL import Image, ImageFile
from pillow_heif import register_heif_opener
from collections import OrderedDict

register_heif_opener()
import subprocess


class ResizableLabel(QLabel):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.setAlignment(Qt.AlignCenter)


class ProgressIndicator(QWidget):
    """画像リスト用プログレスバー。1画像=1セグメントの精度で表示し、
    フォルダ毎に2色を交互に塗ることで境界を視認できるようにする。"""

    DONE_COLORS = ("#007bff", "#66b0ff")
    PENDING_COLORS = ((0, 123, 255, 70), (102, 176, 255, 70))

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(10)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.images = []
        self.index = 0
        self.folder_runs = []  # [(start_idx, end_idx_inclusive, parity), ...]

    def set_images(self, images, index=0):
        self.images = images
        self.index = index
        self._compute_folder_runs()
        self.update()

    def set_index(self, index):
        if index != self.index:
            self.index = index
            self.update()

    def clear(self):
        self.images = []
        self.index = 0
        self.folder_runs = []
        self.update()

    def _compute_folder_runs(self):
        self.folder_runs = []
        if not self.images:
            return
        parity = 0
        start = 0
        prev_dir = os.path.dirname(self.images[0])
        for i in range(1, len(self.images)):
            d = os.path.dirname(self.images[i])
            if d != prev_dir:
                self.folder_runs.append((start, i - 1, parity))
                parity = 1 - parity
                start = i
                prev_dir = d
        self.folder_runs.append((start, len(self.images) - 1, parity))

    def paintEvent(self, event):
        painter = QPainter(self)
        try:
            w = self.width()
            h = self.height()
            n = len(self.images)
            if n == 0 or w == 0:
                return

            done_qcolors = [QColor(c) for c in self.DONE_COLORS]
            pending_qcolors = [QColor(*c) for c in self.PENDING_COLORS]

            for start, end, parity in self.folder_runs:
                x0 = int(start * w / n)
                x_end = int((end + 1) * w / n)
                if x_end <= x0:
                    x_end = x0 + 1

                if end <= self.index:
                    painter.fillRect(x0, 0, x_end - x0, h, done_qcolors[parity])
                elif start > self.index:
                    painter.fillRect(x0, 0, x_end - x0, h, pending_qcolors[parity])
                else:
                    x_mid = int((self.index + 1) * w / n)
                    if x_mid < x0:
                        x_mid = x0
                    if x_mid > x_end:
                        x_mid = x_end
                    painter.fillRect(x0, 0, x_mid - x0, h, done_qcolors[parity])
                    painter.fillRect(x_mid, 0, x_end - x_mid, h, pending_qcolors[parity])
        finally:
            painter.end()


class ThumbnailLoader(QThread):
    """一覧表示用に元画像を読み込み、表示用サイズへ縮小した QImage を返すワーカー。
    ディスクにサムネイルファイルは作らず、元画像をその場で縮小するだけ。
    縮小結果(小さいQImage)を ThumbnailGrid 側でキャッシュし、スクロールの度に
    元の大きな画像を読み直さないようにする。"""

    thumbnailReady = pyqtSignal(str, QImage)
    thumbnailFailed = pyqtSignal(str)

    THUMB_MAX = 256  # 縮小後の最大辺(px)。セルサイズと独立にしておけば列数変更でも再生成不要。

    def __init__(self, rotate_func, parent=None):
        super().__init__(parent)
        self._rotate = rotate_func
        self._mutex = QMutex()
        self._cond = QWaitCondition()
        self._queue = []        # 読み込み待ちパス (末尾ほど優先 = 直近に要求されたもの)
        self._requested = set()  # 重複要求の防止
        self._running = True

    def request(self, paths):
        """表示に必要なパス群を要求する。既に要求済みのものは無視。"""
        self._mutex.lock()
        for p in paths:
            if p not in self._requested:
                self._requested.add(p)
                self._queue.append(p)
        self._cond.wakeAll()
        self._mutex.unlock()

    def clear(self):
        self._mutex.lock()
        self._queue.clear()
        self._requested.clear()
        self._mutex.unlock()

    def stop(self):
        self._mutex.lock()
        self._running = False
        self._cond.wakeAll()
        self._mutex.unlock()
        self.wait()

    def run(self):
        while True:
            self._mutex.lock()
            while self._running and not self._queue:
                self._cond.wait(self._mutex)
            if not self._running:
                self._mutex.unlock()
                return
            path = self._queue.pop()  # LIFO: 直近に見えたセルを優先して処理
            self._mutex.unlock()

            qimg = self._generate(path)

            self._mutex.lock()
            self._requested.discard(path)
            self._mutex.unlock()

            if qimg is None:
                self.thumbnailFailed.emit(path)
            else:
                self.thumbnailReady.emit(path, qimg)

    def _generate(self, path):
        if not os.path.exists(path):
            return None
        try:
            ImageFile.LOAD_TRUNCATED_IMAGES = True
            with open(path, "rb") as f:
                image = Image.open(f)
                try:
                    exif = image._getexif()
                    image = self._rotate(image, exif)
                except AttributeError:
                    pass
                if image.mode != "RGB":
                    image = image.convert("RGB")
                image.thumbnail((self.THUMB_MAX, self.THUMB_MAX), Image.LANCZOS)
                data = image.tobytes("raw", "RGB")
                qimg = QImage(
                    data,
                    image.size[0],
                    image.size[1],
                    image.size[0] * 3,
                    QImage.Format_RGB888,
                )
                # data は関数終了で解放されるため、独立したコピーを返す
                return qimg.copy()
        except Exception:
            return None


class ThumbnailGrid(QWidget):
    """画像を正方セルのグリッドに並べる一覧ウィジェット。
    列数を固定し、ウィンドウ幅に応じて各セルを拡大縮小する。
    表示範囲のサムネイルだけを ThumbnailLoader に遅延要求する。"""

    thumbnailClicked = pyqtSignal(int)

    PAD = 6
    SEP_THICKNESS = 2  # フォルダ区切り線の太さ(px)
    BG = QColor("#2D2D2D")
    PLACEHOLDER = QColor("#3a3a3a")
    FAILED_COLOR = QColor("#5a3a3a")
    HIGHLIGHT = QColor("#007bff")
    SEP_COLOR = QColor("#6a6a6a")  # フォルダ区切り線

    MIN_COLS = 2
    MAX_COLS = 8

    def __init__(self, loader, parent=None):
        super().__init__(parent)
        self.loader = loader
        self.images = []
        self.columns = 5
        self.current_index = 0
        self.group_by_folder = True   # フォルダの切れ目で改行するか
        self.cache = OrderedDict()    # path -> QPixmap (LRU)
        self.cache_cap = 600
        self.failed = set()
        self._index_of = {}           # path -> index (セル矩形の部分更新用)
        self._positions = []          # index -> (row, col)
        self._row_starts = []         # row -> その行の先頭 index
        self._folder_start_rows = set()  # 区切り線を引く行
        self._rows = 0
        self.scroll_area = None
        self.loader.thumbnailReady.connect(self._on_ready)
        self.loader.thumbnailFailed.connect(self._on_failed)

    def set_images(self, images, index, columns=None, group_by_folder=True):
        self.images = images
        self.current_index = index
        self.group_by_folder = group_by_folder
        if columns:
            self.columns = max(self.MIN_COLS, min(columns, self.MAX_COLS))
        self._index_of = {p: i for i, p in enumerate(images)}
        self.failed.clear()
        self.loader.clear()
        self._relayout()
        self.update()

    def set_current_index(self, index):
        self.current_index = index
        self.update()

    def set_columns(self, cols):
        self.columns = max(self.MIN_COLS, min(cols, self.MAX_COLS))
        self._relayout()
        self.update()

    def row_of(self, idx):
        if 0 <= idx < len(self._positions):
            return self._positions[idx][0]
        return 0

    def _viewport_width(self):
        if self.scroll_area is not None:
            return self.scroll_area.viewport().width()
        return self.width()

    def _cell_size(self):
        return max(40, self._viewport_width() // max(1, self.columns))

    def _build_layout(self):
        """各画像の (row, col) を確定する。group_by_folder のときは
        フォルダが変わるたびに次の行の先頭(col=0)から並べ直す。"""
        cols = self.columns
        positions = []
        row_starts = []
        folder_start_rows = set()  # 新しいフォルダが始まる行(区切り線を引く対象)
        if not self.images:
            self._positions = []
            self._row_starts = []
            self._folder_start_rows = set()
            self._rows = 0
            return

        row = 0
        col = 0
        prev_dir = None
        last_row_recorded = -1
        for p in self.images:
            if self.group_by_folder:
                d = os.path.dirname(p)
                if prev_dir is not None and d != prev_dir:
                    row += 1
                    col = 0
                    folder_start_rows.add(row)  # 先頭行(row 0)以外が対象になる
                elif col >= cols:
                    row += 1
                    col = 0
                prev_dir = d
            elif col >= cols:
                row += 1
                col = 0
            if row != last_row_recorded:
                row_starts.append(len(positions))  # この行の先頭 index
                last_row_recorded = row
            positions.append((row, col))
            col += 1

        self._positions = positions
        self._row_starts = row_starts
        self._folder_start_rows = folder_start_rows
        self._rows = row + 1

    def _relayout(self):
        self._build_layout()
        w = self._viewport_width()
        cell = max(40, w // max(1, self.columns))
        self.setFixedWidth(w)
        self.setFixedHeight(self._rows * cell if self._rows else 1)

    def _cell_rect(self, idx):
        cell = self._cell_size()
        row, col = self._positions[idx]
        return QRect(col * cell, row * cell, cell, cell)

    def _on_ready(self, path, qimg):
        pm = QPixmap.fromImage(qimg)
        self.cache[path] = pm
        self.cache.move_to_end(path)
        while len(self.cache) > self.cache_cap:
            self.cache.popitem(last=False)
        idx = self._index_of.get(path)
        if idx is not None and idx < len(self._positions):
            self.update(self._cell_rect(idx))

    def _on_failed(self, path):
        self.failed.add(path)
        idx = self._index_of.get(path)
        if idx is not None and idx < len(self._positions):
            self.update(self._cell_rect(idx))

    def paintEvent(self, event):
        painter = QPainter(self)
        try:
            painter.fillRect(event.rect(), self.BG)
            n = len(self.images)
            if n == 0 or self._rows == 0:
                return
            cell = self._cell_size()

            # 表示中のビューポート範囲から描画対象セルを決定
            if self.scroll_area is not None:
                top = self.scroll_area.verticalScrollBar().value()
                vh = self.scroll_area.viewport().height()
            else:
                top = event.rect().top()
                vh = event.rect().height()
            first_row = max(0, min(top // cell, self._rows - 1))
            last_row = max(0, min((top + vh) // cell, self._rows - 1))
            first_idx = self._row_starts[first_row]
            last_idx = (
                self._row_starts[last_row + 1] - 1
                if last_row + 1 < len(self._row_starts)
                else n - 1
            )

            need = []
            for idx in range(first_idx, last_idx + 1):
                rect = self._cell_rect(idx)
                inner = rect.adjusted(self.PAD, self.PAD, -self.PAD, -self.PAD)
                path = self.images[idx]
                pm = self.cache.get(path)
                if pm is not None:
                    self.cache.move_to_end(path)
                    scaled = pm.scaled(
                        inner.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
                    )
                    dx = inner.x() + (inner.width() - scaled.width()) // 2
                    dy = inner.y() + (inner.height() - scaled.height()) // 2
                    painter.drawPixmap(dx, dy, scaled)
                elif path in self.failed:
                    painter.fillRect(inner, self.FAILED_COLOR)
                else:
                    painter.fillRect(inner, self.PLACEHOLDER)
                    need.append(path)

                if idx == self.current_index:
                    pen = QPen(self.HIGHLIGHT)
                    pen.setWidth(3)
                    painter.setPen(pen)
                    painter.setBrush(Qt.NoBrush)
                    painter.drawRect(rect.adjusted(2, 2, -2, -2))

            # フォルダの切れ目(各フォルダ先頭行の上端)に細い区切り線を引く
            if self.group_by_folder and self._folder_start_rows:
                for r in self._folder_start_rows:
                    if first_row <= r <= last_row:
                        painter.fillRect(
                            0, r * cell, self.width(), self.SEP_THICKNESS, self.SEP_COLOR
                        )
        finally:
            painter.end()

        if need:
            self.loader.request(need)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            cell = self._cell_size()
            col = event.x() // cell
            row = event.y() // cell
            if not (0 <= row < self._rows) or col >= self.columns:
                return
            start = self._row_starts[row]
            end = (
                self._row_starts[row + 1]
                if row + 1 < len(self._row_starts)
                else len(self.images)
            )
            idx = start + col
            if idx < end:  # 行内の実セル数を超えたクリック(空白部)は無視
                self.thumbnailClicked.emit(idx)

    def wheelEvent(self, event):
        if event.modifiers() == Qt.ControlModifier:
            cell = self._cell_size()
            top = (
                self.scroll_area.verticalScrollBar().value()
                if self.scroll_area is not None
                else 0
            )
            top_idx = (top // cell) * self.columns  # 変更前に見えていた先頭セル
            if event.angleDelta().y() > 0:
                self.set_columns(self.columns - 1)  # 拡大 = 列を減らす
            else:
                self.set_columns(self.columns + 1)  # 縮小 = 列を増やす
            if self.scroll_area is not None:
                new_cell = self._cell_size()
                new_row = top_idx // self.columns
                self.scroll_area.verticalScrollBar().setValue(new_row * new_cell)
            event.accept()
        else:
            event.ignore()  # 通常スクロールは QScrollArea に委ねる


class GridScrollArea(QScrollArea):
    """ThumbnailGrid を内包するスクロール領域。リサイズ時にグリッドを再レイアウトする。"""

    def __init__(self, grid, parent=None):
        super().__init__(parent)
        self.grid = grid
        grid.scroll_area = self
        self.setWidget(grid)
        self.setWidgetResizable(False)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # 縦スクロールバーを常時表示してビューポート幅を一定に保つ
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.grid._relayout()
        self.grid.update()


class ImageViewer(QWidget):
    def __init__(self):
        super().__init__()

        self.is_loading = False
        self.index = 0
        self.images = []
        self.zoom_factor = 1.0
        self.pan_offset = QPoint(0, 0)
        self.is_panning = False
        self.pan_start_pos = QPoint(0, 0)
        self.config_path = "config.json"
        self.supported_extensions = [
            ".png", ".apng",
            ".jpg", ".jpeg", ".jfif", ".jpe", ".mpo",
            ".gif", ".bmp", ".webp",
            ".tif", ".tiff",
            ".heic", ".heif", ".heics", ".heifs", ".hif",
            ".ico", ".cur",
            ".psd", ".tga", ".dds", ".xpm",
        ]
        self.click_count = 0
        self.click_timer = QTimer()
        self.click_timer.setSingleShot(True)
        self.click_timer.timeout.connect(self.reset_click_count)
        self.is_original_size = False
        self.current_root_path = None  # ユーザーが選択した親フォルダ
        self.current_depth = 0  # 選択された階層数 (0=なし, 1-3=階層, -1=全階層)
        self.current_pickup_file = None  # 現在セッションのピックアップ保存先
        self.sort_mode = "folder"  # "folder"=フォルダ順 / "filename"=ファイル名順 (config未保存)

        if os.path.exists(self.config_path):
            with open(self.config_path, "r") as f:
                config = json.load(f)
            history = config.get("history", {})
            position = config.get("position", [0, 0])
            size = config.get("size", [800, 800])
            self.suppress_missing_file_warning = config.get("suppress_missing_file_warning", False)
            self.grid_columns = config.get("grid_columns", 5)
            self.start_maximized = config.get("maximized", False)
            # 旧フォーマットから新フォーマットへの移行
            migrated_history = {}
            for key, value in history.items():
                if isinstance(value, str):
                    # 旧形式: {dir_path: filename}
                    migrated_history[key] = {
                        "root_path": key,
                        "depth": 0,
                        "last_image_path": os.path.join(key, value) if value else None
                    }
                elif isinstance(value, dict):
                    # 新形式
                    migrated_history[key] = value
            history = migrated_history
        else:
            history = {}
            position = [0, 0]
            size = [800, 800]
            self.suppress_missing_file_warning = False
            self.grid_columns = 5
            self.start_maximized = False

        self.history = OrderedDict(history)

        self.layout = QVBoxLayout()
        self.setLayout(self.layout)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(0)

        self.progress_bar_dragging = False
        self.progress_bar = ProgressIndicator(self)
        self.progress_bar.mousePressEvent = self.progress_bar_pressed
        self.progress_bar.mouseReleaseEvent = self.progress_bar_released
        self.layout.addWidget(self.progress_bar)

        self.label = ResizableLabel()
        self.layout.addWidget(self.label)

        # 一覧 (グリッド) 表示
        self.grid_mode = False
        self.thumb_loader = ThumbnailLoader(self.rotate_image_according_to_exif)
        self.thumb_loader.start()
        self.grid = ThumbnailGrid(self.thumb_loader)
        self.grid.set_columns(self.grid_columns)
        self.grid.thumbnailClicked.connect(self.on_thumbnail_clicked)
        self.scroll_area = GridScrollArea(self.grid)
        self.scroll_area.hide()
        self.layout.addWidget(self.scroll_area)
        # グリッド側がキーボードフォーカスを奪わないようにし、左右キー等の
        # キー入力が常にメインウィンドウ(keyPressEvent)へ届くようにする
        self.scroll_area.setFocusPolicy(Qt.NoFocus)
        self.grid.setFocusPolicy(Qt.NoFocus)
        self.setFocusPolicy(Qt.StrongFocus)

        self.setAcceptDrops(True)

        self.resize(*size)
        position = self.ensure_position_on_screen(position, size)
        self.move(*position)
        # 最大化/フルスクリーンでない時の通常ジオメトリ。最大化中はウィンドウの
        # x()/y()/width()/height() が画面外にはみ出した最大化座標を返すため、
        # それをそのまま保存・復元すると開くたびに位置がずれていく。これを防ぐため
        # 通常状態のジオメトリを常時記憶し、保存時はこちらを使う。
        self._normal_pos = [position[0], position[1]]
        self._normal_size = [size[0], size[1]]
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setWindowFlags(self.windowFlags() | Qt.WindowMaximizeButtonHint)

        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self.show_context_menu)

        self.setStyleSheet(
            "ImageViewer, ResizableLabel, ProgressIndicator, "
            "GridScrollArea, ThumbnailGrid { background-color: #2D2D2D; }"
        )

    def ensure_position_on_screen(self, position, size):
        desktop = QDesktopWidget()
        x, y = position
        width, height = size

        for i in range(desktop.screenCount()):
            screen_rect = desktop.screenGeometry(i)
            window_right = x + width
            window_bottom = y + height
            if (x < screen_rect.right() and window_right > screen_rect.left() and
                y < screen_rect.bottom() and window_bottom > screen_rect.top()):
                return position

        primary_screen = desktop.screenGeometry(desktop.primaryScreen())
        new_x = max(primary_screen.left(), min(x, primary_screen.right() - width))
        new_y = max(primary_screen.top(), min(y, primary_screen.bottom() - height))
        return [new_x, new_y]

    def progress_bar_clicked(self, event):
        if self.images:
            x = event.pos().x()
            width = self.progress_bar.width()
            percentage = x / width
            index = int(percentage * (len(self.images) - 1))
            self.index = max(0, min(index, len(self.images) - 1))
            self.zoom_factor = 1.0
            self.pan_offset = QPoint(0, 0)
            self.update_image()

    def progress_bar_pressed(self, event):
        self.progress_bar_dragging = True
        self.progress_bar_clicked(event)

    def progress_bar_released(self, event):
        self.progress_bar_dragging = False

    def mouseMoveEvent(self, event):
        if self.progress_bar_dragging:
            self.progress_bar_clicked(event)
        elif event.buttons() & Qt.LeftButton:
            if not self.is_panning:
                self.is_panning = True
            delta = event.pos() - self.pan_start_pos
            self.pan_offset = self.pan_offset + delta
            self.pan_start_pos = event.pos()
            self.display_pixmap()
        super().mouseMoveEvent(event)

    def reset_click_count(self):
        self.click_count = 0

    def toggle_original_size(self):
        self.is_original_size = not self.is_original_size
        self.zoom_factor = 1.0
        self.pan_offset = QPoint(0, 0)
        if self.images:
            self.display_pixmap()

    def update_image(self):
        if self.images:
            self.load_pixmap()
            self.display_pixmap()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.pan_start_pos = event.pos()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            if not self.is_panning:
                self.click_count += 1
                self.click_timer.start(300)

                if self.click_count == 3:
                    self.click_count = 0
                    self.click_timer.stop()
                    self.toggle_original_size()
                elif self.click_count == 1:
                    x = event.x()
                    if x < self.width() * 0.25:
                        self.move_index(-1)
                    elif x > self.width() * 0.75:
                        self.move_index(1)
            self.is_panning = False

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton and (self.zoom_factor != 1.0 or self.pan_offset != QPoint(0, 0)):
            self.reset_zoom()

    def reset_zoom(self):
        self.zoom_factor = 1.0
        self.pan_offset = QPoint(0, 0)
        self.is_original_size = False
        if self.images:
            self.display_pixmap()

    def wheelEvent(self, event):
        if self.grid_mode:
            return
        delta = event.angleDelta().y()
        if event.modifiers() == Qt.ControlModifier and self.images:
            self.zoom_at_position(event.pos(), delta)
        else:
            if delta < 0:
                self.move_index(1)
            elif delta > 0:
                self.move_index(-1)

    def zoom_at_position(self, pos, delta):
        old_zoom = self.zoom_factor
        zoom_step = 1.1
        if delta > 0:
            self.zoom_factor *= zoom_step
        else:
            self.zoom_factor /= zoom_step
        self.zoom_factor = max(0.1, min(self.zoom_factor, 10.0))

        if old_zoom != self.zoom_factor:
            label_center = QPoint(self.label.width() // 2, self.label.height() // 2)
            mouse_offset = pos - label_center - self.pan_offset
            scale_ratio = self.zoom_factor / old_zoom
            new_mouse_offset = mouse_offset * scale_ratio
            self.pan_offset = pos - label_center - new_mouse_offset
            self.display_pixmap()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            if self.grid_mode:
                self.toggle_grid_mode()
            elif self.isFullScreen():
                self.showNormal()
        elif event.key() == Qt.Key_F:
            self.showFullScreen()
        elif event.key() == Qt.Key_Left:
            if not self.grid_mode:
                self.move_index(-1)
        elif event.key() == Qt.Key_Right:
            if not self.grid_mode:
                self.move_index(1)
        elif event.key() == Qt.Key_F5:
            self.reload_current_dir()

    def move_index(self, delta):
        if not self.images or self.is_loading:
            return
        self.index += delta
        self.index %= len(self.images)
        # ズーム/パン/原寸表示の状態は画像を移動しても維持する
        self.load_pixmap()
        self.display_pixmap()
        self.is_loading = False

    def load_pixmap(self):
        ImageFile.LOAD_TRUNCATED_IMAGES = True
        self.is_loading = True
        image_path = self.images[self.index]

        if not os.path.exists(image_path):
            self.handle_missing_file(image_path)
            return

        with open(image_path, "rb") as f:
            image = Image.open(f)

            creation_time = os.path.getmtime(image_path)
            dt_object = datetime.fromtimestamp(creation_time)
            formatted_time = dt_object.strftime("%Y/%m/%d(%a) %H:%M:%S")
            weekday_conversion = {
                "Mon": "月",
                "Tue": "火",
                "Wed": "水",
                "Thu": "木",
                "Fri": "金",
                "Sat": "土",
                "Sun": "日",
            }
            for eng, jp in weekday_conversion.items():
                formatted_time = formatted_time.replace(eng, jp)

            total = len(self.images)
            if total > 0:
                folder_name = os.path.basename(os.path.dirname(image_path))
                self.progress_bar.set_index(self.index)
                self.setWindowTitle(f"{folder_name} - {os.path.basename(image_path)} - {formatted_time} - {self.index + 1}/{total}")
            else:
                self.setWindowTitle("No images loaded")

            try:
                exif = image._getexif()
                image = self.rotate_image_according_to_exif(image, exif)
            except AttributeError:
                exif = None

            if image.mode != "RGB":
                image = image.convert("RGB")

            data = image.tobytes("raw", "RGB")
            qimage = QImage(
                data,
                image.size[0],
                image.size[1],
                image.size[0] * 3,
                QImage.Format_RGB888,
            )

        self.pixmap = QPixmap.fromImage(qimage)
        self.is_loading = False

    def handle_missing_file(self, image_path):
        if not self.suppress_missing_file_warning:
            msg_box = QMessageBox(self)
            msg_box.setIcon(QMessageBox.Warning)
            msg_box.setWindowTitle("ファイルが見つかりません")
            msg_box.setText(f"ファイルが存在しません:\n{image_path}")

            checkbox = QCheckBox("再度このメッセージを表示しない")
            msg_box.setCheckBox(checkbox)
            msg_box.exec_()

            if checkbox.isChecked():
                self.suppress_missing_file_warning = True

        self.images.remove(image_path)

        if not self.images:
            self.label.clear()
            self.setWindowTitle("No images loaded")
            self.progress_bar.clear()
            self.is_loading = False
            return

        if self.index >= len(self.images):
            self.index = len(self.images) - 1

        self.progress_bar.set_images(self.images, self.index)
        self.is_loading = False
        self.load_pixmap()

    def rotate_image_according_to_exif(self, image, exif):
        if exif is not None:
            orientation = exif.get(0x0112)
            if orientation == 2:
                image = image.transpose(Image.FLIP_LEFT_RIGHT)
            elif orientation == 3:
                image = image.transpose(Image.ROTATE_180)
            elif orientation == 4:
                image = image.transpose(Image.FLIP_TOP_BOTTOM)
            elif orientation == 5:
                image = image.transpose(Image.ROTATE_270).transpose(Image.FLIP_LEFT_RIGHT)
            elif orientation == 6:
                image = image.transpose(Image.ROTATE_270)
            elif orientation == 7:
                image = image.transpose(Image.ROTATE_90).transpose(Image.FLIP_LEFT_RIGHT)
            elif orientation == 8:
                image = image.transpose(Image.ROTATE_90)
        return image

    def display_pixmap(self):
        # 原寸表示モードの場合
        if self.is_original_size:
            base_pixmap = self.pixmap
        # 通常モード: 基準サイズを決定（ウィンドウより大きければ縮小、小さければ原寸）
        elif self.pixmap.width() > self.width() or self.pixmap.height() > self.height():
            base_pixmap = self.pixmap.scaled(self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        else:
            base_pixmap = self.pixmap

        if self.zoom_factor == 1.0 and self.pan_offset == QPoint(0, 0):
            self.label.setPixmap(base_pixmap)
        else:
            if self.zoom_factor == 1.0:
                display_pixmap = base_pixmap
            else:
                scaled_w = int(base_pixmap.width() * self.zoom_factor)
                scaled_h = int(base_pixmap.height() * self.zoom_factor)
                display_pixmap = self.pixmap.scaled(scaled_w, scaled_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)

            result = QPixmap(self.label.size())
            result.fill(QColor("#2D2D2D"))

            x = (self.label.width() - display_pixmap.width()) // 2 + self.pan_offset.x()
            y = (self.label.height() - display_pixmap.height()) // 2 + self.pan_offset.y()

            painter = QPainter(result)
            painter.drawPixmap(x, y, display_pixmap)
            painter.end()

            self.label.setPixmap(result)

    def _remember_normal_geometry(self):
        # 最大化/フルスクリーン中の座標は保存対象にしない (ずれの原因になるため)
        if not (self.isMaximized() or self.isFullScreen()):
            self._normal_pos = [self.x(), self.y()]
            self._normal_size = [self.width(), self.height()]

    def moveEvent(self, event):
        super().moveEvent(event)
        self._remember_normal_geometry()

    def resizeEvent(self, event):
        if self.images and not self.grid_mode:
            self.display_pixmap()
        self._remember_normal_geometry()
        super().resizeEvent(event)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if len(urls) != 1:
            return
        path = urls[0].toLocalFile()
        if os.path.isdir(path):
            dir_path = os.path.normpath(path)
            filename = None
        else:
            dir_path = os.path.normpath(os.path.dirname(path))
            filename = os.path.basename(path)
        self.load_images_from_dir(dir_path, filename)

    def load_images_from_dir(self, dir_path, filename=None, from_history=False, saved_depth=None):
        if self.images:
            self.update_history()
        self.images = []

        # ルートディレクトリを記録
        self.current_root_path = os.path.normpath(dir_path)
        self.current_depth = 0  # デフォルト値
        self.current_pickup_file = None  # フォルダ切り替え時にリセット

        def natural_sort_key(s):
            return [int(text) if text.isdigit() else text for text in re.split(r"(\d+)", s)]

        files = sorted(os.listdir(dir_path), key=natural_sort_key)
        for file in files:
            if file.lower().endswith(tuple(self.supported_extensions)):
                self.images.append(os.path.normpath(os.path.join(dir_path, file)))

        subfolders = [f.path for f in os.scandir(dir_path) if f.is_dir()]
        if subfolders:
            max_depth = None

            if from_history and saved_depth is not None:
                # 履歴から開く場合はダイアログをスキップ
                self.current_depth = saved_depth
                if saved_depth == 0:
                    max_depth = None
                elif saved_depth == -1:
                    max_depth = float("inf")
                else:
                    max_depth = saved_depth
            else:
                # ダイアログを表示
                choices = ["読み込まない", "1階層", "2階層", "3階層", "全階層"]
                choice, ok = QInputDialog.getItem(
                    self,
                    "サブフォルダの読み込み",
                    "サブフォルダ内の画像ファイルを読み込む階層を選択してください：",
                    choices,
                    0,
                    False,
                )
                if ok and choice != "読み込まない":
                    if choice == "全階層":
                        max_depth = float("inf")
                        self.current_depth = -1
                    else:
                        max_depth = int(choice[0])
                        self.current_depth = max_depth
                else:
                    self.current_depth = 0

            if max_depth is not None and max_depth > 0:
                def get_subfolders_recursive(path, current_depth, max_depth):
                    if current_depth > max_depth:
                        return []
                    folders = []
                    try:
                        for f in os.scandir(path):
                            if f.is_dir():
                                folders.append(f.path)
                                folders.extend(get_subfolders_recursive(f.path, current_depth + 1, max_depth))
                    except PermissionError:
                        pass
                    return folders

                all_subfolders = get_subfolders_recursive(dir_path, 1, max_depth)
                all_subfolders.sort(key=natural_sort_key)
                for subfolder in all_subfolders:
                    try:
                        subfiles = sorted(os.listdir(subfolder), key=natural_sort_key)
                        for subfile in subfiles:
                            if subfile.lower().endswith(tuple(self.supported_extensions)):
                                self.images.append(os.path.normpath(os.path.join(subfolder, subfile)))
                    except PermissionError:
                        pass

        self._sort_images()
        self.setup_images_and_index(dir_path, filename)

    def update_history(self):
        if not self.images or self.current_root_path is None:
            return

        current_image_path = os.path.normpath(self.images[self.index])
        root_path = self.current_root_path

        # 既存エントリを削除 (OrderedDictの末尾に移動するため)
        if root_path in self.history:
            del self.history[root_path]

        # 新形式で保存
        self.history[root_path] = {
            "root_path": root_path,
            "depth": self.current_depth,
            "last_image_path": current_image_path
        }

        # 履歴の上限を維持
        if len(self.history) > 20:
            self.history.popitem(last=False)

    def _sort_images(self):
        """現在の sort_mode に従って self.images を並べ替える。
        フォルダ順: (dirname, basename) の自然順 (構築順と等価)。
        ファイル名順: (basename, dirname) の自然順 — 同名ファイルはフォルダ名順で並ぶ。"""
        if not self.images:
            return

        def natural_sort_key(s):
            return [int(text) if text.isdigit() else text for text in re.split(r"(\d+)", s)]

        if self.sort_mode == "filename":
            self.images.sort(key=lambda p: (
                natural_sort_key(os.path.basename(p)),
                natural_sort_key(os.path.dirname(p)),
            ))
        else:  # "folder"
            self.images.sort(key=lambda p: (
                natural_sort_key(os.path.dirname(p)),
                natural_sort_key(os.path.basename(p)),
            ))

    def toggle_sort_mode(self):
        """フォルダ順 / ファイル名順 を切り替える。表示中の画像はそのまま維持。"""
        if not self.images:
            return
        current_image_path = self.images[self.index]
        self.sort_mode = "filename" if self.sort_mode == "folder" else "folder"
        self._sort_images()
        if current_image_path in self.images:
            self.index = self.images.index(current_image_path)
        self.progress_bar.set_images(self.images, self.index)
        if self.grid_mode:
            self.grid.set_images(
                self.images,
                self.index,
                self.grid_columns,
                group_by_folder=(self.sort_mode == "folder"),
            )
            QTimer.singleShot(0, self._scroll_grid_to_current)
        else:
            self.load_pixmap()
            self.display_pixmap()

    def toggle_grid_mode(self):
        """一覧 (グリッド) 表示 と 1枚表示 を切り替える。"""
        if not self.images and not self.grid_mode:
            return
        self.grid_mode = not self.grid_mode
        if self.grid_mode:
            self.label.hide()
            self.progress_bar.hide()
            self.grid.set_images(
                self.images,
                self.index,
                self.grid_columns,
                group_by_folder=(self.sort_mode == "folder"),
            )
            self.scroll_area.show()
            self.setWindowTitle(f"一覧表示 - {len(self.images)}枚")
            # レイアウト確定後に現在の画像までスクロール
            QTimer.singleShot(0, self._scroll_grid_to_current)
        else:
            self.scroll_area.hide()
            self.progress_bar.show()
            self.label.show()
            self.setFocus()
            if self.images:
                self.load_pixmap()
                self.display_pixmap()

    def _scroll_grid_to_current(self):
        if not self.images:
            return
        cell = self.grid._cell_size()
        row = self.grid.row_of(self.index)
        vh = self.scroll_area.viewport().height()
        y = max(0, row * cell - (vh - cell) // 2)
        self.scroll_area.verticalScrollBar().setValue(y)
        self.grid.update()

    def on_thumbnail_clicked(self, idx):
        """一覧でサムネイルをクリック: その画像を1枚表示で開く。"""
        if not (0 <= idx < len(self.images)):
            return
        self.index = idx
        self.zoom_factor = 1.0
        self.pan_offset = QPoint(0, 0)
        self.is_original_size = False
        self.grid_mode = False
        self.scroll_area.hide()
        self.progress_bar.show()
        self.label.show()
        self.setFocus()
        self.progress_bar.set_index(self.index)
        self.load_pixmap()
        self.display_pixmap()

    def setup_images_and_index(self, dir_path, filename=None, last_image_path=None):
        if self.images:
            self.index = 0  # デフォルト

            if filename:
                # ファイルを直接ドロップした場合: ファイル名で検索
                image_filenames = [os.path.basename(img_path) for img_path in self.images]
                if filename in image_filenames:
                    self.index = image_filenames.index(filename)
            elif last_image_path:
                # 履歴から開いた場合: フルパスで検索
                normalized_paths = [os.path.normpath(p) for p in self.images]
                normalized_last = os.path.normpath(last_image_path)
                if normalized_last in normalized_paths:
                    self.index = normalized_paths.index(normalized_last)

            self.zoom_factor = 1.0
            self.pan_offset = QPoint(0, 0)
            self.is_original_size = False
            self.update_history()
            self.progress_bar.set_images(self.images, self.index)
            self.load_pixmap()
            self.display_pixmap()
            if self.grid_mode:
                self.grid.set_images(
                self.images,
                self.index,
                self.grid_columns,
                group_by_folder=(self.sort_mode == "folder"),
            )
                QTimer.singleShot(0, self._scroll_grid_to_current)

    def load_from_history(self, root_path, history_entry):
        """履歴エントリから画像を読み込む"""
        saved_depth = history_entry.get("depth", 0)
        last_image_path = history_entry.get("last_image_path")

        # 画像を読み込み (ダイアログをスキップ)
        self.load_images_from_dir(
            root_path,
            filename=None,
            from_history=True,
            saved_depth=saved_depth
        )

        # 最後に見ていた画像の位置を復元
        if last_image_path and self.images:
            normalized_paths = [os.path.normpath(p) for p in self.images]
            normalized_last = os.path.normpath(last_image_path)
            if normalized_last in normalized_paths:
                self.index = normalized_paths.index(normalized_last)
                self.load_pixmap()
                self.display_pixmap()

    def show_context_menu(self, position):
        context_menu = QMenu(self)
        for dir_path in reversed(list(self.history.keys())):
            if os.path.exists(dir_path):
                history_entry = self.history[dir_path]
                dir_menu = context_menu.addMenu(dir_path)

                open_action = QAction("Open", self)
                open_action.triggered.connect(
                    lambda _, d=dir_path, entry=history_entry: self.load_from_history(d, entry)
                )
                dir_menu.addAction(open_action)

                delete_action = QAction("Delete from history", self)
                delete_action.triggered.connect(lambda _, d=dir_path: self.delete_from_history(d))
                dir_menu.addAction(delete_action)

        if self.images:
            current_dir = os.path.normpath(os.path.dirname(self.images[self.index]))

            grid_label = "1枚表示に戻る" if self.grid_mode else "一覧表示"
            grid_action = QAction(grid_label, self)
            grid_action.triggered.connect(self.toggle_grid_mode)
            context_menu.addAction(grid_action)

            reload_action = QAction("再読み込み (F5)", self)
            reload_action.triggered.connect(self.reload_current_dir)
            context_menu.addAction(reload_action)

            sort_label = "ファイル名順で並べ替え" if self.sort_mode == "folder" else "フォルダ順に戻す"
            sort_action = QAction(sort_label, self)
            sort_action.triggered.connect(self.toggle_sort_mode)
            context_menu.addAction(sort_action)

            pickup_action = QAction("ファイル名の記録", self)
            pickup_action.triggered.connect(self.pickup_current_image)
            context_menu.addAction(pickup_action)

            open_in_explorer_action = QAction("###Open current dir in explorer###", self)
            open_in_explorer_action.triggered.connect(lambda: self.open_in_explorer(current_dir))
            context_menu.addAction(open_in_explorer_action)
        context_menu.exec_(self.mapToGlobal(position))

    def delete_from_history(self, dir_path):
        reply = QMessageBox.warning(
            self,
            "History deletion",
            f"Are you sure you want to delete {dir_path} from history?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            if dir_path in self.history:
                del self.history[dir_path]

                # current_root_path を使用して比較
                if self.images and dir_path == self.current_root_path:
                    self.images = []
                    self.label.clear()
                    self.progress_bar.clear()
                    self.current_root_path = None
                    self.current_depth = 0

    def reload_current_dir(self):
        """現在のフォルダを再スキャンして画像リストを更新。表示中の画像はそのまま維持。"""
        if self.current_root_path is None or not os.path.exists(self.current_root_path):
            return

        current_image_path = (
            os.path.normpath(self.images[self.index]) if self.images else None
        )

        def natural_sort_key(s):
            return [int(text) if text.isdigit() else text for text in re.split(r"(\d+)", s)]

        new_images: list[str] = []
        try:
            files = sorted(os.listdir(self.current_root_path), key=natural_sort_key)
        except OSError:
            return
        for file in files:
            if file.lower().endswith(tuple(self.supported_extensions)):
                new_images.append(os.path.normpath(os.path.join(self.current_root_path, file)))

        if self.current_depth != 0:
            max_depth = float("inf") if self.current_depth == -1 else self.current_depth

            def get_subfolders_recursive(path, depth, max_depth):
                if depth > max_depth:
                    return []
                folders = []
                try:
                    for f in os.scandir(path):
                        if f.is_dir():
                            folders.append(f.path)
                            folders.extend(get_subfolders_recursive(f.path, depth + 1, max_depth))
                except (PermissionError, OSError):
                    pass
                return folders

            all_subfolders = get_subfolders_recursive(self.current_root_path, 1, max_depth)
            all_subfolders.sort(key=natural_sort_key)
            for subfolder in all_subfolders:
                try:
                    subfiles = sorted(os.listdir(subfolder), key=natural_sort_key)
                    for subfile in subfiles:
                        if subfile.lower().endswith(tuple(self.supported_extensions)):
                            new_images.append(os.path.normpath(os.path.join(subfolder, subfile)))
                except (PermissionError, OSError):
                    pass

        old_count = len(self.images)
        self.images = new_images
        self._sort_images()
        new_count = len(self.images)

        if not self.images:
            self.label.clear()
            self.setWindowTitle("No images loaded")
            self.progress_bar.clear()
            return

        if current_image_path and current_image_path in self.images:
            # 表示中画像はそのまま: index 復元、タイトル/プログレスバーのみ更新
            self.index = self.images.index(current_image_path)
            self.progress_bar.set_images(self.images, self.index)
            self.load_pixmap()  # title と progress を最新の総数で更新
        else:
            # 表示中画像が消えた場合は近傍にフォールバック
            self.index = min(self.index, len(self.images) - 1)
            self.zoom_factor = 1.0
            self.pan_offset = QPoint(0, 0)
            self.is_original_size = False
            self.progress_bar.set_images(self.images, self.index)
            self.load_pixmap()
            self.display_pixmap()

        if self.grid_mode:
            self.grid.set_images(
                self.images,
                self.index,
                self.grid_columns,
                group_by_folder=(self.sort_mode == "folder"),
            )

        diff = new_count - old_count
        if diff > 0:
            print(f"[reload] +{diff} files ({old_count} -> {new_count})")
        elif diff < 0:
            print(f"[reload] {diff} files ({old_count} -> {new_count})")

    def pickup_current_image(self):
        if not self.images or self.current_root_path is None:
            return
        current_image = self.images[self.index]
        try:
            rel_path = os.path.relpath(current_image, self.current_root_path)
        except ValueError:
            # 異なるドライブなど relpath が計算できない場合は絶対パスにフォールバック
            rel_path = current_image
        rel_path = rel_path.replace(os.sep, "/")

        if self.current_pickup_file is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            self.current_pickup_file = os.path.join(
                self.current_root_path,
                f"imageviewer_pickup_{timestamp}.txt",
            )

        try:
            with open(self.current_pickup_file, "a", encoding="utf-8") as f:
                f.write(rel_path + "\n")
        except OSError as e:
            QMessageBox.warning(
                self,
                "保存失敗",
                f"ピックアップファイルへの書き込みに失敗しました:\n{e}",
            )

    def open_in_explorer(self, path):
        if sys.platform == "win32":
            subprocess.Popen(["explorer", os.path.normpath(path)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", os.path.normpath(path)])
        else:
            subprocess.Popen(["xdg-open", os.path.normpath(path)])

    def closeEvent(self, event):
        if self.images:
            self.update_history()
        self.thumb_loader.stop()
        config = {
            "history": self.history,
            "position": self._normal_pos,
            "size": self._normal_size,
            "maximized": self.isMaximized(),
            "suppress_missing_file_warning": self.suppress_missing_file_warning,
            "grid_columns": self.grid.columns,
        }
        with open(self.config_path, "w") as f:
            json.dump(config, f)
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)

    viewer = ImageViewer()
    if viewer.start_maximized:
        viewer.showMaximized()
    else:
        viewer.show()

    if len(sys.argv) > 1:
        arg_path = sys.argv[1]
        if os.path.exists(arg_path):
            if os.path.isdir(arg_path):
                dir_path = os.path.normpath(arg_path)
                filename = None
            else:
                dir_path = os.path.normpath(os.path.dirname(arg_path))
                filename = os.path.basename(arg_path)
            viewer.load_images_from_dir(dir_path, filename)

    sys.exit(app.exec_())
