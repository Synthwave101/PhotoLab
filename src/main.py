from __future__ import annotations

import os
import sys
import shutil
import subprocess
from math import gcd
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from PIL import Image
try:  # pragma: no cover - optional Pillow class
    from PIL.Image import Exif as PILExif
except Exception:  # noqa: BLE001
    PILExif = None  # type: ignore[assignment]

try:  # pragma: no cover - optional dependency
    from pillow_heif import register_heif_opener

    register_heif_opener()
except Exception:  # noqa: BLE001 - optional dependency missing or failing
    pass
from PyQt6.QtCore import QDate, QEvent, QObject, QSize, Qt, QTimer, QTime
from PyQt6.QtGui import QImage, QKeyEvent, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTimeEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QDialog,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from metadata_utils import (
    apply_date_with_exiftool,
    get_preferred_datetime,
    MetadataEntry,
    convert_image,
    crop_image,
    load_image_with_metadata,
    save_metadata,
    update_entry_from_string,
)
from preset_storage import CropPreset, PresetStorage


_DROP_VALUE = object()


class FileListItem(QTreeWidgetItem):
    """Tree widget item that sorts the date column using stored timestamps."""

    def __lt__(self, other: "FileListItem") -> bool:  # type: ignore[override]
        tree = self.treeWidget()
        if tree is not None and tree.sortColumn() == 0:
            self_key = self.data(0, Qt.ItemDataRole.UserRole + 1)
            other_key = other.data(0, Qt.ItemDataRole.UserRole + 1)
            if self_key is not None and other_key is not None:
                try:
                    return float(self_key) < float(other_key)
                except (TypeError, ValueError):  # noqa: BLE001
                    pass
        return super().__lt__(other)


class PhotoLabWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Photo Lab")
        self.resize(960, 640)

        self.current_path: Optional[str] = None
        self.entries: List[MetadataEntry] = []
        self.copied_metadata: Optional[List[MetadataEntry]] = None
        self.copied_metadata_label: Optional[str] = None
        self.files: List[str] = []
        downloads_dir = Path.home() / "Downloads"
        self.last_directory: Path = downloads_dir if downloads_dir.exists() else Path.home()

        self.central = QWidget()
        self.setCentralWidget(self.central)
        self.layout = QVBoxLayout()
        self.central.setLayout(self.layout)

        self.metadata_table: Optional[QTableWidget] = None

        self.file_label = QLabel("No hay archivo cargado")
        self.file_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        header_layout = QHBoxLayout()
        header_layout.addWidget(self.file_label)

        self.rename_file_button = QPushButton("🔤")
        self.rename_file_button.setFixedSize(QSize(32, 32))
        self.rename_file_button.setToolTip("Renombrar archivo actual")
        self.rename_file_button.clicked.connect(self.rename_current_file)
        self.rename_file_button.setEnabled(False)
        header_layout.addWidget(self.rename_file_button)

        self.update_app_button = QPushButton("⟳")
        self.update_app_button.setFixedSize(QSize(32, 32))
        self.update_app_button.setToolTip("Buscar actualizaciones y recompilar la app")
        self.update_app_button.clicked.connect(self.check_for_updates)
        header_layout.addWidget(self.update_app_button)

        self.file_size_label = QLabel("")
        self.file_size_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.file_size_label.setStyleSheet("color: #888; padding-right: 12px;")
        header_layout.addWidget(self.file_size_label)
        header_layout.addStretch()
        self.layout.addLayout(header_layout)

        self.count_label = QLabel("0 archivos en la lista")
        self.layout.addWidget(self.count_label)
        self._update_count_label()

        file_buttons_layout = QHBoxLayout()
        self.add_button = QPushButton("Agregar imágenes")
        self.add_button.clicked.connect(self.add_images)
        file_buttons_layout.addWidget(self.add_button)

        self.clear_button = QPushButton("Limpiar lista")
        self.clear_button.clicked.connect(self.clear_images)
        file_buttons_layout.addWidget(self.clear_button)

        self.layout.addLayout(file_buttons_layout)
        files_container = QHBoxLayout()
        self.file_list = QTreeWidget()
        self.file_list.setColumnCount(2)
        self.file_list.setHeaderLabels(["Fecha", "Archivo"])
        self.file_list.setRootIsDecorated(False)
        self.file_list.setAlternatingRowColors(True)
        self.file_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        header = self.file_list.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSortIndicatorShown(True)
        self.file_list.setSortingEnabled(True)
        header.setSortIndicator(0, Qt.SortOrder.DescendingOrder)
        self.file_list.sortItems(0, Qt.SortOrder.DescendingOrder)
        self.file_list.currentItemChanged.connect(self.handle_file_selection_changed)
        files_container.addWidget(self.file_list, 1)

        self.preview_label = QLabel("Vista previa no disponible")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setMinimumSize(240, 240)
        self.preview_label.setStyleSheet("border: 1px solid #555; background-color: #1e1e1e; color: #cccccc;")
        self.preview_label.setWordWrap(True)
        files_container.addWidget(self.preview_label, 1)

        self.preview_pixmap: Optional[QPixmap] = None

        self.layout.addLayout(files_container)

        self.tab_widget = QTabWidget()
        self.layout.addWidget(self.tab_widget)

        metadata_container = QWidget()
        self.metadata_layout = QVBoxLayout()
        metadata_container.setLayout(self.metadata_layout)

        toolbar_layout = QHBoxLayout()
        toolbar_layout.setSpacing(8)

        self.edit_button = QPushButton("✏️")
        self.edit_button.setCheckable(True)
        self.edit_button.setToolTip("Editar metadatos")
        self.edit_button.toggled.connect(self.toggle_edit_mode)
        toolbar_layout.addWidget(self.edit_button)

        self.copy_button = QPushButton("📋")
        self.copy_button.setToolTip("Copiar metadatos")
        self.copy_button.clicked.connect(self.copy_metadata)
        toolbar_layout.addWidget(self.copy_button)

        self.paste_button = QPushButton("📥")
        self.paste_button.setToolTip("Pegar metadatos")
        self.paste_button.clicked.connect(self.paste_metadata)
        toolbar_layout.addWidget(self.paste_button)

        toolbar_layout.addStretch()
        self.metadata_layout.addLayout(toolbar_layout)

        conversion_layout = QHBoxLayout()
        conversion_layout.addWidget(QLabel("Convertir a:"))
        self.format_combo = QComboBox()
        self.format_combo.addItems(["JPEG", "PNG", "HEIC", "ICO", "PDF"])
        conversion_layout.addWidget(self.format_combo)

        self.convert_button = QPushButton("Convertir")
        self.convert_button.clicked.connect(self.convert_format)
        conversion_layout.addWidget(self.convert_button)

        self.metadata_layout.addLayout(conversion_layout)

        batch_layout = QHBoxLayout()
        batch_layout.addWidget(QLabel("Fecha para la pila:"))
        self.date_edit = QDateEdit(QDate.currentDate())
        self.date_edit.setDisplayFormat("yyyy-MM-dd")
        self.date_edit.setCalendarPopup(True)
        batch_layout.addWidget(self.date_edit)

        self.time_edit = QTimeEdit(QTime.currentTime())
        self.time_edit.setDisplayFormat("HH:mm:ss")
        batch_layout.addWidget(self.time_edit)

        self.year_checkbox = QCheckBox("Año")
        self.year_checkbox.setChecked(True)
        batch_layout.addWidget(self.year_checkbox)

        self.month_checkbox = QCheckBox("Mes")
        self.month_checkbox.setChecked(True)
        batch_layout.addWidget(self.month_checkbox)

        self.day_checkbox = QCheckBox("Día")
        self.day_checkbox.setChecked(True)
        batch_layout.addWidget(self.day_checkbox)

        self.hour_checkbox = QCheckBox("Hora")
        self.hour_checkbox.setChecked(True)
        batch_layout.addWidget(self.hour_checkbox)

        self.minute_checkbox = QCheckBox("Minuto")
        self.minute_checkbox.setChecked(True)
        batch_layout.addWidget(self.minute_checkbox)

        self.second_checkbox = QCheckBox("Segundo")
        self.second_checkbox.setChecked(True)
        batch_layout.addWidget(self.second_checkbox)

        self.apply_date_button = QPushButton("Aplicar fecha a pila")
        self.apply_date_button.clicked.connect(self.apply_datetime_to_stack)
        batch_layout.addWidget(self.apply_date_button)
        self.rename_button = QPushButton("Renombrar pila")
        self.rename_button.clicked.connect(self.rename_stack)
        batch_layout.addWidget(self.rename_button)
        self.metadata_layout.addLayout(batch_layout)

        self.metadata_table = QTableWidget(0, 2)
        self.metadata_table.setHorizontalHeaderLabels(["Clave", "Valor"])
        self.metadata_table.horizontalHeader().setStretchLastSection(True)
        self.metadata_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.metadata_table.installEventFilter(self)
        self.metadata_layout.addWidget(self.metadata_table)

        self.tab_widget.addTab(metadata_container, "Metadata Shifter")

        crop_container = QWidget()
        self.crop_layout = QVBoxLayout()
        crop_container.setLayout(self.crop_layout)

        self.crop_info_label = QLabel("Selecciona una imagen para recortar")
        self.crop_layout.addWidget(self.crop_info_label)

        self._crop_ratio_presets: Dict[str, List[Tuple[int, int]]] = {
            "1:1": [(500, 500), (1024, 1024), (2048, 2048)],
            "3:1": [(1200, 400), (1920, 640), (2400, 800)],
            "4:5": [(1080, 1350), (2160, 2700)],
            "16:9": [(1280, 720), (1920, 1080), (3840, 2160)],
            "3:2": [(1500, 1000), (3000, 2000)],
            "2:3": [(1000, 1500), (2000, 3000)],
        }
        self._custom_ratio_label = "Personalizado"

        crop_form = QFormLayout()
        self.crop_ratio_combo = QComboBox()
        self.crop_ratio_combo.addItem(self._custom_ratio_label, None)
        for ratio_label in self._crop_ratio_presets:
            self.crop_ratio_combo.addItem(ratio_label, ratio_label)
        self.crop_ratio_combo.currentIndexChanged.connect(self._on_crop_ratio_changed)
        crop_form.addRow("Proporción:", self.crop_ratio_combo)

        self.crop_size_combo = QComboBox()
        self.crop_size_combo.setEnabled(False)
        self.crop_size_combo.currentIndexChanged.connect(self._on_crop_size_changed)
        crop_form.addRow("Tamaño predefinido:", self.crop_size_combo)

        self._set_custom_ratio_mode()

        self.crop_width_spin = QSpinBox()
        self.crop_width_spin.setRange(1, 100000)
        self.crop_width_spin.setSingleStep(10)
        crop_form.addRow("Ancho (px):", self.crop_width_spin)

        self.crop_height_spin = QSpinBox()
        self.crop_height_spin.setRange(1, 100000)
        self.crop_height_spin.setSingleStep(10)
        crop_form.addRow("Alto (px):", self.crop_height_spin)

        self.crop_offset_x_spin = QSpinBox()
        self.crop_offset_x_spin.setRange(-100000, 100000)
        self.crop_offset_x_spin.setSingleStep(10)
        crop_form.addRow("Desplazamiento X:", self.crop_offset_x_spin)

        self.crop_offset_y_spin = QSpinBox()
        self.crop_offset_y_spin.setRange(-100000, 100000)
        self.crop_offset_y_spin.setSingleStep(10)
        crop_form.addRow("Desplazamiento Y:", self.crop_offset_y_spin)

        self.crop_anchor_combo = QComboBox()
        anchor_options = [
            ("Centro", ("center", "center")),
            ("Arriba", ("center", "top")),
            ("Abajo", ("center", "bottom")),
            ("Izquierda", ("left", "center")),
            ("Derecha", ("right", "center")),
            ("Esquina superior izquierda", ("left", "top")),
            ("Esquina superior derecha", ("right", "top")),
            ("Esquina inferior izquierda", ("left", "bottom")),
            ("Esquina inferior derecha", ("right", "bottom")),
        ]
        for label, data in anchor_options:
            self.crop_anchor_combo.addItem(label, data)
        self.crop_anchor_combo.setCurrentIndex(0)
        crop_form.addRow("Anclaje:", self.crop_anchor_combo)

        mode_layout = QHBoxLayout()
        self.crop_mode_button_fill = QPushButton("⤢")
        self.crop_mode_button_fill.setCheckable(True)
        self.crop_mode_button_fill.setToolTip(
            "Llenar el tamaño objetivo recortando o ampliando si es necesario"
        )
        self.crop_mode_button_fill.clicked.connect(lambda _: self._set_crop_mode("fill"))
        mode_layout.addWidget(self.crop_mode_button_fill)

        self.crop_mode_button_letterbox = QPushButton("⬚")
        self.crop_mode_button_letterbox.setCheckable(True)
        self.crop_mode_button_letterbox.setToolTip(
            "Conservar la imagen completa, añadiendo bordes blancos si hace falta"
        )
        self.crop_mode_button_letterbox.clicked.connect(lambda _: self._set_crop_mode("letterbox"))
        mode_layout.addWidget(self.crop_mode_button_letterbox)

        mode_layout.addStretch()
        crop_form.addRow("Modo de ajuste:", mode_layout)

        self._crop_mode = "fill"

        self.crop_layout.addLayout(crop_form)

        crop_helper_layout = QHBoxLayout()

        self.crop_ratio_copy_button = QPushButton("📐📋")
        self.crop_ratio_copy_button.setToolTip("Copiar proporción de recorte")
        self.crop_ratio_copy_button.clicked.connect(self.copy_crop_ratio)
        crop_helper_layout.addWidget(self.crop_ratio_copy_button)

        self.crop_ratio_paste_button = QPushButton("📐📥")
        self.crop_ratio_paste_button.setToolTip("Pegar proporción de recorte")
        self.crop_ratio_paste_button.clicked.connect(self.paste_crop_ratio)
        crop_helper_layout.addWidget(self.crop_ratio_paste_button)

        self.crop_preset_save_button = QPushButton("💾")
        self.crop_preset_save_button.setToolTip("Guardar resolución personalizada")
        self.crop_preset_save_button.clicked.connect(self.save_custom_preset)
        crop_helper_layout.addWidget(self.crop_preset_save_button)

        self.crop_preset_manage_button = QPushButton("🗂️")
        self.crop_preset_manage_button.setToolTip("Administrar resoluciones personalizadas")
        self.crop_preset_manage_button.clicked.connect(self.manage_custom_presets)
        crop_helper_layout.addWidget(self.crop_preset_manage_button)

        crop_helper_layout.addStretch()
        self.crop_layout.addLayout(crop_helper_layout)

        crop_actions_layout = QHBoxLayout()
        self.crop_current_button = QPushButton("Recortar imagen actual")
        self.crop_current_button.clicked.connect(self.crop_current_image)
        crop_actions_layout.addWidget(self.crop_current_button)

        self.crop_stack_button = QPushButton("Recortar pila")
        self.crop_stack_button.clicked.connect(self.crop_stack_images)
        crop_actions_layout.addWidget(self.crop_stack_button)
        self.crop_layout.addLayout(crop_actions_layout)

        self.tab_widget.addTab(crop_container, "Recortador de imágenes")

        self.status_label = QLabel("")
        self.layout.addWidget(self.status_label)
        self._set_crop_mode("fill")

        self._crop_auto_sync = True
        self._updating_crop_controls = False
        self._current_image_size: Optional[Tuple[int, int]] = None
        self._copied_crop_ratio: Optional[Tuple[int, int]] = None
        self.crop_width_spin.valueChanged.connect(self._on_crop_dimension_changed)
        self.crop_height_spin.valueChanged.connect(self._on_crop_dimension_changed)

        self._update_crop_tab_state()

        self.preset_storage = PresetStorage()
        self.custom_presets = self.preset_storage.load()
        self._refresh_custom_presets()

    def _format_file_size(self, size_bytes: int) -> str:
        units = ["B", "KB", "MB", "GB", "TB"]
        size = float(size_bytes)
        unit_index = 0
        while size >= 1024.0 and unit_index < len(units) - 1:
            size /= 1024.0
            unit_index += 1
        if unit_index == 0:
            return f"{int(size)} {units[unit_index]}"
        return f"{size:.1f} {units[unit_index]}"

    def _update_file_size_label(self, path: Optional[str]) -> None:
        if not path:
            self.file_size_label.setText("")
            return
        try:
            size_bytes = os.path.getsize(path)
        except OSError:
            self.file_size_label.setText("")
            return
        self.file_size_label.setText(self._format_file_size(size_bytes))

    def _refresh_custom_presets(self) -> None:
        custom_indices = [
            idx
            for idx in range(self.crop_ratio_combo.count())
            if isinstance(self.crop_ratio_combo.itemData(idx), tuple)
            and self.crop_ratio_combo.itemData(idx)[0] == "custom"
        ]
        for idx in reversed(custom_indices):
            self.crop_ratio_combo.removeItem(idx)

        for preset in self.custom_presets:
            label = f"{preset.name} ({preset.width}×{preset.height})"
            self.crop_ratio_combo.addItem(label, ("custom", preset.width, preset.height, preset.name))

        if getattr(self, "_current_image_size", None):
            self._update_ratio_selection_from_dimensions()

    def save_custom_preset(self) -> None:
        width = self.crop_width_spin.value()
        height = self.crop_height_spin.value()
        if width <= 0 or height <= 0:
            self.show_error("Define una resolución válida antes de guardarla")
            return

        name, ok = QInputDialog.getText(
            self,
            "Guardar resolución",
            "Nombre del preset:",
        )
        if not ok:
            return
        preset_name = name.strip()
        if not preset_name:
            self.show_error("El nombre no puede estar vacío")
            return

        for preset in self.custom_presets:
            if preset.name.lower() == preset_name.lower():
                preset.width = width
                preset.height = height
                break
        else:
            self.custom_presets.append(CropPreset(name=preset_name, width=width, height=height))

        self._persist_custom_presets()
        self._refresh_custom_presets()
        self.status_label.setText(f"Preset '{preset_name}' guardado")

    def _persist_custom_presets(self) -> None:
        try:
            self.preset_storage.save(self.custom_presets)
        except Exception as exc:  # noqa: BLE001
            self.show_error(f"No se pudieron guardar los presets: {exc}")

    def manage_custom_presets(self) -> None:
        if not self.custom_presets:
            self.show_error("No hay presets personalizados todavía")
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("Administrar presets")
        layout = QVBoxLayout(dialog)

        list_widget = QListWidget(dialog)
        for preset in self.custom_presets:
            item = QListWidgetItem(f"{preset.name} — {preset.width}×{preset.height}")
            item.setData(Qt.ItemDataRole.UserRole, preset)
            list_widget.addItem(item)
        layout.addWidget(list_widget)

        button_row = QHBoxLayout()
        edit_button = QPushButton("Editar", dialog)
        delete_button = QPushButton("Eliminar", dialog)
        close_button = QPushButton("Cerrar", dialog)
        button_row.addWidget(edit_button)
        button_row.addWidget(delete_button)
        button_row.addStretch()
        button_row.addWidget(close_button)
        layout.addLayout(button_row)

        def edit_selected() -> None:
            item = list_widget.currentItem()
            if not item:
                return
            preset = item.data(Qt.ItemDataRole.UserRole)
            if not isinstance(preset, CropPreset):
                return
            name, ok = QInputDialog.getText(
                dialog,
                "Editar preset",
                "Nombre:",
                text=preset.name,
            )
            if not ok:
                return
            new_name = name.strip()
            if not new_name:
                self.show_error("El nombre no puede estar vacío")
                return
            width, ok_w = QInputDialog.getInt(dialog, "Editar ancho", "Ancho (px):", preset.width, 1, 100000)
            if not ok_w:
                return
            height, ok_h = QInputDialog.getInt(dialog, "Editar alto", "Alto (px):", preset.height, 1, 100000)
            if not ok_h:
                return
            preset.name = new_name
            preset.width = width
            preset.height = height
            item.setText(f"{preset.name} — {preset.width}×{preset.height}")
            self._persist_custom_presets()
            self._refresh_custom_presets()

        def delete_selected() -> None:
            item = list_widget.currentItem()
            if not item:
                return
            preset = item.data(Qt.ItemDataRole.UserRole)
            if not isinstance(preset, CropPreset):
                return
            confirm = QMessageBox.question(
                dialog,
                "Eliminar preset",
                f"¿Eliminar '{preset.name}'?",
            )
            if confirm != QMessageBox.StandardButton.Yes:
                return
            self.custom_presets = [p for p in self.custom_presets if p is not preset]
            list_widget.takeItem(list_widget.row(item))
            self._persist_custom_presets()
            self._refresh_custom_presets()

        edit_button.clicked.connect(edit_selected)
        delete_button.clicked.connect(delete_selected)
        close_button.clicked.connect(dialog.accept)

        dialog.exec()

    def add_images(self) -> None:
        file_paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Selecciona imágenes",
            str(self.last_directory),
            "Imágenes (*.jpg *.jpeg *.png *.heic);;Todos los archivos (*)",
        )
        if not file_paths:
            return

        added_paths: List[str] = []
        last_added_item: Optional[FileListItem] = None
        for file_path in file_paths:
            if file_path in self.files:
                continue
            self.files.append(file_path)
            added_paths.append(file_path)
            item = self._create_file_item(file_path)
            self.file_list.addTopLevelItem(item)
            last_added_item = item

        if added_paths:
            self.last_directory = Path(added_paths[-1]).parent
            if self.file_list.isSortingEnabled():
                order = self.file_list.header().sortIndicatorOrder()
                self.file_list.sortItems(0, order)
            if last_added_item is not None:
                self.file_list.setCurrentItem(last_added_item)
        elif self.file_list.currentItem() is None and self.file_list.topLevelItemCount():
            self.file_list.setCurrentItem(self.file_list.topLevelItem(0))
        self._update_count_label()

    def clear_images(self) -> None:
        self.files.clear()
        self.file_list.clear()
        self.current_path = None
        self.entries = []
        self.metadata_table.setRowCount(0)
        self.file_label.setText("No hay archivo cargado")
        self.rename_file_button.setEnabled(False)
        self._update_file_size_label("")
        self.status_label.setText("")
        self.preview_pixmap = None
        self.preview_label.clear()
        self.preview_label.setText("Vista previa no disponible")
        self._update_count_label()
        self._crop_auto_sync = True
        self._current_image_size = None
        self._update_crop_tab_state()

    def handle_file_selection_changed(
        self,
        current: Optional[QTreeWidgetItem],
        previous: Optional[QTreeWidgetItem],
    ) -> None:
        del previous  # unused
        if current is None:
            self.current_path = None
            self.entries = []
            self.metadata_table.setRowCount(0)
            self.file_label.setText("No hay archivo cargado")
            self.rename_file_button.setEnabled(False)
            self._update_file_size_label(None)
            self.preview_pixmap = None
            self.preview_label.clear()
            self.preview_label.setText("Vista previa no disponible")
            self._crop_auto_sync = True
            self._current_image_size = None
            self._update_crop_tab_state()
            return
        file_path = current.data(0, Qt.ItemDataRole.UserRole)
        if not file_path:
            return
        self.load_image_metadata(str(file_path))

    def load_image_metadata(self, file_path: str) -> None:
        try:
            image, entries = load_image_with_metadata(file_path)
            image.close()
        except Exception as exc:  # noqa: BLE001 - surface to user
            self.show_error(f"No se pudo abrir la imagen: {exc}")
            return
        self.current_path = file_path
        self.entries = entries
        self.populate_table()
        self.file_label.setText(f"Archivo: {os.path.basename(file_path)}")
        self.rename_file_button.setEnabled(True)
        self._update_file_size_label(file_path)
        self._refresh_item_label(file_path)
        self.status_label.setText("Metadatos cargados correctamente")
        self.last_directory = Path(file_path).parent
        self.update_preview(file_path)
        self._update_crop_tab_state()

    def populate_table(self) -> None:
        self.metadata_table.setRowCount(len(self.entries))
        for row, entry in enumerate(self.entries):
            key_item = QTableWidgetItem(entry.key)
            key_item.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
            self.metadata_table.setItem(row, 0, key_item)

            value_item = QTableWidgetItem(entry.display_value())
            value_item.setFlags(
                Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsEditable
            )
            self.metadata_table.setItem(row, 1, value_item)
        self.update_edit_mode()

    def toggle_edit_mode(self, checked: bool) -> None:
        self.update_edit_mode()
        self.status_label.setText("Edición activada" if checked else "Edición desactivada")

    def update_edit_mode(self) -> None:
        if self.edit_button.isChecked():
            self.metadata_table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked)
        else:
            self.metadata_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)

    def persist_metadata(self) -> None:
        if not self.current_path:
            self.show_error("Primero selecciona una imagen")
            return
        try:
            self.sync_entries_from_table()
            save_metadata(self.current_path, self.entries)
            image, entries = load_image_with_metadata(self.current_path)
            image.close()
            self.entries = entries
            self.populate_table()
            self._refresh_item_label(self.current_path)
            self.status_label.setText("Metadatos actualizados")
        except ValueError as exc:
            self.show_error(str(exc))
        except Exception as exc:  # noqa: BLE001 - surface to user
            self.show_error(f"Error al guardar metadatos: {exc}")

    def _resolve_timestamp(self, path: str) -> Tuple[Optional[datetime], Optional[str]]:
        try:
            preferred = get_preferred_datetime(path)
        except Exception:  # noqa: BLE001 - fall back to filesystem metadata
            preferred = None
        dt = preferred
        if dt is None:
            try:
                stat_result = Path(path).stat()
                timestamp = getattr(stat_result, "st_birthtime", None) or stat_result.st_mtime
                dt = datetime.fromtimestamp(timestamp)
            except Exception:  # noqa: BLE001
                return None, None
        return dt, dt.strftime("%Y-%m-%d %H:%M")

    def _build_list_columns(self, path: str) -> Tuple[str, str, float]:
        dt, date_str = self._resolve_timestamp(path)
        name = Path(path).name
        sort_key = dt.timestamp() if dt is not None else float("-inf")
        return date_str or "", name, sort_key

    def _create_file_item(self, path: str) -> FileListItem:
        date_text, name_text, sort_key = self._build_list_columns(path)
        item = FileListItem([date_text, name_text])
        item.setToolTip(0, path)
        item.setToolTip(1, path)
        item.setData(0, Qt.ItemDataRole.UserRole, path)
        item.setData(0, Qt.ItemDataRole.UserRole + 1, sort_key)
        return item

    def _update_count_label(self) -> None:
        count = len(self.files)
        self.count_label.setText(f"{count} archivo{'s' if count != 1 else ''} en la lista")

    def _refresh_item_label(self, path: str) -> None:
        for row in range(self.file_list.topLevelItemCount()):
            item = self.file_list.topLevelItem(row)
            stored = item.data(0, Qt.ItemDataRole.UserRole)
            if stored == path:
                date_text, name_text, sort_key = self._build_list_columns(path)
                item.setText(0, date_text)
                item.setText(1, name_text)
                item.setToolTip(0, path)
                item.setToolTip(1, path)
                item.setData(0, Qt.ItemDataRole.UserRole + 1, sort_key)
                break
        if self.file_list.isSortingEnabled():
            order = self.file_list.header().sortIndicatorOrder()
            self.file_list.sortItems(0, order)

    def _refresh_all_item_labels(self) -> None:
        for row in range(self.file_list.topLevelItemCount()):
            item = self.file_list.topLevelItem(row)
            path = item.data(0, Qt.ItemDataRole.UserRole)
            if not path:
                continue
            path_str = str(path)
            date_text, name_text, sort_key = self._build_list_columns(path_str)
            item.setText(0, date_text)
            item.setText(1, name_text)
            item.setToolTip(0, path_str)
            item.setToolTip(1, path_str)
            item.setData(0, Qt.ItemDataRole.UserRole + 1, sort_key)
        if self.file_list.isSortingEnabled():
            order = self.file_list.header().sortIndicatorOrder()
            self.file_list.sortItems(0, order)

    def _clone_metadata_value(self, value: Any) -> Any:
        if value is None:
            return None
        if PILExif is not None and isinstance(value, PILExif):
            return _DROP_VALUE
        if isinstance(value, (int, float, str, bool)):
            return value
        if isinstance(value, bytes):
            return bytes(value)
        if isinstance(value, tuple):
            cloned_items = []
            for item in value:
                cloned = self._clone_metadata_value(item)
                if cloned is _DROP_VALUE:
                    return _DROP_VALUE
                cloned_items.append(cloned)
            return tuple(cloned_items)
        if isinstance(value, list):
            cloned_list = []
            for item in value:
                cloned = self._clone_metadata_value(item)
                if cloned is _DROP_VALUE:
                    return _DROP_VALUE
                cloned_list.append(cloned)
            return cloned_list
        if isinstance(value, dict):
            cloned_dict: Dict[Any, Any] = {}
            for key, item in value.items():
                cloned = self._clone_metadata_value(item)
                if cloned is _DROP_VALUE:
                    return _DROP_VALUE
                cloned_dict[key] = cloned
            return cloned_dict
        if hasattr(value, "numerator") and hasattr(value, "denominator"):
            cls = type(value)
            try:
                return cls(value.numerator, value.denominator)  # type: ignore[arg-type]
            except Exception:  # noqa: BLE001
                from fractions import Fraction

                return Fraction(value.numerator, value.denominator)
        try:
            return deepcopy(value)
        except Exception:  # noqa: BLE001
            return _DROP_VALUE

    def copy_metadata(self) -> None:
        if not self.current_path:
            self.show_error("Primero selecciona una imagen")
            return
        try:
            self.sync_entries_from_table()
        except ValueError as exc:
            self.show_error(str(exc))
            return

        cloned_entries: List[MetadataEntry] = []
        dropped = False
        for entry in self.entries:
            cloned_value = self._clone_metadata_value(entry.value)
            if cloned_value is _DROP_VALUE:
                dropped = True
                continue
            cloned_entries.append(
                MetadataEntry(
                    key=entry.key,
                    source=entry.source,
                    tag_id=entry.tag_id,
                    original_value=cloned_value,
                    value=cloned_value,
                )
            )

        if not cloned_entries:
            self.show_error("No se pudieron copiar metadatos compatibles de esta imagen")
            return

        self.copied_metadata = cloned_entries
        self.copied_metadata_label = Path(self.current_path).name
        message = f"Metadatos copiados de {self.copied_metadata_label}"
        if dropped:
            message += " (algunos campos no se copiaron)"
        self.status_label.setText(message)

    def paste_metadata(self) -> None:
        if not self.current_path:
            self.show_error("Selecciona una imagen destino")
            return
        if not self.copied_metadata:
            self.show_error("No hay metadatos copiados todavía")
            return
        try:
            self.sync_entries_from_table()
        except ValueError as exc:
            self.show_error(str(exc))
            return

        prepared_entries: List[MetadataEntry] = []
        for copied in self.copied_metadata:
            cloned_value = self._clone_metadata_value(copied.value)
            if cloned_value is _DROP_VALUE:
                continue
            prepared_entries.append(
                MetadataEntry(
                    key=copied.key,
                    source=copied.source,
                    tag_id=copied.tag_id,
                    original_value=cloned_value,
                    value=cloned_value,
                )
            )

        if not prepared_entries:
            self.show_error("Los metadatos copiados no son compatibles con esta imagen")
            return

        existing_map = {(entry.source, entry.key): entry for entry in self.entries}
        copied_dict = {(entry.source, entry.key): entry for entry in prepared_entries}

        for key, entry in existing_map.items():
            copied = copied_dict.get(key)
            if copied is not None:
                entry.tag_id = copied.tag_id
                cloned_value = self._clone_metadata_value(copied.value)
                if cloned_value is _DROP_VALUE:
                    entry.value = None
                    entry.original_value = None
                else:
                    entry.original_value = cloned_value
                    entry.value = cloned_value
            else:
                entry.value = None

        ordered_entries: List[MetadataEntry] = []
        for key, copied in copied_dict.items():
            existing = existing_map.get(key)
            if existing is not None:
                ordered_entries.append(existing)
            else:
                cloned_value = self._clone_metadata_value(copied.value)
                if cloned_value is _DROP_VALUE:
                    continue
                ordered_entries.append(
                    MetadataEntry(
                        key=copied.key,
                        source=copied.source,
                        tag_id=copied.tag_id,
                        original_value=cloned_value,
                        value=cloned_value,
                    )
                )

        for key, entry in existing_map.items():
            if key not in copied_dict:
                ordered_entries.append(entry)

        self.entries = ordered_entries
        self.populate_table()

        donor = self.copied_metadata_label or "la imagen copiada"
        self.status_label.setText(
            f"Metadatos pegados desde {donor}. Presiona Enter para guardar los cambios."
        )

    def sync_entries_from_table(self) -> None:
        for row, entry in enumerate(self.entries):
            table_item = self.metadata_table.item(row, 1)
            if table_item is None:
                continue
            new_value = table_item.text()
            try:
                update_entry_from_string(entry, new_value)
            except ValueError as exc:
                raise ValueError(f"Fila {row + 1} ({entry.key}): {exc}") from exc

    def convert_format(self) -> None:
        if not self.files:
            self.show_error("Agrega al menos una imagen")
            return
        if not self.current_path:
            self.show_error("Primero selecciona una imagen")
            return

        target_format = self.format_combo.currentText()
        extension = target_extension(target_format)

        convert_stack = False
        stack_paths = self._get_stack_paths()
        stack_items_selected = bool(self.file_list.selectedItems())
        stack_count = len(stack_paths)

        if stack_count > 1:
            dialog = QMessageBox(self)
            dialog.setIcon(QMessageBox.Icon.Question)
            dialog.setWindowTitle("Convertir imágenes")
            scope_label = "pila actual" if stack_items_selected else "lista completa"
            dialog.setText(
                f"¿Quieres convertir solo la imagen activa o toda la {scope_label}?"
            )
            stack_button = dialog.addButton(
                "Pila actual", QMessageBox.ButtonRole.AcceptRole
            )
            dialog.addButton("Solo selección", QMessageBox.ButtonRole.ActionRole)
            dialog.addButton(QMessageBox.StandardButton.Cancel)
            dialog.exec()
            clicked = dialog.clickedButton()
            if clicked is None or clicked == dialog.button(QMessageBox.StandardButton.Cancel):
                return
            convert_stack = clicked == stack_button
        else:
            convert_stack = False

        if convert_stack:
            destination_dir = QFileDialog.getExistingDirectory(
                self,
                "Selecciona carpeta destino",
                str(self.last_directory),
            )
            if not destination_dir:
                return

            destination_path = Path(destination_dir)
            self.last_directory = destination_path
            destination_path.mkdir(parents=True, exist_ok=True)

            try:
                self.sync_entries_from_table()
            except ValueError as exc:
                self.show_error(str(exc))
                return

            errors: List[str] = []
            converted = 0
            skipped = 0
            aborted = False
            for file_path in stack_paths:
                suffix = Path(file_path).suffix.lower()
                same_format_suffixes = {
                    "JPEG": {".jpg", ".jpeg"},
                    "PNG": {".png"},
                    "HEIC": {".heic", ".heif"},
                    "ICO": {".ico"},
                    "PDF": {".pdf"},
                }
                if suffix in same_format_suffixes.get(target_format, set()):
                    skipped += 1
                    continue
                target_path = destination_path / f"{Path(file_path).stem}.{extension}"
                if target_path.exists():
                    resolved = self.resolve_name_conflict(target_path)
                    if resolved is None:
                        aborted = True
                        break
                    target_path = resolved
                try:
                    if file_path == self.current_path:
                        convert_image(file_path, str(target_path), target_format, self.entries)
                    else:
                        convert_image(file_path, str(target_path), target_format)
                    converted += 1
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{Path(file_path).name}: {exc}")

            if aborted:
                self.status_label.setText("Conversión cancelada por el usuario")
                return

            if errors:
                message = "No se pudo convertir:\n" + "\n".join(errors[:5])
                if len(errors) > 5:
                    message += "\n..."
                self.show_error(message)
            if converted:
                skipped_note = (
                    f" (se omitieron {skipped} que ya estaban en formato)" if skipped else ""
                )
                scope_desc = "pila" if stack_items_selected else "lista"
                self.status_label.setText(
                    f"{converted} archivo{'s' if converted != 1 else ''} convertidos a {target_format} en {destination_path}{skipped_note} ({scope_desc})"
                )
            elif skipped:
                self.status_label.setText(
                    f"No se realizaron conversiones: {skipped} archivo{'s' if skipped != 1 else ''} ya estaban en {target_format}"
                )
            return

        source_path = Path(self.current_path)
        default_path = self.last_directory / f"{source_path.stem}.{extension}"
        destination, _ = QFileDialog.getSaveFileName(
            self,
            "Guardar imagen convertida",
            str(default_path),
            f"{target_format} (*.{extension})",
        )
        if not destination:
            return

        try:
            self.sync_entries_from_table()
            convert_image(self.current_path, destination, target_format, self.entries)
            self.status_label.setText(f"Imagen convertida a {target_format} con éxito")
            self.last_directory = Path(destination).parent
        except ValueError as exc:
            self.show_error(str(exc))
        except Exception as exc:  # noqa: BLE001 - surface to user
            self.show_error(f"No se pudo convertir la imagen: {exc}")

    def match_crop_to_current(self) -> None:
        if not self.current_path:
            self.show_error("Selecciona una imagen para igualar las dimensiones")
            return
        self._crop_auto_sync = True
        self._update_crop_tab_state()

    def copy_crop_ratio(self) -> None:
        width = self.crop_width_spin.value()
        height = self.crop_height_spin.value()
        if width <= 0 or height <= 0:
            self.show_error("Define una región de recorte válida antes de copiar la proporción")
            return
        factor = gcd(width, height) or 1
        ratio = (width // factor, height // factor)
        self._copied_crop_ratio = ratio
        self.status_label.setText(f"Proporción {ratio[0]}:{ratio[1]} copiada")

    def paste_crop_ratio(self) -> None:
        if not self._copied_crop_ratio:
            self.show_error("No hay proporción de recorte copiada")
            return

        ratio_w, ratio_h = self._copied_crop_ratio
        if ratio_w <= 0 or ratio_h <= 0:
            self.show_error("La proporción copiada no es válida")
            return

        current_width = max(1, self.crop_width_spin.value())
        target_width = max(1, current_width)
        target_height = max(1, int(round(target_width * ratio_h / ratio_w)))

        target_width = min(target_width, self.crop_width_spin.maximum())
        target_height = min(target_height, self.crop_height_spin.maximum())
        if target_height <= 0:
            target_height = 1
        if target_width <= 0:
            target_width = 1

        self._crop_auto_sync = False
        self._updating_crop_controls = True
        self.crop_width_spin.setValue(target_width)
        self.crop_height_spin.setValue(target_height)
        self._updating_crop_controls = False
        self.status_label.setText(f"Proporción {ratio_w}:{ratio_h} aplicada")
        self._update_ratio_selection_from_dimensions()

    def crop_current_image(self) -> None:
        if not self.current_path:
            self.show_error("Selecciona una imagen para recortar")
            return
        self._apply_crop([self.current_path], "imagen actual")

    def crop_stack_images(self) -> None:
        stack_paths = self._get_stack_paths()
        if not stack_paths:
            self.show_error("Agrega al menos una imagen a la pila")
            return
        self._apply_crop(stack_paths, "pila")

    def _on_crop_ratio_changed(self, index: int) -> None:
        del index  # unused
        if self._updating_crop_controls:
            return
        data = self.crop_ratio_combo.currentData()
        if data is None:
            self._set_custom_ratio_mode()
            return
        if isinstance(data, tuple) and len(data) >= 3 and data[0] == "custom":
            _, width, height = data[:3]
            self.crop_size_combo.blockSignals(True)
            self.crop_size_combo.clear()
            self.crop_size_combo.addItem("Preset personalizado", None)
            self.crop_size_combo.setEnabled(False)
            self.crop_size_combo.blockSignals(False)
            self._apply_preset_dimensions(width, height, self.crop_ratio_combo.currentText())
            return
        ratio_key = str(data)
        self._populate_crop_size_options(ratio_key)
        if self.crop_size_combo.count():
            self.crop_size_combo.setCurrentIndex(0)

    def _populate_crop_size_options(self, ratio_key: str) -> None:
        presets = self._crop_ratio_presets.get(ratio_key, [])
        self.crop_size_combo.blockSignals(True)
        self.crop_size_combo.clear()
        for width, height in presets:
            label = f"{width} × {height} px"
            self.crop_size_combo.addItem(label, (width, height))
        self.crop_size_combo.setEnabled(bool(presets))
        self.crop_size_combo.blockSignals(False)

    def _on_crop_size_changed(self, index: int) -> None:
        del index  # unused
        if self._updating_crop_controls:
            return
        data = self.crop_size_combo.currentData()
        if not data:
            return
        width, height = data
        ratio_label = self.crop_ratio_combo.currentData()
        ratio_text = str(ratio_label) if ratio_label else None
        self._apply_preset_dimensions(width, height, ratio_text)

    def _apply_preset_dimensions(
        self,
        width: int,
        height: int,
        ratio_label: Optional[str] = None,
    ) -> None:
        self._crop_auto_sync = False
        self._updating_crop_controls = True
        self.crop_width_spin.setValue(width)
        self.crop_height_spin.setValue(height)
        self._updating_crop_controls = False
        if ratio_label:
            self.status_label.setText(
                f"Proporción {ratio_label} establecida: {width} × {height} px"
            )
        else:
            self.status_label.setText(
                f"Dimensiones predefinidas aplicadas: {width} × {height} px"
            )
        self._update_ratio_selection_from_dimensions()

    def _set_custom_ratio_mode(self) -> None:
        if not hasattr(self, "crop_ratio_combo") or not hasattr(self, "crop_size_combo"):
            return
        self.crop_ratio_combo.blockSignals(True)
        custom_index = self.crop_ratio_combo.findData(None)
        if custom_index >= 0:
            self.crop_ratio_combo.setCurrentIndex(custom_index)
        self.crop_ratio_combo.blockSignals(False)

        self.crop_size_combo.blockSignals(True)
        self.crop_size_combo.clear()
        self.crop_size_combo.addItem("Elige una proporción predefinida", None)
        self.crop_size_combo.setEnabled(False)
        self.crop_size_combo.blockSignals(False)

    def _update_ratio_selection_from_dimensions(self) -> None:
        if not hasattr(self, "crop_ratio_combo") or self._updating_crop_controls:
            return
        width = self.crop_width_spin.value()
        height = self.crop_height_spin.value()
        if width <= 0 or height <= 0:
            self._set_custom_ratio_mode()
            return

        factor = gcd(width, height) or 1
        ratio_label = f"{width // factor}:{height // factor}"
        presets = self._crop_ratio_presets.get(ratio_label)
        if not presets:
            self._set_custom_ratio_mode()
            return

        try:
            match_index = next(
                idx for idx, dims in enumerate(presets) if dims == (width, height)
            )
        except StopIteration:
            self._set_custom_ratio_mode()
            return

        ratio_index = self.crop_ratio_combo.findData(ratio_label)
        if ratio_index < 0:
            for idx in range(self.crop_ratio_combo.count()):
                data = self.crop_ratio_combo.itemData(idx)
                if isinstance(data, tuple) and len(data) >= 3 and data[0] == "custom":
                    if data[1] == width and data[2] == height:
                        self._updating_crop_controls = True
                        self.crop_ratio_combo.blockSignals(True)
                        self.crop_ratio_combo.setCurrentIndex(idx)
                        self.crop_ratio_combo.blockSignals(False)
                        self.crop_size_combo.blockSignals(True)
                        self.crop_size_combo.clear()
                        self.crop_size_combo.addItem("Preset personalizado", None)
                        self.crop_size_combo.setEnabled(False)
                        self.crop_size_combo.blockSignals(False)
                        self._updating_crop_controls = False
                        return
            self._set_custom_ratio_mode()
            return

        self._updating_crop_controls = True
        self.crop_ratio_combo.blockSignals(True)
        self.crop_ratio_combo.setCurrentIndex(ratio_index)
        self.crop_ratio_combo.blockSignals(False)

        self._populate_crop_size_options(ratio_label)
        self.crop_size_combo.blockSignals(True)
        self.crop_size_combo.setCurrentIndex(match_index)
        self.crop_size_combo.setEnabled(True)
        self.crop_size_combo.blockSignals(False)
        self._updating_crop_controls = False

    def _on_crop_dimension_changed(self) -> None:
        if self._updating_crop_controls:
            return
        self._crop_auto_sync = False
        self._update_ratio_selection_from_dimensions()

    def check_for_updates(self) -> None:
        repo_root = Path(__file__).resolve().parent.parent
        git_executable = shutil.which("git")
        changes_detected = True
        if git_executable is not None:
            try:
                result = subprocess.run(
                    [git_executable, "status", "--porcelain"],
                    capture_output=True,
                    text=True,
                    cwd=repo_root,
                    check=True,
                )
                changes_detected = bool(result.stdout.strip())
            except subprocess.CalledProcessError:
                changes_detected = True

        if not changes_detected:
            QMessageBox.information(
                self,
                "Actualización",
                "No se detectaron cambios pendientes.",
            )
            return

        confirm = QMessageBox.question(
            self,
            "Reconstruir aplicación",
            "Se generará nuevamente el paquete .app utilizando py2app. ¿Deseas continuar?",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        python_executable = Path(sys.executable)
        venv_python = repo_root / "venv" / "bin" / "python"
        if venv_python.exists():
            python_executable = venv_python

        self.update_app_button.setEnabled(False)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)

        packaging_check = subprocess.run(
            [str(python_executable), "-c", "import packaging"],
            capture_output=True,
            text=True,
        )
        if packaging_check.returncode != 0:
            install_cmd = [str(python_executable), "-m", "pip", "install", "packaging>=23.0", "py2app>=0.28"]
            install_result = subprocess.run(
                install_cmd,
                cwd=repo_root,
                capture_output=True,
                text=True,
            )
            if install_result.returncode != 0:
                QApplication.restoreOverrideCursor()
                self.update_app_button.setEnabled(True)
                message = install_result.stderr.strip() or install_result.stdout.strip() or "No se pudo instalar packaging"
                self.show_error(f"Fallo al preparar el entorno de compilación:\n{message[:1000]}")
                return

        try:
            build_result = subprocess.run(
                [str(python_executable), "setup.py", "py2app"],
                cwd=repo_root,
                capture_output=True,
                text=True,
            )
        except Exception as exc:  # noqa: BLE001
            QApplication.restoreOverrideCursor()
            self.update_app_button.setEnabled(True)
            self.show_error(f"No se pudo ejecutar py2app: {exc}")
            return

        QApplication.restoreOverrideCursor()
        self.update_app_button.setEnabled(True)

        if build_result.returncode != 0:
            message = build_result.stderr.strip() or build_result.stdout.strip() or "Error desconocido"
            self.show_error(f"La compilación falló:\n{message[:1000]}")
            return

        self.status_label.setText("Compilación completada. Iniciando versión empaquetada...")

        app_bundle = repo_root / "dist" / "PhotoLab.app"
        if app_bundle.exists():
            try:
                subprocess.Popen(["open", str(app_bundle)])
            except Exception as exc:  # noqa: BLE001
                QMessageBox.information(
                    self,
                    "Actualización",
                    f"Compilación lista en {app_bundle}. Inicia la app manualmente. Detalle: {exc}",
                )
            else:
                QApplication.instance().quit()
                return

        QMessageBox.information(
            self,
            "Actualización",
            "Compilación finalizada. Ejecuta la app desde la carpeta dist/",
        )

    def _set_crop_mode(self, mode: str) -> None:
        if mode not in {"fill", "letterbox"}:
            return
        self._crop_mode = mode
        fill_checked = mode == "fill"
        letterbox_checked = mode == "letterbox"
        self.crop_mode_button_fill.blockSignals(True)
        self.crop_mode_button_letterbox.blockSignals(True)
        self.crop_mode_button_fill.setChecked(fill_checked)
        self.crop_mode_button_letterbox.setChecked(letterbox_checked)
        self.crop_mode_button_fill.blockSignals(False)
        self.crop_mode_button_letterbox.blockSignals(False)
        mode_label = "Recorte adaptativo" if fill_checked else "Bordes blancos"
        self.status_label.setText(f"Modo de ajuste: {mode_label}")

    @staticmethod
    def _anchor_position(total: int, extent: int, anchor: str) -> float:
        anchor_normalized = anchor.lower()
        if anchor_normalized in {"center", "middle"}:
            return (total - extent) / 2
        if anchor_normalized in {"right", "bottom"}:
            return float(total - extent)
        return 0.0

    def _update_crop_tab_state(self) -> None:
        if not hasattr(self, "crop_info_label"):
            return
        if not self.current_path:
            self._current_image_size = None
            self.crop_info_label.setText("Selecciona una imagen para recortar")
            self._set_custom_ratio_mode()
            if self._crop_auto_sync:
                self._updating_crop_controls = True
                self.crop_width_spin.setValue(max(1, self.crop_width_spin.value()))
                self.crop_height_spin.setValue(max(1, self.crop_height_spin.value()))
                self.crop_offset_x_spin.setValue(0)
                self.crop_offset_y_spin.setValue(0)
                self._updating_crop_controls = False
            return
        try:
            with Image.open(self.current_path) as img:
                width, height = img.size
        except Exception:
            self._current_image_size = None
            self.crop_info_label.setText("No se pudo obtener el tamaño de la imagen")
            self._set_custom_ratio_mode()
            return

        self._current_image_size = (width, height)
        self.crop_info_label.setText(f"Tamaño actual: {width} × {height} px")
        if self._crop_auto_sync:
            self._updating_crop_controls = True
            self.crop_width_spin.setMaximum(max(width, self.crop_width_spin.maximum()))
            self.crop_height_spin.setMaximum(max(height, self.crop_height_spin.maximum()))
            self.crop_width_spin.setValue(width)
            self.crop_height_spin.setValue(height)
            self.crop_offset_x_spin.setValue(0)
            self.crop_offset_y_spin.setValue(0)
            self._updating_crop_controls = False
        self._update_ratio_selection_from_dimensions()

    def _prompt_crop_destination(self, count: int) -> Tuple[Optional[bool], Optional[Path]]:
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Icon.Question)
        dialog.setWindowTitle("Guardar recortes")
        label_scope = "archivos" if count != 1 else "archivo"
        dialog.setText(f"¿Cómo quieres guardar el recorte para {count} {label_scope}?")
        overwrite_button = dialog.addButton(
            "Sobrescribir originales",
            QMessageBox.ButtonRole.AcceptRole,
        )
        copies_button = dialog.addButton(
            "Crear copias",
            QMessageBox.ButtonRole.ActionRole,
        )
        cancel_button = dialog.addButton(
            "Cancelar",
            QMessageBox.ButtonRole.RejectRole,
        )
        dialog.exec()
        clicked = dialog.clickedButton()
        if clicked is None or clicked == cancel_button:
            return None, None
        if clicked == overwrite_button:
            return True, None

        destination = QFileDialog.getExistingDirectory(
            self,
            "Selecciona carpeta destino",
            str(self.last_directory),
        )
        if not destination:
            return None, None
        destination_path = Path(destination)
        self.last_directory = destination_path
        return False, destination_path

    def _apply_crop(self, paths: List[str], scope_label: str) -> None:
        if not paths:
            return
        width = self.crop_width_spin.value()
        height = self.crop_height_spin.value()
        if width <= 0 or height <= 0:
            self.show_error("Las dimensiones de recorte deben ser mayores a cero")
            return

        anchor_data = self.crop_anchor_combo.currentData()
        if anchor_data is None:
            anchor_data = ("center", "center")
        anchor_x, anchor_y = anchor_data
        offset_x = self.crop_offset_x_spin.value()
        offset_y = self.crop_offset_y_spin.value()

        prompt = self._prompt_crop_destination(len(paths))
        if prompt[0] is None:
            return
        update_in_place, destination_dir = prompt

        success_paths: List[Path] = []
        errors: List[str] = []
        aborted = False
        processed_current = False
        for path in paths:
            source = Path(path)
            try:
                with Image.open(source) as img:
                    img_width, img_height = img.size
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{source.name}: {exc}")
                continue

            left = self._anchor_position(img_width, width, anchor_x) + offset_x
            top = self._anchor_position(img_height, height, anchor_y) + offset_y

            if width <= img_width:
                min_left = 0.0
                max_left = img_width - width
            else:
                overflow_x = width - img_width
                min_left = -overflow_x
                max_left = 0.0

            if height <= img_height:
                min_top = 0.0
                max_top = img_height - height
            else:
                overflow_y = height - img_height
                min_top = -overflow_y
                max_top = 0.0

            left = max(min_left, min(left, max_left))
            top = max(min_top, min(top, max_top))
            crop_box = (
                int(round(left)),
                int(round(top)),
                int(round(left + width)),
                int(round(top + height)),
            )

            if update_in_place:
                destination_path = source
            else:
                assert destination_dir is not None
                destination_path = destination_dir / source.name
                if destination_path.exists():
                    resolved = self.resolve_name_conflict(destination_path)
                    if resolved is None:
                        aborted = True
                        break
                    destination_path = resolved

            try:
                result_path = crop_image(
                    str(source),
                    str(destination_path),
                    crop_box,
                    mode=self._crop_mode,
                    anchor=(anchor_x, anchor_y),
                )
                success_paths.append(result_path)
                if self.current_path and Path(self.current_path) == source:
                    processed_current = True
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{source.name}: {exc}")

        if aborted:
            self.status_label.setText("Recorte cancelado por el usuario")
            return

        if errors:
            message = "No se pudo recortar:\n" + "\n".join(errors[:5])
            if len(errors) > 5:
                message += "\n..."
            QMessageBox.warning(self, "Recorte incompleto", message)

        if not success_paths:
            if not errors:
                self.status_label.setText("No se aplicó ningún recorte")
            self._update_crop_tab_state()
            return

        if update_in_place:
            self._refresh_all_item_labels()
            if processed_current and self.current_path:
                self.load_image_metadata(self.current_path)
        else:
            assert destination_dir is not None
            added_items: List[FileListItem] = []
            for result in success_paths:
                result_str = str(result)
                if result_str not in self.files:
                    self.files.append(result_str)
                    item = self._create_file_item(result_str)
                    self.file_list.addTopLevelItem(item)
                    added_items.append(item)
            if added_items and self.file_list.isSortingEnabled():
                order = self.file_list.header().sortIndicatorOrder()
                self.file_list.sortItems(0, order)
            self._update_count_label()

        success_count = len(success_paths)
        scope_desc = "imagen actual" if scope_label == "imagen actual" else "pila"
        destination_desc = "originales" if update_in_place else str(destination_dir)
        self.status_label.setText(
            f"Recorte aplicado a {success_count} archivo{'s' if success_count != 1 else ''} en {destination_desc} ({scope_desc})"
        )
        self._update_crop_tab_state()

    def show_error(self, message: str) -> None:
        QMessageBox.critical(self, "Error", message)
        self.status_label.setText(message)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self.refresh_preview()

    def update_preview(self, file_path: str) -> None:
        pixmap = self._build_preview_pixmap(file_path)
        if pixmap is None:
            self.preview_pixmap = None
            self.preview_label.clear()
            self.preview_label.setText("Vista previa no disponible")
            return
        self.preview_pixmap = pixmap
        self.refresh_preview()

    def refresh_preview(self) -> None:
        if self.preview_pixmap is None:
            self.preview_label.clear()
            self.preview_label.setText("Vista previa no disponible")
            return
        if self.preview_label.width() == 0 or self.preview_label.height() == 0:
            return
        scaled = self.preview_pixmap.scaled(
            self.preview_label.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.preview_label.setPixmap(scaled)
        self.preview_label.setText("")

    def _build_preview_pixmap(self, file_path: str) -> Optional[QPixmap]:
        try:
            with Image.open(file_path) as img:
                resample = getattr(Image, "Resampling", None)
                resample_filter = resample.LANCZOS if resample else Image.LANCZOS
                img.thumbnail((1024, 1024), resample_filter)
                if img.mode != "RGBA":
                    img = img.convert("RGBA")
                data = img.tobytes("raw", "RGBA")
                qimage = QImage(data, img.width, img.height, QImage.Format.Format_RGBA8888)
                return QPixmap.fromImage(qimage)
        except Exception:
            return None

    def resolve_name_conflict(self, path: Path) -> Optional[Path]:
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.setWindowTitle("Archivo existente")
        dialog.setText(f"El archivo {path.name} ya existe. ¿Qué deseas hacer?")
        duplicate_button = dialog.addButton("Duplicar", QMessageBox.ButtonRole.AcceptRole)
        replace_button = dialog.addButton("Reemplazar", QMessageBox.ButtonRole.DestructiveRole)
        cancel_button = dialog.addButton("Cancelar", QMessageBox.ButtonRole.RejectRole)
        dialog.exec()
        clicked = dialog.clickedButton()
        if clicked == cancel_button:
            return None
        if clicked == replace_button:
            return path
        if clicked == duplicate_button:
            return self.generate_duplicate_path(path)
        return None

    def generate_duplicate_path(self, path: Path) -> Path:
        stem = path.stem
        suffix = path.suffix
        counter = 1
        while True:
            candidate = path.with_name(f"{stem} ({counter}){suffix}")
            if not candidate.exists():
                return candidate
            counter += 1


    def apply_datetime_to_stack(self) -> None:
        stack_paths = self._get_stack_paths()
        if not stack_paths:
            self.show_error("Agrega al menos una imagen a la pila")
            return

        target_date = self.date_edit.date().toPyDate()
        target_time = self.time_edit.time().toPyTime()

        component_flags = {
            "year": self.year_checkbox.isChecked(),
            "month": self.month_checkbox.isChecked(),
            "day": self.day_checkbox.isChecked(),
            "hour": self.hour_checkbox.isChecked(),
            "minute": self.minute_checkbox.isChecked(),
            "second": self.second_checkbox.isChecked(),
        }

        if not any(component_flags.values()):
            self.show_error("Selecciona al menos un componente para actualizar")
            return

        mode_dialog = QMessageBox(self)
        mode_dialog.setIcon(QMessageBox.Icon.Question)
        mode_dialog.setWindowTitle("Aplicar fecha")
        mode_dialog.setText("¿Cómo quieres aplicar la fecha a la pila?")
        originals_button = mode_dialog.addButton(
            "Actualizar originales",
            QMessageBox.ButtonRole.AcceptRole,
        )
        copies_button = mode_dialog.addButton(
            "Crear copias",
            QMessageBox.ButtonRole.ActionRole,
        )
        cancel_button = mode_dialog.addButton(
            "Cancelar",
            QMessageBox.ButtonRole.RejectRole,
        )
        mode_dialog.exec()
        clicked = mode_dialog.clickedButton()

        if clicked is None or clicked == cancel_button:
            return

        update_in_place = clicked == originals_button

        destination_path: Optional[Path] = None
        if not update_in_place:
            destination = QFileDialog.getExistingDirectory(
                self,
                "Selecciona carpeta destino",
                str(self.last_directory),
            )
            if not destination:
                return
            destination_path = Path(destination)
            self.last_directory = destination_path

        try:
            updated_paths, errors = apply_date_with_exiftool(
                stack_paths,
                target_date,
                target_time,
                component_flags,
                destination_path,
            )
        except Exception as exc:  # noqa: BLE001
            self.show_error(str(exc))
            return

        if errors:
            message = "No se pudo actualizar la fecha para:\n" + "\n".join(errors[:5])
            if len(errors) > 5:
                message += "\n..."
            self.show_error(message)
            return

        current_item = self.file_list.currentItem()
        if current_item:
            current_path = current_item.data(0, Qt.ItemDataRole.UserRole)
            if current_path:
                self.load_image_metadata(str(current_path))
        count = len(updated_paths)

        convert_choice = False
        chosen_format: Optional[str] = None
        if updated_paths and not update_in_place:
            dialog = QMessageBox(self)
            dialog.setIcon(QMessageBox.Icon.Question)
            dialog.setWindowTitle("¿Convertir también?")
            dialog.setText("¿Deseas cambiar también el formato de las copias?")
            convert_button = dialog.addButton("Cambiar formato", QMessageBox.ButtonRole.AcceptRole)
            dialog.addButton("Mantener formato", QMessageBox.ButtonRole.RejectRole)
            dialog.exec()
            convert_choice = dialog.clickedButton() == convert_button

        converted_message = ""
        if convert_choice:
            formats = ["JPEG", "PNG", "HEIC", "ICO", "PDF"]
            current_text = self.format_combo.currentText()
            current_index = formats.index(current_text) if current_text in formats else 0
            chosen_format, ok = QInputDialog.getItem(
                self,
                "Selecciona formato",
                "Formato de salida:",
                formats,
                current_index,
                False,
            )
            if ok and chosen_format:
                extension = target_extension(chosen_format)
                converted = 0
                conv_errors: List[str] = []
                aborted_conversion = False
                for path in updated_paths:
                    target_path = path.with_suffix(f".{extension}")
                    if target_path.exists():
                        resolved = self.resolve_name_conflict(target_path)
                        if resolved is None:
                            aborted_conversion = True
                            break
                        target_path = resolved
                    try:
                        convert_image(str(path), str(target_path), chosen_format)
                        converted += 1
                    except Exception as exc:  # noqa: BLE001
                        conv_errors.append(f"{path.name}: {exc}")
                if aborted_conversion:
                    converted_message = " | conversión cancelada"
                else:
                    if conv_errors:
                        message = "No se pudo convertir:\n" + "\n".join(conv_errors[:5])
                        if len(conv_errors) > 5:
                            message += "\n..."
                        self.show_error(message)
                    if converted:
                        converted_message = f" | {converted} archivo{'s' if converted != 1 else ''} convertidos a {chosen_format}"

        if update_in_place:
            self._refresh_all_item_labels()

        target_description = "los archivos originales" if update_in_place else str(destination_path)
        self.status_label.setText(
            f"Fecha aplicada a {count} archivo{'s' if count != 1 else ''} en {target_description}{converted_message}"
        )
        self._update_crop_tab_state()

    def rename_current_file(self) -> None:
        if not self.current_path:
            self.show_error("Selecciona un archivo para renombrar")
            return

        current_path = Path(self.current_path)
        new_name, accepted = QInputDialog.getText(
            self,
            "Renombrar archivo",
            "Nuevo nombre:",
            text=current_path.name,
        )

        if not accepted:
            return

        new_name = new_name.strip()
        if not new_name:
            self.show_error("El nombre no puede estar vacío")
            return

        separators = {os.sep}
        if os.altsep:
            separators.add(os.altsep)
        if any(sep and sep in new_name for sep in separators):
            self.show_error("El nombre no puede contener separadores de ruta")
            return

        original_suffix = current_path.suffix
        proposed_name = new_name
        if original_suffix:
            provided_suffix = Path(new_name).suffix
            if not provided_suffix:
                proposed_name = f"{new_name}{original_suffix}"

        if proposed_name == current_path.name:
            return

        new_path = current_path.with_name(proposed_name)
        if new_path.exists():
            self.show_error("Ya existe un archivo con ese nombre")
            return

        try:
            current_path.rename(new_path)
        except Exception as exc:  # noqa: BLE001
            self.show_error(f"No se pudo renombrar el archivo: {exc}")
            return

        old_path_str = str(current_path)
        new_path_str = str(new_path)

        self.current_path = new_path_str
        self.file_label.setText(f"Archivo: {new_path.name}")
        self._update_file_size_label(new_path_str)
        self.files = [new_path_str if path == old_path_str else path for path in self.files]

        for row in range(self.file_list.topLevelItemCount()):
            item = self.file_list.topLevelItem(row)
            stored_path = item.data(0, Qt.ItemDataRole.UserRole)
            if stored_path == old_path_str:
                item.setData(0, Qt.ItemDataRole.UserRole, new_path_str)
                item.setText(1, new_path.name)
                item.setToolTip(0, new_path_str)
                item.setToolTip(1, new_path_str)
                self.file_list.setCurrentItem(item)
                break

        if self.copied_metadata_label == current_path.name:
            self.copied_metadata_label = new_path.name

        self._refresh_item_label(new_path_str)
        self.status_label.setText("Archivo renombrado")
        self.last_directory = new_path.parent

    def rename_stack(self) -> None:
        stack_paths = self._get_stack_paths()
        if not stack_paths:
            self.show_error("Agrega al menos una imagen a la pila")
            return

        default_base = Path(stack_paths[0]).stem or "archivo"
        base_name, ok = QInputDialog.getText(
            self,
            "Renombrar pila",
            "Nombre base:",
            text=default_base,
        )
        if not ok:
            return
        base_name = base_name.strip()
        if not base_name:
            self.show_error("El nombre base no puede estar vacío")
            return

        separators = [os.sep]
        if os.altsep:
            separators.append(os.altsep)
        if any(sep in base_name for sep in separators if sep):
            self.show_error("El nombre base no puede incluir separadores de ruta")
            return

        try:
            dated_files = []
            for path in stack_paths:
                dt = get_preferred_datetime(path)
                dt_value = dt or datetime.min
                dated_files.append((path, dt_value))
        except Exception as exc:  # noqa: BLE001
            self.show_error(f"No se pudo leer la fecha para renombrar: {exc}")
            return

        dated_files.sort(key=lambda item: (item[1], Path(item[0]).name), reverse=True)

        source_paths = {Path(p) for p in stack_paths}
        rename_plan: List[Tuple[Path, Path]] = []
        conflicts: List[Path] = []
        for index, (path_str, _) in enumerate(dated_files, start=1):
            source = Path(path_str)
            target = source.with_name(f"{base_name}_{index}{source.suffix}")
            rename_plan.append((source, target))
            if target.exists() and target not in source_paths:
                conflicts.append(target)

        if conflicts:
            message = "Ya existe un archivo con alguno de los nombres destino:\n" + "\n".join(
                conflict.name for conflict in conflicts[:5]
            )
            if len(conflicts) > 5:
                message += "\n..."
            self.show_error(message)
            return

        temp_mapping: List[Tuple[Path, Path, Path]] = []
        try:
            for source, target in rename_plan:
                temp_name = source.with_name(f".__tmp_{uuid4().hex}_{source.name}")
                os.replace(source, temp_name)
                temp_mapping.append((source, temp_name, target))
        except Exception as exc:  # noqa: BLE001
            for original, temp, _ in reversed(temp_mapping):
                if temp.exists() and not original.exists():
                    try:
                        os.replace(temp, original)
                    except Exception:  # noqa: BLE001
                        pass
            self.show_error(f"No se pudo preparar el renombrado: {exc}")
            return

        completed: List[Tuple[Path, Path]] = []
        try:
            for source, temp, target in temp_mapping:
                os.replace(temp, target)
                completed.append((source, target))
        except Exception as exc:  # noqa: BLE001
            for original, final in reversed(completed):
                if final.exists() and not original.exists():
                    try:
                        os.replace(final, original)
                    except Exception:  # noqa: BLE001
                        pass
            for original, temp, _ in reversed(temp_mapping):
                if temp.exists() and not original.exists():
                    try:
                        os.replace(temp, original)
                    except Exception:  # noqa: BLE001
                        pass
            self.show_error(f"No se pudo renombrar la pila: {exc}")
            return

        rename_map = {str(source): str(target) for source, target in rename_plan}

        current_before = self.current_path
        self.files = [rename_map.get(path, path) for path in self.files]

        selected_targets = {rename_map.get(path, path) for path in stack_paths}

        self.file_list.clear()
        for path in self.files:
            item = self._create_file_item(path)
            self.file_list.addTopLevelItem(item)

        if self.file_list.isSortingEnabled():
            order = self.file_list.header().sortIndicatorOrder()
            self.file_list.sortItems(0, order)

        self.current_path = None
        if current_before and current_before in rename_map:
            new_current = rename_map[current_before]
            for row in range(self.file_list.topLevelItemCount()):
                item = self.file_list.topLevelItem(row)
                if item.data(0, Qt.ItemDataRole.UserRole) == new_current:
                    self.file_list.setCurrentItem(item)
                    item.setSelected(True)
                    break
        elif self.file_list.topLevelItemCount():
            self.file_list.setCurrentItem(self.file_list.topLevelItem(0))

        for row in range(self.file_list.topLevelItemCount()):
            item = self.file_list.topLevelItem(row)
            path = item.data(0, Qt.ItemDataRole.UserRole)
            if path in selected_targets:
                item.setSelected(True)

        self.last_directory = Path(self.files[0]).parent if self.files else self.last_directory
        self._update_count_label()
        self.status_label.setText(
            f"Pila renombrada con prefijo '{base_name}' (_1 ... _{len(rename_plan)})"
        )
        self._update_crop_tab_state()

    def _get_stack_paths(self) -> List[str]:
        selected_items = self.file_list.selectedItems()
        stack: List[str] = []
        for item in selected_items:
            path_value = item.data(0, Qt.ItemDataRole.UserRole)
            if path_value:
                stack.append(str(path_value))
        if stack:
            return stack
        return list(self.files)

    def eventFilter(self, source: QObject, event: QEvent) -> bool:  # type: ignore[override]
        if (
            self.metadata_table is not None
            and source is self.metadata_table
            and event.type() == QEvent.Type.KeyPress
        ):
            if (
                isinstance(event, QKeyEvent)
                and self.metadata_table.state() == QAbstractItemView.State.EditingState
                and event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter}
                and self.edit_button.isChecked()
            ):
                QTimer.singleShot(0, self.persist_metadata)
        return super().eventFilter(source, event)


def target_extension(fmt: str) -> str:
    fmt_upper = fmt.upper()
    if fmt_upper in {"JPEG", "JPG"}:
        return "jpg"
    if fmt_upper == "PNG":
        return "png"
    if fmt_upper == "HEIC":
        return "heic"
    if fmt_upper == "ICO":
        return "ico"
    if fmt_upper == "PDF":
        return "pdf"
    raise ValueError(f"Formato no soportado: {fmt}")


def run_app() -> None:
    app = QApplication(sys.argv)
    window = PhotoLabWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    run_app()
