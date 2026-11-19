"""Проверка лент с серверов GitHub Actions: отвечает ли лента, есть ли полный текст, как часто выходят посты.

    python digest/check_feeds.py            # действующие источники и все кандидаты
    python digest/check_feeds.py decoder    # только перечисленные id

Итог печатается таблицей в журнал и в сводку прогона (GITHUB_STEP_SUMMARY).
"""
from __future__ import annotations

import os
import statistics
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import httpx  # noqa: E402

import build  # noqa: E402
import radar  # noqa: E402
from candidates import CANDIDATES  # noqa: E402
from feeds import FEEDS  # noqa: E402


def check_radar(feed: dict, row: dict) -> dict:
    """Подборка (TLDR AI): разбираются ли выпуски и отдают ли первоисточники текст со страницы."""
    now = build._now()
    rd = radar.Radar({}, now=now)
    try:
        links = rd.primary_links(feed, now - timedelta(days=7))
    except Exception as exc:  # noqa: BLE001
        row["http"] = type(exc).__name__
        return row
    issues = rd.state.get(feed["id"], {}).get("issues", {})
    row.update(http=200, entries=len(issues), last30=len(issues), latest=max((v["date"][:10] for v in issues.values()), default="—"),
               median=sum(len(v["links"]) for v in issues.values()), max=len(links), paywalled=0)
    got = []
    for _, x in links[:6]:
        try:
            full = build.fetch_article(x["u"])
            got.append(len(build.BeautifulSoup(full, "html.parser").get_text()) if full else 0)
        except Exception:  # noqa: BLE001
            got.append(-1)
        print(f"  {feed['id']}: {got[-1]:>6} знаков — {x['u']}")
    row["article"] = "/".join(map(str, got))
    ok = sum(n >= feed.get("article_min", 500) for n in got)
    row["verdict"] = f"подборка: выпусков {len(issues)}, ссылок {row['median']}, первоисточников {len(links)}, с текстом {ok} из {len(got)}"
    return row


def check(feed: dict) -> dict:
    row = {"id": feed["id"], "kind": feed.get("kind", "analysis"), "name": feed.get("name", feed["id"])}
    if feed.get("radar"):
        return check_radar(feed, row)
    try:
        r = httpx.get(feed["url"], timeout=30, follow_redirects=True,
                      headers={"User-Agent": "Mozilla/5.0 (ai-digest; personal RSS reader)"})
        row["http"] = r.status_code
        if r.status_code != 200:
            return row
        posts = build.parse_feed(feed, r.content)
    except Exception as exc:  # noqa: BLE001
        row["http"] = type(exc).__name__
        return row
    now = build._now()
    lens = [len(p.text) for p in posts]
    row.update(
        entries=len(posts),
        last30=sum(p.published >= now - timedelta(days=30) for p in posts),
        latest=max((p.published for p in posts), default=None),
        median=int(statistics.median(lens)) if lens else 0,
        max=max(lens, default=0),
        paywalled=sum(p.paywalled for p in posts),
    )
    row["latest"] = row["latest"].strftime("%Y-%m-%d") if row["latest"] else "—"
    if posts and row["median"] < 1500:  # выдержка — получится ли взять полный текст со страницы статьи
        got = []
        for p in posts[:3]:
            try:
                full = build.fetch_article(p.url)
                got.append(len(build.BeautifulSoup(full, "html.parser").get_text()) if full else 0)
            except Exception:  # noqa: BLE001
                got.append(-1)
        row["article"] = "/".join(map(str, got))
    # полный текст: медиана больше ~1500 знаков (выдержка из статьи обычно 200–600)
    row["verdict"] = ("полный текст" if row["median"] >= 1500 else "выдержка") + \
                     (", платные" if row["paywalled"] > len(posts) / 2 else "") + \
                     (", редко" if row["last30"] < 2 else "")
    return row


def main() -> None:
    want = set(sys.argv[1:])
    pool = [{**f, "kind": f.get("kind", "analysis")} for f in FEEDS] + CANDIDATES
    seen, uniq = set(), []
    for f in pool:  # кандидат, ставший источником, не проверять дважды
        if f["id"] not in seen and (not want or f["id"] in want):
            seen.add(f["id"])
            uniq.append(f)
    pool = uniq
    rows = [check(f) for f in pool]
    head = "| id | раздел | HTTP | постов | за 30 дней | последний | медиана знаков | макс | платных | со страницы | вывод |\n" \
           "|---|---|---|---|---|---|---|---|---|---|---|\n"
    lines = [f"| {r['id']} | {r['kind']} | {r.get('http')} | {r.get('entries', '')} | {r.get('last30', '')} | "
             f"{r.get('latest', '')} | {r.get('median', '')} | {r.get('max', '')} | {r.get('paywalled', '')} | "
             f"{r.get('article', '')} | "
             f"{r.get('verdict', 'нет ответа')} |" for r in rows]
    table = head + "\n".join(lines)
    print(table)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write("## Проверка лент\n\n" + table + "\n")


if __name__ == "__main__":
    main()
