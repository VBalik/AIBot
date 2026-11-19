"""Радар TLDR AI: разбор выпуска, кэш выпусков, первоисточники в ленте новостей, поиск сайтов для пересмотра.

    python tests/test_radar.py

Без сети и без настоящих моделей: подборка, статьи и модель подменены.
"""
import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "digest"))

import build  # noqa: E402
import radar  # noqa: E402
import review  # noqa: E402
import telegram  # noqa: E402
from sources import SourceBook  # noqa: E402

NOW = datetime.now(timezone.utc).replace(microsecond=0)
DAY = lambda n: (NOW - timedelta(days=n)).strftime("%Y-%m-%d")


def article(href, title, section_note="", text="Пересказ подборки в два предложения."):
    return f"""<article class="mt-3"><a class="font-bold" href="{href}" target="_blank"><h3>{title}</h3></a>
      <div class="newsletter-html">{text}</div>{section_note}</article>"""


def issue_page(day: str, items: list[tuple[str, str]], quick: list[tuple[str, str]] = ()) -> str:
    """Страница выпуска в вёрстке tldr.tech: разделы — h3.text-center, материалы — <article>."""
    head = f'<h2 class="text-xl text-center pb-1">Выпуск {day}</h2>'
    body = article("https://ref.wisprflow.ai/tldr?sp=1", "Try our app today (Sponsor)")
    body += '<h3 class="text-center font-bold">Headlines &amp; Launches</h3>'
    body += "".join(article(h + "?utm_source=tldrai", t) for h, t in items)
    body += article("https://x.typeform.com/to/abc?utm_source=tldrai", "Join our community (2 minute form)")
    body += '<h3 class="text-center font-bold">Quick Links</h3>'
    body += "".join(article(h + "?utm_source=tldrai", t) for h, t in quick)
    return f"<html><body>{head}{body}</body></html>"


def rss(days: list[str]) -> str:
    items = "".join(f"<item><title>Issue {d}</title><link>https://tldr.tech/ai/{d}</link>"
                    f"<guid>https://tldr.tech/ai/{d}</guid><pubDate>{datetime.strptime(d, '%Y-%m-%d').strftime('%a, %d %b %Y')} 00:00:00 GMT</pubDate></item>"
                    for d in days)
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>TLDR AI RSS Feed</title>{items}</channel></rss>'


PAGES = {
    "https://tldr.tech/api/rss/ai": rss([DAY(0), DAY(1), DAY(6), DAY(80)]),
    # старый выпуск: первоисточники из него не берутся (старше 4 дней), но ссылки хранятся для пересмотра
    f"https://tldr.tech/ai/{DAY(6)}": issue_page(DAY(6), [("https://deepmind.google/blog/old-news/", "Old news (5 minute read)")]),
    f"https://tldr.tech/ai/{DAY(0)}": issue_page(DAY(0), [
        ("https://openai.com/index/inside-the-codex-sandbox/", "Inside the Codex sandbox (28 minute read)"),
        ("https://kernel.dev/blog/why-carbon-is-slow/", "Why is Carbon so slow? (4 minute read)"),
        ("https://blog.google/technology/ai/gemini-neon/", "Gemini Neon (12 minute read)"),
        ("https://www.anthropic.com/research/interp-update", "Interpretability update (9 minute read)"),
        ("https://simonwillison.net/2026/Nov/19/codex/", "Willison on Codex (3 minute read)"),
    ], quick=[
        ("https://epoch.ai/data-insights/compute-growth", "Compute growth (6 minute read)"),
        ("https://engineering.fb.com/2026/11/18/agents/", "Agents at Meta (12 minute read)"),
        ("https://kernel.dev/blog/another-post/", "Another kernel post (5 minute read)"),
    ]),
    f"https://tldr.tech/ai/{DAY(1)}": issue_page(DAY(1), [
        ("https://openai.com/index/inside-the-codex-sandbox/", "Inside the Codex sandbox (28 minute read)"),  # повтор
        ("https://kernel.dev/blog/third/", "Third kernel post (6 minute read)"),
        ("https://mistral.ai/news/large-4", "Mistral Large 4 (5 minute read)"),
    ]),
}
GETS = []


def fake_get(url):
    GETS.append(url)
    if url not in PAGES:
        raise RuntimeError(f"нет страницы {url}")
    return PAGES[url]


LONG = "<p>" + "Полный текст первоисточника о работе модели и об экспериментах. " * 40 + "</p>"
ARTICLES = {
    "https://openai.com/index/inside-the-codex-sandbox/": LONG,
    "https://www.anthropic.com/research/interp-update": LONG,
    "https://epoch.ai/data-insights/compute-growth": LONG,
    "https://engineering.fb.com/2026/11/18/agents/": LONG,
    "https://mistral.ai/news/large-4": LONG,
    # blog.google не отдаёт текст — такой пост не показывается
}


def fake_article(url):
    return ARTICLES.get(url)


def check_parse():
    links = radar.parse_issue(PAGES[f"https://tldr.tech/ai/{DAY(0)}"])
    urls = [x["u"] for x in links]
    assert len(links) == 8, urls  # без рекламы и формы
    assert all("utm_" not in u for u in urls), urls
    first = links[0]
    assert first == {"u": "https://openai.com/index/inside-the-codex-sandbox/", "t": "Inside the Codex sandbox",
                     "m": 28, "s": "Headlines & Launches"}, first
    assert links[-1]["s"] == "Quick Links"
    assert radar.is_primary("https://openai.com/index/x/") and radar.is_primary("https://www.anthropic.com/research/y")
    assert radar.is_primary("https://huggingface.co/blog/post") and not radar.is_primary("https://huggingface.co/models")
    assert not radar.is_primary("https://kernel.dev/blog/x") and not radar.is_primary("https://notopenai.com/x")
    assert radar.clean_url("https://a.dev/p/?utm_source=tldrai&id=7#top") == "https://a.dev/p/?id=7"
    assert radar.norm_url("https://www.a.dev/p/?utm_source=tldrai") == radar.norm_url("https://a.dev/p")
    assert radar.norm_url("https://qwen.ai/blog?id=a") != radar.norm_url("https://qwen.ai/blog?id=b")
    assert radar.clean_url("https://qwen.ai/blog?id=qwen4-max&amp;amp;utm_source=tldrai") == "https://qwen.ai/blog?id=qwen4-max"
    print("радар: разбор выпуска — ok")


class FakeLLM:
    name = "fake"
    label = "Fake"
    models = {"score": "f-score", "abstract": "f-abstract", "translate": "f-translate"}


class FakeWriter:
    """Модель: Mistral получает 6 (ниже порога подборки 7), остальные — 8."""

    def __init__(self):
        self.llm = FakeLLM()
        self.variant = "fake"
        self.seen = []

    def score(self, post):
        self.seen.append(post.feed["name"])
        return (6 if "Mistral" in post.title else 8), "тест"

    def abstract(self, post):
        return {"title_ru": "Перевод: " + post.title, "abstract_ru": "Абстракт. " * 5}

    def translate(self, post):
        assert "Полный текст первоисточника" in post.text, post.title  # переводится статья, а не пересказ подборки
        return "<p>Перевод статьи.</p>"


SEED = [
    {"id": "willison", "kind": "analysis", "symbol": "Sw", "name": "Simon Willison", "author": "С", "about": "s",
     "url": "https://simonwillison.net/atom/entries/", "site": "https://simonwillison.net"},
    {"id": "tldrai", "kind": "news", "late": True, "radar": "tldr", "paywall": False, "fetch_article": True,
     "article_min": 500, "min_score": 7, "max_per_issue": 4, "lookback_days": 4, "symbol": "Tl", "name": "TLDR AI", "author": "редакция TLDR",
     "about": "первоисточники", "url": "https://tldr.tech/api/rss/ai", "site": "https://tldr.tech/ai"},
]
WILLISON_RSS = f"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>SW</title>
<entry><title>Willison on Codex</title><link href="https://openai.com/index/inside-the-codex-sandbox"/>
<id>sw-1</id><updated>{NOW.strftime('%Y-%m-%dT%H:%M:%SZ')}</updated><content type="html">{LONG.replace('<', '&lt;')}</content></entry></feed>"""


def fake_fetch(feed):
    assert not feed.get("radar"), "подборку не читают как обычную ленту"
    return build.parse_feed(feed, WILLISON_RSS)


def make_data():
    root = Path(tempfile.mkdtemp()) / "data"
    data = build.DataDir.__new__(build.DataDir)
    data.root = root
    (root / "full").mkdir(parents=True)
    data.state = {"version": 1, "posts": {}}
    data.crypto = None
    data.items = {}
    data.sources = SourceBook(root / "sources.json", seed=SEED)
    return data


def check_run():
    cfg = build.Config(password="p", lookback_days=10, max_translations_per_run=10, compare_provider="off", compare_model="off")
    data = make_data()
    vault = build.Vault("p", None, 1000)
    w = FakeWriter()
    stats = build.run(cfg, data, w, vault, fetch=fake_fetch, article=fake_article, radar_get=fake_get)
    assert stats["errors"] == 0, data.state["last_run"]
    radar_items = [it for it in data.items.values() if it["source"] == "tldrai"]
    got = sorted(it["via"] for it in radar_items)
    # главные разделы первыми: openai (дубль — уже пришёл из ленты Willison), blog.google (нет текста — сайт закрыт
    # для серверов, место не занимает), anthropic; затем Quick Links: epoch, engineering.fb. Mistral из второго выпуска
    # (оценка 6 < 7) не показан. Лимит max_per_issue считает только статьи с текстом — проверяется ниже отдельно
    assert got == ["anthropic.com", "engineering.fb.com", "epoch.ai"], got
    assert [it["source"] for it in data.items.values() if "openai.com" in it["url"]] == ["willison"]
    assert all(it["full"] == "ok" for it in radar_items), [it["full"] for it in radar_items]
    assert "anthropic.com" in w.seen and "TLDR AI" not in w.seen  # модель видит сайт первоисточника, а не подборку
    statuses = {p["title"]: p["status"] for p in data.state["posts"].values() if p["source"] == "tldrai"}
    assert statuses == {"Inside the Codex sandbox": "dup", "Gemini Neon": "no_text", "Interpretability update": "shown",
                        "Compute growth": "shown", "Agents at Meta": "shown", "Mistral Large 4": "skipped"}, statuses
    # выпуски разобраны и сохранены, в том числе недельной давности; старше 8 недель — не скачивается
    issues = data.state["radar"]["tldrai"]["issues"]
    assert sorted(issues) == sorted(f"https://tldr.tech/ai/{DAY(n)}" for n in (0, 1, 6)), sorted(issues)
    assert [u for u in GETS if "/ai/2" in u] == [f"https://tldr.tech/ai/{DAY(n)}" for n in (0, 1, 6)], GETS
    assert not any("deepmind" in it["url"] for it in data.items.values())
    # перевод зашифрован, а в публичных данных — только ссылки и заголовки
    blob = json.loads((data.root / "full" / f"{radar_items[0]['id']}.json").read_text())
    assert "Перевод статьи" in vault.decrypt_json(blob)["html"]
    assert "Полный текст первоисточника" not in json.dumps(data.state, ensure_ascii=False)

    # второй прогон: выпуски не скачиваются заново, новых постов нет
    GETS.clear()
    stats2 = build.run(cfg, data, w, vault, fetch=fake_fetch, article=fake_article, radar_get=fake_get)
    assert stats2["new"] == 0 and GETS == ["https://tldr.tech/api/rss/ai"], (stats2, GETS)

    # Telegram и страница подписывают первоисточник
    msg = telegram.build_messages(radar_items[0], data.sources.get("tldrai"), None, "https://x.test")[0]
    assert f"{radar_items[0]['via']} через TLDR AI" in msg, msg

    # лимит: из выпуска не больше max_per_issue статей с текстом
    data2 = make_data()
    data2.sources.get("tldrai")["max_per_issue"] = 2
    build.run(cfg, data2, FakeWriter(), vault, fetch=fake_fetch, article=fake_article, radar_get=fake_get)
    got2 = sorted(it["via"] for it in data2.items.values() if it["source"] == "tldrai")
    assert got2 == ["anthropic.com", "epoch.ai"], got2  # engineering.fb.com — третья с текстом, не взята
    print("радар: первоисточники в новостях, дубли, без текста, порог, кэш выпусков — ok")
    return data


def check_discover(data):
    rd = radar.Radar(data.state["radar"])
    links = rd.links(56)
    assert sum("kernel.dev" in u for u in links) == 3, links
    found = review.discover([], data.sources, {"simonwillison.net"}, extra_links=links,
                            autodiscover=lambda host: f"https://{host}/feed.xml")
    hosts = [c["name"] for c in found]
    assert hosts == ["kernel.dev"], hosts  # три упоминания за 8 недель; лаборатории (openai.com и т. п.) — не блоги
    print("радар: сайты, на которые часто ссылается подборка, — кандидаты пересмотра — ok")


def check_late_seed():
    root = Path(tempfile.mkdtemp())
    path = root / "sources.json"
    old = {"version": 1, "news_seeded": True, "removed": [], "history": [], "candidates": [], "last_review": None,
           "active": [{**{k: SEED[0][k] for k in ("id", "kind", "symbol", "name", "author", "about", "url", "site")},
                       "hue": 196, "added": "2026-11-17T00:00:00Z", "origin": "seed"}]}
    path.write_text(json.dumps(old))
    book = SourceBook(path, seed=SEED)
    assert book.ids() == {"willison", "tldrai"} and book.get("tldrai")["radar"] == "tldr"
    assert book.get("tldrai")["hue"] != 196
    book.remove("tldrai", "тест")
    book.save()
    again = SourceBook(path, seed=SEED)
    assert again.ids() == {"willison"}, again.ids()  # убранный не возвращается сам
    print("источники: дописанный позже начальный источник добавляется один раз — ok")


check_parse()
d = check_run()
check_discover(d)
check_late_seed()
print("ВСЕ ПРОВЕРКИ РАДАРА ПРОЙДЕНЫ")
