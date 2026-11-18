"""Еженедельный пересмотр источников: в каждом разделе (новости, аналитика) отдельно.

1. Кандидаты: курируемый список (candidates.py) и блоги, на которые часто ссылаются действующие источники.
2. Отсев без модели: лента жива, посты выходят, полный текст (или его можно взять со страницы), не платная,
   посты не гигантские.
3. Модель оценивает авторитетность, глубину, соответствие интересам Валерия (и его 👍/👎) и повтор уже читаемого.
4. Если лучший кандидат раздела сильнее порога, он заменяет наименее читаемый и хуже оценённый источник того же
   раздела. Не трогаются закреплённые (📌) и добавленные меньше трёх недель назад; без данных о чтении никто не
   убирается, кандидат только предлагается. Не больше одной замены на раздел в неделю.

Заодно выполняются просьбы «вернуть источник» со страницы (restore в файлах устройств).
"""
from __future__ import annotations

import logging
import re
import statistics
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

import feedback as fbmod
from candidates import CANDIDATES
from sources import KINDS, SourceBook

log = logging.getLogger("ai-digest")

KIND_RU = {"news": "новости", "analysis": "аналитика"}
# Сайты, которые цитируют часто, но это не блоги для дайджеста
NOT_BLOGS = re.compile(
    r"(^|\.)(x\.com|twitter\.com|github\.com|arxiv\.org|youtube\.com|youtu\.be|wikipedia\.org|google\.com|"
    r"linkedin\.com|reddit\.com|news\.ycombinator\.com|huggingface\.co|openai\.com|anthropic\.com|"
    r"deepmind\.google|microsoft\.com|apple\.com|amazon\.com|meta\.com|facebook\.com|instagram\.com|"
    r"bsky\.app|threads\.net|nytimes\.com|wsj\.com|ft\.com|bloomberg\.com|reuters\.com|cnbc\.com|"
    r"theverge\.com|techcrunch\.com|wired\.com|arstechnica\.com|substack\.com|medium\.com|"
    r"docs\.google\.com|drive\.google\.com|apps\.apple\.com|play\.google\.com|discord\.(gg|com)|"
    r"doi\.org|nature\.com|science\.org|ssrn\.com|openreview\.net|paperswithcode\.com|kaggle\.com|"
    r"spotify\.com|podcasts\.apple\.com|simonwillison\.net|platform\.openai\.com|status\.\w+)$", re.I)

REVIEW_TOOL = {
    "name": "rate_source",
    "description": "Оценить блог или новостную ленту как источник для персонального дайджеста об ИИ.",
    "input_schema": {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": ["news", "analysis"],
                     "description": "news — сообщает, что произошло; analysis — разборы, эксперименты, мнения, прогнозы"},
            "authority": {"type": "integer", "minimum": 1, "maximum": 10,
                          "description": "Авторитетность автора или редакции в теме ИИ"},
            "insight": {"type": "integer", "minimum": 1, "maximum": 10,
                        "description": "Глубина и оригинальность: собственный анализ и эксперименты, а не пересказ"},
            "fit": {"type": "integer", "minimum": 1, "maximum": 10,
                    "description": "Насколько темы совпадают с интересами читателя и его оценками"},
            "overlap": {"type": "integer", "minimum": 1, "maximum": 10,
                        "description": "Насколько повторяет то, что читатель уже получает из действующих источников"},
            "name": {"type": "string", "description": "Название блога или издания (как у автора, обычно по-английски)"},
            "author_ru": {"type": "string", "description": "Автор(ы) или редакция по-русски, коротко"},
            "about_ru": {"type": "string",
                         "description": "Одна фраза по-русски без точки в конце: почему стоит читать (как у действующих источников)"},
            "symbol": {"type": "string", "description": "Двухбуквенный символ для плитки: заглавная + строчная, например Lw"},
            "verdict_ru": {"type": "string", "description": "1–2 предложения по-русски: главный довод за или против"},
        },
        "required": ["kind", "authority", "insight", "fit", "overlap", "name", "author_ru", "about_ru", "symbol",
                     "verdict_ru"],
    },
}


def _iso(d: datetime) -> str:
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def due(book: SourceBook, every_days: int, now: datetime) -> bool:
    last = book.data.get("last_review")
    return not last or _parse(last) <= now - timedelta(days=every_days) + timedelta(hours=1)


def apply_restores(book: SourceBook, fb: fbmod.Feedback) -> list[str]:
    """Возвращает убранные источники, которые Валерий попросил вернуть (кнопка на странице)."""
    back = []
    for sid, at in fb.restore.items():
        src = next((s for s in book.data["removed"] if s["id"] == sid), None)
        if src and at > src.get("removed", ""):
            book.add(src, why="возвращён по вашей просьбе", origin="restore")
            back.append(sid)
    return back


def host_key(url: str) -> str | None:
    try:
        host = urlparse(url).netloc.lower().split(":")[0]
    except ValueError:
        return None
    if host.startswith("www."):
        host = host[4:]
    if not host or "." not in host:
        return None
    if host.endswith(".substack.com"):
        return host  # у каждой рассылки свой поддомен
    return None if NOT_BLOGS.search(host) else host


def discover(posts: list, book: SourceBook, known_hosts: set[str], min_posts: int = 3, limit: int = 4,
             autodiscover=None) -> list[dict]:
    """Блоги, на которые ссылаются не меньше min_posts разных постов действующих источников."""
    cites: dict[str, set[str]] = {}
    for p in posts:
        own = host_key(p.feed.get("site", "")) or ""
        for a in BeautifulSoup(p.html or "", "html.parser").find_all("a", href=True):
            h = host_key(a["href"])
            if h and h != own and h not in known_hosts:
                cites.setdefault(h, set()).add(p.id)
    ranked = sorted(((len(v), h) for h, v in cites.items() if len(v) >= min_posts), reverse=True)
    found = []
    for n, host in ranked[: limit * 2]:
        url = (autodiscover or find_feed)(host)
        if not url:
            continue
        base = host[: -len(".substack.com")] if host.endswith(".substack.com") else host.rsplit(".", 1)[0]
        slug = re.sub(r"[^a-z0-9]", "", base.split(".")[-1])
        found.append({"id": slug or re.sub(r"[^a-z0-9]", "", host), "kind": None, "name": host, "url": url,
                      "site": f"https://{host}", "discovered": n})
        if len(found) >= limit:
            break
    return found


def find_feed(host: str) -> str | None:
    """RSS/Atom по ссылке <link rel=alternate> на главной странице сайта."""
    if host.endswith(".substack.com"):
        return f"https://{host}/feed"
    try:
        r = httpx.get(f"https://{host}/", timeout=20, follow_redirects=True,
                      headers={"User-Agent": "Mozilla/5.0 (ai-digest; personal RSS reader)"})
        if r.status_code != 200:
            return None
        for link in BeautifulSoup(r.text, "html.parser").find_all("link", rel="alternate"):
            if re.search(r"(rss|atom)\+xml", link.get("type", "")) and link.get("href"):
                return urljoin(str(r.url), link["href"])
    except httpx.HTTPError:
        return None
    return None


def screen(cfg, cand: dict, posts: list, now: datetime, complete=None) -> tuple[bool, str]:
    """Отсев без модели. Может включить у кандидата fetch_article, если полный текст есть только на странице."""
    recent = [p for p in posts if p.published >= now - timedelta(days=45)]
    need = 6 if cand.get("kind") == "news" else 2
    if len(recent) < need:
        return False, f"мало новых постов ({len(recent)} за 45 дней)"
    lens = [len(p.text) for p in recent]
    med = statistics.median(lens)
    if med < 1500 and complete is not None:
        trial = {**cand, "fetch_article": True}
        sample = recent[:2]
        for p in sample:
            p.feed = trial
            complete(p)
        if statistics.median(len(p.text) for p in sample) >= 1500:
            cand["fetch_article"] = True
            med = statistics.median(len(p.text) for p in sample)
    if med < 1500:
        return False, "в ленте только начало статей"
    if sum(p.paywalled for p in recent) > len(recent) / 2:
        return False, "больше половины постов платные"
    if med > cfg.max_translate_chars:
        return False, "посты слишком длинные для перевода"
    return True, ""


def taste(fb: fbmod.Feedback, data) -> tuple[list[str], list[str]]:
    titles = {pid: (it.get("title_ru") or it.get("title_en")) for pid, it in data.items.items()}
    for pid, p in data.state.get("posts", {}).items():
        titles.setdefault(pid, p.get("title"))
    liked = [titles[p] for p, r in sorted(fb.ratings.items(), key=lambda x: x[1]["at"], reverse=True)
             if r["v"] > 0 and titles.get(p)][:10]
    disliked = [titles[p] for p, r in sorted(fb.ratings.items(), key=lambda x: x[1]["at"], reverse=True)
                if r["v"] < 0 and titles.get(p)][:10]
    return liked, disliked


def judge(cfg, llm, cand: dict, posts: list, book: SourceBook, liked: list[str], disliked: list[str]) -> dict:
    current = "\n".join(f"- [{KIND_RU[s.get('kind', 'analysis')]}] {s['name']} — {s.get('about', '')}"
                        for s in book.active())
    sample = "\n\n".join(f"### {p.title} ({p.published:%Y-%m-%d})\n{p.text[:1200]}" for p in posts[:5])
    system = ("Ты подбираешь источники для персонального дайджеста лучших англоязычных текстов об ИИ. "
              "Читатель — предприниматель и учёный, читает переводы на русский. Его интересы:\n"
              f"{cfg.interests}\n\nОценивай строго и честно: высокие баллы — только действительно сильным авторам "
              "и изданиям, которых стоит читать регулярно.")
    user = (f"Кандидат: {cand.get('name')} ({cand.get('site')})\n"
            f"Предполагаемый раздел: {KIND_RU.get(cand.get('kind') or '', 'не известен')}\n\n"
            f"Действующие источники:\n{current}\n\n"
            f"Читателю понравились: {'; '.join(liked) or 'пока нет оценок'}\n"
            f"Не понравились: {'; '.join(disliked) or 'пока нет оценок'}\n\n"
            f"Последние посты кандидата (начало текста):\n\n{sample}")
    out = llm.structured("abstract", system, REVIEW_TOOL, user, 900)
    clamp = lambda v: max(1, min(10, int(v)))
    a, i, f, o = (clamp(out[k]) for k in ("authority", "insight", "fit", "overlap"))
    out["total"] = round((a + i + 2 * f) / 4 - 0.5 * max(0, o - 6), 2)
    return out


def _symbol(want: str, name: str, taken: set[str]) -> str:
    want = (want or "").strip()
    tries = [want[:1].upper() + want[1:2].lower()] if len(want) >= 2 else []
    letters = re.sub(r"[^A-Za-z]", "", name) or "Xx"
    tries += [letters[0].upper() + c.lower() for c in letters[1:]] + [letters[0].upper() + c for c in "abcdefghiklmnoprstuvwxyz"]
    return next((t for t in tries if t and t not in taken), "Zz")


def weekly_review(cfg, data, fb: fbmod.Feedback, llm, fetch, complete=None, autodiscover=None,
                  now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    book: SourceBook = data.sources
    summary = {"evaluated": 0, "added": [], "removed": [], "suggested": []}

    # действующие источники: свежие посты — для поиска часто цитируемых блогов
    active_posts = []
    for s in book.active():
        try:
            active_posts += fetch(s)
        except Exception as exc:  # noqa: BLE001
            log.info("Пересмотр: лента %s недоступна: %s", s["name"], exc)
    known_hosts = {host_key(s.get("site", "")) for s in book.active() + book.data["removed"]} | \
                  {host_key(c["site"]) for c in CANDIDATES}
    pool = [dict(c) for c in CANDIDATES] + discover(active_posts, book, known_hosts - {None}, autodiscover=autodiscover)

    recent_removed = {s["id"] for s in book.data["removed"]
                      if s.get("removed", "") >= _iso(now - timedelta(days=90))}
    evaluated_at = book.data.setdefault("evaluated", {})
    pool = [c for c in pool if c["id"] not in book.ids() and c["id"] not in recent_removed
            and evaluated_at.get(c["id"], "") < _iso(now - timedelta(days=56))]
    pool.sort(key=lambda c: evaluated_at.get(c["id"], ""))  # сначала те, кого давно (или ни разу) не смотрели

    liked, disliked = taste(fb, data)
    results = []
    # бюджет оценок моделью делится поровну между разделами (найденные по ссылкам блоги — в аналитику)
    budget = {"news": cfg.review_max_candidates // 2, "analysis": cfg.review_max_candidates - cfg.review_max_candidates // 2}
    for cand in pool:
        slot = cand.get("kind") or "analysis"
        if budget[slot] <= 0:
            if all(v <= 0 for v in budget.values()):
                break
            continue
        try:
            posts = fetch(cand)
        except Exception as exc:  # noqa: BLE001
            log.info("Пересмотр: кандидат %s недоступен: %s", cand["id"], exc)
            evaluated_at[cand["id"]] = _iso(now)
            continue
        ok, why = screen(cfg, cand, posts, now, complete)
        evaluated_at[cand["id"]] = _iso(now)
        if not ok:
            log.info("Пересмотр: %s отсеян — %s", cand["id"], why)
            continue
        budget[slot] -= 1
        try:
            verdict = judge(cfg, llm, cand, posts, book, liked, disliked)
        except Exception as exc:  # noqa: BLE001
            log.warning("Пересмотр: оценка %s не удалась: %s", cand["id"], exc)
            continue
        summary["evaluated"] += 1
        cand["kind"] = cand.get("kind") or verdict["kind"]
        results.append((verdict["total"], cand, verdict))
        log.info("Пересмотр: %s [%s] %.2f — %s", cand["id"], cand["kind"], verdict["total"], verdict["verdict_ru"])

    results.sort(key=lambda x: x[0], reverse=True)
    # кандидаты: свежие оценки плюс прежние (если кандидат ещё не источник), лучшие по оценке
    fresh = [{"id": c["id"], "kind": c["kind"], "name": v.get("name") or c["name"], "site": c["site"],
              "total": t, "verdict": v["verdict_ru"], "at": _iso(now)} for t, c, v in results]
    seen = {c["id"] for c in fresh}
    keep = [c for c in book.data.get("candidates", []) if c["id"] not in seen and c["id"] not in book.ids()]
    book.data["candidates"] = sorted(fresh + keep, key=lambda c: c["total"], reverse=True)[:10]

    stats = fbmod.source_stats(fb, data.state.get("posts", {}), data.items, now=now)
    pinned = fb.pinned()
    grace = _iso(now - timedelta(days=cfg.review_grace_days))
    for kind in KINDS:
        best = next(((t, c, v) for t, c, v in results if c["kind"] == kind), None)
        if not best or best[0] < cfg.review_min_score:
            continue
        total, cand, v = best
        eligible = [s for s in book.of_kind(kind)
                    if s["id"] not in pinned and s.get("added", "") <= grace and stats.get(s["id"], {}).get("shown", 0) >= 3]
        weakest = min(eligible, key=lambda s: stats[s["id"]]["score"], default=None)
        w = stats.get(weakest["id"]) if weakest else None
        if fb.empty or not weakest or not (w["score"] < cfg.review_keep_score or w["dislikes"] > w["likes"]):
            why = f"сильный кандидат в раздел «{KIND_RU[kind]}» ({total:.1f} из 10): {v['verdict_ru']}"
            book.suggest({"id": cand["id"], "name": v.get("name") or cand["name"]}, why, _iso(now))
            summary["suggested"].append(cand["id"])
            continue
        feed = {k: cand[k] for k in ("url", "site", "paywall", "exclude", "include", "fetch_article") if k in cand}
        taken = {s.get("symbol") for s in book.active()}
        feed.update(id=cand["id"], kind=kind, name=v.get("name") or cand["name"], author=v["author_ru"],
                    about=v["about_ru"].rstrip(". "), symbol=_symbol(v.get("symbol", ""), v.get("name") or cand["name"], taken))
        book.remove(weakest["id"], why=f"в разделе «{KIND_RU[kind]}» его меньше всего читали и хуже всего оценивали",
                    at=_iso(now))
        book.add(feed, why=f"{v['verdict_ru']} (оценка {total:.1f} из 10)", origin="review", at=_iso(now))
        summary["removed"].append(weakest["id"])
        summary["added"].append(cand["id"])
        log.info("Пересмотр: %s заменяет %s в разделе «%s»", cand["id"], weakest["id"], KIND_RU[kind])

    book.data["last_review"] = _iso(now)
    return summary
