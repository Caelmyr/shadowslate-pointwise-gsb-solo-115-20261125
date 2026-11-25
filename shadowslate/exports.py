"""Export the original shadow evidence and current measured row."""

from io import BytesIO, StringIO
from zipfile import ZIP_DEFLATED, ZipFile
import csv
import json

from PIL import Image, ImageDraw

from .reading import png


def trace_table(result):
    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow(("x_px","y_px","luma","dark"))
    for p in result["profile"]:
        writer.writerow((p["x"],p["y"],f'{p["brightness"]:.3f}',int(p["dark"])))
    return buffer.getvalue().encode()


def proof(image, card, result):
    h,w = image.shape[:2]
    output = Image.new("RGB",(max(420,w),h+85),"#2f3140")
    output.paste(Image.fromarray(image),(0,0))
    draw = ImageDraw.Draw(output)
    c = card["settings"]
    draw.rectangle((c["left"],c["top"],c["right"],c["bottom"]),outline="#8e5f94",width=2)
    if result["profile"]:
        draw.line((c["left"],c["row"],c["right"],c["row"]),fill="#d7a25f",width=2)
    for a,b in result["ranges"]:
        draw.line((a,c["row"],b,c["row"]),fill="#89537a",width=3)
    edge = result.get("edge") or {}
    spread = edge.get("spread")
    if spread:
        row = edge["y"]
        for x in (spread["start"], spread["end"]):
            draw.line((x,row-7,x,row+7),fill="#3f8b7a",width=2)
        draw.line((spread["start"],row,spread["end"],row),fill="#3f8b7a",width=3)
        draw.text((spread["start"]-10,row-26),f'10-90% row {row}',fill="#3f8b7a")
    r = result["reading"]
    draw.text((14,h+14),f'Dark coverage {r["percentage"]:.2f}% / {r["area_mm2"]:.2f} mm2',fill="#eee7f6")
    draw.text((14,h+34),f'Source row {c["row"]}, threshold {c["cutoff"]}, mm/px {c["mm_per_px"]}',fill="#e9c492")
    if spread:
        dark, light = edge["dark"], edge["light"]
        draw.text((14,h+54),f'10-90% at x {spread["start"]:.2f} - {spread["end"]:.2f} px'
                            f' ({spread["width_px"]:.2f} px / {spread["width_mm"]:.2f} mm)',fill="#9fd8c8")
        draw.text((14,h+70),f'dark {dark["left"]}-{dark["right"]} ({dark["brightness"]:.1f}), '
                            f'light {light["left"]}-{light["right"]} ({light["brightness"]:.1f})',fill="#c7e6dc")
    else:
        draw.text((14,h+54),f'Edge: {edge.get("note") or "未取影边"}',fill="#d9b3c9")
    return png(output)


def package(card, original, image, result):
    buffer = BytesIO()
    with ZipFile(buffer,"w",ZIP_DEFLATED) as stream:
        stream.writestr("tabletop.png",original)
        stream.writestr("reading.json",json.dumps({"card":card,"coverage":result["reading"],
                                                    "ranges":result["ranges"],
                                                    "edge":result["edge"]},ensure_ascii=False,indent=2))
        stream.writestr("shadow-profile.csv",trace_table(result))
        stream.writestr("shadow-proof.png",proof(image,card,result))
    return buffer.getvalue()
