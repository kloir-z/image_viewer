# Simple Image Viewer

A PyQt5-based image viewer application. Supports drag-and-drop for image files and directories, full screen mode, and quick access to directory history via context menu.

## Requirements

- Python 3
- PyQt5
- Pillow

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

1. Run the application:

    ```bash
    python image_viewer.py
    ```

    Or use the batch file (Windows, auto-activates venv):

    ```bash
    start.bat
    ```

2. Drag and drop image files or directories onto the application window to view them.

## Features

- **Image navigation**: Navigate through images using arrow keys, mouse clicks (left 25%/right 75% of window), mouse wheel, or the progress bar
- **Subfolder loading**: When opening a directory, prompts to include images from immediate subfolders
- **Full screen mode**: Press 'F' to enter and 'Escape' to exit full screen mode
- **History**: The application keeps a history of the last 20 directories accessed, which can be re-opened quickly via right-click context menu
- **EXIF rotation**: Images are automatically rotated according to their EXIF Orientation metadata
- **Open in explorer**: Open the current directory in your system's file explorer from the context menu

## Supported Formats

- PNG, JPG, BMP, GIF, XPM
