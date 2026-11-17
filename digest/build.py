#!/usr/bin/env python3
"""
AI Digest — сборщик данных для страницы.

Читает RSS-ленты из digest/feeds.py и для каждого нового поста:
  1. оценивает интересность (Claude Haiku, 1–10);
  2. для интересных пишет русский заголовок и абстракт на 3–5 предложений (Claude Sonnet);
  3. делает полный литературный перевод (Claude Opus) и шифрует его паролем DIGEST_PASSWORD.

Результат — каталог данных (в GitHub он живёт в ветке `data`):
  feed.json        список постов с абстрактами + параметры шифрования
  full/<id>.json   полный перевод: gzip(JSON) → AES-256-GCM, ключ = PBKDF2-SHA256(пароль)
  state.json       какие посты уже видели и с какой оценкой

Запуск:
  python digest/build.py --data ./data
  python digest/build.py --data ./data --max-translations 0   # только оценки и абстракты
"""
from __future__ import annotations

import argparse
import base64
import calendar
import gzip
import hashlib
import html
import json
import logging
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import anthropic
import feedparser
import httpx
from bs4 import BeautifulSoup, Comment
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

sys.path.insert(0, str(Path(__file__).resolve().parent))
from feeds import FEEDS  # noqa: E402

log = logging.getLogger("ai-digest")

DEFAULT_INTERESTS = """\
- тестирование и честная оценка новых моделей: что они реально умеют и где ломаются;
- бизнесы и продукты на основе ИИ, экономика и стратегия AI-компаний;
- практическое применение LLM и AI-агентов, программирование с ИИ;
- AI для науки: химия, материаловедение, катализ, энергетика;
- идеи о дальнейшем развитии ИИ от ведущих исследователей и мыслителей;
- безопасность и надёжность AI-систем.
Неинтересно: мелкие заметки, анонсы мероприятий и вакансий, пересказы чужих новостей без собственного анализа."""


# ---------------------------------------------------------------------------
# Настройки
# ---------------------------------------------------------------------------

def _env(name: str, default: str) -> str:
    return os.environ.get(name) or default


@dataclass
class Config:
    anthropic_key: str = ""
    password: str = ""
    model_score: str = "claude-haiku-5-5"
    model_abstract: str = "claude-sonnet-5-5"
    model_translate: str = "claude-opus-5-5"
    show_min_score: int = 6          # с этой оценки пост попадает на страницу (абстракт)
    translate_min_score: int = 6     # с этой оценки делается полный перевод
    max_translations_per_run: int = 4
    max_translate_chars: int = 70000  # длиннее (например, расшифровки подкастов) — только абстракт
    lookback_days: int = 10
    keep_items: int = 300
    chunk_chars: int = 9000
    kdf_iterations: int = 310000
    interests: str = DEFAULT_INTERESTS

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            anthropic_key=_env("ANTHROPIC_API_KEY", ""),
            password=_env("DIGEST_PASSWORD", ""),
            model_score=_env("MODEL_SCORE", cls.model_score),
            model_abstract=_env("MODEL_ABSTRACT", cls.model_abstract),
            model_translate=_env("MODEL_TRANSLATE", cls.model_translate),
            show_min_score=int(_env("SHOW_MIN_SCORE", str(cls.show_min_score))),
            translate_min_score=int(_env("TRANSLATE_MIN_SCORE", str(cls.translate_min_score))),
            max_translations_per_run=int(_env("MAX_TRANSLATIONS_PER_RUN", str(cls.max_translations_per_run))),
            max_translate_chars=int(_env("MAX_TRANSLATE_CHARS", str(cls.max_translate_chars))),
            lookback_days=int(_env("LOOKBACK_DAYS", str(cls.lookback_days))),
            keep_items=int(_env("KEEP_ITEMS", str(cls.keep_items))),
            interests=_env("INTERESTS", DEFAULT_INTERESTS).replace("\\n", "\n"),
        )


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(d: datetime) -> str:
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


# ---------------------------------------------------------------------------
# Ленты и очистка HTML
# ---------------------------------------------------------------------------

@dataclass
class Post:
    id: str
    feed: dict
    title: str
    url: str
    published: datetime
    html: str
    paywalled: bool = False

    @property
    def text(self) -> str:
        return BeautifulSoup(self.html, "html.parser").get_text("\n", strip=True)


HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
BLOCKS = HEADINGS | {"p", "ul", "ol", "li", "pre", "blockquote", "table", "thead", "tbody",
                     "tr", "td", "th", "hr", "figure", "figcaption"}
INLINE = {"a", "b", "strong", "i", "em", "code", "br", "img", "sup", "sub", "s", "del", "u"}
DROP_TAGS = ["script", "style", "iframe", "svg", "button", "form", "input", "noscript",
             "video", "audio", "source", "object", "embed"]
DROP_CLASS_RE = re.compile(r"subscri|share|button|social|paywall|header-anchor|image-link-expand", re.I)
PAYWALL_RE = re.compile(r">\s*Read more\s*<|paid subscribers|Keep reading with a \d+-day free trial|"
                        r"This post is for paid subscribers|Upgrade to paid", re.I)


def sanitize_html(raw: str) -> BeautifulSoup:
    """Оставляет только смысловую разметку: абзацы, заголовки, списки, цитаты, код, ссылки, картинки."""
    soup = BeautifulSoup(raw or "", "html.parser")
    for c in soup.find_all(string=lambda s: isinstance(s, Comment)):
        c.extract()
    for t in soup.find_all(DROP_TAGS):
        t.decompose()
    for t in soup.find_all(True):
        if getattr(t, "decomposed", False):
            continue
        cls = " ".join(t.get("class") or [])
        if cls and DROP_CLASS_RE.search(cls):
            t.decompose()
    for a in soup.find_all("a"):  # ссылка-обёртка вокруг картинки -> просто картинка
        img = a.find("img")
        if img is not None:
            a.replace_with(img)
    for t in soup.find_all(True):
        if t.name not in BLOCKS | INLINE:
            t.unwrap()
            continue
        keep = {"a": ("href",), "img": ("src", "alt")}.get(t.name, ())
        t.attrs = {k: v for k, v in t.attrs.items() if k in keep}
        if t.name == "a" and not str(t.get("href", "")).startswith(("http://", "https://")):
            t.attrs.pop("href", None)
    for img in soup.find_all("img"):
        if not str(img.get("src", "")).startswith("https://"):
            img.decompose()
    for t in soup.find_all(["p", "li", "figcaption", *HEADINGS]):
        if not t.get_text(strip=True) and t.find("img") is None:
            t.decompose()
    return soup


def chunk_html(soup: BeautifulSoup, limit: int) -> list[str]:
    """Режет документ на фрагменты по границам верхнеуровневых блоков."""
    chunks, cur = [], ""
    for node in list(soup.contents):
        s = str(node).strip()
        if not s:
            continue
        if cur and len(cur) + len(s) > limit:
            chunks.append(cur)
            cur = ""
        cur += s + "\n"
    if cur.strip():
        chunks.append(cur)
    return chunks


def post_id(feed: dict, guid: str) -> str:
    return feed["id"] + "-" + hashlib.sha1(guid.encode("utf-8")).hexdigest()[:10]


def parse_feed(feed: dict, raw: bytes | str) -> list[Post]:
    parsed = feedparser.parse(raw)
    exclude = re.compile(feed["exclude"]) if feed.get("exclude") else None
    posts = []
    for e in parsed.entries:
        title = html.unescape(e.get("title", "")).strip()
        if exclude and exclude.search(title):
            continue
        url = e.get("link", "")
        guid = e.get("id") or url
        if not guid:
            continue
        tm = e.get("published_parsed") or e.get("updated_parsed")
        published = datetime.fromtimestamp(calendar.timegm(tm), timezone.utc) if tm else _now()
        body = ""
        if e.get("content"):
            body = max((c.get("value", "") for c in e.content), key=len)
        if len(body) < len(e.get("summary", "")):
            body = e.get("summary", "")
        posts.append(Post(id=post_id(feed, guid), feed=feed, title=title, url=url, published=published,
                          html=body, paywalled=bool(PAYWALL_RE.search(body[-3000:]))))
    return posts


def fetch_feed(feed: dict) -> list[Post]:
    r = httpx.get(feed["url"], timeout=60, follow_redirects=True,
                  headers={"User-Agent": "Mozilla/5.0 (ai-digest; personal RSS reader)"})
    r.raise_for_status()
    return parse_feed(feed, r.content)


# ---------------------------------------------------------------------------
# Claude
# ---------------------------------------------------------------------------

SCORE_TOOL = {
    "name": "rate_post",
    "description": "Оценить, насколько пост интересен читателю.",
    "input_schema": {
        "type": "object",
        "properties": {
            "score": {"type": "integer", "minimum": 1, "maximum": 10,
                      "description": "1 = неинтересно, 10 = обязательно к прочтению"},
            "reason": {"type": "string", "description": "Одно короткое предложение по-русски: почему такая оценка"},
        },
        "required": ["score", "reason"],
    },
}

ABSTRACT_TOOL = {
    "name": "save_abstract",
    "description": "Сохранить русский заголовок и абстракт поста.",
    "input_schema": {
        "type": "object",
        "properties": {
            "title_ru": {"type": "string",
                         "description": "Литературный перевод заголовка на русский, до 110 знаков"},
            "abstract_ru": {"type": "string",
                            "description": "Абстракт по-русски: 3–5 предложений, 350–750 знаков, простым текстом"},
        },
        "required": ["title_ru", "abstract_ru"],
    },
}

ABSTRACT_SYSTEM = """Ты — редактор русскоязычного дайджеста лучших англоязычных текстов об искусственном интеллекте.
Напиши абстракт поста на 3–5 предложений своими словами:
- о чём текст и какой главный тезис или результат у автора;
- ключевые факты, цифры, названия моделей и компаний, если они важны;
- чем это ценно для читателя — что из этого следует.
Если это интервью или подкаст — назови собеседника и 2–3 его главные мысли.
Если перед тобой только начало платного поста — опиши то, что видно, и не домысливай остальное.
Пиши живым литературным русским языком, без канцелярита, без вводных «В этом посте автор…» и без цитат.
Названия моделей, компаний и продуктов — в оригинале латиницей; имена людей — в русской транскрипции."""

TRANSLATOR_SYSTEM = """Ты — профессиональный литературный переводчик научно-популярных и технических текстов \
об искусственном интеллекте с английского на русский. Твои переводы читаются так, будто текст изначально \
написан по-русски хорошим автором.

Точность
- Переводи всё полностью: ничего не пропускай, не сокращай, не пересказывай и не добавляй от себя.
- Сохраняй смысл, оттенки, иронию и интонацию автора. Авторское «I» — от первого лица.
- Цитаты из других источников внутри текста тоже переводи.

Русский язык
- Перестраивай фразы под русский синтаксис; избегай калек («сделать смысл», «в конце дня»), \
лишних местоимений, цепочек существительных и пассива там, где по-русски естественнее актив.
- Идиомы передавай русскими эквивалентами по смыслу, а не дословно.
- Кавычки «ёлочки» (внутренние „лапки“), длинное тире —, типографика по нормам русского языка.
- Числа: в обычном тексте — десятичная запятая и пробел между разрядами (1,5; 10 000); в коде и URL — без изменений.

Терминология
- Устоявшиеся русские термины используй, где они есть: языковая модель, обучение с подкреплением, \
веса модели, вывод (inference), контекстное окно, рассуждение.
- Где русского термина нет или он не прижился, оставляй английский: prompt injection, fine-tuning, \
embeddings, RAG; benchmark — «бенчмарк». Будь последователен во всём тексте.
- Названия компаний, моделей, продуктов, библиотек, статей и сайтов — в оригинале латиницей \
(Claude, OpenAI, GPT, Hugging Face). Имена людей — в устоявшейся русской транскрипции \
(Саймон Уиллисон, Итан Моллик, Джек Кларк).

Разметка
- Вход — HTML-фрагмент. Сохраняй все теги и атрибуты в точности на своих местах, переводи только текст \
(включая alt у картинок и подписи).
- Содержимое <code> и <pre>, URL и атрибуты href/src не переводи и не меняй.
- Верни только переведённый HTML — без пояснений, комментариев и обёрток ```."""


class TruncatedOutput(Exception):
    pass


class Claude:
    def __init__(self, cfg: Config, client: anthropic.Anthropic | None = None):
        self.cfg = cfg
        self.client = client or anthropic.Anthropic(api_key=cfg.anthropic_key or None)

    def _call_tool(self, model: str, system: str, tool: dict, user: str, max_tokens: int) -> dict:
        resp = self.client.messages.create(
            model=model, max_tokens=max_tokens, system=system,
            tools=[tool], tool_choice={"type": "tool", "name": tool["name"]},
            messages=[{"role": "user", "content": user}],
        )
        for block in resp.content:
            if block.type == "tool_use":
                return block.input
        raise RuntimeError(f"Модель не вернула результат ({resp.stop_reason})")

    def score(self, post: Post) -> tuple[int, str]:
        system = ("Ты отбираешь посты для персонального дайджеста лучших текстов об ИИ. Интересы читателя:\n"
                  f"{self.cfg.interests}\n\nОценивай строго: 7+ ставь только тому, что действительно стоит прочитать; "
                  "оригинальный анализ и собственные эксперименты автора ценнее пересказа новостей.")
        user = (f"Источник: {post.feed['name']} ({post.feed['author']})\nЗаголовок: {post.title}\n"
                f"{'(Это только начало платного поста.)' if post.paywalled else ''}\n\n"
                f"Текст (начало):\n{post.text[:12000]}")
        out = self._call_tool(self.cfg.model_score, system, SCORE_TOOL, user, 300)
        return max(1, min(10, int(out["score"]))), str(out.get("reason", "")).strip()

    def abstract(self, post: Post) -> dict:
        user = (f"Источник: {post.feed['name']} ({post.feed['author']})\nЗаголовок: {post.title}\n"
                f"{'(Это только бесплатное начало платного поста.)' if post.paywalled else ''}\n\n"
                f"Текст:\n{post.text[:100000]}")
        out = self._call_tool(self.cfg.model_abstract, ABSTRACT_SYSTEM, ABSTRACT_TOOL, user, 1200)
        return {"title_ru": out["title_ru"].strip(), "abstract_ru": out["abstract_ru"].strip()}

    # --- полный перевод -------------------------------------------------
    def _translate_chunk(self, chunk: str, context: str, post: Post, label: str) -> str:
        user = f"Источник: {post.feed['name']} — «{post.title}». {label}.\n"
        if context:
            user += ("\nКонец перевода предыдущего фрагмента — только для единообразия терминов и стиля, "
                     f"не переводи и не повторяй его:\n<context>\n{context}\n</context>\n")
        user += f"\nПереведи HTML-фрагмент:\n<source>\n{chunk}\n</source>"
        with self.client.messages.stream(
            model=self.cfg.model_translate, max_tokens=16000, system=TRANSLATOR_SYSTEM,
            messages=[{"role": "user", "content": user}],
        ) as stream:
            msg = stream.get_final_message()
        if msg.stop_reason == "max_tokens":
            raise TruncatedOutput()
        text = "".join(b.text for b in msg.content if b.type == "text").strip()
        text = re.sub(r"^```(?:html)?\s*|\s*```$", "", text)
        text = re.sub(r"^\s*<(?:source|translation)>\s*|\s*</(?:source|translation)>\s*$", "", text)
        return text

    def _translate_safe(self, chunk: str, context: str, post: Post, label: str) -> str:
        try:
            return self._translate_chunk(chunk, context, post, label)
        except TruncatedOutput:
            nodes = [str(n) for n in BeautifulSoup(chunk, "html.parser").contents if str(n).strip()]
            if len(nodes) < 2:
                raise RuntimeError("Слишком длинный неделимый фрагмент для перевода")
            mid = len(nodes) // 2
            first = self._translate_safe("\n".join(nodes[:mid]), context, post, label + " (часть 1)")
            ctx = BeautifulSoup(first, "html.parser").get_text(" ", strip=True)[-1500:]
            return first + "\n" + self._translate_safe("\n".join(nodes[mid:]), ctx, post, label + " (часть 2)")

    def translate(self, post: Post) -> str:
        """Полный перевод поста. Возвращает очищенный HTML."""
        chunks = chunk_html(sanitize_html(post.html), self.cfg.chunk_chars)
        if not chunks:
            raise RuntimeError("Пустой текст поста")
        parts, context = [], ""
        for i, ch in enumerate(chunks):
            tr = self._translate_safe(ch, context, post, f"Фрагмент {i + 1} из {len(chunks)}")
            parts.append(tr)
            context = BeautifulSoup(tr, "html.parser").get_text(" ", strip=True)[-1500:]
            log.info("    фрагмент %d/%d готов", i + 1, len(chunks))
        return str(sanitize_html("\n".join(parts))).strip()


# ---------------------------------------------------------------------------
# Шифрование полных переводов
# ---------------------------------------------------------------------------

CHECK_PLAINTEXT = "ai-digest-ok"


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


class Vault:
    """AES-256-GCM, ключ из пароля через PBKDF2-SHA256. Совместимо с WebCrypto в браузере."""

    def __init__(self, password: str, crypto_meta: dict | None, iterations: int):
        if not password:
            raise SystemExit("Не задан DIGEST_PASSWORD")
        if crypto_meta:
            self.meta = crypto_meta
        else:
            self.meta = {"kdf": "PBKDF2-SHA256", "iterations": iterations, "salt": _b64(os.urandom(16)),
                         "cipher": "AES-256-GCM", "compression": "gzip"}
        salt = base64.b64decode(self.meta["salt"])
        self.key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt,
                              iterations=int(self.meta["iterations"])).derive(password.encode("utf-8"))
        if "check" in self.meta:
            try:
                assert self._decrypt_raw(self.meta["check"]) == CHECK_PLAINTEXT.encode()
            except Exception:
                raise SystemExit("DIGEST_PASSWORD не совпадает с паролем, которым зашифрованы прежние переводы. "
                                 "Верните старый пароль или удалите ветку data, чтобы начать заново.")
        else:
            self.meta["check"] = self._encrypt_raw(CHECK_PLAINTEXT.encode())

    def _encrypt_raw(self, data: bytes) -> dict:
        iv = os.urandom(12)
        return {"iv": _b64(iv), "ct": _b64(AESGCM(self.key).encrypt(iv, data, None))}

    def _decrypt_raw(self, blob: dict) -> bytes:
        return AESGCM(self.key).decrypt(base64.b64decode(blob["iv"]), base64.b64decode(blob["ct"]), None)

    def encrypt_json(self, obj) -> dict:
        return self._encrypt_raw(gzip.compress(json.dumps(obj, ensure_ascii=False).encode("utf-8"), mtime=0))

    def decrypt_json(self, blob: dict):
        return json.loads(gzip.decompress(self._decrypt_raw(blob)))


# ---------------------------------------------------------------------------
# Каталог данных
# ---------------------------------------------------------------------------

def _load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def _write_json(path: Path, obj, compact: bool = False) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    text = json.dumps(obj, ensure_ascii=False, separators=(",", ":") if compact else None,
                      indent=None if compact else 1)
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


class DataDir:
    def __init__(self, root: Path):
        self.root = root
        (root / "full").mkdir(parents=True, exist_ok=True)
        self.state = _load_json(root / "state.json", {"version": 1, "posts": {}})
        feed = _load_json(root / "feed.json", {})
        self.crypto = feed.get("crypto")
        self.items: dict[str, dict] = {it["id"]: it for it in feed.get("items", [])}

    def seen(self, pid: str) -> bool:
        return pid in self.state["posts"]

    def mark(self, post: Post, status: str, score=None, reason=None) -> None:
        self.state["posts"][post.id] = {"status": status, "score": score, "reason": reason,
                                        "title": post.title, "published": _iso(post.published)}

    def full_path(self, pid: str) -> Path:
        return self.root / "full" / f"{pid}.json"

    def trim(self, keep: int, state_days: int = 120) -> None:
        ordered = sorted(self.items.values(), key=lambda it: it["published"], reverse=True)
        for it in ordered[keep:]:
            self.full_path(it["id"]).unlink(missing_ok=True)
            del self.items[it["id"]]
        cutoff = _iso(_now() - timedelta(days=state_days))
        self.state["posts"] = {k: v for k, v in self.state["posts"].items()
                               if v.get("published", "") >= cutoff or k in self.items}

    def save(self) -> None:
        items = sorted(self.items.values(), key=lambda it: it["published"], reverse=True)
        sources = [{k: f[k] for k in ("id", "symbol", "name", "author", "about", "site")} for f in FEEDS]
        feed = {
            "version": 1,
            "updated_at": max((it["added"] for it in items), default=None),
            "sources": sources,
            "crypto": self.crypto,
            "items": items,
        }
        _write_json(self.root / "feed.json", feed)
        _write_json(self.root / "state.json", self.state)


# ---------------------------------------------------------------------------
# Основной проход
# ---------------------------------------------------------------------------

def full_status_for(cfg: Config, post: Post, score: int) -> str:
    if score < cfg.translate_min_score:
        return "below"
    if post.paywalled:
        return "paywalled"
    if len(post.text) > cfg.max_translate_chars:
        return "too_long"
    return "pending"


def run(cfg: Config, data: DataDir, claude: Claude, vault: Vault, fetch=fetch_feed) -> dict:
    data.crypto = vault.meta
    cutoff = _now() - timedelta(days=cfg.lookback_days)
    live: dict[str, Post] = {}
    stats = {"new": 0, "shown": 0, "translated": 0, "errors": 0}

    # 1. Новые посты: оценка и абстракт
    for feed in FEEDS:
        try:
            posts = fetch(feed)
        except Exception as exc:
            log.warning("Лента %s недоступна: %s", feed["name"], exc)
            stats["errors"] += 1
            continue
        for post in posts:
            live[post.id] = post
            if data.seen(post.id):
                continue
            if post.published < cutoff:
                data.mark(post, "old")
                continue
            stats["new"] += 1
            try:
                score, reason = claude.score(post)
                abstract = claude.abstract(post) if score >= cfg.show_min_score else None
            except Exception as exc:
                log.error("Не удалось обработать «%s»: %s", post.title, exc)
                stats["errors"] += 1
                continue  # повторим в следующий раз
            if abstract:
                data.items[post.id] = {
                    "id": post.id,
                    "source": feed["id"],
                    "title_en": post.title,
                    "title_ru": abstract["title_ru"],
                    "abstract": abstract["abstract_ru"],
                    "url": post.url,
                    "published": _iso(post.published),
                    "added": _iso(_now()),
                    "score": score,
                    "words_en": len(post.text.split()),
                    "full": full_status_for(cfg, post, score),
                }
                stats["shown"] += 1
            data.mark(post, "shown" if abstract else "skipped", score, reason)
            log.info("[%2d] %-9s %s — %s", score, "показан" if abstract else "пропущен", post.title, reason)
            data.save()

    # 2. Полные переводы: сначала самые свежие
    pending = sorted((it for it in data.items.values() if it["full"] == "pending"),
                     key=lambda it: it["published"], reverse=True)
    for it in pending:
        if stats["translated"] >= cfg.max_translations_per_run:
            break
        post = live.get(it["id"])
        if post is None:
            it["full"] = "unavailable"
            continue
        log.info("Перевожу: %s", post.title)
        try:
            html_ru = claude.translate(post)
        except Exception as exc:
            log.error("Перевод «%s» не удался: %s", post.title, exc)
            stats["errors"] += 1
            it["attempts"] = it.get("attempts", 0) + 1
            if it["attempts"] >= 3:
                it["full"] = "error"
            data.save()
            continue
        blob = vault.encrypt_json({"html": html_ru, "model": cfg.model_translate, "translated_at": _iso(_now())})
        _write_json(data.full_path(it["id"]), blob, compact=True)
        it["full"] = "ok"
        it.pop("attempts", None)
        stats["translated"] += 1
        data.save()

    data.trim(cfg.keep_items)
    data.save()
    return stats


def main() -> None:
    ap = argparse.ArgumentParser(description="AI Digest — сборщик данных")
    ap.add_argument("--data", default="data", help="каталог данных (ветка data)")
    ap.add_argument("--max-translations", type=int, help="сколько полных переводов за проход")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    cfg = Config.from_env()
    if args.max_translations is not None:
        cfg.max_translations_per_run = args.max_translations
    if not cfg.anthropic_key:
        sys.exit("Не задан ANTHROPIC_API_KEY")

    data = DataDir(Path(args.data))
    vault = Vault(cfg.password, data.crypto, cfg.kdf_iterations)
    stats = run(cfg, data, Claude(cfg), vault)
    log.info("Готово: новых %(new)d, на страницу %(shown)d, переведено %(translated)d, ошибок %(errors)d", stats)


if __name__ == "__main__":
    main()
