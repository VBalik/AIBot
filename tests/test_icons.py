"""Фирменные значки источников: где ищутся, какие годятся, как хранятся и обновляются.

    python tests/test_icons.py

Без сети: страницы, ленты и картинки подменены.
"""
import io
import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "digest"))

import icons  # noqa: E402
from sources import SourceBook  # noqa: E402


def png(w, h, color=(200, 30, 30, 255), fmt="PNG"):
    buf = io.BytesIO()
    Image.new("RGBA", (w, h), color).save(buf, fmt, **({"sizes": [(16, 16), (48, 48)]} if fmt == "ICO" else {}))
    return buf.getvalue()


def check_candidates():
    page = """<html><head><base href="/blog/">
      <link rel="icon" href="/favicon-32.png" sizes="32x32">
      <link rel="icon" type="image/svg+xml" href="/logo.svg">
      <link rel="apple-touch-icon" href="touch.png">
      <link rel="icon" href="/favicon-192.png" sizes="192x192">
      <link rel="mask-icon" href="/mask.svg"></head></html>"""
    feed = '<?xml version="1.0"?><rss version="2.0"><channel><title>T</title><image><url>https://cdn.test/logo.png</url><title>T</title><link>https://a.test</link></image></channel></rss>'
    got = icons.candidates("https://a.test/", page, feed)
    assert got == ["https://a.test/blog/touch.png", "https://cdn.test/logo.png", "https://a.test/apple-touch-icon.png",
                   "https://a.test/favicon-192.png", "https://a.test/favicon-32.png", "https://a.test/favicon.ico"], got
    # без страницы (сайт закрыт для серверов) — картинка ленты и запасные адреса
    assert icons.candidates("https://a.test/x/", None, feed)[0] == "https://cdn.test/logo.png"
    print("значки: порядок поиска — apple-touch-icon, картинка ленты, крупные favicon, /favicon.ico — ok")


def check_normalize():
    out = Image.open(io.BytesIO(icons.normalize(png(180, 180))))
    assert out.size == (icons.SIZE, icons.SIZE) and out.mode == "RGBA"
    assert icons.normalize(png(1200, 630)) is None      # баннер, не значок
    assert icons.normalize(png(16, 16)) is None          # слишком мелкий
    assert icons.normalize(b"<html>not an image</html>") is None
    assert Image.open(io.BytesIO(icons.normalize(png(48, 48, fmt="ICO")))).size == (icons.SIZE, icons.SIZE)  # .ico
    assert Image.open(io.BytesIO(icons.normalize(png(200, 180)))).size == (icons.SIZE, icons.SIZE)  # почти квадрат — обрезка
    print("значки: квадрат 160×160, баннеры и крошечные favicon отбрасываются, .ico читается — ok")


SEED = [
    {"id": "good", "kind": "analysis", "symbol": "Gd", "name": "Good", "author": "A", "about": "a",
     "url": "https://good.test/feed", "site": "https://good.test"},
    {"id": "closed", "kind": "news", "symbol": "Cl", "name": "Closed", "author": "B", "about": "b",
     "url": "https://closed.test/feed", "site": "https://closed.test"},
]
WEB = {
    "https://good.test": b'<html><head><link rel="apple-touch-icon" href="/t.png"></head></html>',
    "https://good.test/feed": b'<?xml version="1.0"?><rss version="2.0"><channel><title>G</title></channel></rss>',
    "https://good.test/t.png": png(180, 180),
    "https://cand.test": b'<html><head><link rel="icon" sizes="96x96" href="/i.png"></head></html>',
    "https://cand.test/i.png": png(96, 96, (0, 90, 200, 255)),
}
CALLS = []


def fake_get(url):
    CALLS.append(url)
    if url not in WEB:
        raise RuntimeError("403")
    return WEB[url]


def check_refresh():
    root = Path(tempfile.mkdtemp())
    book = SourceBook(root / "sources.json", seed=SEED)
    now = datetime(2026, 11, 21, 9, 0, tzinfo=timezone.utc)
    cand = [{"id": "cand", "kind": "analysis", "name": "Cand", "site": "https://cand.test"}]
    assert icons.refresh(root, book, fake_get, now, extra=cand) == 2
    meta = book.data["icons"]
    assert meta["good"]["file"] == "icons/good.png" and meta["good"]["src"] == "https://good.test/t.png"
    assert meta["closed"]["fail"] is True
    assert Image.open(root / "icons" / "good.png").size == (icons.SIZE, icons.SIZE)
    assert (root / "icons" / "cand.png").exists()
    pub = {s["id"]: s for s in book.public(set())}
    assert pub["good"]["icon"] == f"icons/good.png?v={meta['good']['v']}" and "icon" not in pub["closed"]
    assert icons.public_path(book, "cand").startswith("icons/cand.png?v=")
    # через день: ничего не скачивается; через 8 дней — повторная попытка только для неудачного
    CALLS.clear()
    assert icons.refresh(root, book, fake_get, now + timedelta(days=1), extra=cand) == 0 and CALLS == []
    icons.refresh(root, book, fake_get, now + timedelta(days=8), extra=cand)
    assert CALLS and all("closed.test" in u for u in CALLS), CALLS
    # через 31 день значки обновляются; новый логотип — новый хэш, страница не возьмёт старый из кэша
    WEB["https://good.test/t.png"] = png(180, 180, (10, 160, 60, 255))
    old_v = meta["good"]["v"]
    icons.refresh(root, book, fake_get, now + timedelta(days=31), extra=cand)
    assert book.data["icons"]["good"]["v"] != old_v
    # сведения сохраняются вместе со списком источников
    book.save()
    assert json.loads((root / "sources.json").read_text())["icons"]["good"]["file"] == "icons/good.png"
    print("значки: поиск, хранение в icons/<id>.png, хэш для кэша, повтор через 7 дней, обновление через 30 — ok")


check_candidates()
check_normalize()
check_refresh()
print("ВСЕ ПРОВЕРКИ ЗНАЧКОВ ПРОЙДЕНЫ")
