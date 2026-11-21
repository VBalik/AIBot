"""Список источников как данные: активные блоги, убранные и история замен.

Раньше список жил только в feeds.py. Теперь feeds.py — начальный набор, а действующий список лежит в ветке data
(sources.json), потому что раз в неделю сборщик сам заменяет источники (см. review.py).

sources.json:
    active   — действующие источники: поля ленты (id, symbol, name, author, about, url, site, exclude, paywall)
               плюс hue (цвет плитки), added (когда добавлен), origin (seed | review | restore), why (почему добавлен)
    removed  — убранные: те же поля плюс removed и why_removed
    history  — события: {at, kind: added|removed|restored|suggested, id, name, why}
    candidates — итог последнего еженедельного пересмотра (лучшие кандидаты с оценками)
    last_review — когда был пересмотр

Подробности о чтении и оценках Валерия сюда не пишутся: ветка data публичная. В причинах — только общие слова.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from feeds import FEEDS

# Тот же набор оттенков, что был на странице: у исходных источников цвета плиток не меняются
HUES = [196, 24, 148, 262, 336, 88, 178, 46, 296, 222, 8, 120, 60, 280, 100, 240, 32, 160, 316, 72]
SEED_DATE = "2026-11-17T00:00:00Z"
FEED_FIELDS = ("id", "kind", "symbol", "name", "author", "about", "url", "site", "exclude", "include", "paywall",
               "fetch_article", "max_chars", "article_min", "radar", "min_score", "max_per_issue", "lookback_days")
PUBLIC_FIELDS = ("id", "kind", "symbol", "name", "author", "about", "site", "hue", "added", "origin", "why")
KINDS = ("news", "analysis")  # новости (что произошло) и аналитика (что это значит)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class SourceBook:
    def __init__(self, path: Path, seed: list[dict] = FEEDS):
        self.path = path
        try:
            self.data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            self.data = {
                "version": 1,
                "active": [{**{k: f[k] for k in FEED_FIELDS if k in f}, "hue": HUES[i % len(HUES)],
                            "added": SEED_DATE, "origin": "seed"} for i, f in enumerate(seed)],
                "removed": [], "history": [], "candidates": [], "last_review": None,
            }
        for key, default in (("removed", []), ("history", []), ("candidates", []), ("last_review", None)):
            self.data.setdefault(key, default)
        for s in self.data["active"] + self.data["removed"]:
            s.setdefault("kind", "analysis")  # первые 11 блогов — аналитика
        # новые начальные источники добавляются в уже существующий список один раз: раздел новостей целиком
        # (news_seeded), а источники, дописанные в feeds.py позже (late), — каждый по отдельности (late_seeded).
        # Убранный при пересмотре так и остаётся убранным.
        known = {s["id"] for s in self.data["active"] + self.data["removed"]}
        late_done = self.data.setdefault("late_seeded", [])
        for f in seed:
            if f["id"] in known or f["id"] in late_done:
                continue
            if f.get("late") or (f.get("kind") == "news" and not self.data.get("news_seeded")):
                self.data["active"].append({**{k: f[k] for k in FEED_FIELDS if k in f}, "hue": self._free_hue(),
                                            "added": _now_iso(), "origin": "seed"})
                known.add(f["id"])
        late_done += [f["id"] for f in seed if f.get("late") and f["id"] not in late_done]
        if any(f.get("kind") == "news" for f in seed):
            self.data["news_seeded"] = True
        # у начальных источников поля ленты берутся из кода: правка feeds.py (адрес, фильтр, лимит) доходит до данных
        by_id = {f["id"]: f for f in seed}
        for s in self.data["active"]:
            f = by_id.get(s["id"])
            if f and s.get("origin") == "seed":
                for k in FEED_FIELDS:
                    if k in f:
                        s[k] = f[k]
                    elif k not in ("id", "kind"):
                        s.pop(k, None)

    # --- чтение ---
    def active(self) -> list[dict]:
        return self.data["active"]

    def get(self, sid: str) -> dict | None:
        for s in self.data["active"]:
            if s["id"] == sid:
                return s
        for s in reversed(self.data["removed"]):
            if s["id"] == sid:
                return s
        return None

    def of_kind(self, kind: str) -> list[dict]:
        return [s for s in self.data["active"] if s.get("kind", "analysis") == kind]

    def ids(self) -> set[str]:
        return {s["id"] for s in self.data["active"]}

    def known_ids(self) -> set[str]:
        return self.ids() | {s["id"] for s in self.data["removed"]}

    def _icon(self, sid: str) -> dict:
        """Фирменный значок (digest/icons.py): путь в ветке data с хэшем для сброса кэша."""
        m = self.data.get("icons", {}).get(sid) or {}
        return {"icon": f"{m['file']}?v={m['v']}"} if m.get("file") else {}

    def public(self, referenced: set[str]) -> list[dict]:
        """Метаданные для feed.json: активные плюс убранные, чьи посты ещё лежат в ленте."""
        out = [{k: s[k] for k in PUBLIC_FIELDS if k in s} | {"active": True} | self._icon(s["id"]) for s in self.data["active"]]
        seen = {s["id"] for s in out}
        for s in reversed(self.data["removed"]):
            if s["id"] in referenced and s["id"] not in seen:
                out.append({k: s[k] for k in PUBLIC_FIELDS if k in s} | {"active": False, "removed": s.get("removed")}
                           | self._icon(s["id"]))
                seen.add(s["id"])
        return out

    # --- изменения ---
    def _free_hue(self, prefer: int | None = None) -> int:
        used = {s.get("hue") for s in self.data["active"]}
        for h in HUES:
            if h not in used:
                return h
        return prefer if prefer is not None else HUES[len(self.data["active"]) % len(HUES)]

    def _log(self, kind: str, src: dict, why: str, at: str) -> None:
        self.data["history"].append({"at": at, "kind": kind, "id": src["id"], "name": src.get("name", src["id"]),
                                     "why": why})
        self.data["history"] = self.data["history"][-100:]

    def add(self, feed: dict, why: str, origin: str = "review", at: str | None = None) -> dict:
        at = at or _now_iso()
        if feed["id"] in self.ids():
            raise ValueError(f"источник {feed['id']} уже в списке")
        old = self.get(feed["id"])  # возвращаем убранный — с прежним цветом, если он свободен
        prefer = old.get("hue") if old else None
        used = {s.get("hue") for s in self.data["active"]}
        hue = prefer if prefer is not None and prefer not in used else self._free_hue()
        src = {**{k: feed[k] for k in FEED_FIELDS if k in feed}, "hue": hue, "added": at, "origin": origin, "why": why}
        self.data["active"].append(src)
        self.data["removed"] = [s for s in self.data["removed"] if s["id"] != feed["id"]]
        self._log("restored" if origin == "restore" else "added", src, why, at)
        return src

    def remove(self, sid: str, why: str, at: str | None = None) -> dict:
        at = at or _now_iso()
        src = next(s for s in self.data["active"] if s["id"] == sid)
        self.data["active"] = [s for s in self.data["active"] if s["id"] != sid]
        self.data["removed"].append({**src, "removed": at, "why_removed": why})
        self.data["removed"] = self.data["removed"][-60:]
        self._log("removed", src, why, at)
        return src

    def suggest(self, cand: dict, why: str, at: str | None = None) -> None:
        self._log("suggested", cand, why, at or _now_iso())

    def save(self) -> None:
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)
