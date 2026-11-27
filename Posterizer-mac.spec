# -*- mode: python ; coding: utf-8 -*-
# Spec de PyInstaller para macOS: genera dist/Posterizer.app (onedir, sin consola).
# La firma y la notarización las hace build_mac.sh, no este archivo.
from PyInstaller.utils.hooks import collect_all

BUNDLE_ID = 'dev.usefulapps.posterizer'   # cámbialo si usas otro identificador
VERSION = '1.0.0'

datas = [('assets/Posterizer.ico', 'assets')]
binaries = []
hiddenimports = []
tmp_ret = collect_all('pillow_heif')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Posterizer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                 # UPX rompe la firma en macOS
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,          # arquitectura de la máquina que compila (arm64 o x86_64)
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='Posterizer',
)

app = BUNDLE(
    coll,
    name='Posterizer.app',
    icon='assets/Posterizer.icns',
    bundle_identifier=BUNDLE_ID,
    version=VERSION,
    info_plist={
        'CFBundleName': 'Posterizer',
        'CFBundleDisplayName': 'Posterizer',
        'CFBundleShortVersionString': VERSION,
        'NSHighResolutionCapable': True,
        'NSHumanReadableCopyright': 'Gratis para siempre.',
    },
)
