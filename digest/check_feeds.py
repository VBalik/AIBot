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
from candidates import CANDIDATES  # noqa: E402
from feeds import FEEDS  # noqa: E402


def check(feed: dict) -> dict:
    row = {"id": feed["id"], "kind": feed.get("kind", "analysis"), "name": feed.get("name", feed["id"])}
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
    # полный текст: медиана больше ~1500 знаков (выдержка из статьи обычно 200–600)
    row["verdict"] = ("полный текст" if row["median"] >= 1500 else "выдержка") + \
                     (", платные" if row["paywalled"] > len(posts) / 2 else "") + \
                     (", редко" if row["last30"] < 2 else "")
    return row


def main() -> None:
    want = set(sys.argv[1:])
    pool = [{**f, "kind": f.get("kind", "analysis")} for f in FEEDS] + CANDIDATES
    pool = [f for f in pool if not want or f["id"] in want]
    rows = [check(f) for f in pool]
    head = "| id | раздел | HTTP | постов | за 30 дней | последний | медиана знаков | макс | платных | вывод |\n" \
           "|---|---|---|---|---|---|---|---|---|---|\n"
    lines = [f"| {r['id']} | {r['kind']} | {r.get('http')} | {r.get('entries', '')} | {r.get('last30', '')} | "
             f"{r.get('latest', '')} | {r.get('median', '')} | {r.get('max', '')} | {r.get('paywalled', '')} | "
             f"{r.get('verdict', 'нет ответа')} |" for r in rows]
    table = head + "\n".join(lines)
    print(table)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write("## Проверка лент\n\n" + table + "\n")


if __name__ == "__main__":
    main()
