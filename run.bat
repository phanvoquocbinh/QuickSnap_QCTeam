@echo off
chcp 65001 >nul
cd /d "%~dp0"
where python >nul 2>nul || (echo [!] Chua cai Python. Tai tai https://www.python.org/downloads/ ^(tick "Add python.exe to PATH"^) & pause & exit /b 1)
if not exist .venv (
  echo Dang tao moi truong lan dau...
  python -m venv .venv || (pause & exit /b 1)
  call .venv\Scripts\activate.bat
  python -m pip install --upgrade pip
  pip install -r requirements.txt || (pause & exit /b 1)
) else (
  call .venv\Scripts\activate.bat
)
start "" pythonw quicksnap.py
