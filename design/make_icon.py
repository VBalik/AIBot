"""Иконка приложения (просьбы Валерия 21.11.2026): чёрно-белая, «AI» набрано типографским шрифтом
(Liberation Serif Bold — свободный аналог Times) и выложено растром из точек 48×48, как в газетной печати:
внутри букв точки крупные, по краям — промежуточные, на фоне — мелкие. Все точки одного белого цвета на чёрном.

    python design/make_icon.py   # пишет design/app-icon.svg, site/favicon.svg и PNG в site/icons/ (нужны Pillow и Playwright)
"""
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
FONT = "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf"  # пакет fonts-liberation, лицензия OFL
N = 48                      # точек в ряду
R_MIN, R_MAX = 0.09, 0.50   # радиус точки в долях шага растра: фон — внутри буквы
S = 2048                    # разрешение маски букв


def letters_mask(text: str = "AI", cap: float = 0.50, max_w: float = 0.74, track: float = 0.06) -> Image.Image:
    """Чёрно-белая маска набранного текста: высота прописной — cap, ширина — не больше max_w квадрата."""
    probe = ImageFont.truetype(FONT, 1000)
    b = probe.getbbox("I")
    size = int(1000 * cap * S / (b[3] - b[1]))
    for _ in range(2):
        font = ImageFont.truetype(FONT, size)
        boxes = [font.getbbox(ch) for ch in text]
        total = sum(x[2] - x[0] for x in boxes) + track * size * (len(text) - 1)
        if total <= max_w * S:
            break
        size = int(size * max_w * S / total)
    im = Image.new("L", (S, S), 0)
    d = ImageDraw.Draw(im)
    x = (S - total) / 2
    ib = font.getbbox("I")
    y = (S - (ib[3] - ib[1])) / 2 - ib[1]
    for ch, bx in zip(text, boxes):
        d.text((x - bx[0], y), ch, font=font, fill=255)
        x += bx[2] - bx[0] + track * size
    return im


def dots(n: int = N) -> str:
    mask, cell, k = letters_mask(), 1024 / n, S / 1024
    out = []
    for r in range(n):
        for c in range(n):
            cx, cy = (c + .5) * cell, (r + .5) * cell
            crop = mask.crop((int((cx - cell / 2) * k), int((cy - cell / 2) * k), int((cx + cell / 2) * k), int((cy + cell / 2) * k)))
            cover = sum(crop.histogram()[i] * i for i in range(256)) / (255 * crop.width * crop.height)
            out.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{cell * (R_MIN + (R_MAX - R_MIN) * math.sqrt(cover)):.1f}"/>')
    return "".join(out)


def icon(scale: float = 1.0, rounded: bool = False, size: int = 1024, n: int = N) -> str:
    k = 512 * (1 - scale)
    clip = '<defs><clipPath id="r"><rect width="1024" height="1024" rx="230"/></clipPath></defs>' if rounded else ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024" width="{size}" height="{size}">{clip}'
            f'<g{" clip-path=" + chr(34) + "url(#r)" + chr(34) if rounded else ""}><rect width="1024" height="1024" fill="#000"/>'
            f'<g transform="translate({k:.1f} {k:.1f}) scale({scale})" fill="#FFF">{dots(n)}</g></g></svg>')


def render() -> None:
    from PIL import Image
    from playwright.sync_api import sync_playwright
    tmp = ROOT / "design" / ".render"
    tmp.mkdir(exist_ok=True)
    (tmp / "maskable.svg").write_text(icon(scale=0.86))   # Android: всё важное — в безопасной зоне
    jobs = [("app-icon.svg", 180, "app-180.png"), ("app-icon.svg", 192, "app-192.png"),
            ("app-icon.svg", 512, "app-512.png"), (".render/maskable.svg", 512, "app-maskable-512.png")]
    with sync_playwright() as p:
        b = p.chromium.launch()
        for src, size, out in jobs:
            pg = b.new_page(viewport={"width": size, "height": size})
            (tmp / "wrap.html").write_text(f'<body style="margin:0"><img src="{(ROOT / "design" / src).as_uri()}" '
                                           f'style="width:{size}px;height:{size}px;display:block">')
            pg.goto((tmp / "wrap.html").as_uri())
            pg.wait_for_timeout(200)
            path = ROOT / "site" / "icons" / out
            pg.screenshot(path=str(path))
            Image.open(path).convert("RGB").save(path, optimize=True)  # без прозрачности: iOS заливает её чёрным
        b.close()
    for f in tmp.iterdir():
        f.unlink()
    tmp.rmdir()


if __name__ == "__main__":
    (ROOT / "design" / "app-icon.svg").write_text(icon())
    (ROOT / "site" / "favicon.svg").write_text(icon(rounded=True, size=64, n=24))  # во вкладке крупнее растр и легче файл
    render()
    print("готово")
