"""Иконка приложения — картинка Валерия (21.11.2026): «AI» с засечками газетным растром из белых точек
на тёмно-синем фоне. Исходник — design/app-icon.png (1254×1254). Скрипт только нарезает размеры:

    python design/make_icon.py   # пишет site/icons/app-*.png и site/favicon.svg (нужен Pillow)

До неё в то же утро были четыре моих варианта (строка текста из точек, матрицы 11×11 и 17×17, чёрно-белый растр
48×48) — Валерий выбрал свою.
"""
import base64
import io
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "design" / "app-icon.png"
ICONS = ROOT / "site" / "icons"


def square(size: int, scale: float = 1.0) -> Image.Image:
    """Иконка size×size без прозрачности (iOS заливает прозрачное чёрным); scale < 1 — с полями цвета фона."""
    src = Image.open(SRC).convert("RGB")
    bg = src.getpixel((2, 2))
    inner = round(size * scale)
    out = Image.new("RGB", (size, size), bg)
    out.paste(src.resize((inner, inner), Image.LANCZOS), ((size - inner) // 2, (size - inner) // 2))
    return out


def main() -> None:
    for name, size, scale in [("app-180.png", 180, 1.0), ("app-192.png", 192, 1.0), ("app-512.png", 512, 1.0),
                              ("app-maskable-512.png", 512, 0.86)]:  # Android: всё важное — в безопасной зоне
        # 64 цвета хватает (белые точки на синем) — файл в несколько раз меньше, на вид разницы нет
        square(size, scale).quantize(64, method=Image.Quantize.MEDIANCUT).save(ICONS / name, optimize=True)
    # значок вкладки: та же картинка 64×64 со скруглёнными углами, внутри SVG (ссылки на favicon.svg не меняются)
    fav = square(64).convert("RGBA")
    mask = Image.new("L", (64, 64), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, 63, 63), 14, fill=255)
    fav.putalpha(mask)
    buf = io.BytesIO()
    fav.save(buf, "PNG", optimize=True)
    b64 = base64.b64encode(buf.getvalue()).decode()
    (ROOT / "site" / "favicon.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 64 64">'
        f'<image width="64" height="64" href="data:image/png;base64,{b64}" xlink:href="data:image/png;base64,{b64}"/></svg>')
    print("готово")


if __name__ == "__main__":
    main()
