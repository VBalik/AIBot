"""Оценки и статистика чтения Валерия.

Страница (а потом и приложение) на каждом устройстве ведёт свой файл devices/<id>.json в закрытом репозитории
VBalik/AIBot-feedback и записывает его через GitHub API. Свой файл у каждого устройства — значит, iPhone и iPad
никогда не перезаписывают друг друга; сборщик и страница сводят файлы вместе.

Формат файла устройства:
    {"version": 1, "device": "d-…", "name": "iPhone", "updated_at": "…",
     "ratings": {"<post id>": {"v": 1 | -1 | 0, "at": "…", "s": "<source id>"}},
     "reads":   {"<post id>": {"s": "<source id>", "first": "…", "last": "…", "sec": 120, "p": 0.85}},
     "pins":    {"<source id>": {"v": true, "at": "…"}},
     "restore": {"<source id>": "…"}}

Сводка: оценка — самая поздняя по времени; чтение — секунды складываются, дочитанность — максимум.
Сюда попадают только данные, ветка data (публичная) получает лишь общие выводы.
"""
from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import httpx

log = logging.getLogger("ai-digest")
DEFAULT_REPO = "VBalik/AIBot-feedback"


@dataclass
class Feedback:
    ratings: dict = field(default_factory=dict)   # pid -> {"v", "at", "s"}
    reads: dict = field(default_factory=dict)     # pid -> {"s", "first", "last", "sec", "p"}
    pins: dict = field(default_factory=dict)      # sid -> {"v", "at"}
    restore: dict = field(default_factory=dict)   # sid -> at
    devices: int = 0

    def merge(self, doc: dict) -> None:
        self.devices += 1
        for pid, r in (doc.get("ratings") or {}).items():
            if not isinstance(r, dict) or r.get("v") not in (-1, 0, 1):
                continue
            cur = self.ratings.get(pid)
            if cur is None or str(r.get("at", "")) > str(cur.get("at", "")):
                self.ratings[pid] = {"v": r["v"], "at": str(r.get("at", "")), "s": r.get("s")}
        for pid, r in (doc.get("reads") or {}).items():
            if not isinstance(r, dict):
                continue
            cur = self.reads.setdefault(pid, {"s": r.get("s"), "first": r.get("first"), "last": r.get("last"),
                                              "sec": 0, "p": 0.0})
            cur["s"] = cur.get("s") or r.get("s")
            cur["sec"] += max(0, int(r.get("sec") or 0))
            cur["p"] = max(cur["p"], min(1.0, float(r.get("p") or 0)))
            if r.get("first") and (not cur.get("first") or r["first"] < cur["first"]):
                cur["first"] = r["first"]
            if r.get("last") and (not cur.get("last") or r["last"] > cur["last"]):
                cur["last"] = r["last"]
        for sid, r in (doc.get("pins") or {}).items():
            cur = self.pins.get(sid)
            if isinstance(r, dict) and (cur is None or str(r.get("at", "")) > str(cur.get("at", ""))):
                self.pins[sid] = {"v": bool(r.get("v")), "at": str(r.get("at", ""))}
        for sid, at in (doc.get("restore") or {}).items():
            if str(at) > str(self.restore.get(sid, "")):
                self.restore[sid] = str(at)

    def pinned(self) -> set[str]:
        return {sid for sid, r in self.pins.items() if r.get("v")}

    @property
    def empty(self) -> bool:
        return not (self.ratings or self.reads)


def load(token: str, repo: str = DEFAULT_REPO, http: httpx.Client | None = None) -> Feedback:
    """Читает все файлы устройств. Без токена или при ошибке — пустая сводка (пересмотр тогда ничего не убирает)."""
    fb = Feedback()
    if not token:
        return fb
    http = http or httpx.Client(timeout=30)
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28"}
    r = http.get(f"https://api.github.com/repos/{repo}/contents/devices", headers=headers)
    if r.status_code == 404:
        return fb  # ни одно устройство ещё ничего не записало
    if r.status_code in (401, 403):
        raise RuntimeError(f"Оценки: нет доступа к {repo} (HTTP {r.status_code}) — проверьте секрет FEEDBACK_TOKEN")
    r.raise_for_status()
    for entry in r.json():
        if entry.get("type") != "file" or not entry["name"].endswith(".json"):
            continue
        f = http.get(f"https://api.github.com/repos/{repo}/contents/{entry['path']}", headers=headers)
        f.raise_for_status()
        try:
            fb.merge(json.loads(base64.b64decode(f.json()["content"]).decode("utf-8")))
        except (ValueError, KeyError) as exc:
            log.warning("Оценки: файл %s не прочитан: %s", entry["path"], exc)
    return fb


def source_stats(fb: Feedback, posts_state: dict, items: dict, days: int = 56,
                 now: datetime | None = None) -> dict[str, dict]:
    """Сводка по источникам за последние недели: сколько показано, открыто, дочитано, 👍 и 👎, и итоговый балл.

    Балл = (открыто + дочитано + 2·👍 − 3·👎 + 1) / (показано + 2): доля текстов, которые вызвали интерес,
    со сглаживанием, чтобы источник с двумя постами не оказался ни лучшим, ни худшим случайно.
    """
    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    stats: dict[str, dict] = {}

    def row(sid: str) -> dict:
        return stats.setdefault(sid, {"shown": 0, "opened": 0, "finished": 0, "likes": 0, "dislikes": 0,
                                      "seconds": 0})

    source_of: dict[str, str] = {}
    for pid, p in posts_state.items():
        sid = p.get("source") or (items.get(pid) or {}).get("source")
        if sid:
            source_of[pid] = sid
        if p.get("status") == "shown" and sid and p.get("published", "") >= since:
            row(sid)["shown"] += 1
    for pid, r in fb.reads.items():
        sid = r.get("s") or source_of.get(pid)
        if not sid or (r.get("last") or r.get("first") or "") < since:
            continue
        x = row(sid)
        if r["sec"] >= 15 or r["p"] >= 0.15:
            x["opened"] += 1
        if r["p"] >= 0.8:
            x["finished"] += 1
        x["seconds"] += r["sec"]
    for pid, r in fb.ratings.items():
        sid = r.get("s") or source_of.get(pid)
        if not sid or r.get("at", "") < since:
            continue
        if r["v"] > 0:
            row(sid)["likes"] += 1
        elif r["v"] < 0:
            row(sid)["dislikes"] += 1
    for x in stats.values():
        x["score"] = round((x["opened"] + x["finished"] + 2 * x["likes"] - 3 * x["dislikes"] + 1) / (x["shown"] + 2), 3)
    return stats
