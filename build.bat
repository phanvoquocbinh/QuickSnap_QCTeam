@echo off
chcp 65001 >nul
cd /d "%~dp0"
where python >nul 2>nul || (echo [!] Chua cai Python. Tai tai https://www.python.org/downloads/ ^(tick "Add python.exe to PATH"^) & pause & exit /b 1)
if not exist .venv python -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt pyinstaller || (pause & exit /b 1)
pyinstaller --noconfirm --onefile --windowed --name QuickSnap_QCTeam --add-data "splash_qcteam.png;." --collect-all imageio_ffmpeg --hidden-import pystray._win32 quicksnap.py || (pause & exit /b 1)
echo.
echo ===== XONG! File chay: dist\QuickSnap_QCTeam.exe =====
explorer dist
pause
