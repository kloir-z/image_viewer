# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A PyQt5-based image viewer for Windows. Single-file application that displays images with drag-and-drop support, EXIF rotation handling, and directory history tracking.

## Commands

```bash
# Setup virtual environment
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt

# Run the application
python image_viewer.py

# Or use the batch file (auto-activates venv)
start.bat
```

## Architecture

The entire application is in [image_viewer.py](image_viewer.py):

- **ImageViewer** (QWidget): Main window class handling all functionality
  - Drag-and-drop file/directory loading
  - Image navigation via keyboard (Left/Right), mouse clicks (left 25%/right 75% of window), mouse wheel, or progress bar
  - Full screen toggle (F key)
  - Context menu with directory history (right-click)
  - Saves window position, size, and history to `config.json` on close

- **ResizableLabel** (QLabel): Custom label that maintains center alignment with ignored size policy

## Key Behaviors

- **Natural sorting**: Files sorted numerically (1, 2, 10 vs 1, 10, 2)
- **Subfolder loading**: Prompts to include images from immediate subfolders
- **EXIF rotation**: Auto-rotates images based on EXIF orientation tag
- **History limit**: Keeps last 20 directories
- **Supported formats**: .png, .xpm, .gif, .bmp, .jpg

## Configuration

`config.json` stores:
- `history`: OrderedDict of directory paths to last-viewed filename
- `position`: Window [x, y] coordinates
- `size`: Window [width, height]
