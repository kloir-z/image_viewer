@echo off
cd /d "%~dp0"

REM Lite 版 (JSON/seed/並べ替え機能なし) のサイレント起動 (ファイル関連付け用)。
REM venv はルート (..) を共有する。
if exist ..\.venv\Scripts\pythonw.exe (
    start "" "..\.venv\Scripts\pythonw.exe" image_viewer.py %*
    goto :eof
)
if exist ..\venv\Scripts\pythonw.exe (
    start "" "..\venv\Scripts\pythonw.exe" image_viewer.py %*
    goto :eof
)
start "" pythonw.exe image_viewer.py %*
