import sys
from pathlib import Path

import ddddocr
from PIL import Image


PROJECT_ROOT = Path(__file__).resolve().parents[1]
QR_FOLDER = PROJECT_ROOT / "ocr_model_benchmark" / "QR"


def iter_images(folder: Path):
    exts = {".png", ".jpg", ".jpeg", ".bmp"}
    for p in sorted(folder.iterdir()):
        if p.is_file() and p.suffix.lower() in exts:
            yield p


def run_qr_test():
    if not QR_FOLDER.exists():
        print(f"QR 文件夹不存在: {QR_FOLDER}")
        return

    ocr = ddddocr.DdddOcr(show_ad=False)

    total = 0
    for img_path in iter_images(QR_FOLDER):
        with img_path.open("rb") as f:
            img_bytes = f.read()
        try:
            text = ocr.classification(img_bytes)
        except Exception as e:
            text = f"<ERROR: {e}>"

        total += 1
        print(f"[{total:03d}] {img_path.name} -> {text}")

    print(f"共测试 {total} 张图片。")


if __name__ == "__main__":
    # 目前仅支持 QR 测试；后续如有 CAPTCHA 目录可再扩展
    run_qr_test()
