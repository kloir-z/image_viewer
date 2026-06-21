import os
import re
import sys
import json
import shutil
import tempfile
import subprocess
from datetime import datetime
from collections import OrderedDict
from PyQt5.QtWidgets import (
    QApplication,
    QWidget,
    QVBoxLayout,
    QSizePolicy,
    QMenu,
    QAction,
    QMessageBox,
    QCheckBox,
    QDesktopWidget,
    QInputDialog,
)
from PyQt5.QtGui import QPixmap, QImage, QPainter, QColor
from PyQt5.QtCore import Qt, QPoint, QTimer
from PIL import Image, ImageFile
from pillow_heif import register_heif_opener
from send2trash import send2trash
from widgets import (
    ResizableLabel,
    ProgressIndicator,
    ThumbnailLoader,
    ThumbnailGrid,
    GridScrollArea,
)

register_heif_opener()


class ImageViewer(QWidget):
    def __init__(self):
        super().__init__()

        self.is_loading = False
        self.index = 0
        self.images = []
        self.pixmap = None  # 画像未読み込み時は None (display_pixmap でガード)
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

        # 削除のアンドゥ (アプリ管理ゴミ箱方式)。削除は一時フォルダへ退避し、
        # Ctrl+Z で元の場所へ確実に戻す。終了時に残りを実ゴミ箱へ送る。
        self._undo_stack = []      # 削除のアンドゥ履歴 (LIFO)
        self._trash_dir = None     # セッション用の一時退避フォルダ
        self._trash_counter = 0    # 退避サブフォルダの連番

        # F5 を押さなくても定期的にフォルダを再スキャンし、追加/削除を自動反映する。
        # reload_current_dir は変化がなければ何もしないため、無駄な再描画はない。
        self.auto_reload_timer = QTimer(self)
        self.auto_reload_timer.setInterval(5000)
        self.auto_reload_timer.timeout.connect(self._auto_reload_tick)
        self.auto_reload_timer.start()
        self.is_original_size = False
        self.current_root_path = None  # ユーザーが選択した親フォルダ (複数選択時は共通の親)
        self.current_roots = []  # 実際に読み込んだフォルダ群 (単一選択時は [current_root_path])
        self.current_depth = 0  # 選択された階層数 (0=なし, 1-3=階層, -1=全階層)
        self.sort_mode = "folder"  # "folder"/"filename"/"mtime" (config未保存)

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
        self.grid.thumbnailSelected.connect(self.on_thumbnail_selected)
        self.grid.thumbnailActivated.connect(self.on_thumbnail_activated)
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
                self.toggle_grid_mode()       # 一覧 → 1枚表示
            elif self.isFullScreen():
                self.showNormal()
            elif self.images:
                self.toggle_grid_mode()       # 1枚表示 → 一覧
        elif event.key() == Qt.Key_F:
            self.showFullScreen()
        elif event.key() == Qt.Key_Left:
            if self.grid_mode:
                self._grid_move(0, -1)
            else:
                self.move_index(-1)
        elif event.key() == Qt.Key_Right:
            if self.grid_mode:
                self._grid_move(0, 1)
            else:
                self.move_index(1)
        elif event.key() == Qt.Key_Up:
            if self.grid_mode:
                self._grid_move(-1, 0)
        elif event.key() == Qt.Key_Down:
            if self.grid_mode:
                self._grid_move(1, 0)
        elif event.key() in (Qt.Key_Return, Qt.Key_Enter):
            if self.grid_mode:
                self.on_thumbnail_activated(self.index)
        elif event.key() == Qt.Key_F5:
            self.reload_current_dir()
        elif event.key() == Qt.Key_S:
            self.cycle_sort_mode()
        elif event.key() == Qt.Key_Delete:
            self.delete_current()
        elif event.key() == Qt.Key_Z and event.modifiers() & Qt.ControlModifier:
            self.undo_delete()

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

        self.progress_bar.set_images(self.images, self.index, self._progress_group_keys())
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
        # まだ画像を読み込んでいない場合は何もしない。
        # 例: サブフォルダ階層ダイアログ表示中に resizeEvent が割り込むと、
        # self.images は非空でも self.pixmap が未生成のことがある。
        if self.pixmap is None:
            return
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
        if not urls:
            return
        dirs = []
        filename = None
        for url in urls:
            path = url.toLocalFile()
            if os.path.isdir(path):
                dirs.append(os.path.normpath(path))
            elif os.path.exists(path):
                dirs.append(os.path.normpath(os.path.dirname(path)))
                if filename is None:
                    filename = os.path.basename(path)
        if not dirs:
            return
        # load_images_from_dirs はフォルダが1つなら単一読み込みに委譲する
        self.load_images_from_dirs(dirs, filename)

    @staticmethod
    def _natural_sort_key(s):
        return [int(text) if text.isdigit() else text for text in re.split(r"(\d+)", s)]

    def _has_subfolders(self, dir_path):
        try:
            return any(f.is_dir() for f in os.scandir(dir_path))
        except OSError:
            return False

    @staticmethod
    def _depth_to_max(depth):
        """current_depth (0=なし / -1=全階層 / 1-3=階層) を再帰walk用の max_depth に変換。"""
        if depth == 0:
            return None
        if depth == -1:
            return float("inf")
        return depth

    def _ask_subfolder_depth(self):
        """サブフォルダ読み込み階層をダイアログで尋ね、max_depth を返す。
        併せて self.current_depth (0/-1/1-3) を設定する。キャンセル/読み込まないは 0。"""
        choices = ["読み込まない", "1階層", "2階層", "3階層", "全階層"]
        choice, ok = QInputDialog.getItem(
            self,
            "サブフォルダの読み込み",
            "サブフォルダ内の画像ファイルを読み込む階層を選択してください：",
            choices,
            4,  # 既定を「全階層」にする
            False,
        )
        if ok and choice != "読み込まない":
            if choice == "全階層":
                self.current_depth = -1
                return float("inf")
            self.current_depth = int(choice[0])
            return self.current_depth
        self.current_depth = 0
        return None

    def _collect_dir_images(self, dir_path, max_depth):
        """dir_path 直下 + (max_depth に応じた) サブフォルダ内の対応画像パスを順に集めて返す。"""
        result = []
        try:
            files = sorted(os.listdir(dir_path), key=self._natural_sort_key)
        except OSError:
            return result
        for file in files:
            if file.lower().endswith(tuple(self.supported_extensions)):
                result.append(os.path.normpath(os.path.join(dir_path, file)))

        if max_depth is not None and max_depth > 0:
            def get_subfolders_recursive(path, depth):
                if depth > max_depth:
                    return []
                folders = []
                try:
                    for f in os.scandir(path):
                        if f.is_dir():
                            folders.append(f.path)
                            folders.extend(get_subfolders_recursive(f.path, depth + 1))
                except (PermissionError, OSError):
                    pass
                return folders

            all_subfolders = get_subfolders_recursive(dir_path, 1)
            all_subfolders.sort(key=self._natural_sort_key)
            for subfolder in all_subfolders:
                try:
                    subfiles = sorted(os.listdir(subfolder), key=self._natural_sort_key)
                    for subfile in subfiles:
                        if subfile.lower().endswith(tuple(self.supported_extensions)):
                            result.append(os.path.normpath(os.path.join(subfolder, subfile)))
                except (PermissionError, OSError):
                    pass
        return result

    def load_images_from_dir(self, dir_path, filename=None, from_history=False, saved_depth=None):
        if self.images:
            self.update_history()

        # ルートディレクトリを記録
        self.current_root_path = os.path.normpath(dir_path)
        self.current_roots = [self.current_root_path]
        self.current_depth = 0  # デフォルト値

        # サブフォルダ階層の決定
        max_depth = None
        if self._has_subfolders(self.current_root_path):
            if from_history and saved_depth is not None:
                # 履歴から開く場合はダイアログをスキップ
                self.current_depth = saved_depth
                max_depth = self._depth_to_max(saved_depth)
            else:
                max_depth = self._ask_subfolder_depth()

        self.images = self._collect_dir_images(self.current_root_path, max_depth)
        self._sort_images()
        self.setup_images_and_index(self.current_root_path, filename)

    def load_images_from_dirs(self, dir_paths, filename=None):
        """複数フォルダをまとめて読み込む。サブフォルダ階層のダイアログは
        1回だけ尋ね、選択した全フォルダに共通で適用する。
        有効なフォルダが1つなら load_images_from_dir に委譲する。"""
        dirs = []
        for d in dir_paths:
            nd = os.path.normpath(d)
            if os.path.isdir(nd) and nd not in dirs:
                dirs.append(nd)
        if not dirs:
            return
        if len(dirs) == 1:
            self.load_images_from_dir(dirs[0], filename)
            return

        if self.images:
            self.update_history()

        self.current_roots = dirs
        # 相対パス等の基準として共通の親フォルダを記録
        try:
            self.current_root_path = os.path.normpath(os.path.commonpath(dirs))
        except ValueError:
            # 別ドライブ等で共通パスが取れない場合は先頭フォルダを基準にする
            self.current_root_path = dirs[0]
        self.current_depth = 0

        # いずれかのフォルダにサブフォルダがあれば階層を1回だけ尋ねる
        has_sub = any(self._has_subfolders(d) for d in dirs)
        max_depth = self._ask_subfolder_depth() if has_sub else None

        images = []
        for d in dirs:
            images.extend(self._collect_dir_images(d, max_depth))
        self.images = images
        self._sort_images()
        self.setup_images_and_index(self.current_root_path, filename)

    @staticmethod
    def _is_under(path, root):
        """path が root 配下 (または一致) かを判定する。
        画像パスは root を起点に join して構築されるため接頭辞比較で十分。"""
        path = os.path.normpath(path)
        root = os.path.normpath(root)
        return path == root or path.startswith(root + os.sep)

    def update_history(self):
        if not self.images or not self.current_roots:
            return

        current_image_path = os.path.normpath(self.images[self.index])

        # 読み込んだ各フォルダを個別エントリとして記録する。
        # 表示中の画像が属するフォルダはその画像を、それ以外は先頭画像を
        # last_image_path として保存する (後から1つずつ開き直せる)。
        for root in self.current_roots:
            root = os.path.normpath(root)
            if self._is_under(current_image_path, root):
                last_img = current_image_path
            else:
                last_img = next(
                    (os.path.normpath(p) for p in self.images
                     if self._is_under(os.path.normpath(p), root)),
                    None,
                )

            # 既存エントリを削除 (OrderedDictの末尾に移動するため)
            if root in self.history:
                del self.history[root]

            self.history[root] = {
                "root_path": root,
                "depth": self.current_depth,
                "last_image_path": last_img,
            }

        # 履歴の上限を維持
        while len(self.history) > 20:
            self.history.popitem(last=False)

    def _sorted_images(self, images):
        """与えられたパス列を現在の sort_mode に従って並べ替えた新しいリストを返す。
        フォルダ順: (dirname, basename) の自然順 (構築順と等価)。
        ファイル名順: (basename, dirname) の自然順 — 同名ファイルはフォルダ名順で並ぶ。
        更新日時順: 更新日時の新しい順 (降順)。同時刻は (dirname, basename) 順。"""
        def natural_sort_key(s):
            return [int(text) if text.isdigit() else text for text in re.split(r"(\d+)", s)]

        if self.sort_mode == "filename":
            key = lambda p: (
                natural_sort_key(os.path.basename(p)),
                natural_sort_key(os.path.dirname(p)),
            )
        elif self.sort_mode == "mtime":
            key = lambda p: (
                -self._mtime_of(p),  # 更新日時の新しい順 (降順)
                natural_sort_key(os.path.dirname(p)),
                natural_sort_key(os.path.basename(p)),
            )
        else:  # "folder"
            key = lambda p: (
                natural_sort_key(os.path.dirname(p)),
                natural_sort_key(os.path.basename(p)),
            )
        return sorted(images, key=key)

    @staticmethod
    def _mtime_of(path):
        """ファイルの更新日時 (epoch秒)。取得できない場合は 0。"""
        try:
            return os.path.getmtime(path)
        except OSError:
            return 0.0

    def _sort_images(self):
        """現在の sort_mode に従って self.images を並べ替える。"""
        if not self.images:
            return
        self.images = self._sorted_images(self.images)

    def set_sort_mode(self, mode):
        """並べ替えモード ('folder'/'filename'/'mtime') を設定する。
        表示中の画像はそのまま維持する。"""
        if not self.images or mode == self.sort_mode:
            return
        current_image_path = self.images[self.index]
        self.sort_mode = mode
        self._sort_images()
        if current_image_path in self.images:
            self.index = self.images.index(current_image_path)
        self.progress_bar.set_images(self.images, self.index, self._progress_group_keys())
        if self.grid_mode:
            self.grid.set_images(
                self.images,
                self.index,
                self.grid.columns,
                group_keys=self._grid_group_keys(),
            )
            QTimer.singleShot(0, self._scroll_grid_to_current)
        else:
            self.load_pixmap()
            self.display_pixmap()

    def cycle_sort_mode(self):
        """並べ替えモードを folder → filename → mtime → folder の順に切り替える。"""
        if not self.images:
            return
        modes = ("folder", "filename", "mtime")
        next_mode = modes[(modes.index(self.sort_mode) + 1) % len(modes)]
        self.set_sort_mode(next_mode)

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
                self.grid.columns,
                group_keys=self._grid_group_keys(),
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

    def on_thumbnail_selected(self, idx):
        """一覧で単クリック/カーソルキー: 選択(青枠)を移動する。表示は切り替えない。"""
        if 0 <= idx < len(self.images):
            self.index = idx

    def on_thumbnail_activated(self, idx):
        """一覧でダブルクリック/Enter: その画像を1枚表示で開く。"""
        if not (0 <= idx < len(self.images)):
            return
        self.index = idx
        # マウスイベント列(press/release/dblclick/release)が完了してから切り替える。
        # 切替中に一覧を隠すとマウスグラブが外れ、直後の release がメインウィンドウへ
        # 漏れて左右クリックナビゲーションを誤発火するのを防ぐ。
        QTimer.singleShot(0, self._enter_single_view)

    def _enter_single_view(self):
        """現在の index を1枚表示で開く(一覧→1枚)。"""
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

    def _grid_move(self, drow, dcol):
        """一覧表示でカーソルキーにより選択(青枠)を移動する。"""
        if not self.images:
            return
        new = self.grid.index_in_direction(self.index, drow, dcol)
        if new != self.index:
            self.index = new
            self.grid.set_current_index(new)
            self._scroll_grid_to_current()

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
            self.progress_bar.set_images(self.images, self.index, self._progress_group_keys())
            self.load_pixmap()
            self.display_pixmap()
            if self.grid_mode:
                self.grid.set_images(
                self.images,
                self.index,
                self.grid.columns,
                group_keys=self._grid_group_keys(),
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

            sort_menu = context_menu.addMenu("並べ替え")
            for label, mode in (("フォルダ順", "folder"), ("ファイル名順", "filename"), ("更新日時順", "mtime")):
                act = QAction(label, self)
                act.setCheckable(True)
                act.setChecked(self.sort_mode == mode)
                act.triggered.connect(lambda _, m=mode: self.set_sort_mode(m))
                sort_menu.addAction(act)

            context_menu.addSeparator()

            delete_action = QAction("画像を削除", self)
            delete_action.triggered.connect(self.delete_current)
            context_menu.addAction(delete_action)

            context_menu.addSeparator()

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

    def _auto_reload_tick(self):
        """自動リロードの定期処理。読み込み中やダイアログ/コンテキストメニュー
        表示中は見送り、それ以外で現在フォルダを再スキャンする。"""
        if self.is_loading:
            return
        # モーダルダイアログ(階層選択・保存先選択等)やポップアップ(右クリック
        # メニュー)の表示中は、その操作が終わるまでリロードしない。
        if (
            QApplication.activeModalWidget() is not None
            or QApplication.activePopupWidget() is not None
        ):
            return
        self.reload_current_dir()

    def reload_current_dir(self):
        """現在のフォルダ群を再スキャンして画像リストを更新。表示中の画像はそのまま維持。
        複数フォルダを読み込んでいる場合は全フォルダを共通の階層で再スキャンする。"""
        roots = [r for r in self.current_roots if os.path.exists(r)]
        if not roots:
            return

        current_image_path = (
            os.path.normpath(self.images[self.index]) if self.images else None
        )

        max_depth = self._depth_to_max(self.current_depth)
        new_images = []
        for r in roots:
            new_images.extend(self._collect_dir_images(r, max_depth))
        new_images = self._sorted_images(new_images)

        # ファイル構成に変化がなければ何もしない。これにより5秒ごとの自動リロード
        # でも画像/プログレスバー/グリッドを無駄に再構築せず、ちらつきを防ぐ。
        if new_images == self.images:
            return

        old_count = len(self.images)
        self.images = new_images
        new_count = len(self.images)

        if not self.images:
            self.label.clear()
            self.setWindowTitle("No images loaded")
            self.progress_bar.clear()
            return

        if current_image_path and current_image_path in self.images:
            # 表示中画像はそのまま: index 復元、タイトル/プログレスバーのみ更新
            self.index = self.images.index(current_image_path)
            self.progress_bar.set_images(self.images, self.index, self._progress_group_keys())
            self.load_pixmap()  # title と progress を最新の総数で更新
        else:
            # 表示中画像が消えた場合は近傍にフォールバック
            self.index = min(self.index, len(self.images) - 1)
            self.zoom_factor = 1.0
            self.pan_offset = QPoint(0, 0)
            self.is_original_size = False
            self.progress_bar.set_images(self.images, self.index, self._progress_group_keys())
            self.load_pixmap()
            self.display_pixmap()

        if self.grid_mode:
            self.grid.set_images(
                self.images,
                self.index,
                self.grid.columns,
                group_keys=self._grid_group_keys(),
            )

        diff = new_count - old_count
        if diff > 0:
            print(f"[reload] +{diff} files ({old_count} -> {new_count})")
        elif diff < 0:
            print(f"[reload] {diff} files ({old_count} -> {new_count})")

    def delete_current(self):
        """現在の画像をアプリ管理ゴミ箱へ退避し、次の画像へ進む。Ctrl+Z で戻せる。"""
        if not self.images:
            return
        image_path = self.images[self.index]
        if self._perform_soft_delete([image_path], image_path):
            self._remove_image_from_list(image_path)

    def _ensure_trash_dir(self):
        """セッション用のアプリ管理ゴミ箱フォルダを用意して返す。失敗時 None。"""
        if self._trash_dir and os.path.isdir(self._trash_dir):
            return self._trash_dir
        base = os.path.join(tempfile.gettempdir(), f"imageviewer_trash_{os.getpid()}")
        try:
            os.makedirs(base, exist_ok=True)
        except OSError as e:
            QMessageBox.warning(self, "削除失敗", f"一時フォルダの作成に失敗しました:\n{e}")
            return None
        self._trash_dir = base
        return base

    def _perform_soft_delete(self, file_paths, image_path, seed=None):
        """file_paths をアプリ管理ゴミ箱へ退避し、undo 用に記録する。成功で True。
        image_path は表示/一覧から取り除く画像本体のパス。"""
        trash_dir = self._ensure_trash_dir()
        if trash_dir is None:
            return False
        sub = os.path.join(trash_dir, str(self._trash_counter))
        try:
            os.makedirs(sub, exist_ok=True)
        except OSError as e:
            QMessageBox.warning(self, "削除失敗", f"一時フォルダの作成に失敗しました:\n{e}")
            return False
        moved = []
        for p in file_paths:
            np = os.path.normpath(p)
            if not os.path.exists(np):
                continue
            dest = os.path.join(sub, os.path.basename(np))
            try:
                shutil.move(np, dest)
            except Exception as e:
                # 失敗したら既に退避した分を元へ戻してから中止
                for d, orig in moved:
                    try:
                        shutil.move(d, orig)
                    except Exception:
                        pass
                QMessageBox.warning(self, "削除失敗", f"ゴミ箱への移動に失敗しました:\n{e}")
                return False
            moved.append((dest, np))
        if not moved:
            return False
        self._trash_counter += 1
        self._undo_stack.append({
            "moved": moved,
            "image_path": os.path.normpath(image_path),
            "seed": seed,
        })
        return True

    def undo_delete(self):
        """直近の削除を元に戻す (アプリ管理ゴミ箱から復元)。Ctrl+Z。"""
        if not self._undo_stack:
            return
        entry = self._undo_stack.pop()
        restored_image = None
        for trash_path, original_path in entry["moved"]:
            if os.path.exists(original_path):
                continue  # 同名ファイルが既にあるなら上書きしない
            try:
                os.makedirs(os.path.dirname(original_path), exist_ok=True)
                shutil.move(trash_path, original_path)
            except Exception as e:
                QMessageBox.warning(self, "復元失敗", f"ファイルの復元に失敗しました:\n{e}")
                continue
            if original_path == entry["image_path"]:
                restored_image = original_path
        # 画像をリストへ戻して表示する
        if restored_image and restored_image not in self.images:
            self.images.append(restored_image)
            self.images = self._sorted_images(self.images)
            self.index = self.images.index(restored_image)
            self.progress_bar.set_images(self.images, self.index, self._progress_group_keys())
            if self.grid_mode:
                self.grid.set_images(
                    self.images, self.index, self.grid.columns,
                    group_keys=self._grid_group_keys(),
                )
                QTimer.singleShot(0, self._scroll_grid_to_current)
            else:
                self.load_pixmap()
                self.display_pixmap()

    def _flush_trash_to_recycle_bin(self):
        """アプリ管理ゴミ箱に残った(undoされなかった)削除ファイルを実ゴミ箱へ送る。"""
        if self._trash_dir and os.path.isdir(self._trash_dir):
            try:
                send2trash(os.path.normpath(self._trash_dir))
            except Exception:
                pass

    def _remove_image_from_list(self, image_path):
        """画像をリストから除去し、表示を次の画像へ更新する。"""
        if image_path in self.images:
            self.images.remove(image_path)

        if not self.images:
            self.label.clear()
            self.setWindowTitle("No images loaded")
            self.progress_bar.clear()
            if self.grid_mode:
                self.grid.set_images(
                    [], 0, self.grid.columns,
                    group_keys=self._grid_group_keys(),
                )
            return

        if self.index >= len(self.images):
            self.index = len(self.images) - 1

        self.progress_bar.set_images(self.images, self.index, self._progress_group_keys())
        if self.grid_mode:
            self.grid.set_images(
                self.images, self.index, self.grid.columns,
                group_keys=self._grid_group_keys(),
            )
            QTimer.singleShot(0, self._scroll_grid_to_current)
        else:
            self.load_pixmap()
            self.display_pixmap()

    def _grid_group_keys(self):
        """一覧(グリッド)の区切り基準キー列。フォルダ順のときだけ
        フォルダ(dirname)で区切り、それ以外は区切らない(None)。"""
        if self.sort_mode == "folder":
            return [os.path.dirname(p) for p in self.images]
        return None

    def _progress_group_keys(self):
        """プログレスバーの色替え基準キー列。フォルダ(dirname)で区切る。"""
        return [os.path.dirname(p) for p in self.images]

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
        self.auto_reload_timer.stop()
        self.thumb_loader.stop()
        self._flush_trash_to_recycle_bin()
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
        # 「送る」やドラッグ&ドロップで複数フォルダ/ファイルを渡せる。
        # 各パスを読み込み対象フォルダに変換し、まとめて読み込む
        # (フォルダが1つなら従来通り単一読み込みになる)。
        dir_args = []
        file_filename = None
        for arg_path in sys.argv[1:]:
            if not os.path.exists(arg_path):
                continue
            if os.path.isdir(arg_path):
                dir_args.append(os.path.normpath(arg_path))
            else:
                dir_args.append(os.path.normpath(os.path.dirname(arg_path)))
                if file_filename is None:
                    file_filename = os.path.basename(arg_path)
        if dir_args:
            viewer.load_images_from_dirs(dir_args, file_filename)

    sys.exit(app.exec_())
