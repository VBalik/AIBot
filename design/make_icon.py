"""Иконка приложения — картинка Валерия (21.11.2026): «AI» с засечками газетным растром из белых точек
на тёмно-синем фоне. Исходник — design/app-icon.png (1254×1254). Скрипт обрезает пустые поля и нарезает размеры:

    python design/make_icon.py   # пишет site/icons/app-*.png и site/favicon.svg (нужен Pillow)

До неё в то же утро были четыре моих варианта (строка текста из точек, матрицы 11×11 и 17×17, чёрно-белый растр
48×48) — Валерий выбрал свою.
"""
import base64
import io
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "design" / "app-icon.png"
ICONS = ROOT / "site" / "icons"


def _centers(profile: np.ndarray) -> list[float]:
    """Середины подряд идущих ненулевых отрезков профиля — центры рядов точек."""
    xs = np.flatnonzero(profile)
    runs = np.split(xs, np.flatnonzero(np.diff(xs) > 1) + 1)
    return [(r[0] + r[-1]) / 2 for r in runs]


def grid_crop(src: Image.Image) -> tuple[float, float, float, float]:
    """Квадрат вокруг сетки точек: от крайних точек до края — столько же пустого места, сколько между точками
    (21.11.2026 Валерий: «между точками и краем пустые чёрные поля»). Крайние ряды сетки — только фоновые точки."""
    a = np.asarray(src.convert("L")) > 128
    rows = _centers(a.sum(axis=1))
    cols = _centers(a.sum(axis=0))
    top, bottom, left, right = rows[0], rows[-1], cols[0], cols[-1]
    edge_rows = _centers(a[:, int(left) - 10:int(left) + 10].sum(axis=1))   # ряды в крайнем столбце (без букв)
    edge_cols = _centers(a[int(top) - 10:int(top) + 10, :].sum(axis=0))     # столбцы в верхнем ряду
    pitch_y = (edge_rows[-1] - edge_rows[0]) / (len(edge_rows) - 1)
    pitch_x = (edge_cols[-1] - edge_cols[0]) / (len(edge_cols) - 1)
    dot = int(a[int(top) - 8:int(top) + 9, int(left) - 8:int(left) + 9].sum(axis=0).astype(bool).sum())  # диаметр точки
    # от центра крайней точки до края: шаг минус радиус — тогда просвет до края равен просвету между точками
    side = max(right - left + 2 * (pitch_x - dot / 2), bottom - top + 2 * (pitch_y - dot / 2))
    cx, cy = (left + right) / 2, (top + bottom) / 2
    return (cx - side / 2, cy - side / 2, cx + side / 2, cy + side / 2)


def square(size: int, scale: float = 1.0) -> Image.Image:
    """Иконка size×size без прозрачности (iOS заливает прозрачное чёрным); scale < 1 — с полями цвета фона."""
    raw = Image.open(SRC).convert("RGB")
    bg = raw.getpixel((2, 2))
    box = grid_crop(raw)
    src = raw.resize((round(box[2] - box[0]),) * 2, Image.LANCZOS, box=box)
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
