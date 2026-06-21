"""ImageViewer の補助ウィジェット群。

image_viewer.py から分離した、表示用の自己完結したウィジェット群:
ResizableLabel / ProgressIndicator / ThumbnailLoader / ThumbnailGrid /
GridScrollArea。

HEIF のオープナ登録 (register_heif_opener) は import 元の image_viewer.py が
起動時に行うため、ここでは行わない (サムネイル生成は実行時=登録後)。
"""
import os
from collections import OrderedDict
from PyQt5.QtWidgets import (
    QLabel,
    QWidget,
    QSizePolicy,
    QScrollArea,
    QFrame,
)
from PyQt5.QtGui import QPixmap, QImage, QPainter, QColor, QPen
from PyQt5.QtCore import (
    Qt,
    QRect,
    QPointF,
    pyqtSignal,
    QThread,
    QMutex,
    QWaitCondition,
)
from PIL import Image, ImageFile


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
        self.group_keys = None  # 色替えの基準キー(list)。None ならフォルダで区切る
        self.folder_runs = []  # [(start_idx, end_idx_inclusive, parity), ...]

    def set_images(self, images, index=0, group_keys=None):
        self.images = images
        self.index = index
        self.group_keys = group_keys
        self._compute_folder_runs()
        self.update()

    def set_index(self, index):
        if index != self.index:
            self.index = index
            self.update()

    def clear(self):
        self.images = []
        self.index = 0
        self.group_keys = None
        self.folder_runs = []
        self.update()

    def _compute_folder_runs(self):
        self.folder_runs = []
        if not self.images:
            return
        keys = self.group_keys
        if keys is None:
            keys = [os.path.dirname(p) for p in self.images]
        parity = 0
        start = 0
        prev = keys[0]
        for i in range(1, len(self.images)):
            if keys[i] != prev:
                self.folder_runs.append((start, i - 1, parity))
                parity = 1 - parity
                start = i
                prev = keys[i]
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

    # 生成に使った最大辺(gen_max)も併せて通知する。セルが大きい(列が少ない)
    # ときに低解像のままだとボケるため、グリッド側が要求解像度を引き上げ、
    # それより低い解像度でキャッシュ済みのセルだけ取り直せるようにする。
    thumbnailReady = pyqtSignal(str, QImage, int)
    thumbnailFailed = pyqtSignal(str)

    THUMB_MAX = 256  # 縮小後の最大辺の既定値(px)。グリッドが set_thumb_max で上書きする。

    def __init__(self, rotate_func, parent=None):
        super().__init__(parent)
        self._rotate = rotate_func
        self._mutex = QMutex()
        self._cond = QWaitCondition()
        self._queue = []        # 読み込み待ちパス (末尾ほど優先 = 直近に要求されたもの)
        self._requested = set()  # 重複要求の防止
        self._running = True
        self._thumb_max = self.THUMB_MAX  # 現在の生成解像度(セルサイズに応じて変動)

    def set_thumb_max(self, n):
        """以後生成するサムネイルの最大辺(px)を設定する。"""
        self._thumb_max = max(1, int(n))

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

            result = self._generate(path)

            self._mutex.lock()
            self._requested.discard(path)
            self._mutex.unlock()

            if result is None:
                self.thumbnailFailed.emit(path)
            else:
                qimg, gen_max = result
                self.thumbnailReady.emit(path, qimg, gen_max)

    def _generate(self, path):
        if not os.path.exists(path):
            return None
        thumb_max = self._thumb_max  # 取り出し時点の要求解像度を採用し、それを通知する
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
                image.thumbnail((thumb_max, thumb_max), Image.LANCZOS)
                data = image.tobytes("raw", "RGB")
                qimg = QImage(
                    data,
                    image.size[0],
                    image.size[1],
                    image.size[0] * 3,
                    QImage.Format_RGB888,
                )
                # data は関数終了で解放されるため、独立したコピーを返す
                return qimg.copy(), thumb_max
        except Exception:
            return None


class ThumbnailGrid(QWidget):
    """画像を正方セルのグリッドに並べる一覧ウィジェット。
    列数を固定し、ウィンドウ幅に応じて各セルを拡大縮小する。
    表示範囲のサムネイルだけを ThumbnailLoader に遅延要求する。"""

    thumbnailSelected = pyqtSignal(int)   # 単クリック/カーソルキーで選択(青枠移動)
    thumbnailActivated = pyqtSignal(int)  # ダブルクリックで1枚表示を開く

    PAD = 6
    SEP_THICKNESS = 2  # フォルダ区切り線の太さ(px)
    BG = QColor("#2D2D2D")
    PLACEHOLDER = QColor("#3a3a3a")
    FAILED_COLOR = QColor("#5a3a3a")
    HIGHLIGHT = QColor("#007bff")
    SEP_COLOR = QColor("#6a6a6a")  # フォルダ区切り線

    MIN_COLS = 2
    MAX_COLS = 8

    # サムネイル解像度の調整。セル(=表示サイズ)が大きいほど高解像で生成する。
    THUMB_MIN = 256        # 最小解像度(列が多くセルが小さいとき)
    THUMB_MAX_CAP = 768    # 最大解像度(列が少なくセルが大きいとき)。メモリ/CPU の上限
    THUMB_STEP = 128       # 量子化幅。リサイズの度に作り直さないよう段階化する

    def __init__(self, loader, parent=None):
        super().__init__(parent)
        self.loader = loader
        self.images = []
        self.columns = 5
        self.current_index = 0
        self.group_keys = None        # 各画像のグループ化キー(list) / None=区切りなし
        self.cache = OrderedDict()    # path -> (QPixmap, gen_max) (LRU)
        # キャッシュは件数ではなく総ピクセル面積で上限を設ける。こうすると高解像
        # サムネイルは少なく、低解像なら多く保持でき、解像度が変動してもメモリが
        # 概ね一定(約 600 枚 × 256² ≒ 157MB 相当)に収まる。
        self.cache_area_budget = 600 * 256 * 256
        self._cache_area = 0          # 現在のキャッシュ総ピクセル数
        self.failed = set()
        self._index_of = {}           # path -> index (セル矩形の部分更新用)
        self._positions = []          # index -> (row, col)
        self._row_starts = []         # row -> その行の先頭 index
        self._group_start_rows = set()  # 区切り線を引く行 (グループ先頭行)
        self._rows = 0
        self._press_idx = None        # 押下時のセル index (解放時に同一なら発火)
        self.scroll_area = None
        self.loader.thumbnailReady.connect(self._on_ready)
        self.loader.thumbnailFailed.connect(self._on_failed)

    def set_images(self, images, index, columns=None, group_keys=None):
        self.images = images
        self.current_index = index
        # group_keys: 画像と同じ長さのキー列。隣り合うキーが変わる所でグループ
        # (= 改行 + 区切り線) を作る。None なら区切りなしの連続フロー。
        self.group_keys = group_keys
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

    def index_in_direction(self, idx, drow, dcol):
        """現在 index から上下左右へ1セル移動した先の index を返す。
        左右は読み順で線形移動、上下は同じ列で隣の行へ(グループ改行も考慮)。
        範囲外なら idx をそのまま返す。"""
        if not self.images or idx is None or not (0 <= idx < len(self._positions)):
            return idx
        if dcol:
            new = idx + dcol
            return new if 0 <= new < len(self.images) else idx
        if drow:
            row, col = self._positions[idx]
            nrow = row + drow
            if not (0 <= nrow < self._rows):
                return idx
            start = self._row_starts[nrow]
            end = (
                self._row_starts[nrow + 1]
                if nrow + 1 < len(self._row_starts)
                else len(self.images)
            )
            return min(start + col, end - 1)  # その行が短ければ行末へ
        return idx

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
        """各画像の (row, col) を確定する。group_keys があるときは
        キーが変わるたびに次の行の先頭(col=0)から並べ直す。"""
        cols = self.columns
        keys = self.group_keys
        positions = []
        row_starts = []
        group_start_rows = set()  # 新しいグループが始まる行(区切り線を引く対象)
        if not self.images:
            self._positions = []
            self._row_starts = []
            self._group_start_rows = set()
            self._rows = 0
            return

        row = 0
        col = 0
        prev_key = None
        last_row_recorded = -1
        for i in range(len(self.images)):
            if keys is not None:
                k = keys[i]
                if prev_key is not None and k != prev_key:
                    row += 1
                    col = 0
                    group_start_rows.add(row)  # 先頭行(row 0)以外が対象になる
                elif col >= cols:
                    row += 1
                    col = 0
                prev_key = k
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
        self._group_start_rows = group_start_rows
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

    def _cache_put(self, path, pm, gen_max):
        """サムネイルをキャッシュへ格納し、総面積が上限を超えたら LRU で退避する。"""
        old = self.cache.pop(path, None)
        if old is not None:
            self._cache_area -= old[0].width() * old[0].height()
        self.cache[path] = (pm, gen_max)
        self._cache_area += pm.width() * pm.height()
        while self._cache_area > self.cache_area_budget and len(self.cache) > 1:
            _p, (opm, _g) = self.cache.popitem(last=False)
            self._cache_area -= opm.width() * opm.height()

    def _desired_thumb_max(self):
        """現在のセルサイズ(と高DPI倍率)から望ましいサムネイル解像度を求める。
        リサイズの度に作り直さないよう THUMB_STEP 単位に切り上げて段階化する。"""
        cell = self._cell_size()
        inner = max(1, cell - 2 * self.PAD)
        target = inner * self.devicePixelRatioF()
        step = self.THUMB_STEP
        quantized = ((int(target) + step - 1) // step) * step
        return max(self.THUMB_MIN, min(quantized, self.THUMB_MAX_CAP))

    def _on_ready(self, path, qimg, gen_max):
        pm = QPixmap.fromImage(qimg)
        self._cache_put(path, pm, gen_max)
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

            desired = self._desired_thumb_max()
            dpr = self.devicePixelRatioF()
            need = []
            for idx in range(first_idx, last_idx + 1):
                rect = self._cell_rect(idx)
                inner = rect.adjusted(self.PAD, self.PAD, -self.PAD, -self.PAD)
                path = self.images[idx]
                entry = self.cache.get(path)
                if entry is not None:
                    pm, gen_max = entry
                    self.cache.move_to_end(path)
                    # 高DPIでは物理ピクセル数まで拡大してから dpr を設定すると、
                    # 縮小描画でディテールが失われずくっきり表示される。
                    tw = max(1, int(inner.width() * dpr))
                    th = max(1, int(inner.height() * dpr))
                    scaled = pm.scaled(
                        tw, th, Qt.KeepAspectRatio, Qt.SmoothTransformation
                    )
                    scaled.setDevicePixelRatio(dpr)
                    lw = scaled.width() / dpr
                    lh = scaled.height() / dpr
                    dx = inner.x() + (inner.width() - lw) / 2
                    dy = inner.y() + (inner.height() - lh) / 2
                    painter.drawPixmap(QPointF(dx, dy), scaled)
                    # セルが大きくなり、今より高い解像度が必要なら取り直す
                    # (それまでは現在の低解像をそのまま表示し続ける)
                    if gen_max < desired:
                        need.append(path)
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

            # グループの切れ目(各グループ先頭行の上端)に細い区切り線を引く
            if self.group_keys is not None and self._group_start_rows:
                for r in self._group_start_rows:
                    if first_row <= r <= last_row:
                        painter.fillRect(
                            0, r * cell, self.width(), self.SEP_THICKNESS, self.SEP_COLOR
                        )
        finally:
            painter.end()

        if need:
            self.loader.set_thumb_max(desired)  # 以後の生成を現在のセルに見合う解像度へ
            self.loader.request(need)

    def _index_at(self, pos):
        """ウィジェット座標からセル index を返す。空白部は None。"""
        if self._rows == 0:
            return None
        cell = self._cell_size()
        col = pos.x() // cell
        row = pos.y() // cell
        if not (0 <= row < self._rows) or col < 0 or col >= self.columns:
            return None
        start = self._row_starts[row]
        end = (
            self._row_starts[row + 1]
            if row + 1 < len(self._row_starts)
            else len(self.images)
        )
        idx = start + col
        if idx < end:  # 行内の実セル数を超えたクリック(空白部)は無視
            return idx
        return None

    def mousePressEvent(self, event):
        # 押下 index を記録し、押下/解放の双方をここで消費する。こうしないと、
        # ダブルクリックで一覧を隠した直後に解放イベントがメインウィンドウへ
        # 伝播し、左右クリックナビゲーション(move_index)を誤発火して隣の画像が
        # 開いてしまう。
        if event.button() == Qt.LeftButton:
            self._press_idx = self._index_at(event.pos())
            event.accept()
        else:
            super().mousePressEvent(event)  # 右クリック等は既定動作(コンテキストメニュー)へ

    def mouseReleaseEvent(self, event):
        # 単クリックは「選択」(青枠の移動)のみ。表示の切替はダブルクリックで行う。
        if event.button() == Qt.LeftButton:
            idx = self._index_at(event.pos())
            press = self._press_idx
            self._press_idx = None
            event.accept()
            if idx is not None and idx == press:
                self.current_index = idx
                self.update()
                self.thumbnailSelected.emit(idx)
        else:
            super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        # ダブルクリックでその画像を1枚表示で開く。
        if event.button() == Qt.LeftButton:
            idx = self._index_at(event.pos())
            event.accept()
            if idx is not None:
                self.current_index = idx
                self.update()
                self.thumbnailActivated.emit(idx)
        else:
            super().mouseDoubleClickEvent(event)

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
