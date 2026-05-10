# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A PyQt5-based image viewer for Windows. Single-file application that displays images with drag-and-drop support, EXIF rotation handling, selectable-depth subfolder browsing, zoom/pan, and directory history tracking.

## Commands

```bash
# Setup virtual environment
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt

# Run the application
python image_viewer.py

# Or use the launcher batch file (auto-activates .venv or venv)
start.bat
```

### Launcher scripts

- **start.bat**: foreground launcher. Activates `.venv` or `venv` and runs `image_viewer.py` with a console window. Pauses on exit.
- **start.vbs**: silently invokes `start.bat` (no console flash).
- **open.bat**: silent launcher used for Windows file association. Detects `.venv` / `venv` and runs `image_viewer.py` with `pythonw.exe`, forwarding any `%*` args. Designed to be set as the "Open with..." target for image files so that double-clicking opens the file in this viewer.

## Architecture

The entire application is in [image_viewer.py](image_viewer.py):

- **ImageViewer** (QWidget): main window class handling all functionality
  - Drag-and-drop file/directory loading
  - Command-line file/directory argument support (so `open.bat` can pass the clicked file)
  - Image navigation via keyboard (Left/Right), mouse clicks (left 25% / right 75%), mouse wheel, or progress bar
  - Full screen toggle (F key, Escape to exit)
  - Ctrl+wheel zoom centered at cursor, drag to pan, double-click to reset, triple-click to toggle 1:1 original size
  - F5 reload: re-scan the current directory tree, preserving the displayed image when possible
  - Context menu (right-click) with directory history, reload, pickup-file recording, and "open in explorer"
  - Saves window position, size, history, and warning-suppression flag to `config.json` on close

- **ResizableLabel** (QLabel): custom label that maintains center alignment with ignored size policy (lets the parent freely resize without the label fighting back)

- **ProgressIndicator** (QWidget): custom progress bar drawn via `paintEvent`
  - 1 image = 1 segment for precise position display (regardless of total count)
  - Folders are colored in two alternating shades so subfolder boundaries are visible
  - Done vs. pending portions are differentiated by alpha
  - Folder runs are precomputed in `_compute_folder_runs()` (list of `(start_idx, end_idx, parity)`) so each repaint is O(folders) not O(images)
  - Click/drag on the bar jumps to the corresponding image (handler functions remain on `ImageViewer`)

## Key Behaviors

- **Natural sorting**: files sorted numerically (1, 2, 10 vs 1, 10, 2) using `re.split(r"(\d+)", s)` — applied to both files and subfolder names
- **Subfolder loading**: on drop, if the directory contains subfolders, prompts via `QInputDialog` for "読み込まない / 1階層 / 2階層 / 3階層 / 全階層". The chosen depth is stored on the history entry so re-opening from history skips the dialog.
- **F5 reload**: walks the same root + depth, replaces the image list, and re-locates the currently displayed image. Falls back to the nearest neighbor if the current image was deleted.
- **EXIF rotation**: auto-rotates images based on EXIF Orientation tag (values 2-8 supported)
- **Missing file handling**: shows a "ファイルが見つかりません" warning with a "再度このメッセージを表示しない" checkbox; the suppression flag is persisted to `config.json`. Missing entries are dropped from the in-memory list.
- **Pickup file**: "ファイル名の記録" appends the current image's path (relative to `current_root_path`) to a per-session `imageviewer_pickup_YYYYMMDD_HHMMSS.txt` in the root directory. The filename is generated once on first use within a session and reused until the user opens a different folder.
- **History limit**: keeps last 20 directories
- **Supported formats**: defined in `self.supported_extensions`. Currently:
  - PNG family: `.png`, `.apng`
  - JPEG family: `.jpg`, `.jpeg`, `.jfif`, `.jpe`, `.mpo`
  - `.gif`, `.bmp`, `.webp`
  - TIFF: `.tif`, `.tiff`
  - HEIC family: `.heic`, `.heif`, `.heics`, `.heifs`, `.hif` (via `pillow-heif`)
  - Icons: `.ico`, `.cur`
  - `.psd`, `.tga`, `.dds`, `.xpm`
- **Off-screen window recovery**: on startup, if the saved window position falls outside any connected display, the window is repositioned onto the primary screen

## Configuration

`config.json` (created in the working directory on close):

- `history`: `OrderedDict` of `root_path → { root_path, depth, last_image_path }`
  - `depth`: `0` = no subfolders, `1`/`2`/`3` = N levels, `-1` = all levels
  - Old `{ root_path: filename }` format is auto-migrated on load
- `position`: window `[x, y]` coordinates
- `size`: window `[width, height]`
- `suppress_missing_file_warning`: bool, true once the user checks "don't show again"
