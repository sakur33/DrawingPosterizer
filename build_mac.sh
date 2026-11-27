#!/usr/bin/env bash
# ============================================================
#  Construye Posterizer.app para macOS, y opcionalmente la firma,
#  la notariza y deja un .zip listo para distribuir.
#
#  Requisitos: macOS, Python 3.9+, Xcode Command Line Tools.
#
#  Uso:
#    ./build_mac.sh                 # solo compila (sin firmar)
#    ./build_mac.sh --sign          # compila + firma + notariza + zip
#
#  Para --sign define antes:
#    export CODESIGN_IDENTITY="Developer ID Application: Tu Nombre (TEAMID)"
#    export NOTARY_PROFILE="posterizer-notary"
#  y crea el perfil de notarización UNA vez (guarda las credenciales en el llavero):
#    xcrun notarytool store-credentials "posterizer-notary" \
#        --apple-id "tu@correo" --team-id "TEAMID" --password "contraseña-de-app"
#  (la contraseña de app se crea en https://account.apple.com > Seguridad)
# ============================================================
set -euo pipefail
cd "$(dirname "$0")"

SIGN=0
[[ "${1:-}" == "--sign" ]] && SIGN=1

if [[ $SIGN -eq 1 ]]; then
  : "${CODESIGN_IDENTITY:?Define CODESIGN_IDENTITY (ver cabecera del script)}"
  : "${NOTARY_PROFILE:?Define NOTARY_PROFILE (ver cabecera del script)}"
fi

echo "[1/5] Creando entorno virtual..."
python3 -m venv venv
source venv/bin/activate

echo "[2/5] Instalando dependencias..."
pip install --upgrade pip
pip install -r requirements.txt

echo "[3/5] Generando iconos (assets/Posterizer.icns)..."
python make_icon.py

echo "[4/5] Empaquetando con PyInstaller..."
rm -rf build dist
pyinstaller --noconfirm --clean Posterizer-mac.spec

APP="dist/Posterizer.app"
ZIP="dist/Posterizer-mac.zip"

if [[ $SIGN -eq 0 ]]; then
  echo
  echo "Listo (sin firmar): $APP"
  echo "Para abrirla en este Mac: clic derecho > Abrir. Para distribuirla usa --sign."
  exit 0
fi

echo "[5/5] Firmando, notarizando y empaquetando..."
codesign --force --deep --options runtime --timestamp \
  --entitlements entitlements.plist \
  --sign "$CODESIGN_IDENTITY" "$APP"
codesign --verify --deep --strict --verbose=2 "$APP"

# Se envía a Apple un zip temporal; el definitivo se crea tras grapar el ticket.
ditto -c -k --keepParent "$APP" "$ZIP"
xcrun notarytool submit "$ZIP" --keychain-profile "$NOTARY_PROFILE" --wait
xcrun stapler staple "$APP"
xcrun stapler validate "$APP"
rm -f "$ZIP"
ditto -c -k --keepParent "$APP" "$ZIP"

echo
echo "============================================================"
echo " Listo. Distribuye:  $ZIP"
echo "============================================================"
spctl --assess --type execute --verbose "$APP" || true
