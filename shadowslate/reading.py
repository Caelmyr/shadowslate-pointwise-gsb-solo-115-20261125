"""Measure dark coverage and a real source row in a bounded rectangle.

两种判断方式共用同一份原片像素：

* ``threshold``：原全框亮度阈，框内亮度不超过 cutoff 即记为暗区。
* ``reference``：按同列纸色参照。框最上、最下各 ``STRIP_ROWS`` 行是没有被
  挡光的参照纸条；每一列只用本列上下条的原片像素，两条各自至少有
  ``MIN_STRIP_PIXELS`` 个 RGB 三通道都落在 1–254 的实拍像素时该列才可用。
  可用列在测量点高度上用上下纸条亮度线性插值出当地纸色，原片亮度除以当地
  纸色低于填写比例才记为阴影；任一条不足的列，其中的测量点一律标待核，
  不借用旁列。
"""

from io import BytesIO
import math

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps, UnidentifiedImageError

from image_lab.validation import Invalid, choice, integer, number


LUMA = np.array([0.2126, 0.7152, 0.0722])
MAX_IMAGE_BYTES = 18 * 1024 * 1024

# 参照纸条：测量框最上、最下各 8 行；每条至少 4 个实拍像素才可用。
STRIP_ROWS = 8
MIN_STRIP_PIXELS = 4
METHODS = ("threshold", "reference")


def png(image):
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def decode(data):
    if len(data) > MAX_IMAGE_BYTES:
        raise Invalid("单张原片不得超过18 MiB")
    try:
        with Image.open(BytesIO(data)) as photo:
            if photo.format not in {"PNG", "JPEG", "TIFF", "BMP", "WEBP"} or getattr(photo, "n_frames", 1) != 1:
                raise Invalid("请选择单帧 PNG、JPEG、TIFF、BMP 或 WebP")
            if min(photo.size) < 32 or max(photo.size) > 2600 or math.prod(photo.size) > 4_000_000:
                raise Invalid("原片边长须在32–2600 px内，总像素不超过400万")
            normal = ImageOps.exif_transpose(photo).convert("RGB")
            return normal.size, png(normal)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        raise Invalid("照片无法解码") from error


def pixels(path):
    with Image.open(path) as photo:
        return np.asarray(photo.convert("RGB"))


def settings(value, size):
    w, h = size
    left = integer(value.get("left"), "左界 X", 0, w-1)
    top = integer(value.get("top"), "上界 Y", 0, h-1)
    config = {"left": left, "right": integer(value.get("right"), "右界 X", left, w-1),
              "top": top, "bottom": integer(value.get("bottom"), "下界 Y", top, h-1),
              "row": integer(value.get("row"), "读数行 Y", 0, h-1),
              "cutoff": integer(value.get("cutoff"), "暗区亮度上限", 1, 254),
              "method": choice(value.get("method", "threshold"), "判断方式", METHODS),
              "ratio": number(value.get("ratio", 0.85), "阴影比例", 0.01, 0.99),
              "mm_per_px": number(value.get("mm_per_px"), "标尺", 0.001, 500)}
    if config["method"] == "reference" and config["bottom"] - config["top"] + 1 < 2 * STRIP_ROWS + 1:
        raise Invalid(f"同列纸色参照要求测量框高度至少 {2 * STRIP_ROWS + 1} 行，"
                      "以容纳上下各 {0} 行参照纸条和中间测量带".format(STRIP_ROWS))
    return config


def defaults(size):
    w, h = size
    return settings({"left": w//6, "right": w*5//6, "top": h//5, "bottom": h*4//5,
                     "row": h//2, "cutoff": 155, "method": "reference", "ratio": 0.85,
                     "mm_per_px": 1}, size)


def _strip_reference(strip):
    """一条参照纸条的纸色：仅用三通道都在 1–254 的实拍像素，取亮度中位数。

    中位数让参照条里的光亮变化（局部高光、印刷深浅跳动）不会整体抬高或压低
    该列参考值，避免把参考条的光亮变化变成整排假影。像素不足返回 None。
    """
    usable = np.all((strip >= 1) & (strip <= 254), axis=1)
    if int(np.count_nonzero(usable)) < MIN_STRIP_PIXELS:
        return None
    levels = strip[usable] @ LUMA
    return float(np.median(levels))


def reference_columns(image, config):
    """逐列计算同列纸色参照，绝不借用旁列。

    返回 dict：每列带 top/bottom 参照亮度、是否可用；以及上下条中心行号和
    插值分母。任何一列只用自己上下条的原片像素。
    """
    left, right, top, bottom = (config[k] for k in ("left", "right", "top", "bottom"))
    upper = image[top:top + STRIP_ROWS, left:right + 1]
    lower = image[bottom - STRIP_ROWS + 1:bottom + 1, left:right + 1]
    top_center = top + (STRIP_ROWS - 1) / 2
    bottom_center = bottom - (STRIP_ROWS - 1) / 2
    span = max(bottom_center - top_center, 1e-9)
    columns = {}
    for offset in range(right - left + 1):
        x = left + offset
        top_ref = _strip_reference(upper[:, offset])
        bottom_ref = _strip_reference(lower[:, offset])
        usable = top_ref is not None and bottom_ref is not None
        columns[x] = {"top_ref": top_ref, "bottom_ref": bottom_ref, "usable": usable}
    return {"columns": columns, "top_center": top_center,
            "bottom_center": bottom_center, "span": span}


def local_reference(reference, x, y):
    """测量点 (x, y) 高度上的当地纸色：上下条亮度按距离线性插值。

    距离权重兼顾靠近测量点的纸色与上下纸色变化的连贯；权重截断在两条中心
    之间，不向外推断，避免边缘行被外推放大。
    """
    column = reference["columns"][x]
    if not column["usable"]:
        return None
    weight = (y - reference["top_center"]) / reference["span"]
    weight = float(min(1.0, max(0.0, weight)))
    value = column["top_ref"] * (1 - weight) + column["bottom_ref"] * weight
    return value if value > 0 else None


def point(image, x, y, config, reference=None):
    rgb = image[y, x]
    light = float(rgb @ LUMA)
    in_bounds = config["left"] <= x <= config["right"] and config["top"] <= y <= config["bottom"]
    result = {"x": x, "y": y, "rgb": [int(value) for value in rgb],
              "brightness": light, "dark": None, "pending": False,
              "reference": None, "ratio_value": None, "status": "框外"}
    if not in_bounds:
        return result
    if config["method"] == "threshold":
        dark = bool(light <= config["cutoff"])
        result.update(dark=dark, status="暗区" if dark else "亮区")
        return result
    if reference is None:
        reference = reference_columns(image, config)
    ref = local_reference(reference, x, y)
    if ref is None:
        result.update(dark=False, pending=True, status="待核")
        return result
    ratio_value = light / ref
    dark = bool(ratio_value < config["ratio"])
    result.update(dark=dark, ratio_value=ratio_value, reference=ref,
                  status="阴影" if dark else "纸面")
    return result


def profile(image, config, reference=None):
    y = config["row"]
    if not config["top"] <= y <= config["bottom"]:
        return []
    left, right = config["left"], config["right"]
    row = image[y, left:right + 1]
    values = row @ LUMA
    if config["method"] == "threshold":
        return [{"x": x, "y": y, "brightness": float(value),
                 "dark": bool(value <= config["cutoff"]), "pending": False,
                 "reference": None, "ratio_value": None,
                 "status": "暗区" if value <= config["cutoff"] else "亮区"}
                for x, value in zip(range(left, right + 1), values)]
    if reference is None:
        reference = reference_columns(image, config)
    points = []
    for x, value in zip(range(left, right + 1), values):
        ref = local_reference(reference, x, y)
        if ref is None:
            points.append({"x": x, "y": y, "brightness": float(value), "dark": False,
                           "pending": True, "reference": None, "ratio_value": None,
                           "status": "待核"})
            continue
        ratio_value = float(value) / ref
        dark = bool(ratio_value < config["ratio"])
        points.append({"x": x, "y": y, "brightness": float(value), "dark": dark,
                       "pending": False, "reference": ref, "ratio_value": ratio_value,
                       "status": "阴影" if dark else "纸面"})
    return points


def frame_masks(image, config, reference=None):
    """整框逐点判定，返回与框同形的 (阴影掩码, 待核掩码)。

    截线、点读、覆盖比例和 PNG 校样都经由同一套同列参照规则，这里给出框内
    每个像素的结论，面积统计与校样着色不会各算各的。
    """
    left, right, top, bottom = (config[k] for k in ("left", "right", "top", "bottom"))
    frame = image[top:bottom + 1, left:right + 1]
    luma = frame @ LUMA
    if config["method"] == "threshold":
        return luma <= config["cutoff"], np.zeros(luma.shape, dtype=bool)
    if reference is None:
        reference = reference_columns(image, config)
    columns = reference["columns"]
    xs = np.arange(left, right + 1)
    usable = np.array([columns[x]["usable"] for x in xs])
    top_refs = np.array([columns[x]["top_ref"] if columns[x]["usable"] else np.nan for x in xs])
    bottom_refs = np.array([columns[x]["bottom_ref"] if columns[x]["usable"] else np.nan for x in xs])
    ys = np.arange(top, bottom + 1)
    weights = np.clip((ys - reference["top_center"]) / reference["span"], 0.0, 1.0)[:, None]
    refs = top_refs[None, :] * (1 - weights) + bottom_refs[None, :] * weights
    with np.errstate(invalid="ignore", divide="ignore"):
        ratios = luma / refs
    dark = usable[None, :] & (ratios < config["ratio"])
    pending = np.broadcast_to(~usable[None, :], luma.shape).copy()
    return dark, pending


def coverage(image, config, reference=None):
    left, right, top, bottom = (config[k] for k in ("left", "right", "top", "bottom"))
    frame = image[top:bottom + 1, left:right + 1]
    area = int(frame.shape[0] * frame.shape[1])
    dark_mask, pending_mask = frame_masks(image, config, reference)
    dark = int(np.count_nonzero(dark_mask))
    pending = int(np.count_nonzero(pending_mask))
    if config["method"] == "threshold":
        return {"dark_px": dark, "total_px": area, "pending_px": 0,
                "evidence_px": area, "percentage": dark * 100 / area,
                "area_mm2": dark * config["mm_per_px"] ** 2, "method": "threshold"}
    evidence = area - pending
    return {"dark_px": dark, "total_px": area, "pending_px": pending,
            "evidence_px": evidence,
            "percentage": dark * 100 / evidence if evidence else 0.0,
            "area_mm2": dark * config["mm_per_px"] ** 2, "method": "reference"}


def report(image, config):
    reference = reference_columns(image, config) if config["method"] == "reference" else None
    values = profile(image, config, reference)
    ranges = []
    for p in values:
        if not p["dark"]:
            continue
        if ranges and p["x"] == ranges[-1][1] + 1:
            ranges[-1][1] = p["x"]
        else:
            ranges.append([p["x"], p["x"]])
    return {"reading": coverage(image, config, reference), "row": config["row"],
            "profile": values, "ranges": ranges,
            "reference": None if reference is None else {
                "strip_rows": STRIP_ROWS,
                "min_strip_pixels": MIN_STRIP_PIXELS,
                "top": [config["top"], config["top"] + STRIP_ROWS - 1],
                "bottom": [config["bottom"] - STRIP_ROWS + 1, config["bottom"]],
                "columns": reference["columns"],
                "top_center": reference["top_center"],
                "bottom_center": reference["bottom_center"]}}


def practice():
    w, h = 750, 462
    cfg = settings({"left": 250, "right": 658, "top": 138, "bottom": 359,
                    "row": 264, "cutoff": 155, "method": "reference", "ratio": .85,
                    "mm_per_px": .55}, (w, h))
    yy, xx = np.indices((h, w))
    # 深浅竖纹纸面：竖纹随列缓慢变化，全框亮度阈会把深纹误当阴影，
    # 同列纸色参照则以本列上下纸条为基准，只认真正被挡光的区域。
    stripes = 13 * np.sin(xx * 0.11) + 5 * np.sin(xx * 0.037 + 1.3)
    surface = 221 - 15*xx/w - 16*yy/h + stripes + 1.9*np.sin(xx*.57+yy*.2) \
        + 1.25*np.cos(xx*.43-yy*.32)
    shadow = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(shadow)
    draw.polygon([(210, 299), (271, 260), (492, 194), (609, 228), (585, 281), (309, 330)], fill=240)
    sharp = np.asarray(shadow.filter(ImageFilter.GaussianBlur(2.5)))/255
    soft = np.asarray(shadow.filter(ImageFilter.GaussianBlur(14)))/255
    t = np.clip((xx-222)/401, 0, 1)
    atten = (sharp*(1-t)+soft*t)
    base = np.stack([surface+d-atten*k for d, k in [(10, 92), (4, 83), (-10, 71)]], axis=-1)
    image = Image.fromarray(base.clip(0, 255).astype("uint8"), "RGB")
    draw = ImageDraw.Draw(image)
    draw.polygon([(132, 193), (172, 167), (248, 190), (209, 217)], fill="#b87056")
    draw.polygon([(132, 193), (209, 217), (209, 303), (132, 280)], fill="#a4614f")
    draw.polygon([(209, 217), (248, 190), (248, 273), (209, 303)], fill="#704944")
    draw.line([(132, 193), (209, 217), (248, 190)], fill="#d99270", width=2)
    draw.text((29, 56), "TABLETOP SHADOW / A", fill="#564b46", font=ImageFont.load_default(size=17))
    draw.text((29, 386), "SIMULATED LIGHTING STUDY", fill="#665b50", font=ImageFont.load_default(size=11))
    draw.line((39, 416, 139, 416), fill="#65564b", width=2)
    for x in range(39, 140, 10):
        draw.line((x, 416, x, 407 if (x-39) % 50 else 402), fill="#65564b", width=2)
    draw.text((153, 405), "55 mm", fill="#66574b", font=ImageFont.load_default(size=12))
    return image.size, png(image), cfg
