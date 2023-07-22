import os
import sys
import json
from datetime import datetime
from PyQt5.QtWidgets import QApplication, QWidget, QLabel, QVBoxLayout, QSizePolicy ,QMenu, QAction
from PyQt5.QtGui import QPixmap, QImage
from PyQt5.QtCore import Qt
from PIL import Image
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

        self.index = 0
        self.images = []
        self.config_path = 'config.json'
        self.supported_extensions = ['.png', '.xpm', '.gif', '.bmp', '.jpg']

        if os.path.exists(self.config_path):
            with open(self.config_path, 'r') as f:
                config = json.load(f)
            history = list(config.get('history', {}).keys())
            position = config.get('position', [0, 0])
            size = config.get('size', [800, 800])
        else:
            history = []
            position = [0, 0]
            size = [800, 800]

        self.history = [x for n, x in enumerate(history[::-1]) if x not in history[::-1][n+1:]]
        self.history = OrderedDict.fromkeys(self.history)

        self.layout = QVBoxLayout()
        self.setLayout(self.layout)

        self.label = ResizableLabel()
        self.layout.addWidget(self.label)
        
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self.show_context_menu)

        self.setAcceptDrops(True)

        screen_size = QApplication.instance().desktop().screenGeometry()
        self.setMaximumSize(screen_size.width(), screen_size.height())

        self.resize(*size)
        self.move(*position)

        self.original_pixmap = None
        self.is_loading = False

    def mousePressEvent(self, event):
        x = event.x()
        if x < self.width() * 0.25:
            self.show_prev_image()
        elif x > self.width() * 0.75:
            self.show_next_image()

    def show_next_image(self):
        self.move_index(1)

    def show_prev_image(self):
        self.move_index(-1)

    def move_index(self, delta):
        if not self.images or self.is_loading:
            return
        self.index += delta
        self.index %= len(self.images)
        self.update_image()
        self.label.setPixmap(self.get_scaled_pixmap()) 
        self.is_loading = False

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_F:
            self.showFullScreen()
        elif event.key() == Qt.Key_Escape and self.isFullScreen():
            self.showNormal()
        elif event.key() == Qt.Key_Left:
            self.show_prev_image()
        elif event.key() == Qt.Key_Right:
            self.show_next_image()
 
    def update_image(self):
        self.is_loading = True
        image_path = self.images[self.index]
        with open(image_path, 'rb') as f:
            image = Image.open(f)

            creation_time = os.path.getmtime(image_path)
            dt_object = datetime.fromtimestamp(creation_time)
            formatted_time = dt_object.strftime("%Y/%m/%d(%a) %H:%M:%S")
            weekday_conversion = {"Mon": "月", "Tue": "火", "Wed": "水", "Thu": "木", "Fri": "金", "Sat": "土", "Sun": "日"}
            for eng, jp in weekday_conversion.items():
                formatted_time = formatted_time.replace(eng, jp)
            self.setWindowTitle(f"{os.path.basename(image_path)} - {formatted_time} - {self.index + 1}/{len(self.images)}")

            try:
                exif = image._getexif()
            except AttributeError:
                exif = None
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

            if image.mode != "RGB":
                image = image.convert("RGB")

            data = image.tobytes("raw", "RGB")
            qimage = QImage(data, image.size[0], image.size[1], image.size[0]*3, QImage.Format_RGB888)

        self.original_pixmap = QPixmap.fromImage(qimage)
        self.label.setPixmap(self.original_pixmap)
        self.is_loading = False

    def get_scaled_pixmap(self):
        pixmap = QPixmap(self.images[self.index])
        if pixmap.width() > self.width() or pixmap.height() > self.height():
            return self.original_pixmap.scaled(self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        else:
            return self.original_pixmap
 
    def resizeEvent(self, event):
        if self.images:
            self.label.setPixmap(self.get_scaled_pixmap())
        super().resizeEvent(event)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def closeEvent(self, event):
        config = {
            'history': self.history,
            'position': [self.x(), self.y()],
            'size': [self.width(), self.height()],
        }
        with open(self.config_path, 'w') as f:
            json.dump(config, f)
        event.accept()

    def dropEvent(self, event):
        self.images = []
        dropped_file_path = None
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if os.path.isdir(path):
                dir_path = path
            else:
                if path.lower().endswith(tuple(self.supported_extensions)):
                    dropped_file_path = os.path.normpath(path)
                dir_path = os.path.dirname(path)
            files = sorted(os.listdir(dir_path))
            for file in files:
                if file.lower().endswith(tuple(self.supported_extensions)):
                    self.images.append(os.path.normpath(os.path.join(dir_path, file)))
        if dropped_file_path:
            self.index = self.images.index(dropped_file_path)
        else:
            self.index = 0

        if self.images:
            self.history[dir_path] = None
            self.history = OrderedDict(list(self.history.items())[-20:])

            self.update_image()
            self.label.setPixmap(self.get_scaled_pixmap())

    def show_context_menu(self, position):
        context_menu = QMenu(self)

        for dir_path in list(self.history.keys())[::-1]:
            if os.path.exists(dir_path):
                action = QAction(dir_path, self)
                action.triggered.connect(lambda _, d=dir_path: self.load_images_from_dir(d))
                context_menu.addAction(action)
        
        if self.images:
            current_dir = os.path.dirname(self.images[self.index])
            open_in_explorer_action = QAction(f"Open current dir in explorer.", self)
            open_in_explorer_action.triggered.connect(lambda: self.open_in_explorer(current_dir))
            context_menu.addAction(open_in_explorer_action)

        context_menu.exec_(self.mapToGlobal(position))

    def open_in_explorer(self, path):
        if sys.platform == "win32":
            subprocess.Popen(['explorer', os.path.normpath(path)])
        elif sys.platform == "darwin":
            subprocess.Popen(['open', os.path.normpath(path)])
        else:
            subprocess.Popen(['xdg-open', os.path.normpath(path)])

    def load_images_from_dir(self, dir_path):
        self.images = []
        files = sorted(os.listdir(dir_path))
        for file in files:
            if file.lower().endswith(('.png', '.xpm', '.gif', '.bmp', '.jpg')):
                self.images.append(os.path.normpath(os.path.join(dir_path, file)))

        if self.images:
            self.index = 0
            self.update_image()
            self.label.setPixmap(self.get_scaled_pixmap())

if __name__ == "__main__":
    app = QApplication(sys.argv)

    viewer = ImageViewer()
    viewer.show()

    sys.exit(app.exec_())