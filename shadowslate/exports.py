"""Export the original shadow evidence and current measured row."""

from io import BytesIO, StringIO
from zipfile import ZIP_DEFLATED, ZipFile
import csv
import json

import numpy as np
from PIL import Image, ImageDraw

from .reading import STRIP_ROWS, frame_masks, png


def trace_table(result):
    buffer = StringIO()
    writer = csv.writer(buffer)
    if result["reading"]["method"] == "reference":
        writer.writerow(("x_px", "y_px", "luma", "reference_luma", "ratio", "status"))
        for p in result["profile"]:
            writer.writerow((p["x"], p["y"], f'{p["brightness"]:.3f}',
                             "" if p["reference"] is None else f'{p["reference"]:.3f}',
                             "" if p["ratio_value"] is None else f'{p["ratio_value"]:.4f}',
                             p["status"]))
    else:
        writer.writerow(("x_px", "y_px", "luma", "dark", "status"))
        for p in result["profile"]:
            writer.writerow((p["x"], p["y"], f'{p["brightness"]:.3f}',
                             int(p["dark"]), p["status"]))
    return buffer.getvalue().encode()


def _reference_layer(image, card, result):
    """校样叠层：阴影半透明紫、待核列琥珀色，参照纸条画青色边界。"""
    c = card["settings"]
    overlay = Image.new("RGBA", (image.shape[1], image.shape[0]), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    dark_mask, pending_mask = frame_masks(image, c, _rebuild_reference(result))
    layer = np.zeros((image.shape[0], image.shape[1], 4), dtype=np.uint8)
    layer[pending_mask.nonzero()[0] + c["top"], pending_mask.nonzero()[1] + c["left"]] = (223, 158, 74, 90)
    layer[dark_mask.nonzero()[0] + c["top"], dark_mask.nonzero()[1] + c["left"]] = (121, 60, 116, 110)
    overlay = Image.alpha_composite(overlay, Image.fromarray(layer, "RGBA"))
    draw = ImageDraw.Draw(overlay)
    draw.rectangle((c["left"], c["top"], c["right"], c["top"] + STRIP_ROWS - 1),
                   outline=(74, 168, 178, 255), width=1)
    draw.rectangle((c["left"], c["bottom"] - STRIP_ROWS + 1, c["right"], c["bottom"]),
                   outline=(74, 168, 178, 255), width=1)
    return overlay


def _rebuild_reference(result):
    info = result.get("reference")
    if info is None:
        return None
    return {"columns": info["columns"], "top_center": info["top_center"],
            "bottom_center": info["bottom_center"],
            "span": info["bottom_center"] - info["top_center"]}


def proof(image, card, result):
    h, w = image.shape[:2]
    base = Image.fromarray(image).convert("RGBA")
    c = card["settings"]
    if c["method"] == "reference":
        base = Image.alpha_composite(base, _reference_layer(image, card, result))
    output = Image.new("RGB", (max(420, w), h + 88), "#2f3140")
    output.paste(base.convert("RGB"), (0, 0))
    draw = ImageDraw.Draw(output)
    draw.rectangle((c["left"], c["top"], c["right"], c["bottom"]), outline="#8e5f94", width=2)
    if result["profile"]:
        draw.line((c["left"], c["row"], c["right"], c["row"]), fill="#d7a25f", width=2)
    for a, b in result["ranges"]:
        draw.line((a, c["row"], b, c["row"]), fill="#89537a", width=3)
    r = result["reading"]
    draw.text((14, h + 12), f'Shadow coverage {r["percentage"]:.2f}% / {r["area_mm2"]:.2f} mm2',
              fill="#eee7f6")
    if r["method"] == "reference":
        draw.text((14, h + 32),
                  f'Per-column paper reference, ratio < {c["ratio"]:.2f}, '
                  f'pending {r["pending_px"]} px / evidence {r["evidence_px"]} px',
                  fill="#e9c492")
        draw.text((14, h + 52), f'Source row {c["row"]}, strips {STRIP_ROWS} rows each (cyan)',
                  fill="#9fd4da")
    else:
        draw.text((14, h + 34), f'Source row {c["row"]}, full-frame threshold {c["cutoff"]}',
                  fill="#e9c492")
    return png(output)


def package(card, original, image, result):
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as stream:
        stream.writestr("tabletop.png", original)
        stream.writestr("reading.json", json.dumps({"card": card, "coverage": result["reading"],
                                                    "ranges": result["ranges"]},
                                                   ensure_ascii=False, indent=2))
        stream.writestr("shadow-profile.csv", trace_table(result))
        stream.writestr("shadow-proof.png", proof(image, card, result))
    return buffer.getvalue()
