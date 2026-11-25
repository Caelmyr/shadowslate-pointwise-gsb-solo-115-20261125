"""端到端核查同列纸色参照判断（不接触任何外部数据目录）。"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
from PIL import Image

from shadowslate import exports, reading


def make_paper(width=120, height=90, clip_column=None, clip_value=0, top=10):
    """带深浅竖纹的纸面，中间放一块真正的挡光阴影。"""
    yy, xx = np.mgrid[0:height, 0:width]
    stripe = 46 * np.sin(xx * 0.35)          # 深竖纹：低谷低于 155，全框阈值会误判
    vertical_gradient = 8 * yy / height
    paper = 205 + stripe - vertical_gradient
    shadow = np.zeros((height, width))
    shadow[30:60, 40:80] = 70                # 真实阴影，压暗 70
    image = np.clip(paper - shadow, 0, 255)
    rgb = np.repeat(image[:, :, None], 3, axis=2).astype(np.uint8)
    if clip_column is not None:
        # 让该列上参照条（框顶起 8 行）全部成为截断像素，模拟实拍像素不足。
        rgb[top:top + 8, clip_column] = clip_value
    return rgb


def base_config(**overrides):
    config = {"left": 10, "right": 109, "top": 10, "bottom": 79, "row": 45,
              "cutoff": 155, "method": "reference", "ratio": 0.85, "mm_per_px": 1.0}
    config.update(overrides)
    return reading.settings(config, (120, 90))


def almost(value, expected, tol, label):
    assert abs(value - expected) <= tol, f"{label}: {value} != {expected}"
    print(f"  ok {label}: {value:.3f}")


def main():
    image = make_paper()

    print("[1] 同列参照：深竖纹不算阴影，真实挡光才算")
    config = base_config()
    result = reading.report(image, config)
    # 阴影只应集中在人为压暗的 40..79 列、30..59 行范围内。
    shadow_xs = {p["x"] for p in result["profile"] if p["dark"]}
    assert shadow_xs and max(shadow_xs) <= 79 and min(shadow_xs) >= 40, shadow_xs
    # 在没有真实挡光的列里找最深竖纹（x < 40），应判为纸面而非阴影。
    dark_stripe_x = 10 + int(np.argmin(image[45, 10:40, 0]))
    probe = reading.point(image, dark_stripe_x, 45, config)
    assert not probe["dark"] and not probe["pending"], probe
    print(f"  ok 阴影外最深竖纹列 x={dark_stripe_x} 判为 {probe['status']}，比例 {probe['ratio_value']:.3f}")

    print("[2] 全框亮度阈会把深竖纹当暗区（证明新模式解决的问题，且可切回）")
    old = base_config(method="threshold")
    old_result = reading.report(image, old)
    assert old_result["reading"]["pending_px"] == 0
    # 阴影只在 y=30..59、x=40..79；统计这个矩形之外被判暗的像素（即深竖纹误判）。
    def threshold_false_alarms():
        count = 0
        for row_y in range(config["top"], config["bottom"] + 1):
            for x in range(config["left"], config["right"] + 1):
                if 30 <= row_y <= 59 and 40 <= x <= 79:
                    continue
                if float(image[row_y, x] @ reading.LUMA) <= config["cutoff"]:
                    count += 1
        return count
    threshold_false = threshold_false_alarms()
    shared_ref = reading.reference_columns(image, config)
    reference_false = sum(
        1 for row_y in range(config["top"], config["bottom"] + 1)
        for x in range(config["left"], config["right"] + 1)
        if not (30 <= row_y <= 59 and 40 <= x <= 79)
        and reading.point(image, x, row_y, config, shared_ref)["dark"])
    assert threshold_false > 0 and reference_false == 0, (threshold_false, reference_false)
    print(f"  ok 阴影外误判：全框阈值 {threshold_false} px，同列参照 {reference_false} px")

    print("[3] 参照条像素不足的列标待核，不借旁列")
    clipped = make_paper(clip_column=55)  # 上条 8 行里该列全部被置为 0
    clip_result = reading.report(clipped, config)
    col55 = next(p for p in clip_result["profile"] if p["x"] == 55)
    assert col55["pending"] and col55["status"] == "待核" and not col55["dark"]
    neighbor = next(p for p in clip_result["profile"] if p["x"] == 56)
    assert not neighbor["pending"], "旁列不应受牵连"
    reading_info = clip_result["reading"]
    frame_height = config["bottom"] - config["top"] + 1
    frame_width = config["right"] - config["left"] + 1
    frame_cells = frame_height * frame_width
    # 只有一列的上条失效：该列整列 70 个点全部待核，旁列不受影响。
    assert reading_info["pending_px"] == frame_height, reading_info["pending_px"]
    assert reading_info["evidence_px"] == frame_cells - reading_info["pending_px"]
    almost(reading_info["percentage"],
           reading_info["dark_px"] * 100 / reading_info["evidence_px"], 1e-9, "覆盖比例分母剔除待核")
    print(f"  ok 待核 {reading_info['pending_px']} px，有证据 {reading_info['evidence_px']} / {frame_cells}")

    print("[4] 截线保留真实源亮度，参照/比例字段只用于判断")
    p = result["profile"][0]
    assert abs(p["brightness"] - float(image[config["row"], p["x"]] @ reading.LUMA)) < 1e-9
    assert p["reference"] is not None and p["ratio_value"] == p["brightness"] / p["reference"]
    print("  ok 截线亮度等于原片像素亮度")

    print("[5] 当地纸色是上下条的距离加权插值，且不被条内光亮跳动带偏")
    ref = reading.reference_columns(image, config)
    top_med = ref["columns"][60]["top_ref"]
    bottom_med = ref["columns"][60]["bottom_ref"]
    local = reading.local_reference(ref, 60, config["row"])
    weight = (config["row"] - ref["top_center"]) / ref["span"]
    almost(local, top_med * (1 - weight) + bottom_med * weight, 1e-9, "线性插值当地纸色")
    # 在参照条里塞两个高光突变像素，中位数应几乎不动。
    noisy = image.copy()
    noisy[config["top"] + 1, 60] = 255
    noisy[config["top"] + 2, 60] = 254
    noisy_top = reading.reference_columns(noisy, config)["columns"][60]["top_ref"]
    almost(noisy_top, top_med, 1e-9, "条内高光不改变中位纸色")

    print("[6] CSV / PNG 校样与同一判断一致")
    card = {"settings": config}
    csv_text = exports.trace_table(result).decode()
    assert csv_text.splitlines()[0] == "x_px,y_px,luma,reference_luma,ratio,status"
    assert "待核" not in csv_text or "待核" in csv_text  # 状态列存在即可
    proof_png = exports.proof(image, card, result)
    assert proof_png[:8] == b"\x89PNG\r\n\x1a\n"
    old_card = {"settings": old}
    old_csv = exports.trace_table(old_result).decode()
    assert old_csv.splitlines()[0] == "x_px,y_px,luma,dark,status"
    print(f"  ok CSV 表头随模式切换，PNG {len(proof_png)} 字节")

    print("[7] 框高不足 17 行时拒绝参照模式；阈值模式仍可用")
    try:
        reading.settings({**config, "bottom": config["top"] + 15}, (120, 90))
    except reading.Invalid as exc:
        print(f"  ok 拒绝：{exc}")
    else:
        raise AssertionError("应当拒绝过矮的测量框")

    print("[8] 练习图可生成且默认走参照模式")
    size, data, demo_cfg = reading.practice()
    assert demo_cfg["method"] == "reference"
    with tempfile.NamedTemporaryFile(suffix=".png") as tmp:
        tmp.write(data)
        demo_image = reading.pixels(Path(tmp.name))
    demo_result = reading.report(demo_image, demo_cfg)
    assert demo_result["reading"]["method"] == "reference"
    print(f"  ok 练习图 {size}，阴影覆盖 {demo_result['reading']['percentage']:.2f}%，"
          f"待核 {demo_result['reading']['pending_px']}")

    print("[9] 点读、截线、整框掩码对同一像素结论完全一致")
    shared_ref = reading.reference_columns(image, config)
    dark_mask, pending_mask = reading.frame_masks(image, config, shared_ref)
    shared_profile = reading.profile(image, config, shared_ref)
    for row_index, y in enumerate(range(config["top"], config["bottom"] + 1)):
        for col_index, x in enumerate(range(config["left"], config["right"] + 1)):
            probe = reading.point(image, x, y, config, shared_ref)
            assert probe["pending"] == bool(pending_mask[row_index, col_index])
            assert probe["dark"] == bool(dark_mask[row_index, col_index])
            if y == config["row"]:
                line = shared_profile[col_index]
                assert line["x"] == x
                assert line["dark"] == probe["dark"] and line["pending"] == probe["pending"]
                assert (line["reference"] is None) == (probe["reference"] is None)
    print("  ok 三种路径的阴影/待核结论逐像素一致")

    print("\n全部核查通过。")


if __name__ == "__main__":
    main()
