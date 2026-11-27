@echo off
REM ============================================================
REM  Construye Posterizer.exe (un unico archivo, sin consola)
REM  Requisitos: Python 3.9+ instalado y en el PATH
REM ============================================================

echo [1/4] Creando entorno virtual...
python -m venv venv
call venv\Scripts\activate.bat

echo [2/4] Instalando dependencias...
pip install --upgrade pip
pip install -r requirements.txt

echo [3/4] Generando icono (assets\Posterizer.ico)...
python make_icon.py

echo [4/4] Empaquetando con PyInstaller...
pyinstaller --noconfirm --clean ^
  --name "Posterizer" ^
  --windowed ^
  --onefile ^
  --icon "assets\Posterizer.ico" ^
  --add-data "assets\Posterizer.ico;assets" ^
  --collect-all pillow_heif ^
  app.py

echo.
echo ============================================================
echo  Listo. El ejecutable esta en:  dist\Posterizer.exe
echo ============================================================
pause
