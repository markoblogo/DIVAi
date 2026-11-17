"""Read a pixel edit exported from editor.html and apply it to native 11 × 11 frames."""
import json
import re
from dataclasses import dataclass
from pathlib import Path

import faces

FRAME_KEYS = ("look", "camera", "blink", "talk0", "talk1")


@dataclass
class Skin:
    face: str
    side: str
    changes: dict

    @classmethod
    def load(cls, path):
        path = Path(path)
        if path.stat().st_size > 100_000:
            raise ValueError("Файл персонажа слишком большой.")
        with path.open(encoding="utf-8") as source:
            data = json.load(source)
        if not isinstance(data, dict) or data.get("version") != 1:
            raise ValueError("Неизвестная версия файла персонажа.")
        face, side = data.get("face"), data.get("side")
        if not isinstance(face, str) or face not in faces.FACES or side not in ("left", "right"):
            raise ValueError("В файле неизвестный персонаж или направление взгляда.")
        raw = data.get("changes")
        if not isinstance(raw, dict):
            raise ValueError("В файле отсутствуют пиксельные правки.")
        changes = {}
        for frame, points in raw.items():
            if frame not in FRAME_KEYS or not isinstance(points, dict):
                raise ValueError("Некорректные данные позы.")
            changes[frame] = {}
            for key, value in points.items():
                if not re.fullmatch(r"[0-9]{1,3}", key) or not 0 <= int(key) < 121:
                    raise ValueError("Пиксель за пределами матрицы 11 × 11.")
                if not isinstance(value, str) or not re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
                    raise ValueError("Некорректный цвет пикселя.")
                changes[frame][int(key)] = tuple(bytes.fromhex(value[1:]))
        return cls(face, side, changes)

    def render(self, pose, phase=0, side="left", accent=None):
        pixels = list(faces.render(self.face, pose, phase=phase, side=side, accent=accent))
        frame = "talk" + str(phase % 2) if pose == "talk" else pose
        for index, rgb in self.changes.get(frame, {}).items():
            if frame == "look" and side == "right":
                row, col = divmod(index, 11)
                index = row * 11 + 10 - col
            pixels[index] = rgb
        return pixels
