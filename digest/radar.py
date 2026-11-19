"""Радар по ежедневной подборке TLDR AI (просьба Валерия 19.11.2026).

TLDR AI — не блог, а подборка ссылок: в каждом выпуске 15–20 чужих материалов с пересказом в 2–3 предложения.
Переводить пересказы бессмысленно, поэтому подборка используется как радар:
  1. каждый новый выпуск разбирается один раз, ссылки на первоисточники хранятся в state.json (radar) ~70 дней;
  2. сайты, на которые подборка часто ссылается, становятся кандидатами еженедельного пересмотра (review.discover);
  3. первоисточники с сайтов лабораторий и исследовательских центров (PRIMARY) идут в раздел «Новости»
     как обычные посты: полный текст берётся со страницы статьи (fetch_article), без него пост не показывается.

Страница выпуска (tldr.tech/ai/<дата>, Next.js): каждый материал — <article>, в нём <a class="font-bold" href>
с <h3>«Заголовок (N minute read)»</h3>; рекламные помечены «(Sponsor)». Разделы — <h3 class="text-center">.
Ссылки и заголовки — открытые данные, их можно хранить в публичной ветке data; тексты статей — нельзя.
"""
from __future__ import annotations

import calendar
import html
import logging
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import feedparser
import httpx
from bs4 import BeautifulSoup

log = logging.getLogger("digest.radar")

KEEP_DAYS = 70          # сколько хранить разобранные выпуски (пересмотр смотрит 8 недель)
MAX_NEW_ISSUES = 12     # сколько новых выпусков разбирать за один прогон (первый запуск — с запасом)

# Первоисточники, которые стоит читать целиком: лаборатории, их инженерные и исследовательские блоги,
# исследовательские центры. Запись — домен или домен с началом пути.
PRIMARY = (
    "openai.com", "anthropic.com", "claude.com/blog", "deepmind.google", "blog.google", "research.google",
    "developers.googleblog.com", "ai.meta.com", "engineering.fb.com", "mistral.ai", "huggingface.co/blog",
    "microsoft.com/en-us/research", "blogs.nvidia.com", "developer.nvidia.com/blog", "machinelearning.apple.com",
    "amazon.science", "allenai.org", "x.ai", "qwen.ai", "qwenlm.github.io", "deepseek.com", "cohere.com",
    "perplexity.ai/hub", "thinkingmachines.ai", "primeintellect.ai", "epoch.ai", "metr.org",
    "transformer-circuits.pub", "hai.stanford.edu", "bair.berkeley.edu", "ai.stanford.edu",
)

SKIP_TITLE_RE = re.compile(r"\((?:Sponsor|\d+\s+minute\s+form)\)\s*$", re.I)
MINUTES_RE = re.compile(r"\s*\((\d+)\s+minute\s+read\)\s*$", re.I)
SKIP_HOST_RE = re.compile(r"(^|\.)(tldr\.tech|typeform\.com|forms\.gle|docs\.google\.com)$")
TRACKING_RE = re.compile(r"^(utm_\w+|ref|ref_src|sp|source)$", re.I)
UA = {"User-Agent": "Mozilla/5.0 (ai-digest; personal RSS reader)"}


def http_get(url: str) -> str:
    r = httpx.get(url, timeout=40, follow_redirects=True, headers=UA)
    r.raise_for_status()
    return r.text


def host_of(url: str) -> str:
    host = urlparse(url).netloc.lower().split(":")[0]
    return host[4:] if host.startswith("www.") else host


def clean_url(url: str) -> str:
    """Ссылка без меток рассылки (utm_*, ref) и без якоря. В вёрстке выпуска бывает «&amp;amp;» — раскрываем."""
    p = urlparse(html.unescape(html.unescape(url.strip())))
    query = urlencode([(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if not TRACKING_RE.match(k)])
    return urlunparse((p.scheme, p.netloc, p.path, p.params, query, ""))


def norm_url(url: str) -> str:
    """Ключ для поиска дублей: домен без www, путь без конечной косой черты, значимые параметры по порядку
    (у некоторых блогов адрес статьи — это параметр: qwen.ai/blog?id=…)."""
    p = urlparse(clean_url(url))
    query = "&".join(sorted(f"{k}={v}" for k, v in parse_qsl(p.query, keep_blank_values=True)))
    return host_of(url) + (p.path.rstrip("/") or "/") + ("?" + query if query else "")


def is_primary(url: str) -> bool:
    host, path = host_of(url), urlparse(url).path
    for entry in PRIMARY:
        dom, _, prefix = entry.partition("/")
        if (host == dom or host.endswith("." + dom)) and path.startswith("/" + prefix if prefix else "/"):
            return True
    return False


def parse_issue(page: str) -> list[dict]:
    """Материалы выпуска: {u: ссылка, t: заголовок, m: минуты чтения, s: раздел}. Рекламу и формы пропускает."""
    soup = BeautifulSoup(page, "html.parser")
    out, seen = [], set()
    for art in soup.find_all("article"):
        a = art.find("a", href=True)
        h = a.find(["h3", "h2"]) if a else None
        if not a or not h:
            continue
        title = " ".join(h.get_text(" ", strip=True).split())
        url = clean_url(a["href"])
        if SKIP_TITLE_RE.search(title) or not url.startswith("http") or SKIP_HOST_RE.search(host_of(url)):
            continue
        if norm_url(url) in seen:
            continue
        seen.add(norm_url(url))
        m = MINUTES_RE.search(title)
        sec = art.find_previous("h3", class_="text-center")
        out.append({"u": url, "t": MINUTES_RE.sub("", title).strip(), "m": int(m.group(1)) if m else None,
                    "s": sec.get_text(" ", strip=True) if sec else ""})
    return out


SECTION_ORDER = ("headlines", "deep dives", "engineering", "research")


def section_rank(section: str) -> int:
    low = section.lower()
    return next((i for i, key in enumerate(SECTION_ORDER) if key in low), len(SECTION_ORDER))


def site_label(url: str) -> str:
    """Подпись первоисточника на странице: домен без www (openai.com, blog.google, engineering.fb.com)."""
    return host_of(url)


class Radar:
    """Выпуски подборок с кэшем в state["radar"][id ленты]["issues"][адрес выпуска] = {date, links}."""

    def __init__(self, state: dict, get=None, now: datetime | None = None):
        self.state = state
        self.get = get or http_get
        self.now = now or datetime.now(timezone.utc)

    def _book(self, feed: dict) -> dict:
        return self.state.setdefault(feed["id"], {}).setdefault("issues", {})

    def refresh(self, feed: dict, since: datetime) -> dict:
        """Разбирает новые выпуски (каждый — один раз) и возвращает все сохранённые."""
        issues = self._book(feed)
        parsed = feedparser.parse(self.get(feed["url"]))
        new = 0
        for e in parsed.entries:
            link = e.get("link", "")
            tm = e.get("published_parsed") or e.get("updated_parsed")
            date = datetime.fromtimestamp(calendar.timegm(tm), timezone.utc) if tm else self.now
            if not link or link in issues or date < since or new >= MAX_NEW_ISSUES:
                continue
            try:
                links = parse_issue(self.get(link))
            except Exception as exc:  # noqa: BLE001
                log.warning("Выпуск %s не разобран: %s", link, exc)
                continue
            new += 1
            if not links:  # вёрстка сменилась или страница пустая — попробуем в следующий раз
                log.warning("В выпуске %s не нашлось ссылок", link)
                continue
            issues[link] = {"date": date.strftime("%Y-%m-%dT%H:%M:%SZ"), "title": e.get("title", ""), "links": links}
            log.info("Радар %s: выпуск %s — ссылок %d, первоисточников %d", feed["id"], link, len(links),
                     sum(is_primary(x["u"]) for x in links))
        edge = (self.now - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
        for link in [k for k, v in issues.items() if v["date"] < edge]:
            del issues[link]
        return issues

    def primary_links(self, feed: dict, since: datetime, cache_since: datetime | None = None) -> list[tuple[datetime, dict]]:
        """Первоисточники из выпусков не старше since: (дата выпуска, ссылка).

        Выпуски разбираются и сохраняются за более долгий срок cache_since: их ссылки нужны пересмотру источников.
        Внутри выпуска сначала главные разделы (запуски, разборы, исследования), «Quick Links» и прочее — в конце:
        сборщик берёт из выпуска не больше feed["max_per_issue"] статей, у которых нашёлся текст."""
        out = []
        for issue in self.refresh(feed, min(since, cache_since or since)).values():
            date = datetime.strptime(issue["date"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            if date < since:
                continue
            picks = sorted((x for x in issue["links"] if is_primary(x["u"])), key=lambda x: section_rank(x["s"]))
            out += [(date, x) for x in picks]
        return out

    def links(self, days: int = 56) -> list[str]:
        """Все ссылки всех подборок за days дней — для поиска часто цитируемых сайтов."""
        edge = (self.now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        return [x["u"] for book in self.state.values() for issue in book.get("issues", {}).values()
                if issue["date"] >= edge for x in issue["links"]]
