"""Иконка приложения (просьба Валерия 21.11.2026): точечная матрица 17×17 на «озёрном» градиенте.
Все точки одного золотого цвета и различаются только размером: крупные образуют «AI», остальные — мелкий фон.

    python design/make_icon.py   # пишет design/app-icon.svg, site/favicon.svg и PNG в site/icons/ (нужен Playwright)
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
N = 17  # точек в ряду (21.11.2026 Валерий попросил больше точек и все одного цвета — различаются только размером)
# буквы 7×11 и 5×11, у «A» острый верх (иначе читается как «H»)
A = ["...#...", "..#.#..", "..#.#..", ".#...#.", ".#...#.", "#.....#", "#######", "#.....#", "#.....#", "#.....#", "#.....#"]
I = ["#####", "..#..", "..#..", "..#..", "..#..", "..#..", "..#..", "..#..", "..#..", "..#..", "#####"]
LETTER = {(r + 3, c + 2) for r, row in enumerate(A) for c, ch in enumerate(row) if ch == "#"} | \
         {(r + 3, c + 10) for r, row in enumerate(I) for c, ch in enumerate(row) if ch == "#"}


def icon(scale: float = 1.0, rounded: bool = False, size: int = 1024) -> str:
    start, big, small = 112, 22, 6
    step = (1024 - 2 * start) / (N - 1)
    dots = "".join(f'<circle cx="{start + c * step:.1f}" cy="{start + r * step:.1f}" r="{big if (r, c) in LETTER else small}"/>'
                   for r in range(N) for c in range(N))
    k = 512 * (1 - scale)
    clip = '<clipPath id="r"><rect width="1024" height="1024" rx="230"/></clipPath>' if rounded else ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024" width="{size}" height="{size}"><defs>{clip}'
            '<linearGradient id="bg" x1="0" y1="0" x2="0.35" y2="1"><stop offset="0" stop-color="#1E8A84"/>'
            '<stop offset="0.55" stop-color="#0F6B68"/><stop offset="1" stop-color="#0A4547"/></linearGradient>'
            '<radialGradient id="glow" cx="0.28" cy="0.18" r="0.75"><stop offset="0" stop-color="#FFFFFF" stop-opacity=".16"/>'
            '<stop offset="1" stop-color="#FFFFFF" stop-opacity="0"/></radialGradient>'
            '<linearGradient id="gold" gradientUnits="userSpaceOnUse" x1="100" y1="100" x2="924" y2="924">'
            '<stop offset="0" stop-color="#FFDB8E"/><stop offset="1" stop-color="#EFA437"/></linearGradient></defs>'
            f'<g{" clip-path=" + chr(34) + "url(#r)" + chr(34) if rounded else ""}>'
            '<rect width="1024" height="1024" fill="url(#bg)"/><rect width="1024" height="1024" fill="url(#glow)"/>'
            f'<g transform="translate({k:.1f} {k:.1f}) scale({scale})" fill="url(#gold)">{dots}</g></g></svg>')


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
    (ROOT / "site" / "favicon.svg").write_text(icon(rounded=True, size=64))
    render()
    print("готово")
