import os
import sys
from datetime import datetime
from PyQt5.QtWidgets import QApplication, QWidget, QLabel, QVBoxLayout, QSizePolicy  
from PyQt5.QtGui import QPixmap, QImage
from PyQt5.QtCore import Qt
from PIL import Image

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

        self.layout = QVBoxLayout()
        self.setLayout(self.layout)

        self.label = ResizableLabel()
        self.layout.addWidget(self.label)

        self.setAcceptDrops(True)

        screen_size = QApplication.instance().desktop().screenGeometry()
        self.setMaximumSize(screen_size.width(), screen_size.height())

        self.resize(800, 800)
        self.move(0, 0)

        self.original_pixmap = None
 
        self.is_loading = False

    def mousePressEvent(self, event):
        x = event.x()
        if x < self.width() * 0.25:
            self.show_prev_image()
        elif x > self.width() * 0.75:
            self.show_next_image()

    def show_prev_image(self):
        if not self.images or self.is_loading:
            return
        self.index -= 1
        if self.index < 0:
            self.index = len(self.images) - 1
        self.update_image()
        self.label.setPixmap(self.get_scaled_pixmap()) 
        self.is_loading = False

    def show_next_image(self):
        if not self.images or self.is_loading:
            return
        self.index += 1
        if self.index >= len(self.images):
            self.index = 0
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
            self.setWindowTitle(f"{os.path.basename(image_path)} - {formatted_time}")

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
            pixmap = QPixmap.fromImage(qimage)
 
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

    def dropEvent(self, event):
        self.images = []
        dropped_file_path = None
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if os.path.isdir(path):
                dir_path = path
            else:
                if path.lower().endswith(('.png', '.xpm', '.gif', '.bmp', '.jpg')):
                    dropped_file_path = os.path.normpath(path)
                dir_path = os.path.dirname(path)
            files = sorted(os.listdir(dir_path))
            for file in files:
                if file.lower().endswith(('.png', '.xpm', '.gif', '.bmp', '.jpg')):
                    self.images.append(os.path.normpath(os.path.join(dir_path, file)))
        if dropped_file_path:
            self.index = self.images.index(dropped_file_path)
        else:
            self.index = 0
        if self.images:
            self.update_image()
            self.label.setPixmap(self.get_scaled_pixmap()) 

if __name__ == "__main__":
    app = QApplication(sys.argv)

    viewer = ImageViewer()
    viewer.show()

    sys.exit(app.exec_())