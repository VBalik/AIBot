# Дайджест ИИ

Личная страница с лучшими англоязычными текстами об ИИ. По каждому тексту есть русский абстракт на 3–5 предложений, а по нажатию открывается полный литературный перевод.

**Источники:** Simon Willison, Import AI, One Useful Thing, Interconnects, Latent Space, Ahead of AI (Raschka), Understanding AI, AI as Normal Technology, Andrej Karpathy, Dwarkesh Podcast, Benedict Evans. Список лежит в `digest/feeds.py`.

## Как это работает

```
GitHub Actions (каждый час)                     Netlify
  digest/build.py                                 site/index.html  ← публикуется только при изменении кода
   ├─ RSS 11 блогов                                     │
   ├─ отбор 1–10        (Gemini Flash-Lite / Claude Haiku)   │ при открытии страница сама берёт свежие данные
   ├─ заголовок+абстракт (Gemini Pro / Claude Sonnet)        ▼
   ├─ полный перевод     (Gemini Pro / Claude Opus)  ──►  ветка `data` этого репозитория
   └─ шифрование перевода паролем                        (feed.json, full/*.json)
```

- **Два движка.** По умолчанию работает Gemini: он покрывается ежемесячными кредитами подписки Google AI Ultra. Claude включается переменной `PROVIDER=claude`. Если заданы оба ключа, первые 3 поста дополнительно переводит второй движок, и в читалке появляется переключатель **Gemini / Claude** для сравнения.
- **Netlify без расхода кредитов.** На бесплатном тарифе каждый production-деплой стоит 15 из 300 месячных кредитов. Поэтому Netlify отдаёт только саму страницу, а данные обновляются в ветке `data` на GitHub и деплоев не вызывают.
- **Полные переводы зашифрованы** (AES-256-GCM, ключ из пароля). Абстракты видны всем, у кого есть адрес. Полный перевод открывается только после ввода пароля, и на устройстве пароль запоминается.
- **Без перевода остаются платные посты и очень длинные тексты.** У них только абстракт и ссылка на оригинал.

## Установка (можно с телефона, в браузере)

1. **Ключ Gemini и кредиты Ultra.**
   - Откройте aistudio.google.com. Если в левом меню видны невостребованные кредиты, откройте окно **Claim Google Cloud Credits** и нажмите **Redeem and Continue**. В этом же процессе создаётся профиль Google Developer Program и платёжный аккаунт Google Cloud. Для работы через Cloud billing нужна разовая предоплата $5, после этого начинают списываться кредиты подписки.
   - Ключ: aistudio.google.com/app/apikey → **Create API key** → скопируйте его.
2. **(Необязательно) ключ Claude** — для сравнения переводов или работы на Claude. На platform.claude.com/settings/billing → **Buy credits**, затем на platform.claude.com/settings/keys → **Create key**.
3. **Секреты в GitHub.** На github.com/VBalik/AIBot/settings/secrets/actions → **New repository secret** добавьте:
   - `GEMINI_API_KEY` — ключ из AI Studio;
   - `DIGEST_PASSWORD` — придуманный вами пароль для полных переводов. Лучше длинный, например из четырёх случайных слов;
   - `ANTHROPIC_API_KEY` — по желанию, ключ Claude.
4. **Первый запуск.** github.com/VBalik/AIBot/actions/workflows/update.yml → **Run workflow**. Через несколько минут появится ветка `data`. Дальше сборщик запускается сам каждый час.
5. **Netlify.** На app.netlify.com откройте **Add new project** → **Import an existing project** → **GitHub** → репозиторий `AIBot` → **Deploy**. Настройки подтянутся из `netlify.toml`. Если репозитория нет в списке, откройте Netlify доступ к нему на github.com/settings/installations.
6. На iPhone откройте адрес страницы в Safari → **Share** → **Add to Home Screen**.

## Настройки

Задаются на github.com/VBalik/AIBot/settings/variables/actions (вкладка **Variables**), все необязательные:

| Переменная | По умолчанию | Что делает |
|---|---|---|
| `PROVIDER` | auto (Gemini, если есть его ключ) | Основной движок: `gemini` или `claude` |
| `COMPARE_PROVIDER` | auto (второй движок, если есть его ключ) | Чем переводить для сравнения; `off` — не сравнивать |
| `COMPARE_LIMIT` | 3 | Сколько постов перевести вторым движком |
| `GEMINI_MODEL_TRANSLATE` | gemini-3.1-pro-preview | Модель-переводчик Gemini (и `GEMINI_MODEL_ABSTRACT`, `GEMINI_MODEL_SCORE`) |
| `GEMINI_THINKING_LEVEL` | LOW | Глубина «размышлений» Gemini: MINIMAL, LOW, MEDIUM, HIGH |
| `CLAUDE_MODEL_TRANSLATE` | claude-opus-5-5 | Модель-переводчик Claude (и `CLAUDE_MODEL_ABSTRACT`, `CLAUDE_MODEL_SCORE`) |
| `SHOW_MIN_SCORE` | 6 | С какой оценки (1–10) пост попадает на страницу |
| `TRANSLATE_MIN_SCORE` | 6 | С какой оценки делается полный перевод |
| `MAX_TRANSLATIONS_PER_RUN` | 4 | Сколько переводов за один запуск |
| `MAX_TRANSLATE_CHARS` | 70000 | Тексты длиннее — только абстракт |
| `INTERESTS` | список в `build.py` | Ваши темы для отбора (строки через `\n`) |

## Локальная проверка

```bash
pip install -r requirements.txt playwright && playwright install chromium
python tests/test_build.py    # сборщик с имитацией Gemini и Claude API → tests/out/data
python tests/test_site.py     # страница в браузере → скриншоты в tests/out
```
