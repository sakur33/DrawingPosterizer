import argparse
import os
import numpy as np
from PIL import Image, ImageFilter, ImageOps


def posterizar_niveles(
    ruta_entrada,
    ruta_salida=None,
    output_folder=None,
    niveles=4,
    desenfoque=2.0,
    modo="cuantiles",
):
  """
    Convierte una imagen JPEG a blanco y negro con N niveles tonales
    pensados para ejercicios de dibujo y estudio de valores.
    """
  if niveles < 2:
    raise ValueError("El número de niveles debe ser al menos 2.")

  # 1. Cargar imagen y pasar a escala de grises (L)
  img = Image.open(ruta_entrada).convert("L")

  # 2. Suavizado previo para agrupar detalles pequeños en masas limpias
  if desenfoque > 0:
    img = img.filter(ImageFilter.GaussianBlur(radius=desenfoque))

  # 3. Mejorar contraste global para aprovechar todo el rango de blanco a negro
  img = ImageOps.autocontrast(img, cutoff=1)

  arr = np.array(img, dtype=np.float32)

  # 4. Definir los N - 1 umbrales de corte
  if modo == "cuantiles":
    percentiles = np.linspace(0, 100, niveles + 1)[1:-1]
    umbrales = np.percentile(arr, percentiles)
  else:
    umbrales = np.linspace(0, 255, niveles + 1)[1:-1]

  # 5. Generar los N valores de salida repartidos uniformemente entre 0 (negro) y 255 (blanco)
  valores_salida = np.linspace(0, 255, niveles).round().astype(np.uint8)
  indices = np.digitize(arr, bins=umbrales)
  resultado = valores_salida[indices]

  img_out = Image.fromarray(resultado, mode="L")

  # 6. Construir ruta de salida
  if ruta_salida is None:
    directorio_original = os.path.dirname(ruta_entrada)
    nombre_base, ext = os.path.splitext(os.path.basename(ruta_entrada))
    if not ext:
      ext = ".jpeg"

    # Formatear blur sin decimales innecesarios (ej: 2.0 -> 2, 2.5 -> 2.5)
    blur_str = f"{desenfoque:g}"
    modo_str = "c" if modo == "cuantiles" else "l"

    nombre_archivo = f"{nombre_base}_n{niveles}_b{blur_str}_m{modo_str}{ext}"
    carpeta_destino = (
        output_folder if output_folder is not None else directorio_original
    )
    ruta_salida = os.path.join(carpeta_destino, nombre_archivo)
  else:
    # Si se pasa -o y también -of, y -o es solo un nombre de archivo, usar output_folder
    if output_folder is not None and not os.path.dirname(ruta_salida):
      ruta_salida = os.path.join(output_folder, ruta_salida)

  # Crear la carpeta de destino si no existe
  carpeta_final = os.path.dirname(ruta_salida)
  if carpeta_final:
    os.makedirs(carpeta_final, exist_ok=True)

  img_out.save(ruta_salida, quality=95)
  print(f"Imagen guardada en: {ruta_salida}")


if __name__ == "__main__":
  parser = argparse.ArgumentParser(
      description="Convierte una imagen a N niveles de sombra para dibujo."
  )
  parser.add_argument("imagen", help="Ruta de la imagen JPEG de entrada")
  parser.add_argument(
      "-n",
      "--niveles",
      type=int,
      default=4,
      help="Número de niveles tonales (default: 4)",
  )
  parser.add_argument(
      "-b",
      "--blur",
      type=float,
      default=2.0,
      help="Radio de desenfoque previo (default: 2.0)",
  )
  parser.add_argument(
      "-m",
      "--modo",
      choices=["cuantiles", "lineal"],
      default="cuantiles",
      help="Modo de reparto de niveles: 'cuantiles' o 'lineal'",
  )
  parser.add_argument(
      "-o",
      "--output",
      default=None,
      help="Nombre o ruta personalizada para sobrescribir el nombre automático",
  )
  parser.add_argument(
      "-of",
      "--output_folder",
      default=None,
      help="Carpeta donde guardar la imagen resultante",
  )

  args = parser.parse_args()
  posterizar_niveles(
      ruta_entrada=args.imagen,
      ruta_salida=args.output,
      output_folder=args.output_folder,
      niveles=args.niveles,
      desenfoque=args.blur,
      modo=args.modo,
  )