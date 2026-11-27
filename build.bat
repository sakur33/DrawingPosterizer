@echo off
REM ============================================================
REM  Construye Posterizer.exe (un unico archivo, sin consola)
REM  Requisitos: Python 3.9+ instalado y en el PATH
REM ============================================================

REM Si Posterizer.exe esta abierto, Windows no deja reemplazarlo.
tasklist /fi "imagename eq Posterizer.exe" 2>nul | findstr /i "Posterizer.exe" >nul
if not errorlevel 1 (
  echo.
  echo ERROR: Posterizer.exe esta en ejecucion. Cierralo y vuelve a lanzar build.bat.
  pause
  exit /b 1
)

echo [1/4] Preparando entorno virtual...
if exist venv\Scripts\python.exe (
  echo       Ya existe, se reutiliza.
) else (
  python -m venv venv
  if errorlevel 1 goto :fail
)
call venv\Scripts\activate.bat

echo [2/4] Instalando dependencias...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if errorlevel 1 goto :fail

echo [3/4] Generando iconos (assets\Posterizer.ico y .icns)...
python make_icon.py
if errorlevel 1 goto :fail

echo [4/4] Empaquetando con PyInstaller (Posterizer.spec)...
pyinstaller --noconfirm --clean Posterizer.spec
if errorlevel 1 goto :fail

echo.
echo ============================================================
echo  Listo. El ejecutable esta en:  dist\Posterizer.exe
echo ============================================================
pause
exit /b 0

:fail
echo.
echo ============================================================
echo  ERROR: la compilacion ha fallado (mira los mensajes de arriba).
echo ============================================================
pause
exit /b 1
