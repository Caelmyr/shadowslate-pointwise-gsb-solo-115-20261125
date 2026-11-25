"""Measure dark coverage and a real source row in a bounded rectangle."""

from io import BytesIO
import math

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps, UnidentifiedImageError

from image_lab.validation import Invalid, integer, number


LUMA = np.array([0.2126, 0.7152, 0.0722])
MAX_IMAGE_BYTES = 18 * 1024 * 1024


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
    return {"left": left, "right":integer(value.get("right"),"右界 X",left,w-1),
            "top":top, "bottom":integer(value.get("bottom"),"下界 Y",top,h-1),
            "row":integer(value.get("row"),"读数行 Y",0,h-1),
            "cutoff":integer(value.get("cutoff"),"暗区亮度上限",1,254),
            "mm_per_px":number(value.get("mm_per_px"),"标尺",0.001,500)}


def defaults(size):
    w,h = size
    return settings({"left":w//6,"right":w*5//6,"top":h//5,"bottom":h*4//5,
                     "row":h//2,"cutoff":155,"mm_per_px":1},size)


def point(image, x, y, config):
    rgb = image[y,x]
    light = float(rgb @ LUMA)
    in_bounds = config["left"] <= x <= config["right"] and config["top"] <= y <= config["bottom"]
    return {"x":x, "y":y, "rgb":[int(value) for value in rgb],
            "brightness":light,"dark":bool(light <= config["cutoff"]) if in_bounds else None}


def profile(image, config):
    y = config["row"]
    if not config["top"] <= y <= config["bottom"]:
        return []
    left,right = config["left"],config["right"]
    values = image[y,left:right+1] @ LUMA
    return [{"x":x,"y":y,"brightness":float(value),"dark":bool(value <= config["cutoff"])}
            for x,value in zip(range(left,right+1),values)]


def coverage(image, config):
    left,right,top,bottom = (config[k] for k in ("left","right","top","bottom"))
    levels = image[top:bottom+1,left:right+1] @ LUMA
    dark = int(np.count_nonzero(levels <= config["cutoff"]))
    area = int(levels.size)
    return {"dark_px":dark,"total_px":area,"percentage":dark*100/area,
            "area_mm2":dark * config["mm_per_px"]**2}


def report(image, config):
    values = profile(image,config)
    ranges = []
    for p in values:
        if not p["dark"]:
            continue
        if ranges and p["x"] == ranges[-1][1]+1:
            ranges[-1][1] = p["x"]
        else:
            ranges.append([p["x"],p["x"]])
    return {"reading":coverage(image,config),"row":config["row"],"profile":values,"ranges":ranges}


def practice():
    w,h = 750,462
    cfg = settings({"left":250,"right":658,"top":138,"bottom":359,
                    "row":264,"cutoff":155,"mm_per_px":.55},(w,h))
    yy,xx = np.indices((h,w))
    surface = 221-15*xx/w-16*yy/h + 1.9*np.sin(xx*.57+yy*.2)+1.25*np.cos(xx*.43-yy*.32)
    shadow = Image.new("L",(w,h),0)
    draw = ImageDraw.Draw(shadow)
    draw.polygon([(210,299),(271,260),(492,194),(609,228),(585,281),(309,330)],fill=240)
    sharp = np.asarray(shadow.filter(ImageFilter.GaussianBlur(2.5)))/255
    soft = np.asarray(shadow.filter(ImageFilter.GaussianBlur(14)))/255
    t = np.clip((xx-222)/401,0,1)
    atten = (sharp*(1-t)+soft*t)
    base = np.stack([surface+d-atten*k for d,k in [(10,92),(4,83),(-10,71)]],axis=-1)
    image = Image.fromarray(base.clip(0,255).astype("uint8"),"RGB")
    draw = ImageDraw.Draw(image)
    draw.polygon([(132,193),(172,167),(248,190),(209,217)],fill="#b87056")
    draw.polygon([(132,193),(209,217),(209,303),(132,280)],fill="#a4614f")
    draw.polygon([(209,217),(248,190),(248,273),(209,303)],fill="#704944")
    draw.line([(132,193),(209,217),(248,190)],fill="#d99270",width=2)
    draw.text((29,56),"TABLETOP SHADOW / A",fill="#564b46",font=ImageFont.load_default(size=17))
    draw.text((29,386),"SIMULATED LIGHTING STUDY",fill="#665b50",font=ImageFont.load_default(size=11))
    draw.line((39,416,139,416),fill="#65564b",width=2)
    for x in range(39,140,10):
        draw.line((x,416,x,407 if (x-39)%50 else 402),fill="#65564b",width=2)
    draw.text((153,405),"55 mm",fill="#66574b",font=ImageFont.load_default(size=12))
    return image.size,png(image),cfg
