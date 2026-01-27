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
    QProgressBar,
    QCheckBox,
    QDesktopWidget,
)
from PyQt5.QtGui import QPixmap, QImage
from PyQt5.QtCore import Qt
from PIL import Image, ImageFile
from collections import OrderedDict
import subprocess


class ResizableLabel(QLabel):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.setAlignment(Qt.AlignCenter)


class ImageViewer(QWidget):
    def __init__(self):
        super().__init__()

        self.is_loading = False
        self.index = 0
        self.images = []
        self.config_path = "config.json"
        self.supported_extensions = [".png", ".xpm", ".gif", ".bmp", ".jpg"]

        if os.path.exists(self.config_path):
            with open(self.config_path, "r") as f:
                config = json.load(f)
            history = config.get("history", {})
            position = config.get("position", [0, 0])
            size = config.get("size", [800, 800])
            self.suppress_missing_file_warning = config.get("suppress_missing_file_warning", False)
        else:
            history = []
            position = [0, 0]
            size = [800, 800]
            self.suppress_missing_file_warning = False

        self.history = OrderedDict(history)

        self.layout = QVBoxLayout()
        self.setLayout(self.layout)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(0)

        self.progress_bar_dragging = False
        self.progress_bar = QProgressBar(self)
        self.progress_bar.setMaximumHeight(10)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.progress_bar.setStyleSheet(
            """
            QProgressBar {
                border: none;
                background-color: transparent;
            }
            QProgressBar::chunk {
                background-color: #007bff;
            }
            """
        )
        self.progress_bar.mousePressEvent = self.progress_bar_clicked
        self.progress_bar.mousePressEvent = self.progress_bar_pressed
        self.progress_bar.mouseReleaseEvent = self.progress_bar_released
        self.layout.addWidget(self.progress_bar)

        self.label = ResizableLabel()
        self.layout.addWidget(self.label)

        self.setAcceptDrops(True)

        self.resize(*size)
        position = self.ensure_position_on_screen(position, size)
        self.move(*position)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setWindowFlags(self.windowFlags() | Qt.WindowMaximizeButtonHint)

        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self.show_context_menu)

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

    def update_progress_bar(self, value):
        self.progress_bar.setValue(int(value))

    def progress_bar_clicked(self, event):
        if self.images:
            x = event.pos().x()
            width = self.progress_bar.width()
            percentage = x / width
            index = int(percentage * (len(self.images) - 1))
            self.index = max(0, min(index, len(self.images) - 1))
            self.update_image()

    def progress_bar_pressed(self, event):
        self.progress_bar_dragging = True
        self.progress_bar_clicked(event)

    def progress_bar_released(self, event):
        self.progress_bar_dragging = False

    def mouseMoveEvent(self, event):
        if self.progress_bar_dragging:
            self.progress_bar_clicked(event)
        super().mouseMoveEvent(event)

    def update_image(self):
        if self.images:
            self.load_pixmap()
            self.display_pixmap()

    def mousePressEvent(self, event):
        x = event.x()
        if x < self.width() * 0.25:
            self.move_index(-1)
        elif x > self.width() * 0.75:
            self.move_index(1)

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta < 0:
            self.move_index(1)
        elif delta > 0:
            self.move_index(-1)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_F:
            self.showFullScreen()
        elif event.key() == Qt.Key_Escape and self.isFullScreen():
            self.showNormal()
        elif event.key() == Qt.Key_Left:
            self.move_index(-1)
        elif event.key() == Qt.Key_Right:
            self.move_index(1)

    def move_index(self, delta):
        if not self.images or self.is_loading:
            return
        self.index += delta
        self.index %= len(self.images)
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
                percent = (self.index + 1) / total * 100
                folder_name = os.path.basename(os.path.dirname(image_path))
                self.update_progress_bar(percent)
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
            self.is_loading = False
            return

        if self.index >= len(self.images):
            self.index = len(self.images) - 1

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
        if self.pixmap.width() > self.width() or self.pixmap.height() > self.height():
            self.label.setPixmap(self.pixmap.scaled(self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            self.label.setPixmap(self.pixmap)

    def resizeEvent(self, event):
        if self.images:
            self.display_pixmap()
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

    def load_images_from_dir(self, dir_path, filename=None):
        if self.images:
            self.update_history()
        self.images = []

        def natural_sort_key(s):
            return [int(text) if text.isdigit() else text for text in re.split(r"(\d+)", s)]

        files = sorted(os.listdir(dir_path), key=natural_sort_key)
        for file in files:
            if file.lower().endswith(tuple(self.supported_extensions)):
                self.images.append(os.path.normpath(os.path.join(dir_path, file)))

        subfolders = [f.path for f in os.scandir(dir_path) if f.is_dir()]
        if subfolders:
            reply = QMessageBox.question(
                self,
                "サブフォルダの読み込み",
                "サブフォルダ内の画像ファイルも読み込みますか？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply == QMessageBox.Yes:
                for subfolder in subfolders:
                    subfiles = sorted(os.listdir(subfolder), key=natural_sort_key)
                    for subfile in subfiles:
                        if subfile.lower().endswith(tuple(self.supported_extensions)):
                            self.images.append(os.path.normpath(os.path.join(subfolder, subfile)))

        self.setup_images_and_index(dir_path, filename)

    def update_history(self):
        dir_path = os.path.normpath(os.path.dirname(os.path.dirname(self.images[self.index])))
        filename = os.path.basename(self.images[self.index])
        if dir_path in self.history:
            del self.history[dir_path]
        self.history[dir_path] = filename
        if len(self.history) > 20:
            self.history.popitem(last=False)

    def setup_images_and_index(self, dir_path, filename=None):
        if self.images:
            if filename:
                image_filenames = [os.path.basename(img_path) for img_path in self.images]
                if filename in image_filenames:
                    self.index = image_filenames.index(filename)
            else:
                last_displayed_image = self.history.get(dir_path)
                image_filenames = [os.path.basename(img_path) for img_path in self.images]
                if last_displayed_image is not None and last_displayed_image in image_filenames:
                    self.index = image_filenames.index(last_displayed_image)
                else:
                    self.index = 0
            self.update_history()
            self.load_pixmap()
            self.display_pixmap()

    def show_context_menu(self, position):
        context_menu = QMenu(self)
        for dir_path in reversed(list(self.history.keys())):
            if os.path.exists(dir_path):
                dir_menu = context_menu.addMenu(dir_path)

                open_action = QAction("Open", self)
                open_action.triggered.connect(lambda _, d=dir_path: self.load_images_from_dir(d))
                dir_menu.addAction(open_action)

                delete_action = QAction("Delete from history", self)
                delete_action.triggered.connect(lambda _, d=dir_path: self.delete_from_history(d))
                dir_menu.addAction(delete_action)

        if self.images:
            current_dir = os.path.normpath(os.path.dirname(self.images[self.index]))
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

                if self.images and dir_path == os.path.normpath(os.path.dirname(self.images[self.index])):
                    self.images = []
                    self.label.clear()

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
        config = {
            "history": self.history,
            "position": [self.x(), self.y()],
            "size": [self.width(), self.height()],
            "suppress_missing_file_warning": self.suppress_missing_file_warning,
        }
        with open(self.config_path, "w") as f:
            json.dump(config, f)
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)

    viewer = ImageViewer()
    viewer.show()

    sys.exit(app.exec_())
