"""Фирменные значки источников вместо плиток с буквами (просьба Валерия 21.11.2026).

Сборщик сам находит значок каждого источника и кладёт его в ветку data: icons/<id>.png, квадрат 160×160.
Где ищется, от лучшего к худшему:
  1. <link rel="apple-touch-icon"> на главной странице сайта — обычно 180×180, специально для значков;
  2. картинка ленты (RSS <image>, Atom <icon>/<logo>) — у блогов на Substack это логотип издания;
  3. <link rel="icon" sizes="…"> покрупнее; 4. /apple-touch-icon.png; 5. мелкие favicon и /favicon.ico.
Берётся первая картинка, которая открывается, не меньше 32 px и почти квадратная (баннеры пропускаются).
Значки обновляются раз в REFRESH_DAYS дней, после неудачи — не раньше чем через RETRY_DAYS.
Сведения — в sources.json["icons"][id]: {file, v (хэш для сброса кэша), src, at} или {fail, at}.
Логотипы издателей — открытые картинки, их показывают все читалки лент; хранить их в публичной ветке можно.
"""
from __future__ import annotations

import hashlib
import io
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import feedparser
import httpx
from bs4 import BeautifulSoup
from PIL import Image, ImageStat

log = logging.getLogger("digest.icons")

SIZE = 160              # px: плитка 52 px на экране с плотностью 3×
MIN_SIDE = 32
MAX_BYTES = 2_000_000
REFRESH_DAYS = 30
RETRY_DAYS = 7
MAX_PER_RUN = 30        # источников за один прогон (каждый — до 10 коротких запросов)
GOOD_SIDE = 150         # нашли картинку такого размера — дальше не ищем
ALG = 3                 # версия поиска: значки, найденные прежней версией, пересматриваются сразу
# значки платформ, а не издателей: буква W от WordPress, медвежонок Bear Blog — лучше плитка с буквами
GENERIC = (("s0.wp.com", "/i/"), ("wordpress.com", "/i/"), ("bearblog.dev", "/apple-touch-icon"),
           ("bearblog.dev", "/favicon"))
UA = {"User-Agent": "Mozilla/5.0 (ai-digest; personal RSS reader)"}


def http_get(url: str) -> bytes:
    with httpx.stream("GET", url, timeout=20, follow_redirects=True, headers=UA) as r:
        r.raise_for_status()
        out = b""
        for chunk in r.iter_bytes():
            out += chunk
            if len(out) > MAX_BYTES:
                raise ValueError("слишком большой файл")
        return out


def _size_of(sizes: str | None) -> int:
    m = re.findall(r"(\d+)\s*x\s*(\d+)", sizes or "", re.I)
    return max((min(int(a), int(b)) for a, b in m), default=0)


def is_generic(url: str) -> bool:
    p = urlparse(url)
    host = p.netloc.lower()
    return any((host == h or host.endswith("." + h)) and p.path.startswith(path) for h, path in GENERIC)


def _full_size(url: str) -> str | None:
    """У WordPress значок в ленте — «…png?w=32»; без параметров это исходник 512×512."""
    p = urlparse(url)
    if re.search(r"(^|&)(w|h|resize|fit|size)=", p.query) and re.search(r"\.(png|jpe?g|webp)$", p.path, re.I):
        return p._replace(query="").geturl()
    return None


def candidates(site: str, page: str | None, feed_xml: str | None) -> list[str]:
    """Адреса возможных значков, лучший первым."""
    scored: list[tuple[int, str]] = []
    if page:
        soup = BeautifulSoup(page, "html.parser")
        base = soup.find("base", href=True)
        root = urljoin(site, base["href"]) if base else site
        for link in soup.find_all("link", rel=True, href=True):
            rel = " ".join(link["rel"]).lower() if isinstance(link["rel"], list) else str(link["rel"]).lower()
            href, kind = link["href"].strip(), (link.get("type") or "").lower()
            if not href or href.startswith("data:") or "svg" in kind or href.lower().split("?")[0].endswith(".svg"):
                continue  # SVG без браузера не нарисовать
            size = _size_of(link.get("sizes"))
            if "apple-touch-icon" in rel:
                scored.append((1000 + (size or 180), urljoin(root, href)))
            elif "icon" in rel.split() or rel == "shortcut icon":
                scored.append((size or 16, urljoin(root, href)))
    if feed_xml:
        f = feedparser.parse(feed_xml).feed
        img = (f.get("image") or {}).get("href") or f.get("logo") or f.get("icon")
        if img:
            scored.append((500, urljoin(site, img)))
    origin = "{0.scheme}://{0.netloc}".format(urlparse(site))
    scored += [(300, origin + "/apple-touch-icon.png"), (8, origin + "/favicon.ico")]
    scored += [(score + 1, full) for score, url in list(scored) if (full := _full_size(url))]
    out, seen = [], set()
    for _, url in sorted(scored, key=lambda x: -x[0]):
        if url not in seen and not is_generic(url):
            seen.add(url)
            out.append(url)
    return out


def normalize(raw: bytes, with_side: bool = False):
    """Квадратный PNG SIZE×SIZE или None, если картинка не годится (with_side — ещё и исходный размер)."""
    try:
        im = Image.open(io.BytesIO(raw))
        im.load()
    except Exception:  # noqa: BLE001
        return None
    w, h = im.size
    if min(w, h) < MIN_SIDE or not 0.8 <= w / h <= 1.25:
        return None
    side = min(w, h)
    im = im.convert("RGBA").crop(((w - side) // 2, (h - side) // 2, (w - side) // 2 + side, (h - side) // 2 + side))
    flat = Image.new("RGBA", im.size, (255, 255, 255, 255))
    flat.alpha_composite(im)
    if ImageStat.Stat(flat.convert("L")).stddev[0] < 4:
        return None  # пустая картинка: белая или прозрачная (так выглядел apple-touch-icon у jack-clark.net)
    im = im.resize((SIZE, SIZE), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "PNG", optimize=True)
    return (buf.getvalue(), side) if with_side else buf.getvalue()


def find_icon(source: dict, get) -> tuple[bytes, str] | None:
    site = source.get("site") or source.get("url", "")
    page = feed_xml = None
    try:
        page = get(site).decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        log.info("Значок %s: главная страница недоступна (%s)", source["id"], exc)
    if source.get("url"):
        try:
            feed_xml = get(source["url"]).decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            pass
    urls = candidates(site, page, feed_xml)
    home = feedparser.parse(feed_xml).feed.get("link", "") if feed_xml else ""
    if page is None and home and urlparse(home).netloc != urlparse(site).netloc:
        # сайт закрыт для серверов (Substack отвечает 403), а лента живёт на другом домене — смотрим его главную
        try:
            urls += [u for u in candidates(home, get(home).decode("utf-8", "replace"), None) if u not in urls]
        except Exception:  # noqa: BLE001
            pass
    best = None  # (исходный размер, png, адрес): берём самую крупную годную картинку, а не первую попавшуюся
    for url in urls[:8]:
        try:
            got = normalize(get(url), with_side=True)
        except Exception:  # noqa: BLE001
            continue
        if got and (best is None or got[1] > best[0]):
            best = (got[1], got[0], url)
            if got[1] >= GOOD_SIDE:
                break
    return (best[1], best[2]) if best else None


def refresh(root: Path, book, get=None, now: datetime | None = None, extra: list[dict] | None = None) -> int:
    """Находит недостающие и устаревшие значки. extra — кандидаты пересмотра (их тоже показывает страница)."""
    get = get or http_get
    now = now or datetime.now(timezone.utc)
    meta = book.data.setdefault("icons", {})
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    todo = []
    for s in list(book.active()) + list(extra or []):
        m = meta.get(s["id"], {})
        at = datetime.strptime(m["at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) if m.get("at") else None
        wait = RETRY_DAYS if m.get("fail") else REFRESH_DAYS
        stale = at is None or now - at > timedelta(days=wait) or m.get("alg", 1) < ALG
        if stale and (s.get("site") or s.get("url")):
            todo.append(s)
    done = 0
    for s in todo[:MAX_PER_RUN]:
        found = find_icon(s, get)
        if not found:
            meta[s["id"]] = {"fail": True, "at": stamp, "alg": ALG}
            (root / "icons" / f"{s['id']}.png").unlink(missing_ok=True)
            log.info("Значок %s не найден — останется плитка с буквами", s["id"])
            continue
        png, src = found
        (root / "icons").mkdir(parents=True, exist_ok=True)
        (root / "icons" / f"{s['id']}.png").write_bytes(png)
        meta[s["id"]] = {"file": f"icons/{s['id']}.png", "v": hashlib.sha1(png).hexdigest()[:8], "src": src, "at": stamp,
                         "alg": ALG}
        log.info("Значок %s: %s", s["id"], src)
        done += 1
    return done


def public_path(book, sid: str) -> str | None:
    """Путь для страницы (относительно ветки data) с хэшем — новый значок не застрянет в кэше."""
    return book._icon(sid).get("icon")
