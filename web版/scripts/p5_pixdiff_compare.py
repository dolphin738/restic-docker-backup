"""P5 像素 diff 报告器（方案 §4.7 P5 验收门）。

对每一页、每一主题（亮/暗），比较 P4 基线截图与 P5 改后截图：
- 尺寸是否一致（不一致 = 布局总高变化，属预期内的密度变化，但需记录）
- 变化像素占比（阈值 8.0/255 视为可见变化，过滤抗锯齿噪声）
- 变化区域 bbox（定位到页面纵向位置，便于人工复核是否落在控件形态层）
- 产出并排图 `*-sbs.png`（左基线 / 右改后，差异处染红）与汇总 JSON

用法：python .e2e/p5-pixdiff-compare.py
"""
import json
import sys
from pathlib import Path

from PIL import Image, ImageChops

ROOT = Path(__file__).resolve().parent.parent / ".e2e"  # 截图产物目录（gitignore）
PAGES = ["dashboard", "repo", "settings", "jobs", "snapshots", "restore", "audit", "notifications"]
THEMES = [("light", "p5-base-light", "p5-after-light"), ("dark", "p5-base-dark", "p5-after-dark")]
THRESHOLD = 8  # 0-255 差值阈值：过滤抗锯齿/字体 hinting 噪声
OUT_DIR = ROOT / "p5-diff"


def analyze(base_path: Path, after_path: Path) -> dict:
    base = Image.open(base_path).convert("RGB")
    after = Image.open(after_path).convert("RGB")
    same_size = base.size == after.size

    # 尺寸不同 → 先按最小公共区域裁剪，尺寸差异单独记录
    w = min(base.size[0], after.size[0])
    h = min(base.size[1], after.size[1])
    base_c = base.crop((0, 0, w, h))
    after_c = after.crop((0, 0, w, h))

    diff = ImageChops.difference(base_c, after_c).convert("L")
    mask = diff.point(lambda v: 255 if v >= THRESHOLD else 0)
    changed = sum(1 for p in mask.getdata() if p)
    total = w * h
    bbox = mask.getbbox()

    # 并排图：左基线、右改后，差异区在右侧染红
    red = Image.new("RGB", after_c.size, (255, 0, 0))
    marked = Image.composite(red, after_c, mask)
    sbs = Image.new("RGB", (w * 2 + 12, h), (40, 40, 40))
    sbs.paste(base_c, (0, 0))
    sbs.paste(marked, (w + 12, 0))

    return {
        "base_size": base.size,
        "after_size": after.size,
        "size_changed": not same_size,
        "height_delta": after.size[1] - base.size[1],
        "compared_px": total,
        "changed_px": changed,
        "changed_ratio": round(changed / total * 100, 3) if total else 0.0,
        "bbox": bbox,
        "sbs": sbs,
    }


def main() -> int:
    OUT_DIR.mkdir(exist_ok=True)
    report = {"threshold": THRESHOLD, "themes": {}}

    for theme, base_dir, after_dir in THEMES:
        rows = {}
        print(f"\n=== {theme} ===")
        print(f"{'page':<14}{'base':>12}{'after':>12}{'Δh':>6}{'changed%':>10}  bbox")
        for page in PAGES:
            base_path = ROOT / base_dir / f"{page}.png"
            after_path = ROOT / after_dir / f"{page}.png"
            if not base_path.exists() or not after_path.exists():
                print(f"{page:<14}  MISSING")
                continue
            info = analyze(base_path, after_path)
            sbs_path = OUT_DIR / f"{theme}-{page}-sbs.png"
            info["sbs"].save(sbs_path)
            info["sbs_path"] = str(sbs_path.relative_to(ROOT))
            del info["sbs"]
            rows[page] = info
            print(
                f"{page:<14}{info['base_size'][1]:>12}{info['after_size'][1]:>12}"
                f"{info['height_delta']:>6}{info['changed_ratio']:>9.2f}%  {info['bbox']}"
            )
        report["themes"][theme] = rows

    (OUT_DIR / "p5-diff-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n报告：{OUT_DIR / 'p5-diff-report.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
