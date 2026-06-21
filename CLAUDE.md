# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A PyQt5-based image viewer for Windows that displays images with drag-and-drop support, EXIF rotation handling, selectable-depth subfolder browsing, zoom/pan, and directory history tracking. The code lives in two modules: [image_viewer.py](image_viewer.py) (the `ImageViewer` main window plus the program entry point) and [widgets.py](widgets.py) (the self-contained supporting widgets).

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

The code is split across two modules:

- [image_viewer.py](image_viewer.py): the **ImageViewer** main window class and the `if __name__ == "__main__"` entry point.
- [widgets.py](widgets.py): the self-contained supporting widgets — **ResizableLabel**, **ProgressIndicator**, **ThumbnailLoader**, **ThumbnailGrid**, **GridScrollArea**, **JsonOverlay**. None of them depend on `ImageViewer` (the EXIF-rotation helper that `ThumbnailLoader` needs is injected via its constructor's `rotate_func` argument), so `widgets.py` imports nothing from `image_viewer.py` and there is no circular import. `register_heif_opener()` is called once in `image_viewer.py` at import time, which covers HEIF decoding for both modules (thumbnails are only generated at runtime, after that registration).

The classes:

- **ImageViewer** (QWidget): main window class handling all functionality
  - Drag-and-drop file/directory loading (multiple folders/files can be dropped at once)
  - Command-line file/directory argument support (so `open.bat` can pass the clicked file). **Multiple paths are accepted** (`sys.argv[1:]`), so selecting several folders in Explorer and using "送る"/Send To opens them all together — the SendTo target forwards every selected item to one invocation and `open.bat %*` passes them through. All paths are funneled into `load_images_from_dirs()`. The arg is loaded synchronously after `show()`; the subfolder-depth `QInputDialog` it may raise is safe because `display_pixmap()` no-ops while `self.pixmap` is `None` (the dialog's nested event loop can deliver a `resizeEvent` before the first image is loaded — `self.images` is already populated but `self.pixmap` is not yet)
  - **Multi-folder input** (`load_images_from_dirs`): takes a list of folders, asks the subfolder-depth `QInputDialog` **once** and applies that depth to every folder, then concatenates each folder's images (via the shared `_collect_dir_images`) into one list. Delegates to `load_images_from_dir` when only one valid folder remains after dedup. `self.current_roots` holds the actually-loaded folders (always `[current_root_path]` in the single-folder case); `self.current_root_path` becomes the folders' `os.path.commonpath` (falling back to the first folder across drives) and serves as the base for pickup relative paths. History records each selected folder as its own entry; F5 re-scans all of `current_roots`
  - Image navigation via keyboard (Left/Right), mouse clicks (left 25% / right 75%), mouse wheel, or progress bar
  - Full screen toggle (F key). Escape priority: close JSON overlay → leave grid view → exit fullscreen → (in windowed single view) enter grid view. So in single view Escape opens the grid, and in grid view Escape returns to single — Escape toggles the two
  - Ctrl+wheel zoom centered at cursor, drag to pan, double-click to reset, triple-click to toggle 1:1 original size
  - F5 reload: re-scan the current directory tree, preserving the displayed image when possible. Also runs **automatically every 5 seconds** via `auto_reload_timer` (a `QTimer` started in `__init__`, stopped in `closeEvent`). The tick (`_auto_reload_tick`) skips while `is_loading` or while a modal dialog / popup menu is open (`QApplication.activeModalWidget()` / `activePopupWidget()`). `reload_current_dir` no-ops when the freshly-scanned, sorted list equals the current `self.images`, so an idle poll causes no repaint/flicker
  - Context menu (right-click) with directory history, grid/single-view toggle, reload, a "並べ替え" submenu (フォルダ順 / ファイル名順 / seed順, checkable, calls `set_sort_mode`), pickup-file recording, JSON overlay display, image+JSON deletion with seed exclusion, and "open in explorer"
  - Grid (thumbnail) view: toggled from the context menu ("一覧表示" / "1枚表示に戻る") or Escape; click a thumbnail to open it in single view, Escape to return
  - JSON sidecar handling (`_parse_image_meta`): maps an image to its companion JSON and seed by the filename's `_seed` token. `0001_seed405730226.png` → JSON `0001.json` (the part before `_seed`) and seed `405730226` (the digits after `_seed`). With no `_seed` token, the JSON is the image basename with a `.json` extension and the seed is `None`.
    - **"JSONを表示"** (`show_json_overlay`): reads the companion JSON, pretty-prints it (falls back to raw text on parse failure), and shows it in a `JsonOverlay`
    - **"画像とJSONを削除しseedを除外"** (`delete_and_exclude_current`): appends the seed (one per line) to the excluded-seed file, sends the image and its JSON to the Recycle Bin (`send2trash`), then advances to the next image via `_remove_image_from_list`. No confirmation dialog. If the seed file is unset, a save-file dialog prompts for it once and persists the choice.
    - The excluded-seed file path is chosen via `change_excluded_seed_file` (a save dialog with overwrite-confirmation disabled, since the file is appended to) and persisted as `excluded_seed_file` in `config.json`
  - Saves window position, size, history, warning-suppression flag, grid column count, and excluded-seed file path to `config.json` on close

- **JsonOverlay** (QWidget): semi-transparent full-window overlay (child of `ImageViewer`) showing a JSON sidecar's content in a read-only monospace `QPlainTextEdit`. Dismissed by clicking outside the text area or pressing Escape; follows the window on resize.

- **ResizableLabel** (QLabel): custom label that maintains center alignment with ignored size policy (lets the parent freely resize without the label fighting back)

- **ThumbnailLoader** (QThread): background worker for the grid view
  - Loads each *original* image and downscales it in memory (no thumbnail files are written to disk). The downscale max long edge is **adaptive to cell size**, not a fixed constant: `ThumbnailGrid` calls `set_thumb_max(n)` before requesting, so a big cell (few columns) gets a high-res thumbnail and a small cell (many columns) a cheap low-res one. The value is read at pop time and reported back, so the grid knows the resolution each cached thumbnail was built at. `THUMB_MAX` (256) is only the default/floor
  - Reuses `ImageViewer.rotate_image_according_to_exif` for EXIF orientation
  - LIFO queue (newest-requested processed first) so the currently-visible cells take priority; `request()` dedups via a `_requested` set, `clear()` empties the queue, `stop()` joins the thread on close
  - Emits `thumbnailReady(path, QImage, gen_max)` / `thumbnailFailed(path)`, where `gen_max` is the max long edge actually used. QImage is `.copy()`d so it survives the source buffer going out of scope (QPixmap conversion happens on the GUI thread in `ThumbnailGrid._on_ready`)

- **ThumbnailGrid** (QWidget): square-cell grid drawn via `paintEvent`, inside a `GridScrollArea` (QScrollArea)
  - Column count is the live source of truth on `self.columns` (default 5, clamped to `MIN_COLS`..`MAX_COLS` = 2–8). Initialized from `grid_columns` in config and persisted back from `grid.columns` on close, so a column change made in one grid session survives leaving and re-entering the grid. Cell size = viewport width / columns, so cells scale with the window
  - Ctrl+wheel changes the column count; non-Ctrl wheel is ignored so the scroll area scrolls. Anchors the top-visible item across column changes
  - **Grouping**: `set_images(..., group_keys=...)` takes a per-image key list (or `None`). Adjacent images whose key differs start a new group: the new group begins on a fresh row and a thin separator line (`SEP_COLOR`, `SEP_THICKNESS`) is drawn along the top edge of its first row. `ImageViewer._grid_group_keys()` supplies the keys per sort mode — `dirname` in folder-sort, the seed string in seed-sort, and `None` (plain sequential flow, no separators) in filename-sort. `_build_layout()` precomputes `_positions` (index→(row,col)), `_row_starts` (row→first index), `_group_start_rows` (rows that begin a new group), and `_rows`; paint/click/scroll all go through these instead of `idx // columns`
  - Lazy loading: each `paintEvent` enqueues only the thumbnails for the currently-visible viewport rows; arrivals repaint just their own cell rect (`_index_of` maps path→index)
  - **Adaptive thumbnail resolution**: `_desired_thumb_max()` derives a target long edge from the current cell size × `devicePixelRatioF()`, quantized up to `THUMB_STEP` (128) and clamped to `THUMB_MIN`..`THUMB_MAX_CAP` (256..768). Quantizing means continuous window resizes don't trigger constant regeneration. `paintEvent` re-requests any visible cell whose cached `gen_max` is below the current target (the old low-res thumbnail keeps showing until the higher-res one arrives, so there's no flicker), and calls `loader.set_thumb_max(desired)` before requesting. Drawing scales to physical pixels and `setDevicePixelRatio` so HiDPI cells stay crisp
  - LRU pixmap cache keyed by `path → (QPixmap, gen_max)`, bounded by **total pixel area** (`cache_area_budget` ≈ 600×256², ~157MB-equivalent) rather than a count, so high-res thumbnails are kept in smaller numbers and memory stays roughly constant as resolution varies (`_cache_put` evicts LRU until under budget); failed paths are remembered to avoid retry
  - Current image is highlighted with a blue border. Clicking: `mousePressEvent` records the pressed cell index (`_index_at`), and `mouseReleaseEvent` emits `thumbnailClicked(index)` only if the release lands on the same cell. Both events are consumed (`event.accept()`) — emitting on *release* (not press) and consuming the release prevents the click from leaking to the main window: switching to single view during the press would otherwise drop the implicit mouse grab and deliver the release to `ImageViewer`, mis-firing left/right-click navigation and opening a neighboring image

- **ProgressIndicator** (QWidget): custom progress bar drawn via `paintEvent`
  - 1 image = 1 segment for precise position display (regardless of total count)
  - Runs are colored in two alternating shades so boundaries are visible. The run key comes from `set_images(..., group_keys=...)` (supplied by `ImageViewer._progress_group_keys()` — seed in seed-sort, otherwise `dirname`); when `group_keys` is `None` it falls back to `dirname`
  - Done vs. pending portions are differentiated by alpha
  - Runs are precomputed in `_compute_folder_runs()` (list of `(start_idx, end_idx, parity)`) so each repaint is O(runs) not O(images)
  - Click/drag on the bar jumps to the corresponding image (handler functions remain on `ImageViewer`)

## Key Behaviors

- **Natural sorting**: files sorted numerically (1, 2, 10 vs 1, 10, 2) using `re.split(r"(\d+)", s)` — applied to both files and subfolder names
- **Sort modes** (`sort_mode`, set via the "並べ替え" submenu, not persisted): `folder` (by `(dirname, basename)`), `filename` (by `(basename, dirname)`), `seed` (by `(seed-int, dirname, basename)`; images with no `_seed` token sort last). `_sort_images()` re-sorts the in-memory list, preserving the currently displayed image. Folder and seed modes drive grid/progress-bar grouping (see `_grid_group_keys` / `_progress_group_keys`)
- **Subfolder loading**: on drop, if the directory contains subfolders, prompts via `QInputDialog` for "読み込まない / 1階層 / 2階層 / 3階層 / 全階層", defaulting to "全階層". The chosen depth is stored on the history entry so re-opening from history skips the dialog.
- **F5 reload / 5s auto-reload**: re-walks every folder in `current_roots` with the same depth, replaces the image list, and re-locates the currently displayed image. Falls back to the nearest neighbor if the current image was deleted. The same `reload_current_dir` also fires automatically every 5 seconds (`auto_reload_timer`); it bails out early when the rescanned+sorted list is identical to the current one, so polling an unchanged folder is free. Sorting is shared via `_sorted_images(list)` (pure helper); `_sort_images()` just reassigns `self.images` from it.
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
- **Window geometry persistence**: `_remember_normal_geometry()` (called from `moveEvent`/`resizeEvent`) continuously records the window's geometry only while it is *not* maximized/fullscreen, into `_normal_pos`/`_normal_size`. `closeEvent` saves those plus a `maximized` flag; on launch the window is restored to the normal geometry and then `showMaximized()`'d if the flag was set. This avoids saving the off-screen maximized geometry (negative frame offsets), which previously made the window drift on every open

## Configuration

`config.json` (created in the working directory on close):

- `history`: `OrderedDict` of `root_path → { root_path, depth, last_image_path }`
  - `depth`: `0` = no subfolders, `1`/`2`/`3` = N levels, `-1` = all levels
  - Old `{ root_path: filename }` format is auto-migrated on load
  - When several folders are loaded together, each is recorded as its own entry (sharing the one chosen depth); `last_image_path` is the displayed image for the folder it belongs to, otherwise that folder's first image
- `position`: window `[x, y]` coordinates (the *normal*, non-maximized geometry; maximized/fullscreen geometry is never saved here to avoid the window drifting on each open)
- `size`: window `[width, height]` (normal geometry, as above)
- `maximized`: bool, true if the window was maximized at close; restored via `showMaximized()` on next launch
- `suppress_missing_file_warning`: bool, true once the user checks "don't show again"
- `grid_columns`: int, number of columns in the thumbnail grid view (default 5, range 2–8)
- `excluded_seed_file`: str or null, the path of the file that excluded seeds are appended to (one per line) when deleting an image+JSON via the context menu. Set once via a save-file dialog and reused; changeable from the context menu
