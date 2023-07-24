# Simple Image Viewer

This is a PyQt based application that provides an interactive image viewer. You can navigate through images, show them in full screen mode, and access them via context menu. The application keeps a history of the directories accessed and allows you to re-open those directories quickly. It also supports drag and drop feature for image files and directories.

## Requirements

- Python 3
- PyQt5
- Pillow

## Installation

1. Clone the repository:

    ```bash
    git clone https://github.com/kloir-z/image_viewer.git
    ```

2. Navigate into the cloned repository:

    ```bash
    cd image_viewer
    ```

3. Install the required Python packages:

    ```bash
    pip install -r requirements.txt
    ```

**Note**: This command should be run in a virtual environment to avoid package conflicts.

## Usage

1. Run the application:

    ```bash
    python image_viewer.py
    ```

2. Drag and drop image files or directories onto the application window to view them.

## Features

- Image navigation: Navigate through images using left and right arrow keys or by clicking on the left and right sections of the window.
- Full screen mode: Press 'F' to enter and 'Escape' to exit the full screen mode.
- History: The application keeps a history of the directories accessed, which can be re-opened quickly via context menu.
- Image rotation: Images are automatically rotated according to their EXIF Orientation metadata.
- Open in explorer: Open the current directory in your system's file explorer from the context menu.
