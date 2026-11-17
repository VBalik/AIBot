"""Офлайн-проверка сборщика: ленты -> оценка -> абстракт -> перевод -> шифрование -> feed.json.

Запуск: python tests/test_build.py [каталог_для_данных]
Gemini и Claude имитируются на уровне HTTP, сеть не нужна. Готовые данные можно открыть страницей:
  python -m http.server 8765  →  http://localhost:8765/site/index.html?data=/tests/out/data/
"""
import json, re, shutil, sys, tempfile
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path

import httpx   # транспорт для Gemini (REST)
import httpx2  # транспорт, который использует SDK anthropic
from bs4 import BeautifulSoup, NavigableString

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "digest"))
import build  # noqa: E402

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "tests" / "out" / "data"
PASSWORD = "тест-пароль"
now = datetime.now(timezone.utc)
build.time.sleep = lambda s: None  # не ждать при повторах после 429

# «Переводы» двух движков отличаются, чтобы проверить переключатель на странице
RU = {
    "gemini": ("Крупные языковые модели заметно продвинулись в долгих задачах: агент часами работает над кодом "
               "и сам проверяет результат. Но вместе с автономией растут и риски — модель может уверенно ошибаться, "
               "и замечать это приходится человеку. Автор предлагает правило: доверять агенту ровно настолько, "
               "насколько легко проверить его работу.").split(". "),
    "claude": ("Большие языковые модели заметно продвинулись в долгих задачах: агент теперь часами работает над кодом "
               "и сам проверяет результат. Однако вместе с автономией растут и риски — модель способна уверенно ошибаться, "
               "а замечать это приходится человеку. Автор предлагает простое правило: доверять агенту ровно настолько, "
               "насколько легко проверить его работу.").split(". "),
    "openai": ("Языковые модели заметно выросли в долгих задачах: агент часами пишет код "
               "и сам себя проверяет. Но вместе с самостоятельностью растут и риски — модель умеет ошибаться уверенно, "
               "и заметить это должен человек. Автор предлагает правило: доверять агенту ровно в той мере, "
               "в какой легко проверить его работу.").split(". "),
    "haiku": ("Языковые модели стали лучше справляться с длинными задачами: агент долго пишет код "
              "и проверяет результат. Но с автономией растут и риски — модель может ошибаться уверенно, "
              "и это должен замечать человек. Автор советует доверять агенту настолько, "
              "насколько просто проверить его работу.").split(". "),
}

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
TITLES_RU = {
    "Import AI 481: AI sub-contractors & more": "Import AI 481: ИИ-субподрядчики, мокрая лаборатория для ИИ и хитрости при обучении",
    "Building Visual Intelligence": "Как строить визуальный интеллект: разговор с основателями Black Forest Labs",
    "How big is the open model economy today?": "Насколько велика экономика открытых моделей",
    "Paul Christiano — interview": "Пол Кристиано: до ИИ, способного свергнуть правительство, может оставаться год",
}


def fake_translate(src: str, engine: str) -> str:
    s = BeautifulSoup(src, "html.parser")
    i = 0
    for t in list(s.find_all(string=True)):
        if t.parent.name in ("code", "pre") or not t.strip():
            continue
        words = len(str(t).split())
        piece = RU[engine][i % len(RU[engine])]
        i += 1
        t.replace_with(NavigableString((" " if str(t).startswith(" ") else "") + " ".join(piece.split()[:max(2, words + 1)])
                                       + (" " if str(t).endswith(" ") else "")))
    for img in s.find_all("img"):
        img["alt"] = "График результатов"
    return "```html\n" + str(s) + "\n```"


def source_of(text):
    return re.search(r"<source>\n(.*)\n</source>", text, re.S).group(1)


# ---------- имитация Gemini API (REST) ----------
calls = {"o_score": 0, "o_abstract": 0, "o_translate": 0, "o_truncated": 0, "g_score": 0, "g_abstract": 0, "g_translate": 0, "g_truncated": 0, "g_429": 0,
         "c_score": 0, "c_abstract": 0, "c_translate": 0, "c_truncated": 0}


def gemini_reply(text, finish="STOP"):
    return httpx.Response(200, json={"candidates": [{"content": {"role": "model", "parts": [
        {"text": "Сначала обдумаю задачу…", "thought": True}, {"text": text}]}, "finishReason": finish}],
        "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1, "thoughtsTokenCount": 5}})


def gemini_handler(req: httpx.Request):
    assert req.headers["x-goog-api-key"] == "g-key"
    model = re.search(r"/v1beta/models/([^:]+):generateContent$", req.url.path).group(1)
    body = json.loads(req.content)
    gen = body["generationConfig"]
    assert gen["thinkingConfig"] == {"thinkingLevel": "LOW"}
    user = body["contents"][0]["parts"][0]["text"]
    system = body["systemInstruction"]["parts"][0]["text"]
    if calls["g_429"] == 0:  # первый запрос — перегрузка, клиент должен повторить
        calls["g_429"] += 1
        return httpx.Response(429, json={"error": {"code": 429, "message": "Resource exhausted"}})
    if "responseSchema" in gen:
        schema = gen["responseSchema"]
        assert gen["responseMimeType"] == "application/json" and schema["type"] == "OBJECT"
        if "score" in schema["properties"]:
            calls["g_score"] += 1
            assert model == "gemini-3.1-flash-lite" and schema["properties"]["score"]["type"] == "INTEGER"
            return gemini_reply(json.dumps({"score": 3 if "Boring" in user else 8, "reason": "тест"}, ensure_ascii=False))
        calls["g_abstract"] += 1
        assert model == "gemini-3.1-pro-preview" and system == build.ABSTRACT_SYSTEM
        title = re.search(r"Заголовок: (.*)", user).group(1)
        return gemini_reply("```json\n" + json.dumps({"title_ru": TITLES_RU.get(title, title),
                                                      "abstract_ru": ". ".join(RU["gemini"][:3]) + "."}, ensure_ascii=False) + "\n```")
    calls["g_translate"] += 1
    assert model == "gemini-3.1-pro-preview" and system == build.TRANSLATOR_SYSTEM
    src = source_of(user)
    if len(src) > 4000:
        calls["g_truncated"] += 1
        return gemini_reply("обрыв", "MAX_TOKENS")
    return gemini_reply(fake_translate(src, "gemini"))


# ---------- имитация OpenAI Responses API (REST) ----------
def openai_reply(text, status="completed"):
    body = {"id": "resp_1", "object": "response", "status": status, "output": [
        {"type": "reasoning", "id": "rs_1", "summary": []},
        {"type": "message", "id": "msg_1", "role": "assistant", "status": status,
         "content": [{"type": "output_text", "text": text, "annotations": []}]}]}
    if status == "incomplete":
        body["incomplete_details"] = {"reason": "max_output_tokens"}
    return httpx.Response(200, json=body)


def openai_handler(req: httpx.Request):
    assert req.url.path == "/v1/responses" and req.headers["authorization"] == "Bearer o-key"
    body = json.loads(req.content)
    assert body["reasoning"] == {"effort": "low"} and body["store"] is False
    model, system, user = body["model"], body["instructions"], body["input"]
    fmt = (body.get("text") or {}).get("format")
    if fmt:
        assert fmt["type"] == "json_schema" and fmt["strict"] is True
        schema = fmt["schema"]
        assert schema["additionalProperties"] is False and set(schema["required"]) == set(schema["properties"])
        if "score" in schema["properties"]:
            calls["o_score"] += 1
            assert model == "gpt-6-luna" and "minimum" not in schema["properties"]["score"]
            return openai_reply(json.dumps({"score": 3 if "Boring" in user else 8, "reason": "тест"}, ensure_ascii=False))
        calls["o_abstract"] += 1
        assert model == "gpt-6.1-sol" and system == build.ABSTRACT_SYSTEM
        title = re.search(r"Заголовок: (.*)", user).group(1)
        return openai_reply(json.dumps({"title_ru": TITLES_RU.get(title, title),
                                        "abstract_ru": ". ".join(RU["openai"][:3]) + "."}, ensure_ascii=False))
    calls["o_translate"] += 1
    assert model == "gpt-6-astra" and system == build.TRANSLATOR_SYSTEM
    src = source_of(user)
    if len(src) > 4000:
        calls["o_truncated"] += 1
        return openai_reply("обрыв", "incomplete")
    return openai_reply(fake_translate(src, "openai"))


# ---------- имитация Claude API (SDK anthropic) ----------
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


def claude_handler(req):
    body = json.loads(req.content)
    content = body["messages"][0]["content"]
    if body.get("stream"):
        calls["c_translate"] += 1
        assert body["model"] in ("claude-sonnet-5-5", "claude-haiku-5-5") and body["system"] == build.TRANSLATOR_SYSTEM
        src = source_of(content)
        if len(src) > 4000:
            calls["c_truncated"] += 1
            return httpx2.Response(200, text=sse("обрыв", "max_tokens"), headers={"content-type": "text/event-stream"})
        flavor = "haiku" if "haiku" in body["model"] else "claude"
        return httpx2.Response(200, text=sse(fake_translate(src, flavor)), headers={"content-type": "text/event-stream"})
    name = body["tool_choice"]["name"]
    if name == "rate_post":
        calls["c_score"] += 1
        assert body["model"] == "claude-haiku-5-5"
        return tool_reply(body["model"], name, {"score": 3 if "Boring" in content else 8, "reason": "тест"})
    calls["c_abstract"] += 1
    assert name == "save_abstract" and body["model"] == "claude-haiku-5-5"
    title = re.search(r"Заголовок: (.*)", content).group(1)
    return tool_reply(body["model"], name, {"title_ru": TITLES_RU.get(title, title), "abstract_ru": ". ".join(RU["claude"][:3]) + "."})


# ---------- сборка ----------
def fetch(feed):
    if feed["id"] not in FEEDS_XML:
        raise httpx.ConnectError("нет сети")  # недоступная лента не должна ронять сборку
    return build.parse_feed(feed, FEEDS_XML[feed["id"]])


def writers(cfg):
    def llm(provider, model=None):
        if provider == "openai":
            l = build.OpenAILLM(cfg, http=httpx.Client(transport=httpx.MockTransport(openai_handler)))
        elif provider == "gemini":
            l = build.GeminiLLM(cfg, http=httpx.Client(transport=httpx.MockTransport(gemini_handler)))
        else:
            l = build.ClaudeLLM(cfg, client=build.anthropic.Anthropic(
                api_key="x", http_client=httpx2.Client(transport=httpx2.MockTransport(claude_handler))))
        if model:
            l.models["translate"] = model
        return l
    main = build.Writer(cfg, llm(cfg.main_provider()))
    extras = [build.Writer(cfg, llm(p, m), variant=v) for v, p, m in cfg.variants()]
    return main, extras


def run_pass(cfg, out):
    data = build.DataDir(out)
    vault = build.Vault(PASSWORD, data.crypto, 1000)
    main, extras = writers(cfg)
    return build.run(cfg, data, main, vault, fetch=fetch, extras=extras)


def check_provider_logic():
    C = build.Config
    assert C(gemini_key="g", anthropic_key="a").main_provider() == "gemini"
    assert C(gemini_key="g", anthropic_key="a").compare_providers() == ["claude"]
    assert C(gemini_key="g").compare_providers() == []
    assert C(anthropic_key="a").main_provider() == "claude" and C(anthropic_key="a").compare_providers() == []
    assert C(gemini_key="g", anthropic_key="a", provider="claude").compare_providers() == ["gemini"]
    assert C(gemini_key="g", anthropic_key="a", compare_provider="off").compare_providers() == []
    assert C(gemini_key="g", anthropic_key="a", compare_limit=0).compare_providers() == []
    # OpenAI первым при auto: его оплачивают кредиты подписки ChatGPT Pro
    full = C(openai_key="o", gemini_key="g", anthropic_key="a")
    assert full.main_provider() == "openai" and full.compare_providers() == ["gemini", "claude"]
    assert C(openai_key="o", gemini_key="g", anthropic_key="a", compare_provider="claude").compare_providers() == ["claude"]
    assert C(openai_key="o").variants() == []
    assert C(openai_key="o", anthropic_key="a").variants() == [("claude", "claude", "claude-sonnet-5-5")]
    assert build.to_openai_schema(build.SCORE_TOOL["input_schema"]) == {"type": "object", "properties": {
        "score": {"type": "integer", "description": "1 = неинтересно, 10 = обязательно к прочтению"},
        "reason": {"type": "string", "description": "Одно короткое предложение по-русски: почему такая оценка"}},
        "required": ["score", "reason"], "additionalProperties": False}
    # OpenAI: модель без reasoning.effort — повтор без параметра; закончились кредиты — понятная ошибка
    seen = []
    def no_reasoning(req):
        b = json.loads(req.content)
        seen.append("reasoning" in b)
        if "reasoning" in b:
            return httpx.Response(400, json={"error": {"message": "Unsupported parameter: 'reasoning.effort' is not supported with this model."}})
        return openai_reply("готово")
    o = build.OpenAILLM(C(openai_key="o-key"), http=httpx.Client(transport=httpx.MockTransport(no_reasoning)))
    assert o.text("translate", "sys", "user") == "готово" and seen == [True, False]
    broke = build.OpenAILLM(C(openai_key="o-key"), http=httpx.Client(transport=httpx.MockTransport(
        lambda req: httpx.Response(429, json={"error": {"code": "insufficient_quota", "message": "You exceeded your current quota"}}))))
    try:
        broke.text("translate", "sys", "user")
        raise AssertionError("нет ошибки о кредитах")
    except RuntimeError as exc:
        assert "закончились кредиты" in str(exc)
    # варианты для сравнения: другой движок и/или дешёвая модель того же движка
    assert C(anthropic_key="a").variants() == [("claude-haiku-5-5", "claude", "claude-haiku-5-5")]
    assert C(anthropic_key="a", claude_model_translate="claude-haiku-5-5").variants() == []
    assert C(anthropic_key="a", compare_model="off").variants() == []
    assert C(gemini_key="g").variants() == []
    assert C(gemini_key="g", anthropic_key="a").variants() == [("claude", "claude", "claude-sonnet-5-5")]
    assert C(gemini_key="g", anthropic_key="a", provider="claude").variants() == [
        ("gemini", "gemini", "gemini-3.1-pro-preview"), ("claude-haiku-5-5", "claude", "claude-haiku-5-5")]
    # 429 с «limit: 0» (модель не входит в бесплатный уровень) — без повторов и с понятной ошибкой
    hits = []
    def free_tier(req):
        hits.append(1)
        return httpx.Response(429, json={"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message":
            "Quota exceeded for metric: generate_content_free_tier_requests, limit: 0, model: gemini-3.1-pro"}})
    g = build.GeminiLLM(C(gemini_key="g-key"), http=httpx.Client(transport=httpx.MockTransport(free_tier)))
    try:
        g.text("translate", "sys", "user")
        raise AssertionError("ошибка бесплатного уровня не распознана")
    except RuntimeError as exc:
        assert "бесплатном уровне" in str(exc) and len(hits) == 1, (exc, hits)
    sch = build.to_gemini_schema(build.SCORE_TOOL["input_schema"])
    assert sch == {"type": "OBJECT", "properties": {
        "score": {"type": "INTEGER", "description": "1 = неинтересно, 10 = обязательно к прочтению"},
        "reason": {"type": "STRING", "description": "Одно короткое предложение по-русски: почему такая оценка"}},
        "required": ["score", "reason"]}, sch


def main():
    check_provider_logic()
    by_id = {f["id"]: f for f in build.FEEDS}
    build.FEEDS[:] = [by_id[k] for k in ("importai", "latentspace", "interconnects", "dwarkesh", "willison")]

    # --- Сценарий A: Gemini основной, Claude переводит 1 пост для сравнения ---
    if OUT.exists():
        shutil.rmtree(OUT)
    cfg = build.Config(gemini_key="g-key", anthropic_key="a-key", password=PASSWORD, compare_limit=1,
                       chunk_chars=9000, max_translations_per_run=1)
    st1 = run_pass(cfg, OUT)
    st2 = run_pass(cfg, OUT)
    print("A проход 1:", st1, "\nA проход 2:", st2, "\nвызовы:", calls)

    feed = json.loads((OUT / "feed.json").read_text())
    state = json.loads((OUT / "state.json").read_text())
    items = {it["title_en"]: it for it in feed["items"]}
    assert st1["new"] == 5 and st1["translated"] == 1 and st1["compared"] == 1 and st1["errors"] == 1
    assert st2["new"] == 0 and st2["translated"] == 1 and st2["compared"] == 0
    assert calls["g_429"] == 1 and calls["g_score"] == 5 and calls["g_abstract"] == 4 and calls["g_truncated"] >= 1
    assert calls["c_score"] == 0 and calls["c_abstract"] == 0 and calls["c_translate"] >= 1 and calls["c_truncated"] >= 1
    assert state["compare_done"] == {"claude": 1}
    lr = state["last_run"]
    assert lr["engine"] == "gemini" and lr["stats"]["translated"] == 1 and lr["stats"]["api_errors"] == 0
    assert any("Simon Willison" in e for e in lr["errors"])
    assert build.redact("Incorrect API key provided: sk-proj-abc123****wxyz.") == "Incorrect API key provided: [ключ скрыт]."
    assert "AIza" not in build.redact("key=AIzaSyA1234567890abcdefghijk bad")

    ia = items["Import AI 481: AI sub-contractors & more"]
    ls = items["Building Visual Intelligence"]
    assert ia["full"] == "ok" and ia["engine"] == "gemini" and ia["model"] == "gemini-3.1-pro-preview"
    assert ia["alt"] == [{"id": "claude", "model": "claude-sonnet-5-5"}]
    assert ls["full"] == "ok" and ls["engine"] == "gemini" and "alt" not in ls
    assert ia["title_ru"].startswith("Import AI 481: ИИ-субподрядчики")
    assert items["How big is the open model economy today?"]["full"] == "paywalled"
    assert items["Paul Christiano — interview"]["full"] == "too_long"
    assert "[AINews] Daily recap" not in items and "Boring housekeeping note" not in items

    v = build.Vault(PASSWORD, feed["crypto"], 1000)
    main_doc = v.decrypt_json(json.loads((OUT / "full" / f"{ia['id']}.json").read_text()))
    alt_doc = v.decrypt_json(json.loads((OUT / "full" / f"{ia['id']}--claude.json").read_text()))
    assert main_doc["engine"] == "gemini" and main_doc["model"] == "gemini-3.1-pro-preview"
    assert alt_doc["engine"] == "claude" and alt_doc["model"] == "claude-sonnet-5-5"
    assert "Крупные языковые модели" in main_doc["html"] and "Большие" not in main_doc["html"]
    assert "Большие языковые модели" in alt_doc["html"]
    assert "обдумаю" not in main_doc["html"], "в перевод попали «размышления» Gemini"
    h = main_doc["html"]
    assert "Subscribe" not in h and "Share" not in h and "javascript:" not in h and "data:image" not in h
    assert 'def f(x):\n    return x &lt; 2 and "ok"' in h and "<blockquote>" in h and "<figcaption>" in h
    assert "Large language" not in h, "остался непереведённый текст"
    try:
        build.Vault("не тот", feed["crypto"], 1000)
        raise AssertionError("неверный пароль принят")
    except SystemExit:
        pass

    # --- Пейволл: в бесплатных рассылках (Import AI с jack-clark.net) призыв подписаться — не пейволл ---
    ia_feed = by_id["importai"]
    assert ia_feed.get("paywall") is False
    teaser = para * 2 + '<p><a href="https://x/sub"><span>Read more</span></a> for paid subscribers</p>'
    assert not build.parse_feed(ia_feed, rss([("pp", "Free post", 1, teaser)]))[0].paywalled
    assert build.parse_feed(by_id["interconnects"], rss([("pp", "Paid post", 1, teaser)]))[0].paywalled
    # пост, ошибочно помеченный платным, пересматривается: новый абстракт и перевод
    out_p = Path(tempfile.mkdtemp()) / "data"
    shutil.copytree(OUT, out_p)
    fp = json.loads((out_p / "feed.json").read_text())
    for it in fp["items"]:
        if it["id"] == ia["id"]:
            it["full"], it["abstract"] = "paywalled", "старый абстракт"
            it.pop("alt", None)
    (out_p / "feed.json").write_text(json.dumps(fp, ensure_ascii=False))
    (out_p / "full" / f"{ia['id']}.json").unlink()
    st_p = run_pass(build.Config(gemini_key="g-key", password=PASSWORD, chunk_chars=9000, max_translations_per_run=1), out_p)
    ia_p = next(it for it in json.loads((out_p / "feed.json").read_text())["items"] if it["id"] == ia["id"])
    assert st_p["translated"] == 1 and ia_p["full"] == "ok" and ia_p["abstract"] != "старый абстракт", (st_p, ia_p["full"])
    assert (out_p / "full" / f"{ia['id']}.json").exists()

    # --- Сценарий B: только ключ Claude — перевод Sonnet, для сравнения Haiku ---
    out_b = Path(tempfile.mkdtemp()) / "data"
    cfg_b = build.Config(anthropic_key="a-key", password=PASSWORD, chunk_chars=9000, max_translations_per_run=1)
    before = dict(calls)
    st_b = run_pass(cfg_b, out_b)
    feed_b = json.loads((out_b / "feed.json").read_text())
    ia_b = next(it for it in feed_b["items"] if it["title_en"].startswith("Import AI 481"))
    assert st_b["translated"] == 1 and st_b["compared"] == 1
    assert ia_b["engine"] == "claude" and ia_b["model"] == "claude-sonnet-5-5"
    assert ia_b["alt"] == [{"id": "claude-haiku-5-5", "model": "claude-haiku-5-5"}]
    assert calls["c_score"] - before["c_score"] == 5 and calls["g_score"] == before["g_score"]
    vb = build.Vault(PASSWORD, feed_b["crypto"], 1000)
    sonnet = vb.decrypt_json(json.loads((out_b / "full" / f"{ia_b['id']}.json").read_text()))
    haiku = vb.decrypt_json(json.loads((out_b / "full" / f"{ia_b['id']}--claude-haiku-5-5.json").read_text()))
    assert sonnet["model"] == "claude-sonnet-5-5" and "Большие языковые модели" in sonnet["html"]
    assert haiku["model"] == "claude-haiku-5-5" and "Языковые модели стали лучше" in haiku["html"]
    print("B:", st_b)

    # --- Сценарий C: OpenAI основной (Astra), Claude Sonnet для сравнения ---
    out_c = Path(tempfile.mkdtemp()) / "data"
    cfg_c = build.Config(openai_key="o-key", anthropic_key="a-key", password=PASSWORD, chunk_chars=9000,
                         max_translations_per_run=1)
    before = dict(calls)
    st_c = run_pass(cfg_c, out_c)
    feed_c = json.loads((out_c / "feed.json").read_text())
    ia_c = next(it for it in feed_c["items"] if it["title_en"].startswith("Import AI 481"))
    assert st_c["translated"] == 1 and st_c["compared"] == 1 and st_c["new"] == 5
    assert ia_c["engine"] == "openai" and ia_c["model"] == "gpt-6-astra"
    assert ia_c["alt"] == [{"id": "claude", "model": "claude-sonnet-5-5"}]
    assert calls["o_score"] == 5 and calls["o_abstract"] == 4 and calls["o_truncated"] >= 1
    assert calls["c_score"] == before["c_score"] and calls["g_score"] == before["g_score"]
    vc = build.Vault(PASSWORD, feed_c["crypto"], 1000)
    astra = vc.decrypt_json(json.loads((out_c / "full" / f"{ia_c['id']}.json").read_text()))
    assert astra["engine"] == "openai" and "Языковые модели заметно выросли" in astra["html"]
    assert "Large language" not in astra["html"]
    print("C:", st_c)
    print("ВСЕ ПРОВЕРКИ СБОРЩИКА ПРОЙДЕНЫ →", OUT)


main()
