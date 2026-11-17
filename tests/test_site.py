"""Проверка страницы в настоящем браузере (Chromium/Playwright) на тестовых данных из test_build.py.

Запуск: python tests/test_build.py && python tests/test_site.py
Скриншоты сохраняются в tests/out/.
"""
import functools, http.server, sys, threading
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tests" / "out"
PASSWORD = "тест-пароль"

handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(ROOT))
handler.log_message = lambda *a: None
srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
URL = f"http://localhost:{srv.server_port}/site/index.html?data=/tests/out/data/"

errors = []
with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True)
    page = ctx.new_page()
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: m.type == "error" and "ERR_TUNNEL_CONNECTION_FAILED" not in m.text and errors.append(m.text))

    page.goto(URL)
    page.wait_for_selector(".entry")
    assert page.locator(".entry").count() == 4
    page.screenshot(path=str(OUT / "1-list.png"))

    # фильтр по источнику
    page.click('button[data-f="importai"]')
    assert page.locator(".entry").count() == 1
    assert "Джек Кларк" in page.inner_text("#source-note")
    page.click('button[data-f="all"]')

    # открыть пост: нужен пароль
    page.click('.entry a:has-text("Import AI 481")')
    page.wait_for_selector("#unlock")
    assert page.url.endswith("#/p/" + page.get_attribute('.entry a:has-text("Import AI 481")', "data-id"))
    page.wait_for_timeout(400)  # дождаться конца анимации появления
    page.screenshot(path=str(OUT / "2-locked.png"))
    page.fill("#pw", "неверный")
    page.click("#unlock button")
    page.wait_for_selector("#pw-err:not([hidden])")
    page.fill("#pw", PASSWORD)
    page.click("#unlock button")
    page.wait_for_selector(".prose")
    page.evaluate("document.getElementById('reader').scrollTop = 0")
    prose = page.inner_text(".prose")
    assert "Большие языковые модели" in prose and "def f(x):" in prose
    assert page.locator(".prose img").count() == 1 and page.locator(".prose blockquote").count() == 1
    assert page.locator(".prose a[target=_blank]").count() >= 1
    page.screenshot(path=str(OUT / "3-reader.png"))
    page.evaluate("document.getElementById('reader').scrollTop = 900")
    page.screenshot(path=str(OUT / "4-reader-scrolled.png"))

    # назад к ленте
    page.click("#back")
    page.wait_for_selector("#reader", state="hidden")
    assert "#/p/" not in page.url

    # платный пост — только абстракт
    page.click('.entry a:has-text("экономика открытых моделей")')
    page.wait_for_selector(".notice")
    assert "подписчикам" in page.inner_text(".notice")
    page.go_back()
    page.wait_for_selector("#reader", state="hidden")

    # повторный визит: пароль запомнен, прямая ссылка открывает перевод сразу
    deep = page.get_attribute('.entry a:has-text("Black Forest Labs")', "href")
    page.goto(URL + deep)
    page.wait_for_selector(".prose")
    page.click("#back")
    page.wait_for_selector("#reader", state="hidden")

    # тёмная тема
    dark = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, color_scheme="dark", is_mobile=True)
    dp = dark.new_page()
    dp.goto(URL)
    dp.wait_for_selector(".entry")
    dp.screenshot(path=str(OUT / "5-dark.png"))

    # широкий экран
    wide = browser.new_context(viewport={"width": 1280, "height": 900})
    wp = wide.new_page()
    wp.goto(URL)
    wp.wait_for_selector(".entry")
    wp.screenshot(path=str(OUT / "6-desktop.png"))
    browser.close()

srv.shutdown()
assert not errors, errors
print("ВСЕ ПРОВЕРКИ СТРАНИЦЫ ПРОЙДЕНЫ, скриншоты в", OUT)
