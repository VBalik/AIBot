#!/usr/bin/env python3
"""
AI Digest — сборщик данных для страницы.

Читает RSS-ленты из digest/feeds.py и для каждого нового поста:
  1. оценивает интересность (1–10);
  2. для интересных пишет русский заголовок и абстракт на 3–5 предложений;
  3. делает полный литературный перевод и шифрует его паролем DIGEST_PASSWORD.

Модели: Gemini (по умолчанию, если задан GEMINI_API_KEY) или Claude (ANTHROPIC_API_KEY), переключатель PROVIDER.
Если заданы оба ключа, первые COMPARE_LIMIT постов дополнительно переводит второй движок —
на странице появляется переключатель, чтобы сравнить качество.

Результат — каталог данных (в GitHub он живёт в ветке `data`):
  feed.json        список постов с абстрактами + параметры шифрования
  full/<id>.json   полный перевод: gzip(JSON) → AES-256-GCM, ключ = PBKDF2-SHA256(пароль)
  full/<id>--<движок>.json   второй перевод для сравнения (если есть)
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
import time
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
    gemini_key: str = ""
    password: str = ""
    provider: str = "auto"           # gemini | claude | auto (= gemini, если есть его ключ)
    compare_provider: str = "auto"   # второй переводчик для сравнения: claude | gemini | off | auto (= другой, если есть ключ)
    compare_limit: int = 3           # сколько постов перевести вторым движком
    # Claude
    claude_model_score: str = "claude-haiku-5-5"
    claude_model_abstract: str = "claude-sonnet-5-5"
    claude_model_translate: str = "claude-opus-5-5"
    # Gemini
    gemini_model_score: str = "gemini-3.1-flash-lite"
    gemini_model_abstract: str = "gemini-3.1-pro-preview"
    gemini_model_translate: str = "gemini-3.1-pro-preview"
    gemini_thinking: str = "LOW"     # MINIMAL | LOW | MEDIUM | HIGH; пусто — по умолчанию модели
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
        e = lambda name, attr: _env(name, str(getattr(cls, attr)))
        return cls(
            anthropic_key=_env("ANTHROPIC_API_KEY", ""),
            gemini_key=_env("GEMINI_API_KEY", ""),
            password=_env("DIGEST_PASSWORD", ""),
            provider=e("PROVIDER", "provider").lower(),
            compare_provider=e("COMPARE_PROVIDER", "compare_provider").lower(),
            compare_limit=int(e("COMPARE_LIMIT", "compare_limit")),
            claude_model_score=e("CLAUDE_MODEL_SCORE", "claude_model_score"),
            claude_model_abstract=e("CLAUDE_MODEL_ABSTRACT", "claude_model_abstract"),
            claude_model_translate=e("CLAUDE_MODEL_TRANSLATE", "claude_model_translate"),
            gemini_model_score=e("GEMINI_MODEL_SCORE", "gemini_model_score"),
            gemini_model_abstract=e("GEMINI_MODEL_ABSTRACT", "gemini_model_abstract"),
            gemini_model_translate=e("GEMINI_MODEL_TRANSLATE", "gemini_model_translate"),
            gemini_thinking=e("GEMINI_THINKING_LEVEL", "gemini_thinking").upper(),
            show_min_score=int(e("SHOW_MIN_SCORE", "show_min_score")),
            translate_min_score=int(e("TRANSLATE_MIN_SCORE", "translate_min_score")),
            max_translations_per_run=int(e("MAX_TRANSLATIONS_PER_RUN", "max_translations_per_run")),
            max_translate_chars=int(e("MAX_TRANSLATE_CHARS", "max_translate_chars")),
            lookback_days=int(e("LOOKBACK_DAYS", "lookback_days")),
            keep_items=int(e("KEEP_ITEMS", "keep_items")),
            interests=_env("INTERESTS", DEFAULT_INTERESTS).replace("\\n", "\n"),
        )

    def main_provider(self) -> str:
        if self.provider == "auto":
            return "gemini" if self.gemini_key else "claude"
        return self.provider

    def second_provider(self) -> str | None:
        main = self.main_provider()
        want = self.compare_provider
        if want in ("", "off", "none", "no", "0") or self.compare_limit <= 0:
            return None
        if want == "auto":
            want = "claude" if main == "gemini" else "gemini"
        if want == main or not self.has_key(want):
            return None
        return want

    def has_key(self, provider: str) -> bool:
        return bool(self.gemini_key if provider == "gemini" else self.anthropic_key)

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
    """Ответ модели упёрся в лимит длины — фрагмент нужно разделить."""


class ClaudeLLM:
    name, label = "claude", "Claude"

    def __init__(self, cfg: Config, client: anthropic.Anthropic | None = None):
        self.models = {"score": cfg.claude_model_score, "abstract": cfg.claude_model_abstract,
                       "translate": cfg.claude_model_translate}
        self.client = client or anthropic.Anthropic(api_key=cfg.anthropic_key or None)

    def structured(self, step: str, system: str, tool: dict, user: str, max_tokens: int) -> dict:
        resp = self.client.messages.create(
            model=self.models[step], max_tokens=max_tokens, system=system,
            tools=[tool], tool_choice={"type": "tool", "name": tool["name"]},
            messages=[{"role": "user", "content": user}],
        )
        for block in resp.content:
            if block.type == "tool_use":
                return block.input
        raise RuntimeError(f"Claude не вернул результат ({resp.stop_reason})")

    def text(self, step: str, system: str, user: str) -> str:
        with self.client.messages.stream(
            model=self.models[step], max_tokens=16000, system=system,
            messages=[{"role": "user", "content": user}],
        ) as stream:
            msg = stream.get_final_message()
        if msg.stop_reason == "max_tokens":
            raise TruncatedOutput()
        return "".join(b.text for b in msg.content if b.type == "text")


GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def to_gemini_schema(schema: dict) -> dict:
    """JSON Schema из описания инструмента → подмножество OpenAPI, которое понимает responseSchema Gemini."""
    out: dict = {}
    if "type" in schema:
        out["type"] = str(schema["type"]).upper()
    if "description" in schema:
        out["description"] = schema["description"]
    if "properties" in schema:
        out["properties"] = {k: to_gemini_schema(v) for k, v in schema["properties"].items()}
    if "required" in schema:
        out["required"] = list(schema["required"])
    if "items" in schema:
        out["items"] = to_gemini_schema(schema["items"])
    return out


class GeminiLLM:
    """Gemini API через REST (без SDK): ключ в заголовке x-goog-api-key."""
    name, label = "gemini", "Gemini"
    RETRY_STATUSES = (429, 500, 502, 503, 504)

    def __init__(self, cfg: Config, http: httpx.Client | None = None):
        self.key = cfg.gemini_key
        self.thinking = cfg.gemini_thinking.strip().upper()
        self.models = {"score": cfg.gemini_model_score, "abstract": cfg.gemini_model_abstract,
                       "translate": cfg.gemini_model_translate}
        self.http = http or httpx.Client(timeout=httpx.Timeout(900, connect=30))

    def _generate(self, step: str, system: str, user: str, max_tokens: int, schema: dict | None = None):
        gen: dict = {"maxOutputTokens": max_tokens}
        if self.thinking:
            gen["thinkingConfig"] = {"thinkingLevel": self.thinking}
        if schema:
            gen["responseMimeType"] = "application/json"
            gen["responseSchema"] = schema
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": gen,
        }
        url = GEMINI_URL.format(model=self.models[step])
        for attempt in range(5):
            r = self.http.post(url, json=body, headers={"x-goog-api-key": self.key})
            if r.status_code == 429 and "limit: 0" in r.text:
                # не перегрузка, а модель недоступна на бесплатном уровне — повторять бессмысленно
                raise RuntimeError(f"Модель {self.models[step]} недоступна на бесплатном уровне Gemini API. "
                                   "Включите оплату проекта в AI Studio (Set up billing) или выберите бесплатную "
                                   "модель Flash в переменных GEMINI_MODEL_*.")
            if r.status_code in self.RETRY_STATUSES and attempt < 4:
                wait = min(120, 10 * 2 ** attempt)
                log.warning("Gemini %s, повтор через %d с", r.status_code, wait)
                time.sleep(wait)
                continue
            break
        if r.status_code != 200:
            try:
                detail = r.json().get("error", {}).get("message", "")
            except Exception:
                detail = r.text[:300]
            raise RuntimeError(f"Gemini {r.status_code} ({self.models[step]}): {detail}")
        data = r.json()
        candidates = data.get("candidates") or []
        if not candidates:
            raise RuntimeError(f"Gemini не вернул ответ: {data.get('promptFeedback')}")
        cand = candidates[0]
        parts = (cand.get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        return text, cand.get("finishReason", "")

    def structured(self, step: str, system: str, tool: dict, user: str, max_tokens: int) -> dict:
        # в лимит Gemini входят и «размышления», поэтому даём запас
        text, reason = self._generate(step, system, user, max_tokens + 8192, to_gemini_schema(tool["input_schema"]))
        if reason == "MAX_TOKENS":
            raise RuntimeError("Gemini: ответ обрезан по длине")
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
        return json.loads(text)

    def text(self, step: str, system: str, user: str) -> str:
        text, reason = self._generate(step, system, user, 32768)
        if reason == "MAX_TOKENS":
            raise TruncatedOutput()
        return text


def make_llm(provider: str, cfg: Config):
    if provider == "gemini":
        if not cfg.gemini_key:
            raise SystemExit("Не задан GEMINI_API_KEY")
        return GeminiLLM(cfg)
    if provider == "claude":
        if not cfg.anthropic_key:
            raise SystemExit("Не задан ANTHROPIC_API_KEY")
        return ClaudeLLM(cfg)
    raise SystemExit(f"Неизвестный PROVIDER: {provider} (нужно gemini или claude)")


class Writer:
    """Шаги дайджеста поверх любой модели: оценка, абстракт, полный перевод."""

    def __init__(self, cfg: Config, llm):
        self.cfg = cfg
        self.llm = llm

    def score(self, post: Post) -> tuple[int, str]:
        system = ("Ты отбираешь посты для персонального дайджеста лучших текстов об ИИ. Интересы читателя:\n"
                  f"{self.cfg.interests}\n\nОценивай строго: 7+ ставь только тому, что действительно стоит прочитать; "
                  "оригинальный анализ и собственные эксперименты автора ценнее пересказа новостей.")
        user = (f"Источник: {post.feed['name']} ({post.feed['author']})\nЗаголовок: {post.title}\n"
                f"{'(Это только начало платного поста.)' if post.paywalled else ''}\n\n"
                f"Текст (начало):\n{post.text[:12000]}")
        out = self.llm.structured("score", system, SCORE_TOOL, user, 300)
        return max(1, min(10, int(out["score"]))), str(out.get("reason", "")).strip()

    def abstract(self, post: Post) -> dict:
        user = (f"Источник: {post.feed['name']} ({post.feed['author']})\nЗаголовок: {post.title}\n"
                f"{'(Это только бесплатное начало платного поста.)' if post.paywalled else ''}\n\n"
                f"Текст:\n{post.text[:100000]}")
        out = self.llm.structured("abstract", ABSTRACT_SYSTEM, ABSTRACT_TOOL, user, 1200)
        return {"title_ru": out["title_ru"].strip(), "abstract_ru": out["abstract_ru"].strip()}

    # --- полный перевод -------------------------------------------------
    def _translate_chunk(self, chunk: str, context: str, post: Post, label: str) -> str:
        user = f"Источник: {post.feed['name']} — «{post.title}». {label}.\n"
        if context:
            user += ("\nКонец перевода предыдущего фрагмента — только для единообразия терминов и стиля, "
                     f"не переводи и не повторяй его:\n<context>\n{context}\n</context>\n")
        user += f"\nПереведи HTML-фрагмент:\n<source>\n{chunk}\n</source>"
        text = self.llm.text("translate", TRANSLATOR_SYSTEM, user).strip()
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
            log.info("    %s: фрагмент %d/%d готов", self.llm.label, i + 1, len(chunks))
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

    def full_path(self, pid: str, engine: str | None = None) -> Path:
        return self.root / "full" / (f"{pid}--{engine}.json" if engine else f"{pid}.json")

    def trim(self, keep: int, state_days: int = 120) -> None:
        ordered = sorted(self.items.values(), key=lambda it: it["published"], reverse=True)
        for it in ordered[keep:]:
            self.full_path(it["id"]).unlink(missing_ok=True)
            for engine in it.get("alt", []):
                self.full_path(it["id"], engine).unlink(missing_ok=True)
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


def run(cfg: Config, data: DataDir, writer: Writer, vault: Vault, fetch=fetch_feed,
        second: Writer | None = None) -> dict:
    data.crypto = vault.meta
    cutoff = _now() - timedelta(days=cfg.lookback_days)
    live: dict[str, Post] = {}
    stats = {"new": 0, "shown": 0, "translated": 0, "compared": 0, "errors": 0}

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
                score, reason = writer.score(post)
                abstract = writer.abstract(post) if score >= cfg.show_min_score else None
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
        log.info("Перевожу (%s): %s", writer.llm.label, post.title)
        try:
            html_ru = writer.translate(post)
        except Exception as exc:
            log.error("Перевод «%s» не удался: %s", post.title, exc)
            stats["errors"] += 1
            it["attempts"] = it.get("attempts", 0) + 1
            if it["attempts"] >= 3:
                it["full"] = "error"
            data.save()
            continue
        _save_translation(data, vault, it, writer, html_ru)
        it["full"] = "ok"
        it["engine"] = writer.llm.name
        it.pop("attempts", None)
        stats["translated"] += 1
        data.save()

        # 3. Второй перевод того же поста для сравнения (первые compare_limit постов)
        if second is not None and data.state.get("compare_done", 0) < cfg.compare_limit:
            log.info("Перевожу для сравнения (%s): %s", second.llm.label, post.title)
            try:
                alt_html = second.translate(post)
            except Exception as exc:
                log.warning("Сравнительный перевод не удался: %s", exc)
                stats["errors"] += 1
                continue
            _save_translation(data, vault, it, second, alt_html, alt=True)
            it["alt"] = sorted(set(it.get("alt", [])) | {second.llm.name})
            data.state["compare_done"] = data.state.get("compare_done", 0) + 1
            stats["compared"] += 1
            data.save()

    data.trim(cfg.keep_items)
    data.save()
    return stats


def _save_translation(data: DataDir, vault: Vault, it: dict, writer: Writer, html_ru: str, alt: bool = False) -> None:
    payload = {"html": html_ru, "engine": writer.llm.name, "model": writer.llm.models["translate"],
               "translated_at": _iso(_now())}
    path = data.full_path(it["id"], writer.llm.name if alt else None)
    _write_json(path, vault.encrypt_json(payload), compact=True)


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

    main_name = cfg.main_provider()
    writer = Writer(cfg, make_llm(main_name, cfg))
    second_name = cfg.second_provider()
    second = Writer(cfg, make_llm(second_name, cfg)) if second_name else None
    m = writer.llm.models
    log.info("Движок: %s — отбор %s, абстракты %s, перевод %s", writer.llm.label, m["score"], m["abstract"], m["translate"])
    if second:
        log.info("Сравнение: %s (%s), осталось постов: %d", second.llm.label, second.llm.models["translate"],
                 max(0, cfg.compare_limit))

    data = DataDir(Path(args.data))
    vault = Vault(cfg.password, data.crypto, cfg.kdf_iterations)
    stats = run(cfg, data, writer, vault, second=second)
    log.info("Готово: новых %(new)d, на страницу %(shown)d, переведено %(translated)d, "
             "для сравнения %(compared)d, ошибок %(errors)d", stats)


if __name__ == "__main__":
    main()
