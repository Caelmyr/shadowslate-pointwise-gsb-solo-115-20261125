"""Pair two tabletop cards: integer horizontal registration and common dark pixels.

The two photographs share the same height and the same measurement-frame top /
bottom rows.  The only allowed motion is an integer horizontal shift of the
comparison card, limited to ``±MAX_SHIFT`` original pixels.  Registration uses
the real paper texture inside the top and bottom eight frame rows; nothing
outside either photograph is fabricated, so only pixels that really exist in
both frames take part in every measurement.
"""

import numpy as np
from PIL import Image, ImageDraw

from image_lab.validation import Invalid

from .reading import LUMA, png

MAX_SHIFT = 12
BAND_ROWS = 8
# A reference band must carry real paper texture.  Registration follows the
# horizontal structure, so the mean absolute horizontal gradient is the signal;
# the overall SD guards against featureless flat fields.
GRADIENT_FLOOR = 0.45
TEXTURE_FLOOR = 1.5
# Scores within this relative band of the best score count as "equally likely"
# (a small absolute floor keeps sensor noise from manufacturing ties).
TIE_REL = 0.12
TIE_ABS = 0.5


def luma(image):
    return image @ LUMA


def _frame(config):
    return (config["left"], config["right"], config["top"], config["bottom"])


def prepare(image_a, config_a, image_b, config_b):
    """Validate geometry and return luma arrays plus shared frame geometry."""
    if image_a.shape[:2] != image_b.shape[:2]:
        raise Invalid("两张原片高度（与尺寸）不一致，无法只靠横向错位对照")
    la, ra, ta, ba = _frame(config_a)
    lb, rb, tb, bb = _frame(config_b)
    if (ta, ba) != (tb, bb):
        raise Invalid("两张投影卡测量框的上界/下界行不一致，不能建立双卡对照")
    if ba - ta + 1 < BAND_ROWS * 2:
        raise Invalid(f"测量框至少需要 {BAND_ROWS * 2} 行才能保留上下参照条")
    return luma(image_a.astype(np.float64)), luma(image_b.astype(np.float64)), \
        (la, ra, ta, ba), (lb, rb, tb, bb)


def _band(levels, top, bottom, left, right, kind):
    rows = slice(top, top + BAND_ROWS) if kind == "top" else slice(bottom - BAND_ROWS + 1, bottom + 1)
    return levels[rows, left:right + 1]


def _band_score(band_a, band_b, shift):
    """Mean absolute luma difference for one band at the given shift.

    Returns ``None`` when the overlap carries no real paired pixels.
    """
    if shift >= 0:  # B moved right: B column x_b = x_a + shift
        a = band_a[:, shift:]
        b = band_b[:, :band_b.shape[1] - shift]
    else:
        a = band_a[:, :shift]
        b = band_b[:, -shift:]
    if a.size == 0:
        return None
    return float(np.mean(np.abs(a - b)))


def find_shift(levels_a, levels_b, frame_a, frame_b):
    """Score every allowed integer shift on both real-texture reference bands."""
    la, ra, ta, ba = frame_a
    lb, rb, tb, bb = frame_b
    top_a, bottom_a = _band(levels_a, ta, ba, la, ra, "top"), _band(levels_a, ta, ba, la, ra, "bottom")
    top_b, bottom_b = _band(levels_b, tb, bb, lb, rb, "top"), _band(levels_b, tb, bb, lb, rb, "bottom")

    bands = {}
    for name, band in (("top", top_a), ("bottom", bottom_a)):
        gradient = float(np.mean(np.abs(np.diff(band, axis=1)))) if band.shape[1] > 1 else 0.0
        sd = float(band.std(ddof=0))
        supported = gradient >= GRADIENT_FLOOR and sd >= TEXTURE_FLOOR
        bands[name] = {"texture_sd": round(sd, 3), "horizontal_gradient": round(gradient, 4),
                       "supported": bool(supported), "rows": BAND_ROWS}
    if not bands["top"]["supported"] or not bands["bottom"]["supported"]:
        raise Invalid("上下参照条缺少足够的真实纸面纹理，不能可靠横移配准")

    candidates = []
    for shift in range(-MAX_SHIFT, MAX_SHIFT + 1):
        top_score = _band_score(top_a, top_b, shift)
        bottom_score = _band_score(bottom_a, bottom_b, shift)
        if top_score is None or bottom_score is None:
            continue
        candidates.append({"shift": shift, "top_sad": round(top_score, 4),
                           "bottom_sad": round(bottom_score, 4),
                           "score": round((top_score + bottom_score) / 2, 4)})
    if not candidates:
        raise Invalid("允许的横移范围内没有任何两张都存在的真实像素")

    best = min(candidates, key=lambda item: item["score"])
    limit = max(best["score"] * (1 + TIE_REL), best["score"] + TIE_ABS)
    alike = [item for item in candidates if item["score"] <= limit]
    chosen = min(alike, key=lambda item: (abs(item["shift"]), -np.sign(item["shift"])))
    candidates.sort(key=lambda item: (item["score"], abs(item["shift"]), -np.sign(item["shift"])))
    return {"shift": chosen["shift"], "best_shift": best["shift"],
            "tie_window": round(limit, 4), "bands": bands,
            "candidates": candidates,
            "chosen": next(item for item in candidates if item["shift"] == chosen["shift"])}


def overlap(frame_a, frame_b, shift):
    """Region (in card-A coordinates) where both frames hold real pixels."""
    la, ra, ta, ba = frame_a
    lb, rb, _, _ = frame_b
    x0 = max(la, lb - shift)   # x_a such that x_b = x_a + shift stays inside B
    x1 = min(ra, rb - shift)
    if x0 > x1:
        raise Invalid("该横移位置下两张测量框没有真实像素重合区")
    return x0, x1, ta + BAND_ROWS, ba - BAND_ROWS


def common_dark(levels_a, levels_b, frame_a, frame_b, cutoff_a, cutoff_b, shift):
    """Pixels dark under each card's own threshold, between the reference bands."""
    x0, x1, y0, y1 = overlap(frame_a, frame_b, shift)
    dark_a = levels_a[y0:y1 + 1, x0:x1 + 1] <= cutoff_a
    dark_b = levels_b[y0:y1 + 1, x0 + shift:x1 + shift + 1] <= cutoff_b
    mask = dark_a & dark_b
    ranges = []
    for row_index, row in enumerate(mask):
        y = y0 + row_index
        run = None
        for column, value in enumerate(row):
            if value and run is None:
                run = [x0 + column, x0 + column]
            elif value:
                run[1] = x0 + column
            elif run is not None:
                ranges.append([y, run[0], run[1]])
                run = None
        if run is not None:
            ranges.append([y, run[0], run[1]])
    total = int(mask.size)
    dark_px = int(np.count_nonzero(mask))
    return {"shift": shift, "overlap": {"x0": int(x0), "x1": int(x1), "y0": int(y0), "y1": int(y1)},
            "ranges": ranges, "dark_px": dark_px, "total_px": total,
            "percentage": dark_px * 100 / total}


def analyze(image_a, config_a, image_b, config_b):
    """Full registration + common-dark analysis for two stored cards."""
    levels_a, levels_b, frame_a, frame_b = prepare(image_a, config_a, image_b, config_b)
    registration = find_shift(levels_a, levels_b, frame_a, frame_b)
    shift = registration["shift"]
    dark = common_dark(levels_a, levels_b, frame_a, frame_b,
                       config_a["cutoff"], config_b["cutoff"], shift)
    return {"registration": registration, "common": dark,
            "frame_a": {"left": frame_a[0], "right": frame_a[1], "top": frame_a[2], "bottom": frame_a[3]},
            "frame_b": {"left": frame_b[0], "right": frame_b[1], "top": frame_b[2], "bottom": frame_b[3]}}


def paired_point(image_a, config_a, image_b, config_b, shift, x, y):
    """Read one source pixel and its real counterpart in the other photograph."""
    h, w = image_a.shape[:2]
    rgb_a = image_a[y, x]
    light_a = float(rgb_a @ LUMA)
    inside_a = config_a["left"] <= x <= config_a["right"] and config_a["top"] <= y <= config_a["bottom"]
    here = {"x": int(x), "y": int(y), "rgb": [int(v) for v in rgb_a], "brightness": round(light_a, 3),
            "dark": bool(light_a <= config_a["cutoff"]) if inside_a else None}
    xb, yb = x + shift, y
    if not (0 <= xb < w and 0 <= yb < h):
        return {"here": here, "peer": None, "pending": True,
                "reason": "对应位置落在另一张原片之外，留待核"}
    rgb_b = image_b[yb, xb]
    light_b = float(rgb_b @ LUMA)
    inside_b = config_b["left"] <= xb <= config_b["right"] and config_b["top"] <= yb <= config_b["bottom"]
    peer = {"x": int(xb), "y": int(yb), "rgb": [int(v) for v in rgb_b], "brightness": round(light_b, 3),
            "dark": bool(light_b <= config_b["cutoff"]) if inside_b else None}
    return {"here": here, "peer": peer,
            "pending": not (inside_a and inside_b),
            "reason": None if inside_a and inside_b else "对应点不在双方测量框内，暗亮留待核",
            "common_dark": bool(inside_a and inside_b and here["dark"] and peer["dark"])}


def proof(image_a, image_b, card_a, card_b, result):
    """PNG proof marking one common-dark region on both photographs."""
    h, w = image_a.shape[:2]
    head, gap = 78, 14
    output = Image.new("RGB", (w, h * 2 + head + gap), "#2f3140")
    output.paste(Image.fromarray(image_a), (0, head))
    output.paste(Image.fromarray(image_b), (0, head + h + gap))
    draw = ImageDraw.Draw(output)
    shift = result["registration"]["shift"]
    common = result["common"]
    x0, x1 = common["overlap"]["x0"], common["overlap"]["x1"]
    y0, y1 = common["overlap"]["y0"], common["overlap"]["y1"]
    fa, fb = card_a["settings"], card_b["settings"]

    def decorate(offset_y, cfg, mask_shift, title):
        draw.rectangle((cfg["left"], cfg["top"] + offset_y, cfg["right"], cfg["bottom"] + offset_y),
                       outline="#8e5f94", width=2)
        for top in (cfg["top"], cfg["bottom"] - BAND_ROWS + 1):
            draw.rectangle((cfg["left"], top + offset_y, cfg["right"], top + BAND_ROWS - 1 + offset_y),
                           outline="#d7a25f", width=1)
        for y, xa0, xa1 in common["ranges"]:
            xb0, xb1 = xa0 + mask_shift, xa1 + mask_shift
            draw.rectangle((xb0, y + offset_y, xb1, y + offset_y), fill=(216, 91, 62, 180))
        draw.text((12, offset_y + 6), title, fill="#eee7f6")

    decorate(head, fa, 0, "PHOTO A (current card) · cutoff " + str(fa["cutoff"]))
    decorate(head + h + gap, fb, shift,
             "PHOTO B (comparison card) · cutoff " + str(fb["cutoff"]) + " · shift "
             + f"{shift:+d}" + " px")

    reg = result["registration"]["chosen"]
    draw.text((12, 10),
              f'COMMON DARK {common["dark_px"]} px / {common["percentage"]:.2f}% '
              f'(overlap {common["total_px"]} px, X {x0}-{x1}, Y {y0}-{y1})', fill="#f3d9c8")
    draw.text((12, 30),
              f'shift {shift:+d} px (allowed +/- {MAX_SHIFT}) · top SAD {reg["top_sad"]} · '
              f'bottom SAD {reg["bottom_sad"]} · mean {reg["score"]}', fill="#e9c492")
    draw.text((12, 50), "red = dark in BOTH photos (own cutoffs) · amber = texture reference bands · purple = frame",
              fill="#cdb9d2")
    return png(output)


def practice_pair():
    """Two synthetic photographs of one desktop: same texture, small x-shift, different lamps."""
    from PIL import ImageDraw, ImageFilter, ImageFont

    rng = np.random.default_rng(11503)
    w, h = 750, 462
    cfg = {"left": 250, "right": 658, "top": 138, "bottom": 359,
           "row": 264, "cutoff": 155, "mm_per_px": .55}
    yy, xx = np.indices((h, w))
    # Real paper texture: fine deterministic fibres plus broad tonal drift.
    fibre = (2.6 * np.sin(xx * 1.71 + yy * 0.41)
             + 2.1 * np.cos(xx * 0.93 - yy * 0.67)
             + 1.5 * np.sin(xx * 2.37 + yy * 1.23)
             + rng.normal(0, 1.1, (h, w)))
    surface = 221 - 15 * xx / w - 16 * yy / h + fibre

    def render(relight, edge):
        shadow = Image.new("L", (w, h), 0)
        draw = ImageDraw.Draw(shadow)
        draw.polygon([(210, 299), (271, 260), (492, 194), (609, 228), (585, 281), (309, 330)], fill=240)
        sharp = np.asarray(shadow.filter(ImageFilter.GaussianBlur(2.5))) / 255
        soft = np.asarray(shadow.filter(ImageFilter.GaussianBlur(14))) / 255
        t = np.clip((xx - 222) / 401, 0, 1)
        atten = sharp * (1 - t) + soft * t
        base = np.stack([surface + d - atten * k for d, k in [(10, 92), (4, 83), (-10, 71)]], axis=-1)
        base += relight[..., None]
        image = Image.fromarray(base.clip(0, 255).astype("uint8"), "RGB")
        draw = ImageDraw.Draw(image)
        draw.polygon([(132, 193), (172, 167), (248, 190), (209, 217)], fill="#b87056")
        draw.polygon([(132, 193), (209, 217), (209, 303), (132, 280)], fill="#a4614f")
        draw.polygon([(209, 217), (248, 190), (248, 273), (209, 303)], fill="#704944")
        draw.line([(132, 193), (209, 217), (248, 190)], fill="#d99270", width=2)
        draw.line((39, 416, 139, 416), fill="#65564b", width=2)
        for x in range(39, 140, 10):
            draw.line((x, 416, x, 407 if (x - 39) % 50 else 402), fill="#65564b", width=2)
        draw.text((29, 56), edge, fill="#564b46", font=ImageFont.load_default(size=17))
        return np.asarray(image)

    true_shift = 5
    image_a = render(np.zeros((h, w)), "TABLETOP SHADOW / A")
    # B sees the same desktop displaced by +5 px; the leading columns have no
    # real counterpart and must never be padded.
    image_b = np.empty_like(image_a)
    image_b[:, true_shift:] = image_a[:, :w - true_shift]
    image_b[:, :true_shift] = 0
    relight = 28 * (1 - xx / w) - 14 * (yy / h)
    image_b = np.clip(image_b.astype(np.int16) + relight[..., None].astype(np.int16), 0, 255).astype("uint8")
    return ((w, h), png(Image.fromarray(image_a)), dict(cfg)), \
           ((w, h), png(Image.fromarray(image_b)), dict(cfg))
