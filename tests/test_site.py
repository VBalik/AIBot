"""Проверка страницы-приложения в настоящем браузере (Chromium/Playwright) на тестовых данных из test_build.py.

Запуск: python tests/test_build.py && python tests/test_site.py
Скриншоты сохраняются в tests/out/.
"""
import base64, copy, functools, http.server, json, os, shutil, threading
from pathlib import Path

os.environ["PW_EXPERIMENTAL_SERVICE_WORKER_NETWORK_EVENTS"] = "1"  # route видит и запросы service worker
from playwright.sync_api import sync_playwright  # noqa: E402

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAIAAABLbSncAAAAGElEQVR4nGNk+M+AFTDhEh8VAAJEAQGzAA2DAAFB2AB6Fb7lyAAAAABJRU5ErkJggg==")

GOOGLE_FONT_HITS = []
FONT_LOADED = """[...document.fonts].some(f => f.family.replace(/"/g, '') === 'Onest' && f.status === 'loaded')"""

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tests" / "out"
PASSWORD = "тест-пароль"

# Данные для интерфейса: те же, что после test_build.py, но Latent Space и Simon Willison — в разделе «Новости»
UI = OUT / "data-ui"
shutil.rmtree(UI, ignore_errors=True)
shutil.copytree(OUT / "data", UI)
feed = json.loads((UI / "feed.json").read_text())
for s in feed["sources"]:
    s["kind"] = "news" if s["id"] in ("latentspace", "willison") else "analysis"
feed["source_history"] = [{"at": "2026-11-16T09:00:00Z", "kind": "removed", "id": "willison", "name": "Simon Willison",
                           "why": "в разделе «новости» его меньше всего читали и хуже всего оценивали"}]
feed["source_candidates"] = [{"id": "goedecke", "kind": "analysis", "name": "Sean Goedecke", "site": "https://www.seangoedecke.com",
                              "total": 8.4, "verdict": "Сильный автор об инженерии с ИИ."}]
for s in feed["sources"]:
    if s["id"] == "willison":
        s["active"] = False
# фирменные значки: у Import AI есть, у Interconnects файл пропал (страница должна показать буквы)
from PIL import Image as _Image  # noqa: E402
(UI / "icons").mkdir(exist_ok=True)
_Image.new("RGBA", (160, 160), (196, 60, 40, 255)).save(UI / "icons" / "importai.png")
for s in feed["sources"]:
    if s["id"] == "importai":
        s["icon"] = "icons/importai.png?v=t1"
    if s["id"] == "interconnects":
        s["icon"] = "icons/missing.png?v=1"
feed["source_candidates"][0]["icon"] = "icons/importai.png?v=t1"
(UI / "feed.json").write_text(json.dumps(feed, ensure_ascii=False))
ITEM = {it["source"]: it for it in feed["items"]}


def stub_external(ctx):
    """В песочнице нет интернета: картинки с CDN подменяются заглушками, GitHub API — имитацией.
    Шрифт Onest лежит рядом со страницей; к Google Fonts страница ходить не должна."""
    ctx.route("https://fonts.googleapis.com/**", lambda r: (GOOGLE_FONT_HITS.append(r.request.url), r.abort()))
    ctx.route("https://fonts.gstatic.com/**", lambda r: (GOOGLE_FONT_HITS.append(r.request.url), r.abort()))
    ctx.route("https://substackcdn.com/**", lambda r: r.fulfill(status=200, content_type="image/png", body=PNG))
    # счётчик просмотров (функция Netlify): здесь его нет, отвечает имитация
    ctx.route("**/api/views**", lambda r: (VIEWS.append(r.request.method),
                                           r.fulfill(status=200, json={"day": 5, "today": 3, "total": 41, "since": None})))
    return ctx


VIEWS = []


# ---------- имитация GitHub API (закрытый репозиторий с оценками) ----------
GH = {"puts": [], "files": {}}
OTHER = {"version": 1, "device": "d-ipad", "name": "iPad", "updated_at": "2026-11-18T08:00:00Z",
         "ratings": {ITEM["dwarkesh"]["id"]: {"v": 1, "at": "2026-11-18T08:00:00Z", "s": "dwarkesh"}},
         "reads": {}, "pins": {}, "restore": {}}


def github(route):
    req = route.request
    assert req.headers.get("authorization") == "Bearer test-token", req.headers
    path = req.url.split("/contents/", 1)[1]
    cors = {"Access-Control-Allow-Origin": "*"}
    if req.method == "GET" and path == "devices":
        files = [{"type": "file", "name": "d-ipad.json", "path": "devices/d-ipad.json"}] + \
                [{"type": "file", "name": p.split("/")[-1], "path": p} for p in GH["files"]]
        return route.fulfill(status=200, json=files, headers=cors)
    if req.method == "GET" and path == "devices/d-ipad.json":
        return route.fulfill(status=200, json={"sha": "s0", "content": base64.b64encode(json.dumps(OTHER).encode()).decode()}, headers=cors)
    if req.method == "GET":
        if path in GH["files"]:
            return route.fulfill(status=200, json={"sha": GH["files"][path], "content": ""}, headers=cors)
        return route.fulfill(status=404, json={"message": "Not Found"}, headers=cors)
    if req.method == "PUT":
        body = json.loads(req.post_data)
        GH["puts"].append((path, body))
        GH["files"][path] = f"s{len(GH['puts'])}"
        return route.fulfill(status=201, json={"content": {"sha": GH["files"][path]}}, headers=cors)
    return route.fulfill(status=204, headers=cors)


def touch(page, sel, y0, y1, steps=8):
    """Жест пальцем по вертикали (для «потяните, чтобы обновить»)."""
    page.evaluate("""([sel, y0, y1, steps]) => {
      const el = document.querySelector(sel);
      const mk = (y) => new Touch({ identifier: 1, target: el, clientX: 150, clientY: y });
      const fire = (type, t) => el.dispatchEvent(new TouchEvent(type, { touches: type === 'touchend' ? [] : [t],
        targetTouches: type === 'touchend' ? [] : [t], changedTouches: [t], bubbles: true, cancelable: true }));
      fire('touchstart', mk(y0));
      for (let i = 1; i <= steps; i++) fire('touchmove', mk(y0 + (y1 - y0) * i / steps));
      fire('touchend', mk(y1));
    }""", [sel, y0, y1, steps])


handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(ROOT))
handler.log_message = lambda *a: None
srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
URL = f"http://localhost:{srv.server_port}/site/index.html?data=/tests/out/data-ui/"
BASE = f"http://localhost:{srv.server_port}/"

errors = []
with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = stub_external(browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True))
    ctx.route("https://api.github.com/**", github)
    page = ctx.new_page()
    page.on("pageerror", lambda e: errors.append(str(e)))
    # 404 от GitHub API на ещё не созданный файл устройства — штатный ответ, не ошибка страницы
    page.on("console", lambda m: m.type == "error" and "ERR_TUNNEL_CONNECTION_FAILED" not in m.text
            and "api.github.com" not in (m.location or {}).get("url", "")
            and "icons/missing.png" not in (m.location or {}).get("url", "") and errors.append(m.text))  # значок пропал нарочно

    page.goto(URL)
    page.wait_for_selector("#pane-analysis .entry")
    # два раздела: аналитика (3 текста) и новости (1)
    assert page.locator("#pane-analysis .entry").count() == 3
    assert page.locator("#pane-news .entry").count() == 1
    assert page.get_attribute("#tab-news", "aria-selected") == "true"  # слева новости — с них и начинаем
    assert page.evaluate("document.getElementById('pager').scrollLeft") == 0
    assert page.inner_text("#tab-news .cnt") == "1" and page.inner_text("#tab-analysis .cnt") == "3"
    assert page.inner_text("#updated").startswith("Сервер проверял источники")
    # справа под шестерёнкой — версия и просмотры «сегодня/всего»; браузер под управлением программы не считается
    page.wait_for_function("document.getElementById('release').textContent.endsWith('3/41')")
    assert page.inner_text("#release") == "22.11.2 3/41", page.inner_text("#release")
    assert VIEWS and set(VIEWS) == {"GET"}, VIEWS
    gear = page.locator("#open-settings svg").bounding_box()
    rel = page.locator("#release").bounding_box()
    assert abs((gear["x"] + gear["width"]) - (rel["x"] + rel["width"])) < 2.5 and rel["y"] > gear["y"] + gear["height"] - 4, (gear, rel)
    page.wait_for_function(FONT_LOADED, timeout=10000)  # свой шрифт загрузился и используется
    for sel in (".brand", ".entry-abstract", ".entry-title"):
        fam = page.evaluate(f"getComputedStyle(document.querySelector({sel!r})).fontFamily")
        assert fam.replace('"', "").startswith("Onest"), (sel, fam)
    page.wait_for_timeout(300)
    page.screenshot(path=str(OUT / "1b-news.png"))

    # переключение разделов: кнопкой и смахиванием (аналитика — справа)
    page.click("#tab-analysis")
    page.wait_for_function("document.getElementById('pager').scrollLeft > 300")
    assert page.get_attribute("#tab-analysis", "aria-selected") == "true"
    page.wait_for_timeout(400)
    page.screenshot(path=str(OUT / "1-list.png"))
    page.evaluate("const p = document.getElementById('pager'); p.scrollTo({left: 0}); p.dispatchEvent(new Event('scroll'))")
    page.wait_for_function("document.getElementById('tab-news').getAttribute('aria-selected') === 'true'")
    page.evaluate("const p = document.getElementById('pager'); p.scrollTo({left: p.clientWidth}); p.dispatchEvent(new Event('scroll'))")
    page.wait_for_function("document.getElementById('tab-analysis').getAttribute('aria-selected') === 'true'")
    assert page.evaluate("localStorage.getItem('aid.kind')") == "analysis"

    # к началу ленты: касание заголовка и строки состояния iOS (система прокручивает документ к 0)
    page.evaluate("document.getElementById('pane-analysis').scrollTop = 600")
    page.click("#to-top")
    page.wait_for_function("document.getElementById('pane-analysis').scrollTop === 0")
    page.wait_for_function("window.scrollY === 1")  # страница «взвела» перехват касания строки состояния
    page.evaluate("document.getElementById('pane-analysis').scrollTop = 600")
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_function("document.getElementById('pane-analysis').scrollTop === 0")
    page.wait_for_function("window.scrollY === 1", timeout=5000)  # и снова готова

    # фильтр по источнику внутри раздела
    # фирменные значки вместо букв; не загрузился — снова буквы; число текстов — кружок у угла
    page.wait_for_function("""(() => { const i = document.querySelector('#pane-analysis button[data-f="importai"] .tile.logo img');
                                 return i && i.complete && i.naturalWidth === 160; })()""")
    page.wait_for_function("""!document.querySelector('#pane-analysis button[data-f="interconnects"] .tile').classList.contains('logo')""")
    assert page.locator(f'.entry[data-id="{ITEM["importai"]["id"]}"] .tile.sm.logo img').count() == 1
    sym = page.inner_text('#pane-analysis button[data-f="interconnects"] .tile .sym')
    assert sym and page.is_visible('#pane-analysis button[data-f="interconnects"] .tile .sym'), sym
    badge = page.locator('#pane-analysis button[data-f="importai"] .n').bounding_box()
    tb = page.locator('#pane-analysis button[data-f="importai"] .tile').bounding_box()
    assert badge["x"] + badge["width"] > tb["x"] + tb["width"] and badge["y"] < tb["y"], (badge, tb)
    page.screenshot(path=str(OUT / "1e-logos.png"), clip={"x": 0, "y": tb["y"] - 30, "width": 390, "height": 260})

    page.click('#pane-analysis button[data-f="importai"]')
    assert page.locator("#pane-analysis .entry").count() == 1
    assert "Джек Кларк" in page.inner_text("#pane-analysis .source-note")
    total = page.locator('#pane-analysis button[data-f="all"] .n').inner_text()
    assert f"Показаны только его тексты: 1 из {total}" in page.inner_text("#pane-analysis .filter-on")
    # выбранный источник — зелёная рамка цвета кружка с числом, остальные приглушены
    up = page.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--up').trim()")
    ring = page.evaluate("""getComputedStyle(document.querySelector('#pane-analysis button[data-f="importai"] .tile')).boxShadow""")
    badge_bg = page.evaluate("""getComputedStyle(document.querySelector('#pane-analysis button[data-f="importai"] .n')).backgroundColor""")
    rgb = "rgb(%d, %d, %d)" % tuple(int(up[i:i + 2], 16) for i in (1, 3, 5))
    assert rgb in ring and badge_bg == rgb, (up, ring, badge_bg)
    page.wait_for_timeout(250)
    dim = lambda f: float(page.evaluate(f"""getComputedStyle(document.querySelector('#pane-analysis button[data-f="{f}"] .tile')).opacity"""))
    assert dim("all") < .5 and dim("interconnects") < .5 and dim("importai") == 1
    page.screenshot(path=str(OUT / "1f-filter-green.png"), clip={"x": 0, "y": tb["y"] - 30, "width": 390, "height": 200})
    page.click("#pane-analysis [data-show-all]")  # «Показать все» прямо под описанием источника
    assert page.locator("#pane-analysis .entry").count() == int(total)
    assert page.get_attribute('#pane-analysis button[data-f="all"]', "aria-pressed") == "true"
    assert page.locator("#pane-analysis .filters.filtered").count() == 0  # «Все»: ничего не приглушено
    # фильтр не переживает перезапуск приложения: после перезагрузки снова все тексты
    page.click('#pane-analysis button[data-f="importai"]')
    page.reload()
    page.wait_for_selector("#pane-analysis .entry")
    assert page.locator("#pane-analysis .entry").count() == int(total)
    page.click("#tab-analysis")
    page.wait_for_function("window.scrollY === 1")

    # действия, как под ответом Claude: скопировать, поделиться, палец вверх, палец вниз — справа, без подписей и рамок
    ia = ITEM["importai"]["id"]
    bar = f'.entry[data-id="{ia}"] .rate'
    up, down, cp, sh = f"{bar} .up", f"{bar} .down", f"{bar} .act-copy", f"{bar} .act-share"
    xs = [page.locator(x).bounding_box()["x"] for x in (cp, sh, up, down)]
    assert xs == sorted(xs), xs
    assert all(page.inner_text(x).strip() == "" for x in (cp, sh, up, down))
    assert page.evaluate(f"getComputedStyle(document.querySelector('{up}')).borderTopWidth") == "0px"  # без кружков
    # рукава на одной горизонтали: центр рукава (y=16 у «вверх», y=8 у «вниз») совпадает с центром рамки
    assert page.get_attribute(f"{up} svg", "viewBox") == "0 3 24 26" and page.get_attribute(f"{down} svg", "viewBox") == "0 -5 24 26"
    su, sd = page.locator(f"{up} svg").bounding_box(), page.locator(f"{down} svg").bounding_box()
    sc = page.locator(f"{cp} svg").bounding_box()
    assert abs(su["y"] - sd["y"]) < 0.5 and abs(su["height"] - sd["height"]) < 0.5, (su, sd)
    assert abs((sc["y"] + sc["height"] / 2) - (su["y"] + su["height"] / 2)) < 1.5, (sc, su)  # все значки на одной линии
    # справа, на одной линии со строкой состояния («Полный перевод» / «Переводится»)
    ab = page.locator(f'.entry[data-id="{ia}"] .entry-abstract').bounding_box()
    st = page.locator(f'.entry[data-id="{ia}"] .entry-state').bounding_box()
    assert abs((sd["x"] + sd["width"]) - (ab["x"] + ab["width"])) < 1.5, (sd, ab)
    assert abs((su["y"] + su["height"] / 2) - (st["y"] + st["height"] / 2)) < 2, (su, st)
    state_text = page.evaluate(f"(() => {{ const r = document.createRange(); r.selectNodeContents(document.querySelector('.entry[data-id=\"{ia}\"] .entry-state')); return r.getBoundingClientRect().right; }})()")
    assert state_text < sc["x"], (state_text, sc)  # текст состояния не заходит под значки
    page.screenshot(path=str(OUT / "1d-actions.png"), clip={"x": 0, "y": st["y"] - 140, "width": 390, "height": 190})
    # скопировать: заголовок, абстракт и ссылка; значок ненадолго становится галочкой
    page.evaluate("""Object.defineProperty(navigator, 'clipboard', {configurable: true, value: {writeText: async (t) => { window.__copied = t; }}});
                     navigator.share = async (d) => { window.__shared = d; };""")
    page.click(cp)
    page.wait_for_function("window.__copied !== undefined")
    copied = page.evaluate("window.__copied")
    assert copied.startswith(ITEM["importai"]["title_ru"]) and ITEM["importai"]["abstract"] in copied and ITEM["importai"]["url"] in copied, copied
    assert "done" in page.get_attribute(cp, "class") and page.get_attribute(cp, "aria-label") == "Скопировано"
    page.wait_for_function(f"!document.querySelector('{cp}').classList.contains('done')", timeout=4000)
    # поделиться: абстракт и ссылка на оригинал
    page.click(sh)
    page.wait_for_function("window.__shared !== undefined")
    shared = page.evaluate("window.__shared")
    assert shared["url"] == ITEM["importai"]["url"] and ITEM["importai"]["abstract"] in shared["text"], shared
    page.click(up)
    assert page.get_attribute(up, "aria-pressed") == "true"
    fb = page.evaluate("JSON.parse(localStorage.getItem('aid.fb'))")
    assert fb["ratings"][ia]["v"] == 1 and fb["ratings"][ia]["s"] == "importai"
    page.click(up)
    assert page.get_attribute(up, "aria-pressed") == "false"
    page.click(down)
    assert page.get_attribute(down, "aria-pressed") == "true" and page.get_attribute(up, "aria-pressed") == "false"
    page.click(up)  # итог: «Интересно»

    # открыть пост: нужен пароль
    page.click('#pane-analysis .entry a:has-text("Import AI 481")')
    page.wait_for_selector("#unlock")
    assert page.url.endswith("#/p/" + ia)
    page.wait_for_timeout(400)
    page.screenshot(path=str(OUT / "2-locked.png"))
    page.fill("#pw", "неверный")
    page.click("#unlock button")
    page.wait_for_selector("#pw-err:not([hidden])")
    page.fill("#pw", PASSWORD)
    page.click("#unlock button")
    page.wait_for_selector(".prose")
    page.evaluate("document.getElementById('reader').scrollTop = 0")
    prose = page.inner_text(".prose")
    assert "Крупные языковые модели" in prose and "def f(x):" in prose
    assert "Gemini" in page.inner_text("#engine-line")
    assert page.get_attribute(f'.doc-rate .rate[data-rate="{ia}"] .up', "aria-pressed") == "true"  # оценка видна и в читалке
    # в статье «скопировать» берёт весь перевод, а «поделиться» — по-прежнему только абстракт и ссылку
    page.evaluate("window.__copied = undefined; window.__shared = undefined")
    page.click(f'.doc-rate .rate[data-rate="{ia}"] .act-copy')
    page.wait_for_function("window.__copied !== undefined")
    assert "def f(x):" in page.evaluate("window.__copied")
    page.click(f'.doc-rate .rate[data-rate="{ia}"] .act-share')
    page.wait_for_function("window.__shared !== undefined")
    assert "def f(x):" not in json.dumps(page.evaluate("window.__shared"), ensure_ascii=False)
    dr, dq = page.locator(f'.doc-rate .rate[data-rate="{ia}"] .down svg').bounding_box(), page.locator(".doc-rate p").bounding_box()
    pr = page.locator(".prose").bounding_box()
    assert abs((dr["x"] + dr["width"]) - (pr["x"] + pr["width"])) < 2 and dq["x"] < dr["x"], (dr, pr)  # справа, вопрос слева

    # переключатель Gemini / Claude: у этого поста два перевода
    assert page.locator(".engines button").count() == 2
    assert page.get_attribute('.engines button[data-engine="gemini"]', "aria-pressed") == "true"
    assert page.inner_text('.engines button[data-engine="gemini"]') == "Gemini Pro"
    assert page.inner_text('.engines button[data-engine="claude"]') == "Claude Sonnet"
    page.click('.engines button[data-engine="claude"]')
    page.wait_for_function("document.querySelector('.prose') && document.querySelector('.prose').innerText.includes('Большие языковые модели')")
    assert "Claude" in page.inner_text("#engine-line")
    page.screenshot(path=str(OUT / "3b-reader-claude.png"))
    page.click('.engines button[data-engine="gemini"]')
    page.wait_for_function("document.querySelector('.prose') && document.querySelector('.prose').innerText.includes('Крупные')")
    assert page.locator(".prose img").count() == 1 and page.locator(".prose blockquote").count() == 1
    assert page.locator(".prose a[target=_blank]").count() >= 1
    page.screenshot(path=str(OUT / "3-reader.png"))

    # оформление «Аа», как в Apple Books: размер, тема, шрифт, интервал, поля, выравнивание, жирный
    page.click("#aa")
    page.wait_for_selector("#sheet-aa:not([hidden])")
    assert page.locator("#themes button").count() == 6
    fonts = page.eval_on_selector_all("#fonts button", "bs => bs.map(b => b.dataset.font)")
    assert fonts[:3] == ["onest", "sf", "ny"], fonts
    page.click("#size-up")
    page.click("#size-up")
    assert page.evaluate("getComputedStyle(document.querySelector('.prose')).fontSize") == "20px"
    page.click('#themes button[data-theme="paper"]')
    assert page.evaluate("getComputedStyle(document.getElementById('reader')).backgroundColor") == "rgb(243, 238, 228)"
    page.click('#fonts button[data-font="ny"]')
    assert "ui-serif" in page.evaluate("getComputedStyle(document.querySelector('.prose')).fontFamily")
    page.click('#opt-lh button[data-lh="1.85"]')
    pad = lambda sel: page.evaluate(f"getComputedStyle(document.querySelector('{sel}')).paddingLeft")
    assert pad(".doc") == "20px" and pad("#aa-sample") == "20px" and pad("#pane-news .pane-inner") == "20px"
    page.click('#opt-margin button[data-margin="wide"]')
    # поля видны сразу: в образце, в статье и в ленте под листом
    got = [pad(s) for s in (".doc", "#aa-sample", "#pane-news .pane-inner", ".topbar")]
    assert got == ["36px"] * 4, got
    page.click("#opt-justify")
    page.click("#opt-bold")
    assert page.evaluate("getComputedStyle(document.querySelector('.prose p')).textAlign") == "justify"
    assert int(page.evaluate("getComputedStyle(document.querySelector('.prose p')).fontWeight")) >= 500
    # лист пролистан к нижним настройкам, а образец текста всё равно на виду
    card_top, card_scroll = page.evaluate("(() => { const c = document.querySelector('#sheet-aa .sheet-card'); return [c.getBoundingClientRect().top, c.scrollTop]; })()")
    sample = page.locator("#aa-sample").bounding_box()
    assert card_scroll > 100 and sample["y"] >= card_top - 1 and sample["y"] + sample["height"] < 844 / 2, (card_top, card_scroll, sample)
    page.screenshot(path=str(OUT / "3c-aa-sheet.png"))
    page.click("#sheet-aa [data-close]")
    page.wait_for_selector("#sheet-aa", state="hidden")
    page.evaluate("document.getElementById('reader').scrollTop = 0")
    page.screenshot(path=str(OUT / "3d-reader-paper.png"))
    # тема и шрифт действуют на весь дайджест, а не только на статью
    assert page.evaluate("getComputedStyle(document.body).backgroundColor") == "rgb(243, 238, 228)"
    ab_style = page.evaluate("(() => { const s = getComputedStyle(document.querySelector('.entry-abstract')); return [s.fontFamily, s.fontSize, s.fontWeight, s.textAlign]; })()")
    assert "ui-serif" in ab_style[0] and abs(float(ab_style[1][:-2]) - 17 * 20 / 18) < 0.2 and int(ab_style[2]) >= 500 and ab_style[3] == "justify", ab_style
    assert page.evaluate("document.querySelector('meta[name=theme-color]').content") == "#F3EEE4"
    saved = page.evaluate("JSON.parse(localStorage.getItem('aid.reading'))")
    assert saved == {"theme": "paper", "font": "ny", "size": 20, "lh": "1.85", "margin": "wide", "justify": True, "bold": True}, saved
    page.click("#aa")
    page.click('#themes button[data-theme="quiet"]')
    page.click("#sheet-aa [data-close]")
    page.screenshot(path=str(OUT / "3e-reader-quiet.png"))
    assert page.evaluate("getComputedStyle(document.querySelector('.topbar')).backgroundColor") == "rgb(74, 74, 77)"  # «Тихая» — и шапка
    page.click("#aa")
    page.click("#aa-reset")
    page.click("#sheet-aa [data-close]")
    assert page.evaluate("getComputedStyle(document.querySelector('#pane-news .pane-inner')).paddingLeft") == "20px"
    assert page.evaluate("JSON.parse(localStorage.getItem('aid.reading')).theme") == "original"
    assert page.evaluate("getComputedStyle(document.body).backgroundColor") == "rgb(241, 244, 243)"
    assert page.evaluate("getComputedStyle(document.querySelector('.entry-abstract')).fontSize") == "17px"

    # учёт чтения: долистал до конца — текст дочитан
    page.evaluate("const r = document.getElementById('reader'); r.scrollTop = r.scrollHeight; r.dispatchEvent(new Event('scroll'))")
    page.wait_for_timeout(200)
    page.evaluate("document.getElementById('reader').scrollTop = 900")
    page.screenshot(path=str(OUT / "4-reader-scrolled.png"))

    # назад к ленте: текст отмечен прочитанным
    page.click("#back")
    page.wait_for_selector("#reader", state="hidden")
    assert "#/p/" not in page.url
    fb = page.evaluate("JSON.parse(localStorage.getItem('aid.fb'))")
    assert fb["reads"][ia]["p"] >= 0.95 and fb["reads"][ia]["s"] == "importai", fb["reads"]
    assert "is-read" in page.get_attribute(f'.entry[data-id="{ia}"]', "class")

    # платный пост — только абстракт
    page.click('#pane-analysis .entry a:has-text("экономика открытых моделей")')
    page.wait_for_selector(".notice")
    assert "подписчикам" in page.inner_text(".notice")
    page.go_back()
    page.wait_for_selector("#reader", state="hidden")

    # «Аа» открывается и из настроек; образец текста показывает выбранную тему
    page.click("#open-settings")
    page.click("#open-aa")
    page.wait_for_selector("#sheet-aa:not([hidden])")
    assert page.is_hidden("#sheet-settings")
    page.click('#themes button[data-theme="calm"]')
    assert page.evaluate("getComputedStyle(document.getElementById('aa-sample')).backgroundColor") == "rgb(238, 226, 203)"
    assert page.evaluate("getComputedStyle(document.querySelector('.pane')).backgroundColor") in ("rgba(0, 0, 0, 0)", "rgb(238, 226, 203)")
    assert page.evaluate("getComputedStyle(document.body).backgroundColor") == "rgb(238, 226, 203)"  # фон ленты сменился
    page.screenshot(path=str(OUT / "3f-aa-from-settings.png"))
    page.click('#themes button[data-theme="original"]')
    page.click("#sheet-aa [data-close]")

    # для друзей: в источниках только список, без рейтинга, закрепления, кандидатов и замен
    assert page.evaluate("document.documentElement.classList.contains('owner')") is False
    page.click("#open-sources")
    page.wait_for_selector("#sheet-sources:not([hidden])")
    assert page.locator("#ranking.plain li").count() == 3
    assert page.locator("#ranking button[data-pin]").count() == 0 and page.locator("#ranking .bar").count() == 0
    assert page.is_hidden("#ranking-h") and page.is_hidden("#candidates") and page.is_hidden("#history")
    assert "Откуда берутся тексты" in page.inner_text("#src-explain")
    page.screenshot(path=str(OUT / "9a-sources-friends.png"))
    page.click("#sheet-sources [data-close]")
    # в настройках нет синхронизации; пять быстрых касаний версии включают режим владельца
    page.click("#open-settings")
    page.wait_for_selector("#sheet-settings:not([hidden])")
    assert page.is_hidden("#sync-form") and page.is_visible("#forget")  # пароль на этом устройстве введён
    for _ in range(4):
        page.click("#app-version")
    assert page.is_hidden("#sync-form")
    page.click("#app-version")
    assert page.is_visible("#sync-form") and page.evaluate("localStorage.getItem('aid.owner')") == "1"
    page.click("#sheet-settings [data-close]")

    # источники у владельца: рейтинг раздела, закрепление, история и «вернуть»
    page.click("#open-sources")
    page.wait_for_selector("#sheet-sources:not([hidden])")
    rows = page.locator("#ranking li")
    assert rows.count() == 3  # importai, dwarkesh, interconnects
    assert "Import AI" in rows.nth(0).inner_text() and "👍 1" in rows.nth(0).inner_text()
    page.click('#ranking button[data-pin="interconnects"]')
    assert page.get_attribute('#ranking button[data-pin="interconnects"]', "aria-pressed") == "true"
    assert "Sean Goedecke" in page.inner_text("#candidates")
    page.screenshot(path=str(OUT / "9-sources.png"))
    page.click('#src-tabs button[data-kind="news"]')
    assert page.locator("#ranking li").count() == 1  # Latent Space (Willison убран)
    assert "Убран: Simon Willison" in page.inner_text("#history")
    page.click('#history button[data-restore="willison"]')
    assert "Вернётся" in page.inner_text("#history")
    fb = page.evaluate("JSON.parse(localStorage.getItem('aid.fb'))")
    assert fb["pins"]["interconnects"]["v"] is True and "willison" in fb["restore"]
    page.click("#sheet-sources [data-close]")

    # синхронизация оценок через GitHub: свой файл уходит, оценка с iPad приходит
    page.click("#open-settings")
    page.wait_for_selector("#sheet-settings:not([hidden])")
    assert "Версия приложения" in page.inner_text("#app-version")
    page.fill("#gh-token", "test-token")
    page.click("#sync-form button[type=submit]")
    page.wait_for_function("document.getElementById('sync-status').textContent.includes('Устройств: 2')", timeout=10000)
    assert "Синхронизировано" in page.inner_text("#sync-status")
    path, body = GH["puts"][-1]
    assert path.startswith("devices/d-") and path.endswith(".json")
    doc = json.loads(base64.b64decode(body["content"]).decode())
    assert doc["ratings"][ia]["v"] == 1 and doc["pins"]["interconnects"]["v"] and doc["reads"][ia]["p"] >= 0.95
    page.screenshot(path=str(OUT / "10-settings.png"))
    page.click("#sheet-settings [data-close]")
    dw = ITEM["dwarkesh"]["id"]
    assert page.get_attribute(f'.entry[data-id="{dw}"] .rate .up', "aria-pressed") == "true"  # оценка с iPad
    n_puts = len(GH["puts"])
    page.click(f'.entry[data-id="{dw}"] .rate .down')
    page.wait_for_function(f"window.__n = {n_puts}; true")
    page.wait_for_timeout(4600)  # отправка через 4 секунды после последней оценки
    assert len(GH["puts"]) == n_puts + 1 and GH["puts"][-1][1].get("sha"), GH["puts"][-1]

    # «потяните, чтобы обновить»: на сервере появился новый текст
    fresh = copy.deepcopy(ITEM["latentspace"])
    fresh.update(id="latentspace-fresh00001", title_ru="Свежая новость про агентов", added="2026-11-18T09:00:00Z",
                 published="2026-11-18T08:30:00Z", full="pending")
    feed2 = json.loads((UI / "feed.json").read_text())
    feed2["items"].insert(0, fresh)
    (UI / "feed.json").write_text(json.dumps(feed2, ensure_ascii=False))
    page.click("#tab-news")
    page.wait_for_function("document.getElementById('pager').scrollLeft < 5")
    touch(page, "#pane-news", 120, 420)
    page.wait_for_selector('#pane-news .entry.is-new:has-text("Свежая новость")', timeout=10000)
    assert "1 новый текст" in page.inner_text("#toast")
    page.screenshot(path=str(OUT / "11-pulled.png"))
    touch(page, "#pane-news", 120, 160)  # слишком короткий жест — не обновляет
    page.wait_for_timeout(300)

    # повторный визит: пароль запомнен, прямая ссылка открывает перевод сразу
    bfl = ITEM["latentspace"]["id"]
    page.goto(URL + "#/p/" + bfl)
    page.wait_for_selector(".prose")
    assert page.locator(".engines").count() == 0
    page.click("#back")
    page.wait_for_selector("#reader", state="hidden")

    # тёмная тема
    dark = stub_external(browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, color_scheme="dark", is_mobile=True))
    dp = dark.new_page()
    dp.goto(URL)
    dp.wait_for_selector(".entry")
    dp.wait_for_timeout(300)
    dp.screenshot(path=str(OUT / "5-dark.png"))
    # на новом устройстве без пароля ссылки «Забыть пароль» нет (раньше её показывало правило all: unset)
    dp.click("#open-settings")
    dp.wait_for_selector("#sheet-settings:not([hidden])")
    assert dp.is_hidden("#forget") and dp.is_hidden("#sync-form")
    assert "спросят один раз" in dp.inner_text("#pw-state")
    dp.click("#sheet-settings [data-close]")

    # иконка для экрана «Домой»: 180×180 без прозрачности, иконки манифеста на месте
    from PIL import Image
    import io
    touch = page.get_attribute('link[rel="apple-touch-icon"]', "href")
    r = page.request.get(BASE + "site/" + touch)
    assert r.ok, touch
    img = Image.open(io.BytesIO(r.body()))
    assert img.size == (180, 180) and img.mode in ("RGB", "P") and "transparency" not in img.info, (img.size, img.mode)
    man = page.request.get(BASE + "site/manifest.webmanifest").json()
    for ic in man["icons"]:
        got = Image.open(io.BytesIO(page.request.get(BASE + "site/" + ic["src"]).body()))
        assert f"{got.size[0]}x{got.size[1]}" == ic["sizes"], ic

    # первоисточник из подборки TLDR AI подписан сайтом статьи
    feed_r = json.loads((UI / "feed.json").read_text())
    feed_r["sources"].append({"id": "tldrai", "kind": "news", "symbol": "Tl", "name": "TLDR AI", "author": "редакция TLDR",
                              "about": "Из ежедневной подборки берутся только первоисточники", "site": "https://tldr.tech/ai",
                              "hue": 60, "active": True})
    rad = copy.deepcopy(ITEM["latentspace"])
    rad.update(id="tldrai-0123456789", source="tldrai", via="openai.com", url="https://openai.com/index/inside-the-codex-sandbox/",
               title_ru="Как устроена песочница Codex", full="pending", added="2026-11-18T07:00:00Z", alt=[])
    feed_r["items"].insert(0, rad)
    (UI / "feed.json").write_text(json.dumps(feed_r, ensure_ascii=False))

    # шапка: слева значок источников (линии по левому краю ленты), справа шестерёнка; «Обновить» на телефоне не нужен
    page.goto(URL)
    page.wait_for_selector("#pane-news .entry")
    page.click("#tab-news")
    page.wait_for_selector('#pane-news .entry:has-text("Как устроена песочница Codex")')
    meta = page.inner_text('#pane-news .entry:has-text("Как устроена песочница Codex") .entry-meta')
    assert meta.startswith("openai.com через TLDR AI"), meta
    page.click('#pane-news .entry:has-text("Как устроена песочница Codex") .entry-title')
    page.wait_for_selector("#reader:not([hidden]) .doc-source")
    src_txt = page.inner_text(".doc-source")
    assert "openai.com" in src_txt and "найдено в подборке TLDR AI" in src_txt, src_txt
    page.click("#back")
    page.wait_for_selector("#reader", state="hidden")
    xs = [page.locator(s).bounding_box()["x"] for s in ("#open-sources", "#to-top", "#open-settings")]
    assert xs[0] < xs[1] < xs[2], xs
    lines_x = page.evaluate("(() => { const b = document.querySelector('#open-sources svg').getBoundingClientRect(); return b.left + b.width * 2.6 / 24; })()")
    text_x = page.evaluate("(() => { const e = document.querySelector('#pane-news .pane-inner'); return e.getBoundingClientRect().left + parseFloat(getComputedStyle(e).paddingLeft); })()")
    assert abs(lines_x - text_x) < 2, (lines_x, text_x)
    assert page.is_hidden("#refresh-btn")
    page.screenshot(path=str(OUT / "1c-header.png"), clip={"x": 0, "y": 0, "width": 390, "height": 140})

    # iPad: оба раздела рядом, без переключателя
    wide = stub_external(browser.new_context(viewport={"width": 1024, "height": 1366}, device_scale_factor=2))
    wp = wide.new_page()
    wp.goto(URL)
    wp.wait_for_selector("#pane-news .entry")
    assert wp.is_hidden(".topbar .seg") and wp.is_visible("#pane-news .pane-title") and wp.is_visible("#pane-analysis .pane-title")
    box_n, box_a = wp.locator("#pane-news").bounding_box(), wp.locator("#pane-analysis").bounding_box()
    assert box_n["x"] < box_a["x"] and box_a["x"] + box_a["width"] <= 1024 + 1, (box_n, box_a)
    wp.wait_for_timeout(300)
    wp.screenshot(path=str(OUT / "6-ipad.png"))
    # компьютер (мышь): кнопка «Обновить» и клавиша R вместо жеста «потяните вниз»
    assert wp.is_visible("#refresh-btn")
    fresh2 = copy.deepcopy(ITEM["latentspace"])
    fresh2.update(id="latentspace-fresh00002", title_ru="Ещё одна свежая новость", added="2026-11-18T10:00:00Z",
                  published="2026-11-18T09:30:00Z", full="pending")
    feed3 = json.loads((UI / "feed.json").read_text())
    feed3["items"].insert(0, fresh2)
    (UI / "feed.json").write_text(json.dumps(feed3, ensure_ascii=False))
    wp.click("#refresh-btn")
    wp.wait_for_selector('#pane-news .entry.is-new:has-text("Ещё одна свежая новость")', timeout=10000)
    assert "1 новый текст" in wp.inner_text("#toast")
    wp.wait_for_function("!document.getElementById('refresh-btn').disabled")
    wp.evaluate("document.getElementById('toast').textContent = ''")
    wp.keyboard.press("r")
    wp.wait_for_function("document.getElementById('toast').textContent.includes('Новых текстов нет')", timeout=10000)
    browser.close()

srv.shutdown()
assert not errors, errors
assert not GOOGLE_FONT_HITS, GOOGLE_FONT_HITS
print("ВСЕ ПРОВЕРКИ СТРАНИЦЫ ПРОЙДЕНЫ, скриншоты в", OUT)
