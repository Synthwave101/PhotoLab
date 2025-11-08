from __future__ import annotations

import calendar
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, time
from fractions import Fraction
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from PIL import ExifTags, Image
try:  # pragma: no cover - optional Pillow feature
    from PIL.PngImagePlugin import PngInfo
except Exception:  # noqa: BLE001 - PNG metadata support missing
    PngInfo = None  # type: ignore[assignment]

try:  # pragma: no cover - optional Pillow class
    from PIL.Image import Exif as PILExif
except Exception:  # noqa: BLE001 - older Pillow versions
    PILExif = None  # type: ignore[assignment]

try:  # pragma: no cover - type imported for runtime checks only
    from PIL.TiffImagePlugin import IFDRational
except Exception:  # noqa: BLE001 - pillow builds without TIFF module
    IFDRational = Fraction  # type: ignore[misc,assignment]

# Register HEIC support if pillow-heif is available. Fall back silently otherwise.
try:  # pragma: no cover - optional dependency
    from pillow_heif import register_heif_opener, register_heif_writer

    register_heif_opener()
    register_heif_writer()
except Exception:  # noqa: BLE001 - best effort registration
    pass

TAG_ID_TO_NAME: Dict[int, str] = {tag_id: tag_name for tag_id, tag_name in ExifTags.TAGS.items()}
TAG_NAME_TO_ID: Dict[str, int] = {tag_name: tag_id for tag_id, tag_name in TAG_ID_TO_NAME.items()}


@dataclass
class MetadataEntry:
    """Represents a single piece of metadata that can be displayed and edited."""

    key: str
    source: str  # Either "exif" or "info"
    tag_id: Optional[int]
    original_value: Any
    value: Any

    def display_value(self) -> str:
        return value_to_display(self.value)

    def reset(self) -> None:
        self.value = self.original_value


def value_to_display(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return value.hex()
    if isinstance(value, (list, tuple)):
        return ", ".join(value_to_display(v) for v in value)
    return str(value)


def parse_value(raw_value: str, reference: Any) -> Any:
    """Attempt to coerce the edited string back to the original python type."""
    if reference is None or isinstance(reference, str):
        return raw_value
    if isinstance(reference, IFDRational):
        numerator, denominator = _parse_fraction_pair(raw_value)
        return IFDRational(numerator, denominator)
    if isinstance(reference, bytes):
        return raw_value.encode("utf-8")
    if isinstance(reference, bool):
        return raw_value.lower() in {"1", "true", "yes", "si"}
    if isinstance(reference, int):
        return int(raw_value)
    if isinstance(reference, float):
        return float(raw_value)
    if isinstance(reference, Fraction):
        numerator, denominator = _parse_fraction_pair(raw_value)
        return Fraction(numerator, denominator)
    if isinstance(reference, tuple):
        if len(reference) == 2 and all(isinstance(v, int) for v in reference):
            try:
                return tuple(_parse_int_sequence(raw_value, len(reference)))
            except ValueError:
                numerator, denominator = _parse_fraction_pair(raw_value)
                return (numerator, denominator)
        parts = [part.strip() for part in raw_value.split(",")]
        coerced: List[Any] = []
        for idx, part in enumerate(parts):
            try:
                coerced.append(parse_value(part, reference[idx]))
            except (IndexError, ValueError, TypeError) as exc:
                raise ValueError(f"No se pudo interpretar '{part}' para el índice {idx}") from exc
        return tuple(coerced)
    return raw_value


def _parse_fraction_pair(raw_value: str) -> Tuple[int, int]:
    cleaned = raw_value.strip()
    if not cleaned:
        raise ValueError("El valor no puede estar vacío")

    normalized = cleaned.replace(" ", "")
    if "/" in normalized:
        parts = normalized.split("/")
        if len(parts) != 2:
            raise ValueError("El valor debe tener el formato numerador/denominador")
        return int(parts[0]), int(parts[1])

    try:
        fraction = Fraction(normalized)
    except (ValueError, ZeroDivisionError) as exc:
        raise ValueError("El valor debe ser un número válido, por ejemplo 72, 72.0 o 72/1") from exc

    limited = fraction.limit_denominator(10000)
    return limited.numerator, limited.denominator


def _parse_int_sequence(raw_value: str, expected_length: int) -> Tuple[int, ...]:
    normalized = raw_value.replace("/", " ").replace(",", " ").replace("-", " ").replace(".", " ")
    parts = [part for part in normalized.split() if part]
    if len(parts) != expected_length:
        raise ValueError("Número de componentes incorrecto")
    try:
        return tuple(int(part) for part in parts)
    except ValueError as exc:  # noqa: BLE001
        raise ValueError("Los valores deben ser enteros") from exc


def load_image_with_metadata(path: str) -> Tuple[Image.Image, List[MetadataEntry]]:
    image = Image.open(path)
    entries: List[MetadataEntry] = []

    # EXIF metadata (available for JPEG, HEIC, PNG with EXIF chunk)
    exif = image.getexif()
    for tag_id, value in exif.items():
        tag_name = TAG_ID_TO_NAME.get(tag_id, f"Tag {tag_id}")
        entries.append(
            MetadataEntry(
                key=tag_name,
                source="exif",
                tag_id=tag_id,
                original_value=value,
                value=value,
            )
        )

    # Other textual metadata (common in PNG)
    for key, value in image.info.items():
        if key.lower() == "exif":
            continue
        if PILExif is not None and isinstance(value, PILExif):
            continue
        entries.append(
            MetadataEntry(
                key=key,
                source="info",
                tag_id=None,
                original_value=value,
                value=value,
            )
        )

    return image, entries


def convert_image(
    source_path: str,
    destination_path: str,
    target_format: str,
    entries: Optional[List[MetadataEntry]] = None,
) -> None:
    with Image.open(source_path) as image:
        save_image_with_metadata(image, destination_path, target_format, entries)


def _prepare_exif_for_save(image: Image.Image) -> Any:
    if PILExif is None:
        try:
            return image.getexif()
        except Exception:  # noqa: BLE001
            return {}

    try:
        raw_exif = image.getexif()
    except Exception:  # noqa: BLE001
        raw_exif = PILExif()

    try:
        items = list(raw_exif.items())  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        items = []

    cloned = PILExif()
    for tag, value in items:
        try:
            cloned[tag] = value
        except Exception:  # noqa: BLE001
            continue
    return cloned


def save_metadata(path: str, entries: List[MetadataEntry]) -> None:
    with Image.open(path) as image:
        extension = os.path.splitext(path)[1]
        target_format = image.format or extension_to_format(extension)
        temp_fd, temp_path = tempfile.mkstemp(suffix=extension)
        os.close(temp_fd)
        try:
            save_image_with_metadata(image, temp_path, target_format, entries)
            shutil.move(temp_path, path)
            if target_format.upper() in {"JPEG", "JPG", "HEIF", "HEIC"}:
                timestamp = extract_preferred_timestamp(entries)
                if timestamp:
                    apply_exiftool_timestamp(path, timestamp)
        finally:
            if os.path.exists(temp_path):  # Clean up on failure
                os.remove(temp_path)


def save_image_with_metadata(
    image: Image.Image,
    destination_path: str,
    target_format: str,
    entries: Optional[List[MetadataEntry]] = None,
) -> None:
    normalized_format = normalize_format(target_format)
    exif = _prepare_exif_for_save(image)
    info: Dict[str, Any] = {k: v for k, v in image.info.items() if k.lower() != "exif"}

    image_to_save = image
    if normalized_format == "PDF":
        if image.mode not in {"RGB", "L"}:
            image_to_save = image.convert("RGB")
    elif normalized_format == "ICO":
        if image.mode not in {"RGBA", "RGB", "P", "L"}:
            image_to_save = image.convert("RGBA")

    if entries is not None:
        apply_entries(entries, exif, info)

    try:
        exif_size = len(exif)
    except Exception:  # noqa: BLE001 - fallback if EXIF object is not readable
        exif_size = 0
    exif_bytes = None
    if exif_size:
        try:
            exif_bytes = exif.tobytes()
        except Exception:  # noqa: BLE001
            exif_bytes = None
    save_kwargs: Dict[str, Any] = {}
    if exif_bytes and normalized_format not in {"PDF", "ICO"}:
        save_kwargs["exif"] = exif_bytes

    if normalized_format == "PNG" and PngInfo is not None:
        png_info = PngInfo()
        for key, value in info.items():
            png_info.add_text(key, value_to_display(value))
        save_kwargs["pnginfo"] = png_info
    elif normalized_format in {"JPEG", "JPG"}:
        save_kwargs.setdefault("quality", 95)

    image_to_save.save(destination_path, format=normalized_format, **save_kwargs)


def _set_dimension_entry(entries: List[MetadataEntry], key: str, value: int) -> None:
    for entry in entries:
        if entry.key == key and entry.source == "exif":
            entry.value = value
            return
    tag_id = TAG_NAME_TO_ID.get(key)
    entries.append(
        MetadataEntry(
            key=key,
            source="exif",
            tag_id=tag_id,
            original_value=value,
            value=value,
        )
    )


def _update_dimension_entries(entries: List[MetadataEntry], width: int, height: int) -> None:
    mapping = {
        "PixelXDimension": width,
        "ImageWidth": width,
        "PixelYDimension": height,
        "ImageLength": height,
    }
    for key, value in mapping.items():
        _set_dimension_entry(entries, key, value)


def _white_color_for_mode(mode: str) -> Any:
    if mode in {"RGB", "YCbCr"}:
        return (255, 255, 255)
    if mode == "RGBA":
        return (255, 255, 255, 255)
    if mode == "L":
        return 255
    if mode == "LA":
        return (255, 255)
    if mode == "CMYK":
        return (0, 0, 0, 0)
    if mode == "1":
        return 1
    return (255, 255, 255)


def _anchor_offset(container: int, content: int, anchor: str) -> int:
    if container <= content:
        return 0
    anchor_normalized = anchor.lower()
    if anchor_normalized in {"right", "bottom"}:
        return container - content
    if anchor_normalized in {"center", "middle"}:
        return (container - content) // 2
    return 0


def _anchor_start(content: int, target: int, anchor: str) -> int:
    if content <= target:
        return 0
    anchor_normalized = anchor.lower()
    if anchor_normalized in {"right", "bottom"}:
        return content - target
    if anchor_normalized in {"center", "middle"}:
        return (content - target) // 2
    return 0


def crop_image(
    source_path: str,
    destination_path: str,
    crop_box: Tuple[int, int, int, int],
    *,
    mode: str = "fill",
    anchor: Tuple[str, str] = ("center", "center"),
) -> Path:
    left, top, right, bottom = crop_box
    if right <= left or bottom <= top:
        raise ValueError("La región de recorte es inválida")

    source = Path(source_path)
    destination = Path(destination_path)
    if not destination.suffix:
        if source.suffix:
            destination = destination.with_suffix(source.suffix)
        else:
            destination = destination.with_suffix(".jpg")
    dest_suffix = destination.suffix

    destination.parent.mkdir(parents=True, exist_ok=True)

    image, entries = load_image_with_metadata(str(source))
    output: Optional[Image.Image] = None
    region: Optional[Image.Image] = None
    try:
        width = right - left
        height = bottom - top

        img_width, img_height = image.size
        image_mode = image.mode
        image_format = image.format

        mode_normalized = mode.lower()

        downscale_only = (
            mode_normalized == "fill"
            and width <= img_width
            and height <= img_height
            and left >= 0
            and top >= 0
            and right <= img_width
            and bottom <= img_height
        )

        if downscale_only:
            source_box = (0, 0, img_width, img_height)
        else:
            source_box = (
                max(left, 0),
                max(top, 0),
                min(right, img_width),
                min(bottom, img_height),
            )
        region = image.crop(source_box)
        region.load()
    finally:
        image.close()

    resample_attr = getattr(Image, "Resampling", None)
    resample_filter = resample_attr.LANCZOS if resample_attr else Image.LANCZOS
    if mode_normalized not in {"fill", "letterbox"}:
        mode_normalized = "fill"

    try:
        region_width, region_height = region.size
        target_width = width
        target_height = height

        if mode_normalized == "letterbox":
            min_scale = min(
                target_width / region_width if region_width else 1.0,
                target_height / region_height if region_height else 1.0,
            )
            scale = min(1.0, min_scale)
            if scale < 1.0:
                new_size = (
                    max(1, int(round(region_width * scale))),
                    max(1, int(round(region_height * scale))),
                )
                region = region.resize(new_size, resample_filter)
                region_width, region_height = region.size

            fill_color = _white_color_for_mode(image_mode)
            try:
                output = Image.new(image_mode, (target_width, target_height), fill_color)
            except ValueError:
                output = Image.new("RGBA", (target_width, target_height), (255, 255, 255, 255))
                if region.mode != "RGBA":
                    region = region.convert("RGBA")
                    region_width, region_height = region.size
            if region.mode != output.mode:
                region = region.convert(output.mode)

            offset_x = _anchor_offset(target_width, region_width, anchor[0])
            offset_y = _anchor_offset(target_height, region_height, anchor[1])
            output.paste(region, (offset_x, offset_y))
        else:
            if region_width >= target_width and region_height >= target_height:
                region = region.resize((target_width, target_height), resample_filter)
                output = region
            else:
                cover_scale = max(
                    target_width / region_width if region_width else 1.0,
                    target_height / region_height if region_height else 1.0,
                )
                if abs(cover_scale - 1.0) > 1e-9:
                    new_size = (
                        max(1, int(round(region_width * cover_scale))),
                        max(1, int(round(region_height * cover_scale))),
                    )
                    region = region.resize(new_size, resample_filter)
                    region_width, region_height = region.size

                start_x = _anchor_start(region_width, target_width, anchor[0])
                start_y = _anchor_start(region_height, target_height, anchor[1])
                crop_rect = (
                    start_x,
                    start_y,
                    start_x + target_width,
                    start_y + target_height,
                )
                output = region.crop(crop_rect)

        try:
            output.info.pop("exif", None)
        except Exception:  # noqa: BLE001 - info may be read-only for some modes
            pass

        _update_dimension_entries(entries, target_width, target_height)

        target_format = image_format or extension_to_format(dest_suffix)
        normalized_format = normalize_format(target_format)
        if normalized_format in {"JPEG", "HEIF"} and output.mode not in {"RGB", "L"}:
            output = output.convert("RGB")

        if destination == source:
            temp_fd, temp_path = tempfile.mkstemp(suffix=dest_suffix or source.suffix or ".tmp")
            os.close(temp_fd)
            temp_destination = Path(temp_path)
            try:
                save_image_with_metadata(output, str(temp_destination), target_format, entries)
                shutil.move(str(temp_destination), str(destination))
            finally:
                if temp_destination.exists():
                    temp_destination.unlink(missing_ok=True)
        else:
            save_image_with_metadata(output, str(destination), target_format, entries)
    finally:
        if region is not None and region is not output:
            region.close()
        if output is not None:
            output.close()

    return destination


def apply_entries(entries: List[MetadataEntry], exif: Image.Exif, info: Dict[str, Any]) -> None:
    for entry in entries:
        if entry.source == "exif":
            tag_id = entry.tag_id or TAG_NAME_TO_ID.get(entry.key)
            if tag_id is None:
                continue
            if entry.value in (None, ""):
                exif.pop(tag_id, None)
            else:
                exif[tag_id] = entry.value
        else:
            if entry.value in (None, ""):
                info.pop(entry.key, None)
            else:
                info[entry.key] = entry.value


def normalize_format(target_format: str) -> str:
    fmt = target_format.upper()
    if fmt in {"JPG", "JPEG"}:
        return "JPEG"
    if fmt in {"HEIC", "HEIF"}:
        return "HEIF"
    if fmt == "PNG":
        return "PNG"
    if fmt == "ICO":
        return "ICO"
    if fmt == "PDF":
        return "PDF"
    raise ValueError(f"Formato no soportado: {target_format}")


def extension_to_format(extension: str) -> str:
    return normalize_format(extension.lstrip(".") or "JPEG")


def update_entry_from_string(entry: MetadataEntry, new_value: str) -> None:
    if new_value is None:
        entry.value = None
        return
    value_str = new_value.strip()
    if value_str == "" and isinstance(entry.original_value, (str, bytes)):
        entry.value = type(entry.original_value)()
        return
    if value_str == "":
        entry.value = None
        return
    entry.value = parse_value(value_str, entry.original_value)


EXIF_DATETIME_FORMAT = "%Y:%m:%d %H:%M:%S"


def set_datetime_entries(
    entries: List[MetadataEntry],
    target_timestamp: str,
) -> None:
    try:
        target_dt = datetime.strptime(target_timestamp, EXIF_DATETIME_FORMAT)
    except ValueError as exc:  # noqa: BLE001
        raise ValueError("El formato de fecha debe ser YYYY:MM:DD HH:MM:SS") from exc

    relevant_keys = ("DateTime", "DateTimeOriginal", "DateTimeDigitized")
    existing: Dict[str, MetadataEntry] = {entry.key: entry for entry in entries}

    for key in relevant_keys:
        entry = existing.get(key)
        base_dt = _parse_exif_datetime(entry.value) if entry else None
        if base_dt is None:
            base_dt = target_dt

        base_dt = base_dt.replace(
            year=target_dt.year,
            month=target_dt.month,
            day=target_dt.day,
        )

        new_value = base_dt.strftime(EXIF_DATETIME_FORMAT)

        if entry:
            entry.value = new_value
        else:
            tag_id = TAG_NAME_TO_ID.get(key)
            entries.append(
                MetadataEntry(
                    key=key,
                    source="exif",
                    tag_id=tag_id,
                    original_value=None,
                    value=new_value,
                )
            )


def _parse_exif_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8")
        except UnicodeDecodeError:
            return None
    if not isinstance(value, str):
        value = str(value)
    try:
        return datetime.strptime(value.strip(), EXIF_DATETIME_FORMAT)
    except ValueError:
        return None


def _preferred_datetime(entries: Iterable[MetadataEntry]) -> Optional[datetime]:
    for key in ("DateTimeOriginal", "CreateDate", "DateTime", "ModifyDate", "FileCreateDate"):
        for entry in entries:
            if entry.key == key:
                parsed = _parse_exif_datetime(entry.value)
                if parsed:
                    return parsed
    return None


def extract_preferred_timestamp(entries: Iterable[MetadataEntry]) -> Optional[str]:
    dt = _preferred_datetime(entries)
    if dt is None:
        return None
    return dt.strftime(EXIF_DATETIME_FORMAT)


def get_preferred_datetime(path: str) -> Optional[datetime]:
    image, entries = load_image_with_metadata(path)
    try:
        return _preferred_datetime(entries)
    finally:
        image.close()


def ensure_exiftool() -> str:
    tool_path = shutil.which("exiftool")
    if tool_path is None:
        raise RuntimeError("No se encontró 'exiftool'. Instálalo con 'brew install exiftool'.")
    return tool_path


def apply_date_with_exiftool(
    files: List[str],
    target_date: date,
    target_time: time,
    components: Dict[str, bool],
    destination_dir: Optional[Path] = None,
) -> Tuple[List[Path], List[str]]:
    exiftool_path = ensure_exiftool()

    updated_paths: List[Path] = []
    errors: List[str] = []

    for source in files:
        source_path = Path(source)
        try:
            image, entries = load_image_with_metadata(str(source_path))
            image.close()
            existing_dt = _preferred_datetime(entries)
            if existing_dt is None:
                try:
                    stat_info = source_path.stat()
                    base_ts = getattr(stat_info, "st_birthtime", None) or stat_info.st_mtime
                    existing_dt = datetime.fromtimestamp(base_ts)
                except Exception:  # noqa: BLE001
                    existing_dt = None

            if existing_dt is None:
                base_dt = datetime.combine(target_date, target_time)
            else:
                base_dt = existing_dt

            year = target_date.year if components.get("year", False) else base_dt.year
            month = target_date.month if components.get("month", False) else base_dt.month
            max_day = calendar.monthrange(year, month)[1]
            day_source = target_date.day if components.get("day", False) else base_dt.day
            day = min(day_source, max_day)

            hour = target_time.hour if components.get("hour", False) else base_dt.hour
            minute = target_time.minute if components.get("minute", False) else base_dt.minute
            second = target_time.second if components.get("second", False) else base_dt.second

            adjusted_dt = base_dt.replace(
                year=year,
                month=month,
                day=day,
                hour=hour,
                minute=minute,
                second=second,
                microsecond=base_dt.microsecond,
            )
            timestamp = adjusted_dt.strftime(EXIF_DATETIME_FORMAT)

            if destination_dir is None:
                destination_path = source_path
            else:
                destination_dir.mkdir(parents=True, exist_ok=True)
                destination_path = destination_dir / source_path.name
                if destination_path != source_path:
                    shutil.copy2(source_path, destination_path)

            cmd = [
                exiftool_path,
                "-overwrite_original",
                f"-DateTimeOriginal={timestamp}",
                f"-CreateDate={timestamp}",
                f"-ModifyDate={timestamp}",
                f"-FileCreateDate={timestamp}",
                f"-FileModifyDate={timestamp}",
                str(destination_path),
            ]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                raise RuntimeError(result.stderr.strip() or result.stdout.strip())

            updated_paths.append(destination_path)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{source_path.name}: {exc}")

    return updated_paths, errors


def apply_exiftool_timestamp(target_path: str, timestamp: str) -> None:
    exiftool_path = ensure_exiftool()
    targets = [
        f"-DateTimeOriginal={timestamp}",
        f"-CreateDate={timestamp}",
        f"-ModifyDate={timestamp}",
        f"-FileCreateDate={timestamp}",
        f"-FileModifyDate={timestamp}",
    ]
    cmd = [exiftool_path, "-overwrite_original"] + targets + [target_path]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
