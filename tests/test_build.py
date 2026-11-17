"""Офлайн-проверка сборщика: ленты -> оценка -> абстракт -> перевод -> шифрование -> feed.json.

Запуск: python tests/test_build.py [каталог_для_данных]
Claude API имитируется на уровне HTTP, сеть не нужна. Готовые данные можно открыть страницей:
  python -m http.server -d tests/out 8765  →  http://localhost:8765/site/index.html?data=/data/
"""
import json, re, shutil, sys
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path

import httpx2  # транспорт, который использует SDK anthropic
from bs4 import BeautifulSoup, NavigableString

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "digest"))
import build  # noqa: E402

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "tests" / "out" / "data"
PASSWORD = "тест-пароль"
now = datetime.now(timezone.utc)

RU = ("Большие языковые модели заметно продвинулись в долгих задачах: агент теперь часами работает над кодом "
      "и сам проверяет результат. Но вместе с автономией растут и риски — модель может уверенно ошибаться, "
      "и замечать это приходится человеку. Автор предлагает простое правило: доверять агенту ровно настолько, "
      "насколько легко проверить его работу.").split(". ")

para = ("<p>Large language models are getting <strong>much better</strong> at <em>long tasks</em>. "
        "See <a href=\"https://example.com/paper\">the paper</a> &amp; the <code>eval_harness</code> code.</p>")
rich = f"""
<div class="subscription-widget-wrap"><p>Subscribe now</p><form><input></form></div>
<h2>What changed this month</h2>{para * 4}
<div class="captioned-image-container"><figure><a class="image-link" href="https://cdn.x/img.png">
<picture><source srcset="x"><img src="https://substackcdn.com/image/fetch/chart.png" alt="Benchmark chart"></picture></a>
<figcaption>Scores on long-horizon tasks</figcaption></figure></div>
<ul><li>First <b>bold</b> point</li><li>Second point<ul><li>nested item</li></ul></li></ul>
<blockquote><p>A quoted <i>remark</i> here.</p></blockquote>
<pre><code>def f(x):
    return x &lt; 2 and "ok"
</code></pre>
<p class="button-wrapper"><a class="button" href="https://x/share">Share</a></p>
<h3>Why it matters</h3>{para * 30}
<p><a href="javascript:alert(1)">bad link</a> <img src="data:image/gif;base64,AAA"></p>
"""
paywalled = para * 3 + '<p class="button-wrapper"><a class="button primary" href="https://x/p/paid"><span>Read more</span></a></p>'
huge = para * 1000


def rss(items):
    out = ['<?xml version="1.0"?><rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel><title>T</title>']
    for guid, title, days, body in items:
        out.append(f"<item><title>{title}</title><link>https://blog.example/p/{guid}</link><guid>{guid}</guid>"
                   f"<pubDate>{format_datetime(now - timedelta(days=days))}</pubDate>"
                   f"<content:encoded><![CDATA[{body}]]></content:encoded></item>")
    return "".join(out) + "</channel></rss>"


FEEDS_XML = {
    "importai": rss([("ia-1", "Import AI 481: AI sub-contractors &amp; more", 1, rich),
                     ("ia-old", "Ancient post", 60, "<p>old</p>")]),
    "latentspace": rss([("ls-news", "[AINews] Daily recap", 0.5, rich),
                        ("ls-1", "Building Visual Intelligence", 2, rich)]),
    "interconnects": rss([("ic-paid", "How big is the open model economy today?", 3, paywalled),
                          ("ic-boring", "Boring housekeeping note", 2, "<p>meh</p>")]),
    "dwarkesh": rss([("dw-1", "Paul Christiano — interview", 1.5, huge)]),
}

# ---------- имитация Anthropic API ----------
calls = {"score": 0, "abstract": 0, "translate": 0, "truncated": 0}


def fake_translate(src: str) -> str:
    s = BeautifulSoup(src, "html.parser")
    i = 0
    for t in list(s.find_all(string=True)):
        if t.parent.name in ("code", "pre") or not t.strip():
            continue
        words = len(str(t).split())
        piece = RU[i % len(RU)]
        i += 1
        t.replace_with(NavigableString((" " if str(t).startswith(" ") else "") + " ".join(piece.split()[:max(2, words + 1)])
                                       + (" " if str(t).endswith(" ") else "")))
    for img in s.find_all("img"):
        img["alt"] = "График результатов"
    return "```html\n" + str(s) + "\n```"


def sse(text, stop="end_turn"):
    ev = [("message_start", {"type": "message_start", "message": {"id": "m", "type": "message", "role": "assistant",
           "model": "claude-opus-5-5", "content": [], "stop_reason": None, "stop_sequence": None,
           "usage": {"input_tokens": 1, "output_tokens": 0}}}),
          ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
          ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}}),
          ("content_block_stop", {"type": "content_block_stop", "index": 0}),
          ("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None}, "usage": {"output_tokens": 5}}),
          ("message_stop", {"type": "message_stop"})]
    return "".join(f"event: {e}\ndata: {json.dumps(d)}\n\n" for e, d in ev)


def tool_reply(model, name, data):
    return httpx2.Response(200, json={"id": "m", "type": "message", "role": "assistant", "model": model,
        "content": [{"type": "tool_use", "id": "t", "name": name, "input": data}],
        "stop_reason": "tool_use", "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1}})


def handler(req):
    body = json.loads(req.content)
    content = body["messages"][0]["content"]
    if body.get("stream"):
        calls["translate"] += 1
        assert body["model"] == "claude-opus-5-5" and body["system"] == build.TRANSLATOR_SYSTEM
        src = re.search(r"<source>\n(.*)\n</source>", content, re.S).group(1)
        if len(src) > 4000:
            calls["truncated"] += 1
            return httpx2.Response(200, text=sse("обрыв", "max_tokens"), headers={"content-type": "text/event-stream"})
        return httpx2.Response(200, text=sse(fake_translate(src)), headers={"content-type": "text/event-stream"})
    name = body["tool_choice"]["name"]
    if name == "rate_post":
        calls["score"] += 1
        assert body["model"] == "claude-haiku-5-5"
        return tool_reply(body["model"], name, {"score": 3 if "Boring" in content else 8, "reason": "тест"})
    calls["abstract"] += 1
    assert name == "save_abstract" and body["model"] == "claude-sonnet-5-5"
    title = re.search(r"Заголовок: (.*)", content).group(1)
    return tool_reply(body["model"], name, {
        "title_ru": {"Import AI 481: AI sub-contractors & more": "Import AI 481: ИИ-субподрядчики, мокрая лаборатория для ИИ и хитрости при обучении",
                     "Building Visual Intelligence": "Как строить визуальный интеллект: разговор с основателями Black Forest Labs",
                     "How big is the open model economy today?": "Насколько велика экономика открытых моделей",
                     "Paul Christiano — interview": "Пол Кристиано: до ИИ, способного свергнуть правительство, может оставаться год"}.get(title, title),
        "abstract_ru": ". ".join(RU[:3]) + "."})


def main():
    if OUT.exists():
        shutil.rmtree(OUT)
    by_id = {f["id"]: f for f in build.FEEDS}
    build.FEEDS[:] = [by_id[k] for k in ("importai", "latentspace", "interconnects", "dwarkesh", "willison")]

    def fetch(feed):
        if feed["id"] not in FEEDS_XML:
            raise httpx2.ConnectError("нет сети")  # недоступная лента не должна ронять сборку
        return build.parse_feed(feed, FEEDS_XML[feed["id"]])

    cfg = build.Config(anthropic_key="x", password=PASSWORD, chunk_chars=9000, max_translations_per_run=1)
    client = build.anthropic.Anthropic(api_key="x", http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))

    # проход 1: всё оценено, переведён только 1 пост (лимит)
    data = build.DataDir(OUT)
    vault = build.Vault(PASSWORD, data.crypto, 1000)
    st1 = build.run(cfg, data, build.Claude(cfg, client), vault, fetch=fetch)
    # проход 2: новые посты не появились, переводится следующий по очереди
    data = build.DataDir(OUT)
    vault = build.Vault(PASSWORD, data.crypto, 1000)
    st2 = build.run(cfg, data, build.Claude(cfg, client), vault, fetch=fetch)
    print("проход 1:", st1, "\nпроход 2:", st2, "\nвызовы API:", calls)

    feed = json.loads((OUT / "feed.json").read_text())
    state = json.loads((OUT / "state.json").read_text())["posts"]
    items = {it["title_en"]: it for it in feed["items"]}
    print({k: v["full"] for k, v in items.items()})

    assert st1["errors"] == 1 and st1["new"] == 5 and st1["translated"] == 1 and st2["translated"] == 1 and st2["new"] == 0
    assert calls["score"] == 5 and calls["abstract"] == 4 and calls["truncated"] >= 1
    assert "[AINews] Daily recap" not in items and not any("AINews" in v["title"] for v in state.values())
    assert items["Import AI 481: AI sub-contractors & more"]["full"] == "ok"
    assert items["Building Visual Intelligence"]["full"] == "ok"
    assert items["How big is the open model economy today?"]["full"] == "paywalled"
    assert items["Paul Christiano — interview"]["full"] == "too_long"
    assert "Boring housekeeping note" not in items
    assert sorted(v["status"] for v in state.values()) == ["old", "shown", "shown", "shown", "shown", "skipped"]
    assert [it["published"] for it in feed["items"]] == sorted((it["published"] for it in feed["items"]), reverse=True)
    assert [s["symbol"] for s in feed["sources"]] == ["Ia", "Ls", "Ic", "Dw", "Sw"]

    # шифрование: неверный пароль отвергается, верный — расшифровывает
    try:
        build.Vault("не тот", feed["crypto"], 1000)
        raise AssertionError("неверный пароль принят")
    except SystemExit:
        pass
    v = build.Vault(PASSWORD, feed["crypto"], 1000)
    it = items["Import AI 481: AI sub-contractors & more"]
    blob = json.loads((OUT / "full" / f"{it['id']}.json").read_text())
    doc = v.decrypt_json(blob)
    h = doc["html"]
    assert "Subscribe" not in h and "Share" not in h and "javascript:" not in h and "data:image" not in h
    assert 'def f(x):\n    return x &lt; 2 and "ok"' in h
    assert '<img alt="График результатов" src="https://substackcdn.com/image/fetch/chart.png"/>' in h
    assert "<blockquote>" in h and "<figcaption>" in h and "Большие языковые модели" in h
    assert "Large language" not in h, "остался непереведённый текст"
    raw = (OUT / "full" / f"{it['id']}.json").read_text()
    assert "Большие" not in raw and "html" not in raw  # на диске только шифротекст
    print("размер зашифрованного перевода:", len(raw), "байт")
    print("ВСЕ ПРОВЕРКИ СБОРЩИКА ПРОЙДЕНЫ →", OUT)


main()
