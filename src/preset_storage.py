from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List

APP_DIR_NAME = ".photolab"
PRESETS_FILENAME = "crop_presets.json"


@dataclass
class CropPreset:
    name: str
    width: int
    height: int


class PresetStorage:
    def __init__(self, base_dir: Path | None = None) -> None:
        home = Path.home()
        self.base_dir = base_dir or home / APP_DIR_NAME
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.preset_file = self.base_dir / PRESETS_FILENAME

    def load(self) -> List[CropPreset]:
        if not self.preset_file.exists():
            return []
        try:
            data = json.loads(self.preset_file.read_text(encoding="utf-8"))
            presets = [CropPreset(**item) for item in data if self._is_valid(item)]
            return presets
        except Exception:
            return []

    def save(self, presets: List[CropPreset]) -> None:
        serialized = [asdict(preset) for preset in presets]
        self.preset_file.write_text(json.dumps(serialized, indent=2), encoding="utf-8")

    @staticmethod
    def _is_valid(item: dict) -> bool:
        if not isinstance(item, dict):
            return False
        width = item.get("width")
        height = item.get("height")
        name = item.get("name")
        return (
            isinstance(name, str)
            and isinstance(width, int)
            and isinstance(height, int)
            and width > 0
            and height > 0
        )
