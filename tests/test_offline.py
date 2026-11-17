"""Офлайн-режим страницы в настоящем Chromium: service worker, сохранённые переводы и иллюстрации.

Запуск: python tests/test_build.py && python tests/test_offline.py
Иллюстрации «с чужого CDN» подменяются картинкой через context.route (включая запросы service worker).
"""
import base64, functools, http.server, os, threading
from pathlib import Path

os.environ["PW_EXPERIMENTAL_SERVICE_WORKER_NETWORK_EVENTS"] = "1"  # чтобы route видел запросы service worker
from playwright.sync_api import sync_playwright  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tests" / "out"
PASSWORD = "тест-пароль"
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAIAAABLbSncAAAAGElEQVR4nGNk+M+AFTDhEh8VAAJEAQGzAA2DAAFB2AB6Fb7lyAAAAABJRU5ErkJggg==")

handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(ROOT))
handler.log_message = lambda *a: None
srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
URL = f"http://localhost:{srv.server_port}/site/index.html?data=/tests/out/data/"

errors, image_hits = [], []
with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True,
                              service_workers="allow")
    def cdn(route):
        image_hits.append(route.request.url)
        route.fulfill(status=200, content_type="image/png", body=PNG, headers={"Access-Control-Allow-Origin": "*"})
    ctx.route("https://substackcdn.com/**", cdn)
    ctx.route("https://fonts.googleapis.com/**", lambda r: r.abort())
    ctx.route("https://fonts.gstatic.com/**", lambda r: r.abort())
    page = ctx.new_page()
    page.on("pageerror", lambda e: errors.append(str(e)))

    # 1. Онлайн: SW берёт страницу под контроль и сохраняет переводы
    page.goto(URL)
    page.wait_for_selector(".entry")
    page.wait_for_function("navigator.serviceWorker.controller !== null", timeout=15000)
    page.wait_for_function("document.getElementById('offline-status').textContent.includes('2 перевода')", timeout=15000)
    print("онлайн:", page.inner_text("#offline-status"))

    # 2. Ввод пароля → страница находит иллюстрации в переводах, SW их докачивает
    page.click('.entry a:has-text("Import AI 481")')
    page.fill("#pw", PASSWORD)
    page.click("#unlock button")
    page.wait_for_selector(".prose")
    page.click("#back")
    page.wait_for_function("/и \\d+ иллюстрац/.test(document.getElementById('offline-status').textContent)", timeout=15000)
    print("после пароля:", page.inner_text("#offline-status"))
    cached = page.evaluate("""async () => {
        const out = {};
        for (const k of await caches.keys()) out[k] = (await (await caches.open(k)).keys()).map(r => r.url);
        return out; }""")
    assert any("chart.png" in u for u in cached.get("images", [])), cached.get("images")
    assert any(u.endswith("/feed.json") for u in cached.get("data", [])), cached.get("data")
    assert sum("/full/" in u for u in cached.get("data", [])) == 3, cached.get("data")  # 2 статьи + 1 перевод для сравнения

    # 3. Без сети: страница, лента, переводы и иллюстрации — из кэша
    ctx.set_offline(True)
    hits_before = len(image_hits)
    page.reload()
    page.wait_for_selector(".entry", timeout=15000)
    assert page.locator(".entry").count() == 4
    assert "Нет сети" in page.inner_text("#offline-status")
    page.screenshot(path=str(OUT / "7-offline-list.png"))
    # статью Black Forest Labs в этой сессии не открывали — она должна была сохраниться в фоне
    page.click('.entry a:has-text("Black Forest Labs")')
    page.wait_for_selector(".prose", timeout=15000)
    assert "Крупные языковые модели" in page.inner_text(".prose")
    page.click("#back")
    page.click('.entry a:has-text("Import AI 481")')
    page.wait_for_selector(".prose img", timeout=15000)
    page.wait_for_function("(() => { const i = document.querySelector('.prose img'); i.loading = 'eager'; return i.complete && i.naturalWidth > 0; })()", timeout=15000)
    assert len(image_hits) == hits_before, "без сети картинка должна браться из кэша"
    page.evaluate("document.getElementById('reader').scrollTop = 0")
    page.screenshot(path=str(OUT / "8-offline-reader.png"))
    browser.close()

srv.shutdown()
assert not errors, errors
print("ВСЕ ПРОВЕРКИ ОФЛАЙН-РЕЖИМА ПРОЙДЕНЫ, скриншоты в", OUT)
