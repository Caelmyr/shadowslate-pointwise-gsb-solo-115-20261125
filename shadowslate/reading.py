"""Measure dark coverage, a real source row, and one picked shadow edge."""

from io import BytesIO
import math

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps, UnidentifiedImageError

from image_lab.validation import Invalid, integer, number, object_value


LUMA = np.array([0.2126, 0.7152, 0.0722])
MAX_IMAGE_BYTES = 18 * 1024 * 1024
END_PIXELS = 4      # least real pixels in one end
END_RANGE = 6       # greatest brightness range inside one end
END_GAP = 20        # least brightness gap between the two ends
WINDOW = 32         # columns searched either side of the picked edge


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
    config = {"left": left, "right":integer(value.get("right"),"右界 X",left,w-1),
              "top":top, "bottom":integer(value.get("bottom"),"下界 Y",top,h-1),
              "row":integer(value.get("row"),"读数行 Y",0,h-1),
              "cutoff":integer(value.get("cutoff"),"暗区亮度上限",1,254),
              "mm_per_px":number(value.get("mm_per_px"),"标尺",0.001,500)}
    edge = value.get("edge")
    if edge is None:
        config["edge"] = None
    else:
        edge = object_value(edge, "影边位置")
        config["edge"] = {"x":integer(edge.get("x"),"影边 X",0,w-1),
                          "y":integer(edge.get("y"),"影边 Y",0,h-1)}
    return config


def defaults(size):
    w,h = size
    return settings({"left":w//6,"right":w*5//6,"top":h//5,"bottom":h*4//5,
                     "row":h//2,"cutoff":155,"mm_per_px":1},size)


def point(image, x, y, config, edge=None):
    rgb = image[y,x]
    light = float(rgb @ LUMA)
    in_bounds = config["left"] <= x <= config["right"] and config["top"] <= y <= config["bottom"]
    result = {"x":x, "y":y, "rgb":[int(value) for value in rgb],
              "brightness":light,"dark":bool(light <= config["cutoff"]) if in_bounds else None}
    if edge:
        spread = edge.get("spread")
        applies = edge.get("y") == y
        position = None
        if spread and applies:
            if x < spread["start"]:
                position = "暗端一侧" if spread["dark_left"] else "亮端一侧"
            elif x > spread["end"]:
                position = "亮端一侧" if spread["dark_left"] else "暗端一侧"
            else:
                position = "中间"
        result["edge"] = {"x":edge.get("x"), "y":edge.get("y"), "status":edge.get("status"),
                          "note":edge.get("note"), "row_applies":applies, "position":position}
    return result


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


def spread_of(levels, left, right):
    return float(np.ptp(levels[left:right+1]))


def stable_ends(levels, low, high, seed):
    """Maximal runs of at least four real pixels whose whole brightness range is at most 6.

    A run holding the picked point is split there so the real end beside the point can
    still serve; only pixels away from the point make up that end.
    """
    ends, left = [], low
    while left <= high:
        right = left
        while right + 1 <= high and spread_of(levels, left, right+1) <= END_RANGE:
            right += 1
        pieces = [(left, seed-1), (seed+1, right)] if left <= seed <= right else [(left, right)]
        for a, b in pieces:
            if b - a + 1 >= END_PIXELS:
                ends.append({"left":a, "right":b, "pixels":b-a+1,
                             "brightness":float(np.mean(levels[a:b+1])),
                             "spread":spread_of(levels, a, b)})
        left = right + 1
    return ends


def crossing(levels, from_x, to_x, level):
    """Read a level where two adjacent real pixels bracket it; never extrapolate."""
    step = 1 if to_x > from_x else -1
    for left in range(from_x, to_x, step):
        right = left + step
        low, high = sorted((float(levels[left]), float(levels[right])))
        if high > low and low <= level <= high:
            return left + step * (level - levels[left]) / (levels[right] - levels[left])
    return None


def transition(image, config):
    """Read the 10/90% transition around the picked edge on the current reading row."""
    seed = config.get("edge")
    result = {"x":None, "y":None, "status":"pending",
              "note":"尚未点取影边位置，待核", "dark":None, "light":None, "spread":None}
    if not seed:
        return result
    x0, y = seed["x"], seed["y"]
    result.update(x=x0, y=y)
    if y != config["row"]:
        return {**result, "note":"影边点不在当前读数行，其他原片行不套本次读数，待核"}
    if not config["top"] <= y <= config["bottom"]:
        return {**result, "note":"影边点不在测量框内，待核"}
    levels = image[y] @ LUMA
    low, high = max(0, x0-WINDOW), min(levels.size-1, x0+WINDOW)
    ends = stable_ends(levels, low, high, x0)
    dark = [end for end in ends if end["brightness"] <= config["cutoff"]]
    light = [end for end in ends if end["brightness"] > config["cutoff"]]
    if not dark or not light:
        missing = "暗端" if not dark else "亮端"
        return {**result, "note":f"影边左右各32列内凑不齐连续4像素的实际{missing}，待核"}
    if not any(end["right"] < x0 for end in dark + light) or \
       not any(end["left"] > x0 for end in dark + light):
        return {**result, "note":"实际暗端和亮端都在影边同一侧，待核"}

    options, low_contrast = [], False
    for darker in dark:
        for brighter in light:
            if darker["right"] < x0 < brighter["left"]:
                dark_x, light_x, dark_left = darker["right"], brighter["left"], True
                dark_near, light_near = x0-darker["right"], brighter["left"]-x0
            elif brighter["right"] < x0 < darker["left"]:
                dark_x, light_x, dark_left = darker["left"], brighter["right"], False
                dark_near, light_near = darker["left"]-x0, x0-brighter["right"]
            else:
                continue
            if brighter["brightness"] - darker["brightness"] < END_GAP:
                low_contrast = True
                continue
            low_level = darker["brightness"] + 0.1 * (brighter["brightness"] - darker["brightness"])
            high_level = darker["brightness"] + 0.9 * (brighter["brightness"] - darker["brightness"])
            low_at = crossing(levels, dark_x, light_x, low_level)
            high_at = crossing(levels, dark_x, light_x, high_level)
            if low_at is None or high_at is None:
                continue
            first, second = sorted((float(low_at), float(high_at)))
            options.append({"dark":darker, "light":brighter, "start":first, "end":second,
                            "low_level":low_level, "high_level":high_level,
                            "dark_left":dark_left, "near":dark_near + light_near,
                            "steady":darker["spread"] + brighter["spread"]})
    if not options:
        return {**result, "note":("两个实际端的代表亮度差不足20，待核" if low_contrast
                                   else "10%与90%亮度处没有相邻原片像素夹住的交点，待核")}
    # Several dark ends may qualify: stay with the edge closest to the picked point,
    # and let steadiness of the two ends decide between edges that are nearly as close.
    best = min(options, key=lambda option: (option["near"], option["steady"]))
    rivals = [option for option in options if option is not best
              and option["near"] <= best["near"] + 2 and option["steady"] <= best["steady"]]
    if rivals and min(rivals, key=lambda option: option["steady"])["steady"] == best["steady"]:
        return {**result, "note":"两段实际端离影边点同样近、端内亮度也相仿，待核"}
    result.update(
        status="ok", note="", dark=best["dark"], light=best["light"],
        spread={"start":best["start"], "end":best["end"],
                "width_px":best["end"]-best["start"],
                "width_mm":(best["end"]-best["start"]) * config["mm_per_px"],
                "low_level":best["low_level"], "high_level":best["high_level"],
                "dark_left":best["dark_left"]})
    return result


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
    return {"reading":coverage(image,config),"row":config["row"],"profile":values,"ranges":ranges,
            "edge":transition(image,config)}


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
