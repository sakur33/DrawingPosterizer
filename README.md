# Posterizer

**Convierte una foto en varias versiones con pocos tonos para estudiar volúmenes y dibujar. Gratis para siempre.**

<!--
  El bloque entre site:start y site:end es la única parte de este README que se
  publica en https://usefulapps.dev/apps/posterizer. Está en inglés porque el sitio
  lo está. Todo lo de fuera (build, desarrollo, notas) se queda en el repo.
  Si borras los marcadores, el build del sitio falla a propósito.
-->

<!-- site:start -->

Posterizer is a **free, offline desktop app that turns a photo into a set of
tonal studies** — the same picture reduced to 2, 3, 4, 5, 6… flat grey values — so
you can learn to see and draw volumes. No upload, no account, no licence key.

## What it does

Drawing volume is mostly about seeing a scene as a few big masses of light and
shadow instead of a thousand details. Posterizer does that squinting for you:
load a photo, crop it, pick how many tonal levels you want, and it renders one
image per level so you can compare a 3-value study with a 6-value one side by side.

- **Crop** the photo with a free or fixed-ratio frame (1:1, 4:3, A4…), and rotate it.
- **Pick the levels** you want (2 to 10) and see a live example of each before exporting.
- **Tune it**: smoothing groups small details into clean masses, and the tone split
  can be balanced by *quantiles* (every level covers a similar area) or *linear*.
- **Layered shadows** mode: white stays as empty paper and only shadows are drawn.
  Each extra level keeps the shapes and greys of the previous ones and just adds
  darker shadows *inside* them, so a 2-level drawing can be darkened into the 4- and
  6-level versions without redoing anything. An in-app panel explains how to use it.
- **Upscale** up to x4 before posterizing, so edges between masses come out smooth
  and you can print large to draw from.
- **Export** the images to a folder (PNG or JPG), and/or a **PDF** with one level per
  page or all levels on a single sheet — or send it straight to the printer.

## Workflow

1. Open or drag in a photo (JPG, PNG, WEBP, TIFF, BMP, HEIC).
2. Crop it.
3. Choose levels, smoothing and upscale, checking the previews.
4. Save the images, the PDF, or print.

Everything runs on your own computer; your photos never leave it.

## The model

- **Free for real.** No trial, no clock, no locked feature.
- **Nothing to sign up for.** If it saved you a bad afternoon, there is a button to
  buy a coffee. That is the whole business model.
- **One reminder.** Every 10 exports a notice appears, makes the joke, and closes
  itself. It never blocks anything.

## Download

**Windows 10 or 11.** `Posterizer.exe` — a single portable file. Download it,
double-click it, done. Nothing gets installed.

<!-- site:end -->

---

## Ejecutar en desarrollo

```bash
pip install -r requirements.txt
python app.py
```

## Estructura

- `app.py` — interfaz (PyQt5): asistente de 4 pasos, exportación y PDF/impresión.
- `cropwidget.py` — widget de recorte (mover, redimensionar, proporción fija).
- `posterizer_core.py` — lógica de posterizado (sin interfaz).
- `make_icon.py` — genera `assets/Posterizer.ico` y `assets/Posterizer.icns`.

## Modo «Sombras en capas»

Tercer método de reparto (`capas` en `posterizer_core.py`). Blanco = papel vacío;
solo hay sombras. Cada profundidad tiene un umbral y un tono **fijos**
(`NESTED_THRESHOLDS` / `NESTED_TONES`), así que subir de nivel no cambia lo ya
existente: solo añade una sombra más oscura dentro de la anterior (n niveles = n-1
sombras, de 2 a 10). La ventana de niveles incluye un panel «¿Cómo funciona?»
(`InfoWidget` en `app.py`) que lo explica al elegir el método.

## Cómo se posteriza

recorte → gris → **ampliación (Lanczos)** → desenfoque → autocontraste →
cuantización a N niveles. La ampliación va antes de cuantizar para que los bordes
salgan suaves. El desenfoque se indica en píxeles de la imagen original y se
escala con la ampliación, así el resultado se ve igual a cualquier tamaño.
El lado mayor de la salida se limita a 12 000 px.

## Construir el .exe (Windows)

```bat
build.bat
```

Deja `dist\Posterizer.exe` (onefile, sin consola). Al publicar el release en
GitHub, el nombre del asset debe casar con el `assetPattern` de
`UsefulApps/src/content/apps/posterizer.md`.

## Construir la app de macOS

En un Mac (PyInstaller no compila de forma cruzada), con Python 3.9+ y las Xcode
Command Line Tools:

```bash
./build_mac.sh            # solo compila: dist/Posterizer.app (sin firmar)
./build_mac.sh --sign     # compila + firma + notariza + dist/Posterizer-mac.zip
```

Para `--sign` hace falta la cuenta de desarrollador de Apple:

```bash
export CODESIGN_IDENTITY="Developer ID Application: Tu Nombre (TEAMID)"
export NOTARY_PROFILE="posterizer-notary"
# Una sola vez: guarda las credenciales de notarización en el llavero
xcrun notarytool store-credentials "posterizer-notary"     --apple-id "tu@correo" --team-id "TEAMID" --password "contraseña-de-app"
```

El `.spec` es `Posterizer-mac.spec` (bundle id y versión al principio) y los permisos
del hardened runtime están en `entitlements.plist`. El binario resultante es para la
arquitectura del Mac que compila (Apple Silicon o Intel).

## Configurar la donación

En `app.py`: `DONATE_URL` y `NAG_EVERY` (cada cuántas exportaciones recordar).
