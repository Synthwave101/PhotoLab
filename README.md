# Photo Lab

Aplicación de escritorio para macOS (y otros sistemas compatibles con PyQt6) que permite:

- Convertir fotografías entre los formatos **HEIC**, **JPEG**, **PNG**, **ICO** y **PDF**.
- Visualizar todos los metadatos embebidos en la imagen y una vista previa de la foto seleccionada.
- Editar metadatos directamente en la interfaz y guardarlos en el archivo original.

## Requisitos

- Python 3.10 o superior.
- [Homebrew](https://brew.sh/) (recomendado en macOS) para instalar la librería nativa necesaria para HEIC.
- [Exiftool](https://exiftool.org/) instalado en el sistema para que el ajuste masivo de fechas y la actualización de marcas de tiempo editadas funcionen en macOS.

### Dependencias del sistema para HEIC

```bash
brew install libheif
brew install exiftool
```

### Librerías utilizadas y propósito

- **PyQt6**: framework de interfaz gráfica que construye la ventana principal, pestañas, tablas y todos los controles interactivos de la aplicación.
- **Pillow**: biblioteca de procesamiento de imágenes que abre archivos, genera la vista previa, gestiona los metadatos embebidos y guarda los cambios de recorte o conversión.
- **pillow-heif**: extensión opcional de Pillow que registra lectores y escritores HEIC/HEIF para que la aplicación pueda abrir y exportar ese formato.
- **ExifTool** _(herramienta externa)_: sincroniza marcas de tiempo y aplica cambios masivos de fecha cuando se editan los metadatos o se procesa una pila completa.
- **libheif** _(dependencia nativa)_: códec requerido por pillow-heif para decodificar y codificar imágenes HEIC en sistemas como macOS.

## Instalación

1. Crear y activar un entorno virtual (opcional, pero recomendado):

```bash
python3 -m venv .venv
source .venv/bin/activate
```

2. Instalar las dependencias de Python:

```bash
pip install -r requirements.txt
```

## Ejecución

```bash
python src/main.py
# o bien
./run_app.sh
```

## Uso

1. Usa **“Agregar imágenes”** para cargar una o varias fotos (puedes limpiar la pila con **“Limpiar lista”**); justo debajo verás un contador con el total de archivos cargados y la lista muestra columnas al estilo hoja de cálculo con la fecha/hora detectada a la izquierda y el nombre a la derecha, pudiendo ordenar la columna de fecha ascendente o descendente desde el encabezado. Cualquier selección múltiple en la lista actúa como la pila activa para los comandos masivos.
2. Selecciona una imagen de la lista para visualizar sus metadatos en la tabla (y el previsualizador lateral). Activa **“Editar metadatos”** para desbloquear la edición puntual y, al terminar, presiona **Enter** mientras editas para aplicar y guardar los metadatos en el archivo original; la aplicación invoca `exiftool` para sincronizar las marcas de tiempo relevantes con macOS.
3. En la sección "Fecha para la pila" define la fecha y hora objetivo con los selectores dedicados y marca los casilleros de los componentes que quieras sobrescribir (año, mes, día, hora, minuto, segundo); al pulsar **“Aplicar fecha a pila”** podrás elegir entre actualizar los archivos originales o generar copias en otra carpeta (usando `exiftool` en ambos casos). Justo después, si creaste copias, podrás decidir si también las conviertes a otro formato.
4. Usa **“Copiar/Pegar metadatos”** para duplicar la información de otra foto de la lista: selecciona la imagen fuente, copia, luego selecciona la imagen destino y pega (después presiona **Enter** para guardar los cambios aplicados).
5. Usa **“Renombrar pila”** para asignar un prefijo común y numerar automáticamente los archivos seleccionados (con sufijos `_1`, `_2`, …) ordenados desde la foto con fecha más reciente a la más antigua.
6. El botón **“Convertir”** permite procesar solo la imagen seleccionada (pidiéndote un archivo de salida) o la pila activa (selección o lista completa), guardando copias con el mismo nombre en la carpeta destino que elijas; si ya existe un archivo, podrás elegir entre duplicar, reemplazar o cancelar y los archivos que ya están en el formato objetivo se omiten automáticamente.

> Nota: Algunas etiquetas EXIF complejas (por ejemplo, valores binarios o estructuras no textuales) se muestran en formato hexadecimal y es mejor no alterarlas a menos que sepas exactamente qué representan.
