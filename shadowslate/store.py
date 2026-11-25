"""Keep an immutable photo with independently editable shadow measurement settings."""

import json
from pathlib import Path
from threading import RLock
from uuid import uuid4

from image_lab.validation import Conflict, Invalid, identifier, integer, object_value, text

from . import reading


class Cards:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = RLock()

    def folder(self, key):
        folder = self.root / identifier(key)
        if not (folder / "card.json").is_file():
            raise Invalid("没有找到这份投影卡")
        return folder

    def read(self, key, revision=None):
        with self.lock:
            card = json.loads((self.folder(key) / "card.json").read_text())
            if revision is not None and integer(revision, "工作版本", 1) != card["revision"]:
                raise Conflict("投影卡已经改动，请重新打开")
            return card

    @staticmethod
    def commit(folder, card):
        stage = folder / (uuid4().hex + ".tmp")
        stage.write_text(json.dumps(card, ensure_ascii=False, allow_nan=False))
        stage.replace(folder / "card.json")

    def list(self):
        with self.lock:
            rows = []
            for path in self.root.glob("*/card.json"):
                card = json.loads(path.read_text())
                rows.append({**{key: card[key] for key in ("id", "name", "synthetic", "revision")},
                             "modified_ns": path.stat().st_mtime_ns})
            return sorted(rows, key=lambda row: (-row["modified_ns"], row["id"]))

    def create(self, name, title, size, data, config, synthetic=False):
        name = text(name, "投影卡名称", 80, False)
        title = text(title, "原片名称", 140, False)
        with self.lock:
            if len(self.list()) >= 150:
                raise Invalid("投影卡库已达150份")
            key = uuid4().hex
            folder = self.root / key
            folder.mkdir()
            (folder / "capture.png").write_bytes(data)
            card = {"id": key, "revision": 1, "name": name, "photo_name": title, "synthetic": synthetic,
                    "width": size[0], "height": size[1], "settings": config}
            self.commit(folder, card)
            return card

    def demo(self):
        size, image, config = reading.practice()
        return self.create("练习 · 桌面投影", "合成桌面.png", size, image, config, synthetic=True)

    def demo_pair(self):
        from . import pairing

        (size, image_a, cfg_a), (_, image_b, cfg_b) = pairing.practice_pair()
        first = self.create("练习 · 双灯 A", "合成桌面-A.png", size, image_a, cfg_a, synthetic=True)
        second = self.create("练习 · 双灯 B", "合成桌面-B.png", size, image_b, cfg_b, synthetic=True)
        return first, second

    def update(self, key, value):
        with self.lock:
            card = self.read(key, integer(value.get("revision"), "工作版本", 1))
            incoming = object_value(value.get("settings", {}), "投影设置")
            config = reading.settings({**card["settings"], **incoming}, (card["width"], card["height"]))
            card.update(name=text(value.get("name", card["name"]), "投影卡名称", 80, False),
                        settings=config, revision=card["revision"] + 1)
            self.commit(self.folder(key), card)
            return card

    def photo(self, key):
        return self.folder(key) / "capture.png"
