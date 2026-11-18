"""Оценки, статистика по источникам и еженедельный пересмотр источников — без сети и без настоящих моделей.

    python tests/test_review.py
"""
import base64
import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "digest"))

import build  # noqa: E402
import feedback as fbmod  # noqa: E402
import review  # noqa: E402
from sources import SourceBook  # noqa: E402

NOW = datetime(2026, 12, 21, 9, 0, tzinfo=timezone.utc)
iso = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")

SEED = [
    {"id": "aa", "kind": "analysis", "symbol": "Aa", "name": "Alpha", "author": "А", "about": "a", "url": "https://aa.test/feed", "site": "https://aa.test"},
    {"id": "bb", "kind": "analysis", "symbol": "Bb", "name": "Beta", "author": "Б", "about": "b", "url": "https://bb.test/feed", "site": "https://bb.test"},
    {"id": "cc", "kind": "analysis", "symbol": "Cc", "name": "Gamma", "author": "Г", "about": "c", "url": "https://cc.test/feed", "site": "https://cc.test"},
    {"id": "nn", "kind": "news", "symbol": "Nn", "name": "Newsy", "author": "Н", "about": "n", "url": "https://nn.test/feed", "site": "https://nn.test"},
    {"id": "mm", "kind": "news", "symbol": "Mm", "name": "Media", "author": "М", "about": "m", "url": "https://mm.test/feed", "site": "https://mm.test"},
]


def device_file(ratings=None, reads=None, pins=None, restore=None, device="d-1"):
    return {"version": 1, "device": device, "updated_at": iso(NOW), "ratings": ratings or {}, "reads": reads or {},
            "pins": pins or {}, "restore": restore or {}}


# ---------- 1. сводка оценок с двух устройств ----------
iphone = device_file(
    ratings={"aa-1": {"v": 1, "at": "2026-12-10T10:00:00Z", "s": "aa"}, "bb-1": {"v": -1, "at": "2026-12-10T10:00:00Z", "s": "bb"}},
    reads={"aa-1": {"s": "aa", "first": "2026-12-10T09:00:00Z", "last": "2026-12-10T09:10:00Z", "sec": 300, "p": 0.6}},
    pins={"cc": {"v": True, "at": "2026-12-01T00:00:00Z"}})
ipad = device_file(
    device="d-2",
    ratings={"bb-1": {"v": 0, "at": "2026-12-11T10:00:00Z", "s": "bb"}},  # позже передумал на iPad
    reads={"aa-1": {"s": "aa", "first": "2026-12-11T09:00:00Z", "last": "2026-12-11T09:05:00Z", "sec": 200, "p": 0.95}},
    restore={"zz": "2026-12-20T00:00:00Z"})


def gh_handler(req: httpx.Request):
    assert req.headers["authorization"] == "Bearer fb-token"
    path = req.url.path
    if path == "/repos/VBalik/AIBot-feedback/contents/devices":
        return httpx.Response(200, json=[{"type": "file", "name": "d-1.json", "path": "devices/d-1.json"},
                                         {"type": "file", "name": "d-2.json", "path": "devices/d-2.json"},
                                         {"type": "file", "name": "README.md", "path": "devices/README.md"}])
    name = path.rsplit("/", 1)[-1]
    doc = {"d-1.json": iphone, "d-2.json": ipad}[name]
    return httpx.Response(200, json={"content": base64.b64encode(json.dumps(doc).encode()).decode()})


fb = fbmod.load("fb-token", http=httpx.Client(transport=httpx.MockTransport(gh_handler)))
assert fb.devices == 2
assert fb.ratings["bb-1"]["v"] == 0 and fb.ratings["aa-1"]["v"] == 1, fb.ratings  # побеждает более поздняя оценка
assert fb.reads["aa-1"]["sec"] == 500 and fb.reads["aa-1"]["p"] == 0.95
assert fb.pinned() == {"cc"} and fb.restore == {"zz": "2026-12-20T00:00:00Z"}
assert fbmod.load("", http=None).empty  # без токена — пусто, без ошибок
denied = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(401, json={})))
try:
    fbmod.load("bad", http=denied)
    raise AssertionError("ждали ошибку доступа")
except RuntimeError as exc:
    assert "FEEDBACK_TOKEN" in str(exc)
print("оценки: сводка с двух устройств — ok")


# ---------- 2. пересмотр ----------
def make_data(fb_doc):
    root = Path(tempfile.mkdtemp()) / "data"
    data = build.DataDir.__new__(build.DataDir)
    data.root = root
    (root / "full").mkdir(parents=True)
    data.state = {"version": 1, "posts": {}}
    data.crypto = None
    data.items = {}
    data.sources = SourceBook(root / "sources.json", seed=SEED)
    # по 6 показанных постов у каждого источника за последние недели
    for s in SEED:
        for i in range(6):
            pid = f"{s['id']}-{i}"
            data.state["posts"][pid] = {"status": "shown", "source": s["id"], "title": f"{s['name']} post {i}",
                                        "published": iso(NOW - timedelta(days=3 + i))}
    feedback = fbmod.Feedback()
    if fb_doc:
        feedback.merge(fb_doc)
    return data, feedback


def reads(sid, n, p=0.9):
    return {f"{sid}-{i}": {"s": sid, "first": iso(NOW - timedelta(days=2)), "last": iso(NOW - timedelta(days=2)),
                           "sec": 240, "p": p} for i in range(n)}


TEXT = "<p>" + ("Deep analysis of frontier model evaluations with original experiments and numbers. " * 40) + "</p>"


def post(feed, i, days=2, html=TEXT, links=(), paywalled=False):
    extra = "".join(f'<a href="{u}">x</a>' for u in links)
    return build.Post(id=f"{feed['id']}-p{i}", feed=feed, title=f"{feed.get('name', feed['id'])} #{i}",
                      url=f"{feed['site']}/p/{i}", published=NOW - timedelta(days=days + i), html=html + extra,
                      paywalled=paywalled)


def fake_fetch(feed):
    fid = feed["id"]
    if fid in ("aa", "bb", "cc"):  # действующие аналитики часто ссылаются на blog.cited.test
        return [post(feed, i, links=["https://blog.cited.test/a", "https://x.com/z"]) for i in range(4)]
    if fid in ("nn", "mm"):
        return [post(feed, i) for i in range(8)]
    if fid == "goedecke":
        return [post(feed, i) for i in range(5)]
    if fid == "decoder":  # в ленте выдержки, полный текст — на странице
        return [post(feed, i, html="<p>Short teaser.</p>") for i in range(10)]
    if fid == "cited":
        return [post(feed, i) for i in range(3)]
    if fid == "exponentialview":
        return [post(feed, i, paywalled=True) for i in range(5)]
    raise httpx.ConnectError("нет такой ленты в тесте")


def fake_complete(p):
    if p.feed.get("fetch_article"):
        p.html = TEXT
    return p


class FakeLLM:
    name = "fake"
    calls = []

    def structured(self, step, system, tool, user, max_tokens):
        assert tool["name"] == "rate_source" and step == "abstract"
        cand = user.split("\n", 1)[0]
        self.calls.append(cand)
        high = "Sean Goedecke" in cand or "The Decoder" in cand
        return {"kind": "news" if "Decoder" in cand else "analysis", "authority": 9 if high else 5,
                "insight": 9 if high else 5, "fit": 9 if high else 4, "overlap": 3,
                "name": "The Decoder" if "Decoder" in cand else ("Sean Goedecke" if high else "Some Blog"),
                "author_ru": "автор", "about_ru": "Сильный автор об инженерии с ИИ.", "symbol": "Bb",
                "verdict_ru": "Сильный источник." if high else "Средне."}


cfg = build.Config(review_max_candidates=6)

# 2а. Есть статистика: Beta мало читают и ставят 👎 → его заменяет Sean Goedecke; Gamma закреплён; новости —
# The Decoder заменяет Media
fb_doc = device_file(
    ratings={"bb-0": {"v": -1, "at": iso(NOW - timedelta(days=2)), "s": "bb"},
             "aa-0": {"v": 1, "at": iso(NOW - timedelta(days=2)), "s": "aa"},
             "mm-0": {"v": -1, "at": iso(NOW - timedelta(days=2)), "s": "mm"},
             "nn-1": {"v": 1, "at": iso(NOW - timedelta(days=2)), "s": "nn"}},
    reads={**reads("aa", 5), **reads("nn", 5), **reads("bb", 1, p=0.1), **reads("mm", 1, p=0.1)},
    pins={"cc": {"v": True, "at": iso(NOW - timedelta(days=30))}})
data, feedback = make_data(fb_doc)
stats = fbmod.source_stats(feedback, data.state["posts"], data.items, now=NOW)
assert stats["aa"]["opened"] == 5 and stats["aa"]["finished"] == 5 and stats["aa"]["likes"] == 1
assert stats["bb"]["dislikes"] == 1 and stats["bb"]["score"] < stats["aa"]["score"]
assert stats["cc"]["score"] < stats["bb"]["score"] + 1  # Gamma не читали вовсе, но он закреплён

FakeLLM.calls = []
summary = review.weekly_review(cfg, data, feedback, FakeLLM(), fetch=fake_fetch, complete=fake_complete,
                               autodiscover=lambda host: f"https://{host}/feed" if host == "blog.cited.test" else None,
                               now=NOW)
assert summary["added"] == ["goedecke", "decoder"] or set(summary["added"]) == {"goedecke", "decoder"}, summary
assert set(summary["removed"]) == {"bb", "mm"}, summary
ids = data.sources.ids()
assert "cc" in ids and "bb" not in ids and "goedecke" in ids and "decoder" in ids
dec = data.sources.get("decoder")
assert dec["kind"] == "news" and dec["fetch_article"] is True and dec["symbol"] not in ("Bb", "Aa", "Cc", "Nn", "Mm")
assert any(c.startswith("Кандидат: blog.cited.test") for c in FakeLLM.calls), FakeLLM.calls  # найден по ссылкам
assert not any("Exponential" in c for c in FakeLLM.calls), "платный кандидат не должен доходить до модели"
hist = data.sources.data["history"]
assert [h["kind"] for h in hist].count("removed") == 2 and [h["kind"] for h in hist].count("added") == 2
assert all("читали" not in h["why"] or "меньше всего" in h["why"] for h in hist)
assert data.sources.data["last_review"] == iso(NOW)
assert not review.due(data.sources, 7, NOW + timedelta(days=3)) and review.due(data.sources, 7, NOW + timedelta(days=7))
# публичные данные: без цифр чтения и без оценок
data.save()
public = (data.root / "feed.json").read_text() + (data.root / "sources.json").read_text()
assert "dislikes" not in public and "seconds" not in public
feed_json = json.loads((data.root / "feed.json").read_text())
assert {s["id"] for s in feed_json["sources"]} >= {"goedecke", "decoder"}
assert {s["kind"] for s in feed_json["sources"]} == {"news", "analysis"}
assert feed_json["source_history"][-1]["kind"] == "added"
print("пересмотр: замена слабых источников в обоих разделах — ok")

# 2б. Повторно через неделю тех же кандидатов не оценивает (не чаще раза в 8 недель)
FakeLLM.calls = []
review.weekly_review(cfg, data, feedback, FakeLLM(), fetch=fake_fetch, complete=fake_complete,
                     autodiscover=lambda h: None, now=NOW + timedelta(days=7))
assert not any("Sean Goedecke" in c or "Decoder" in c for c in FakeLLM.calls)

# 2в. Нет статистики чтения: никто не убирается, сильный кандидат только предлагается
data, feedback = make_data(None)
summary = review.weekly_review(cfg, data, feedback, FakeLLM(), fetch=fake_fetch, complete=fake_complete,
                               autodiscover=lambda h: None, now=NOW)
assert summary["removed"] == [] and set(summary["suggested"]) == {"goedecke", "decoder"}, summary
assert data.sources.ids() == {s["id"] for s in SEED}
assert data.sources.data["history"][-1]["kind"] == "suggested"
print("пересмотр: без данных о чтении — только предложение — ok")

# 2г. Источник, добавленный меньше трёх недель назад, не убирается, даже если его не читают
data, feedback = make_data(fb_doc)
data.sources.data["active"][1]["added"] = iso(NOW - timedelta(days=10))  # Beta — новый
summary = review.weekly_review(cfg, data, feedback, FakeLLM(), fetch=fake_fetch, complete=fake_complete,
                               autodiscover=lambda h: None, now=NOW)
assert "bb" not in summary["removed"] and "bb" in data.sources.ids(), summary

# 2д. «Вернуть источник»: убранный Beta возвращается по просьбе со страницы, с прежним цветом
data, feedback = make_data(fb_doc)
hue = data.sources.get("bb")["hue"]
data.sources.remove("bb", why="тест", at=iso(NOW - timedelta(days=5)))
feedback.merge(device_file(restore={"bb": iso(NOW - timedelta(days=1))}, device="d-3"))
assert review.apply_restores(data.sources, feedback) == ["bb"]
assert data.sources.get("bb")["origin"] == "restore" and data.sources.get("bb")["hue"] == hue
assert review.apply_restores(data.sources, feedback) == []  # второй раз — ничего
print("пересмотр: новые источники защищены, возврат по просьбе — ok")

# ---------- 3. сборщик целиком: пересмотр раз в неделю внутри обычного прогона ----------
assert review.due(SourceBook(Path(tempfile.mkdtemp()) / "s.json", seed=SEED), 7, NOW)
print("ВСЕ ПРОВЕРКИ ПЕРЕСМОТРА ПРОЙДЕНЫ")
