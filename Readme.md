# Simple Image Viewer

A PyQt5-based image viewer for Windows. Supports drag-and-drop, file association via a launcher script, EXIF rotation handling, subfolder browsing with selectable depth, zoom/pan, and quick directory history access from the context menu.

## Requirements

- Python 3
- PyQt5
- Pillow
- pillow-heif (HEIC/HEIF support)

## Installation

1. Clone the repository:

    ```bash
    git clone https://github.com/kloir-z/image_viewer.git
    cd image_viewer
    ```

2. Create a virtual environment:

    ```bash
    python -m venv venv
    ```

3. Activate the virtual environment:

    ```bash
    # Windows
    venv\Scripts\activate

    # macOS/Linux
    source venv/bin/activate
    ```

4. Install dependencies:

    ```bash
    pip install -r requirements.txt
    ```

### Alternative: Using uv (faster)

[uv](https://docs.astral.sh/uv/) is a fast package manager written in Rust.

1. Install uv:

    ```bash
    # Windows (PowerShell)
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

    # macOS/Linux
    curl -LsSf https://astral.sh/uv/install.sh | sh
    ```

2. Create virtual environment and install dependencies:

    ```bash
    uv venv
    uv pip install -r requirements.txt
    ```

## Usage

Run the application:

```bash
python image_viewer.py
```

Or use the batch file (Windows, auto-activates venv):

```bash
start.bat
```

You can also pass an image file or directory on the command line:

```bash
python image_viewer.py path\to\image.jpg
python image_viewer.py path\to\directory
```

### Open from File Explorer (Windows file association)

`open.bat` is a silent launcher that opens a specific image with this viewer using `pythonw.exe` (no console window). It auto-detects `.venv` or `venv` in the project directory.

To associate image files with the viewer:

1. Right-click an image file → "Open with" → "Choose another app" → "Look for another app on this PC"
2. Select `open.bat` from the project directory

Double-clicking the image will then open it directly in this viewer.

## Features

### Navigation

- **Arrow keys** (Left/Right): previous / next image
- **Mouse click**: left 25% of the window = previous, right 75% = next
- **Mouse wheel**: previous / next
- **Progress bar**: click or drag to jump. The bar shows one segment per image and uses two alternating colors to make sub-folder boundaries visible.

### Display

- **F**: enter full screen
- **Escape**: exit full screen
- **Ctrl + mouse wheel**: zoom in/out centered at the cursor position
- **Left-button drag**: pan when zoomed in
- **Triple click**: toggle original (1:1) size
- **Double click**: reset zoom and pan
- **EXIF rotation**: images are auto-rotated based on EXIF Orientation metadata

### Folder browsing

- **Drag & drop**: drop a file (opens its directory) or a directory onto the window
- **Subfolder loading**: when the dropped directory contains subfolders, you can choose to include images from 1, 2, 3, or all sub-levels (or skip subfolders entirely)
- **F5 / Reload**: re-scan the current directory tree to pick up newly added or removed files. The currently displayed image stays in place when possible.

### Context menu (right-click)

- **History**: re-open one of the last 20 directories. Each entry restores both the previously viewed image and the subfolder depth that was used.
- **再読み込み (F5)**: same as F5
- **ファイル名の記録**: append the current image's relative path to a per-session pickup file (`imageviewer_pickup_YYYYMMDD_HHMMSS.txt` in the root directory). Useful for marking files for later batch processing.
- **Open current dir in explorer**: open the current image's containing folder in the system file explorer

## Supported Formats

PNG, JPG, BMP, GIF, XPM, HEIC, HEIF

## Configuration

`config.json` is created in the working directory on close and stores:

- `history`: per-directory entries with the subfolder depth and the last-viewed image path
- `position`, `size`: window geometry
- `suppress_missing_file_warning`: whether the "file not found" dialog has been silenced
