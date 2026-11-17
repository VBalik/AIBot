"""Зеркало дайджеста в Telegram.

Бот отправляет каждую отобранную статью в личный чат (или закрытый канал):
заголовок, абстракт, полный перевод (несколькими сообщениями, если длинный) и ссылки на оригинал и страницу.
В публичный чат или канал уходит только абстракт: полные переводы чужих статей открыто не публикуются.

Если TELEGRAM_CHAT_ID ещё не задан, бот отвечает на /start в личке и подсказывает, какой chat_id прописать.
"""
from __future__ import annotations

import html
import logging
import re
import time
from datetime import datetime

import httpx
from bs4 import BeautifulSoup, Comment, NavigableString, Tag

log = logging.getLogger("ai-digest")

TG_LIMIT = 4096
MSG_BUDGET = 3900
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля",
          "августа", "сентября", "октября", "ноября", "декабря"]
HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
BLOCKS = HEADINGS | {"p", "ul", "ol", "li", "pre", "blockquote", "table", "thead", "tbody",
                     "tr", "td", "th", "hr", "figure", "figcaption"}
STATUS_NOTE = {
    "paywalled": "Полный текст доступен только подписчикам автора.",
    "too_long": "Текст очень длинный, поэтому здесь только абстракт.",
    "below": "Полный перевод для этого текста не делался.",
    "unavailable": "Текст пропал из ленты автора до перевода.",
    "error": "Перевести этот текст не получилось.",
}


# ---------------------------------------------------------------------------
# HTML перевода → Telegram-HTML
# ---------------------------------------------------------------------------

def esc(s: str) -> str:
    return html.escape(s or "", quote=False)


def _wrap(tag: str, inner: str) -> str:
    return f"<{tag}>{inner}</{tag}>" if inner.strip() else inner


def _img(node: Tag) -> str:
    src = str(node.get("src", ""))
    return f'<a href="{html.escape(src)}">🖼 иллюстрация</a>' if src.startswith("http") else ""


def _inline(node, in_link: bool = False) -> str:
    if isinstance(node, Comment):
        return ""
    if isinstance(node, NavigableString):
        return esc(re.sub(r"\s+", " ", str(node)))
    name = node.name
    inner = lambda: "".join(_inline(c, in_link or name == "a") for c in node.children)
    if name in ("b", "strong") or name in HEADINGS:
        return _wrap("b", inner())
    if name in ("i", "em"):
        return _wrap("i", inner())
    if name in ("s", "del"):
        return _wrap("s", inner())
    if name == "u":
        return _wrap("u", inner())
    if name == "code":
        return f"<code>{esc(node.get_text())}</code>"
    if name == "br":
        return "\n"
    if name == "img":
        return "" if in_link else _img(node)
    if name == "a":
        href = str(node.get("href", ""))
        text = inner()
        if href.startswith("http") and text.strip() and not in_link:
            return f'<a href="{html.escape(href)}">{text}</a>'
        return text
    if name in BLOCKS:
        return "\n".join(_blocks(node))
    return inner()


def _list(node: Tag, depth: int) -> str:
    lines, n = [], 0
    for li in node.find_all("li", recursive=False):
        n += 1
        marker = f"{n}." if node.name == "ol" else "•"
        body = "\n".join(_blocks(li, depth + 1)).strip()
        lines.append("   " * depth + f"{marker} {body}")
    return "\n".join(lines)


def _block(node, depth: int = 0) -> str:
    name = node.name
    if name in ("ul", "ol"):
        return _list(node, depth)
    if name == "pre":
        return f"<pre>{esc(node.get_text().strip(chr(10)))}</pre>"
    if name == "blockquote":
        inner = "\n".join(_blocks(node, depth)).strip()
        return f"<blockquote>{inner}</blockquote>" if inner else ""
    if name == "hr":
        return "⁂"
    if name == "table":
        rows = []
        for tr in node.find_all("tr"):
            cells = [_inline(c).strip() for c in tr.find_all(["td", "th"], recursive=False)]
            rows.append(" | ".join(cells))
        return "\n".join(rows)
    if name == "figure":
        parts = [_img(i) for i in node.find_all("img")]
        cap = node.find("figcaption")
        if cap is not None:
            parts.append(_wrap("i", _inline(cap).strip()))
        return "\n".join(p for p in parts if p)
    return _inline(node).strip()


def _blocks(node, depth: int = 0) -> list[str]:
    """Абзацы из детей узла; строчные куски склеиваются в один абзац."""
    out, buf = [], ""
    for child in node.children:
        if isinstance(child, Tag) and child.name in BLOCKS:
            if buf.strip():
                out.append(buf.strip())
            buf = ""
            b = _block(child, depth)
            if b.strip():
                out.append(b)
        else:
            buf += _inline(child)
    if buf.strip():
        out.append(buf.strip())
    return out


def html_to_tg_blocks(fragment: str) -> list[str]:
    return _blocks(BeautifulSoup(fragment, "html.parser"))


def _split_oversize(block: str, limit: int) -> list[str]:
    if len(block) <= limit:
        return [block]
    is_pre = block.startswith("<pre>")
    plain = BeautifulSoup(block, "html.parser").get_text()
    sep = "\n" if is_pre else " "
    pieces, cur = [], ""
    for part in plain.split(sep):
        if cur and len(esc(cur + sep + part)) > limit - 20:
            pieces.append(cur)
            cur = part
        else:
            cur = f"{cur}{sep}{part}" if cur else part
    if cur:
        pieces.append(cur)
    return [f"<pre>{esc(p)}</pre>" if is_pre else esc(p) for p in pieces]


def pack_messages(blocks: list[str], limit: int = MSG_BUDGET) -> list[str]:
    msgs, cur = [], ""
    for block in blocks:
        for piece in _split_oversize(block, limit):
            if cur and len(cur) + 2 + len(piece) > limit:
                msgs.append(cur)
                cur = piece
            else:
                cur = f"{cur}\n\n{piece}" if cur else piece
    if cur:
        msgs.append(cur)
    return msgs


def _date_ru(iso: str) -> str:
    d = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return f"{d.day} {MONTHS[d.month - 1]} {d.year}"


def build_messages(item: dict, source: dict, html_ru: str | None, site_url: str) -> list[str]:
    """Сообщения для одной статьи: шапка + абстракт + (полный перевод) + ссылки."""
    name = source.get("name", item.get("source", ""))
    head = (f"<b>{esc(item['title_ru'])}</b>\n"
            f"<i>{esc(name)}, {esc(source.get('author', ''))}, {_date_ru(item['published'])}</i>")
    blocks = [head, f"<blockquote>{esc(item['abstract'])}</blockquote>"]
    if html_ru:
        blocks += html_to_tg_blocks(html_ru)
    elif item.get("full") in STATUS_NOTE:
        blocks.append(f"<i>{esc(STATUS_NOTE[item['full']])}</i>")
    links = f'<a href="{html.escape(item["url"])}">Оригинал: {esc(item["title_en"])}</a>'
    if site_url:
        links += f'\n<a href="{html.escape(site_url.rstrip("/"))}/#/p/{html.escape(item["id"])}">Открыть на странице</a>'
    blocks.append(links)
    return pack_messages(blocks)


# ---------------------------------------------------------------------------
# Bot API
# ---------------------------------------------------------------------------

class TelegramAPI:
    def __init__(self, token: str, http: httpx.Client | None = None):
        self.base = f"https://api.telegram.org/bot{token.strip()}"  # при копировании с телефона цепляются пробелы
        self.http = http or httpx.Client(timeout=30)

    def call(self, method: str, payload: dict) -> dict | list:
        last = ""
        for attempt in range(5):
            try:
                r = self.http.post(f"{self.base}/{method}", json=payload)
            except httpx.TransportError as exc:  # обрыв соединения, таймаут
                last = f"нет связи ({type(exc).__name__})"
                time.sleep(5 * (attempt + 1))
                continue
            try:
                data = r.json()
            except ValueError:
                data = {"description": f"HTTP {r.status_code}"}
            if data.get("ok"):
                return data["result"]
            last = data.get("description") or f"HTTP {r.status_code}"
            retry = (data.get("parameters") or {}).get("retry_after")
            if r.status_code == 429 and retry:
                time.sleep(min(int(retry), 60) + 1)
                continue
            if r.status_code >= 500:  # «Bad Gateway» и подобное у Telegram обычно проходит за секунды
                time.sleep(5 * (attempt + 1))
                continue
            if r.status_code == 401:
                last += " — токен бота неверный: проверьте секрет TELEGRAM_BOT_TOKEN"
            raise RuntimeError(f"Telegram {method}: {last}")
        raise RuntimeError(f"Telegram {method}: {last} (после 5 попыток)")

    def send(self, chat_id: str, text: str) -> dict:
        return self.call("sendMessage", {"chat_id": chat_id, "text": text, "parse_mode": "HTML",
                                         "link_preview_options": {"is_disabled": True}})

    def is_public(self, chat_id: str) -> bool:
        chat = self.call("getChat", {"chat_id": chat_id})
        return chat.get("type") != "private" and bool(chat.get("username"))


PAUSE = 1.1          # секунды между сообщениями: Telegram не любит больше одного сообщения в секунду в один чат
MAX_ATTEMPTS = 3     # после стольких неудачных попыток статья больше не отправляется


def answer_start(api: TelegramAPI, state: dict, chat_id: str) -> None:
    """Отвечает на сообщения в личке бота: подсказывает chat_id, если он ещё не задан."""
    offset = state.get("update_offset")
    updates = api.call("getUpdates", {"timeout": 0, **({"offset": offset} if offset else {})})
    for u in updates:
        state["update_offset"] = u["update_id"] + 1
        msg = u.get("message") or {}
        chat = msg.get("chat") or {}
        if chat.get("type") != "private":
            continue
        if not chat_id:
            api.send(str(chat["id"]),
                     f"👋 Это бот «Дайджеста ИИ». Ваш chat_id: <code>{chat['id']}</code>\n\n"
                     "Добавьте его в секреты репозитория GitHub под именем <b>TELEGRAM_CHAT_ID</b> — "
                     "после этого статьи начнут приходить сюда.")
        elif str(chat["id"]) == str(chat_id) and (msg.get("text") or "").startswith("/start"):
            api.send(str(chat["id"]), "✅ Бот подключён: новые статьи дайджеста приходят в этот чат.")
    if updates:  # подтвердить прочтение, чтобы не отвечать повторно
        api.call("getUpdates", {"timeout": 0, "offset": state["update_offset"]})


def _plain(message: str) -> str:
    """Запасной вариант, если Telegram не принял разметку: тот же текст без тегов."""
    soup = BeautifulSoup(message, "html.parser")
    for a in soup.find_all("a"):
        href = a.get("href", "")
        a.replace_with(f"{a.get_text()} ({href})" if href and href != a.get_text() else a.get_text())
    return soup.get_text()[:TG_LIMIT]


def _send(api: TelegramAPI, chat_id: str, message: str) -> None:
    try:
        api.send(chat_id, message)
    except RuntimeError as exc:
        if "parse" not in str(exc).lower() and "entit" not in str(exc).lower():
            raise
        log.warning("Telegram не принял разметку (%s) — отправляю без неё", exc)
        api.call("sendMessage", {"chat_id": chat_id, "text": _plain(message),
                                 "link_preview_options": {"is_disabled": True}})


def mirror(items: list[dict], sources: dict, load_full, state: dict, *, api: TelegramAPI, chat_id: str,
           site_url: str, max_posts: int, problem, save=lambda: None) -> int:
    """Отправляет в Telegram готовые статьи, которых там ещё не было. Возвращает число отправленных статей.

    Статья готова, когда судьба полного перевода решена (переведена, платная, слишком длинная…).
    В личный чат или закрытый канал уходит полный перевод, в публичный — только абстракт и ссылки.
    """
    try:
        answer_start(api, state, chat_id)
    except Exception as exc:
        problem(str(exc))
    if not chat_id:
        log.info("Telegram: TELEGRAM_CHAT_ID не задан — напишите боту /start, он подскажет chat_id")
        return 0
    try:
        public = api.is_public(chat_id)
    except Exception as exc:
        problem(str(exc))
        return 0
    ready = [it for it in items if not it.get("tg") and it.get("full") != "pending"]
    ready.sort(key=lambda it: it["published"], reverse=True)  # сначала самые свежие
    sent = 0
    for it in ready[:max_posts]:
        html_ru = None
        try:
            if it.get("full") == "ok" and not public:
                html_ru = load_full(it)
            msgs = build_messages(it, sources.get(it["source"], {}), html_ru, site_url)
        except Exception as exc:
            problem(f"Telegram: не удалось подготовить «{it['title_en']}»: {exc}")
            it["tg"] = "error"
            continue
        delivered = 0
        try:
            for m in msgs:
                if delivered:
                    time.sleep(PAUSE)
                _send(api, chat_id, m)
                delivered += 1
        except Exception as exc:
            problem(f"Telegram: отправка «{it['title_en']}»: {exc}")
            it["tg_attempts"] = it.get("tg_attempts", 0) + 1
            if delivered:
                it["tg"] = "partial"  # часть ушла — не дублировать
            elif it["tg_attempts"] >= MAX_ATTEMPTS:
                it["tg"] = "error"
            break  # чаще всего это сеть или лимит Telegram — остальное в следующий раз
        it["tg"] = "full" if html_ru else "abstract"
        it.pop("tg_attempts", None)
        save()  # сразу записать отметку: если прогон оборвётся, статья не уйдёт второй раз
        sent += 1
        log.info("Telegram: отправлено (%d сообщ.) «%s»", delivered, it["title_en"])
        time.sleep(PAUSE)
    return sent
