# Дайджест ИИ

Личная страница с лучшими англоязычными текстами об ИИ. По каждому тексту есть русский абстракт на 3–5 предложений, а по нажатию открывается полный литературный перевод.

**Источники:** Simon Willison, Import AI, One Useful Thing, Interconnects, Latent Space, Ahead of AI (Raschka), Understanding AI, AI as Normal Technology, Andrej Karpathy, Dwarkesh Podcast, Benedict Evans. Список лежит в `digest/feeds.py`.

## Как это работает

```
GitHub Actions (каждый час)                    Netlify
  digest/build.py                                site/index.html  ← публикуется только при изменении кода
   ├─ RSS 11 блогов                                   │
   ├─ Claude Haiku: оценка 1–10                       │ при открытии страница сама берёт свежие данные
   ├─ Claude Sonnet: заголовок + абстракт             ▼
   ├─ Claude Opus: полный перевод        ──►  ветка `data` этого репозитория
   └─ шифрование перевода паролем             (feed.json, full/*.json)
```

- **Netlify без расхода кредитов.** На бесплатном тарифе каждый production-деплой стоит 15 из 300 месячных кредитов. Поэтому Netlify отдаёт только саму страницу, а данные обновляются в ветке `data` на GitHub и деплоев не вызывают.
- **Полные переводы зашифрованы** (AES-256-GCM, ключ из пароля). Абстракты видны всем, у кого есть адрес. Полный перевод открывается только после ввода пароля, и на устройстве пароль запоминается. Так полные тексты авторов не лежат в открытом доступе.
- **Без перевода остаются платные посты и очень длинные тексты** (например, расшифровки подкастов на 2–3 часа). У них только абстракт и ссылка на оригинал.

## Установка (можно с телефона, в браузере)

1. **Ключ Claude API.** На console.anthropic.com откройте **API Keys** → **Create Key** и скопируйте ключ.
2. **Секреты в GitHub.** В репозитории откройте **Settings** → **Secrets and variables** → **Actions** → **New repository secret**. Добавьте два секрета:
   - `ANTHROPIC_API_KEY` — ключ из шага 1;
   - `DIGEST_PASSWORD` — придуманный вами пароль для полных переводов. Сделайте его длинным: например, четыре случайных слова.
3. **Первый запуск.** Откройте **Actions** → **Update digest** → **Run workflow**. Через несколько минут появится ветка `data`. Дальше сборщик запускается сам каждый час.
4. **Netlify.** Откройте app.netlify.com → **Add new project** → **Import an existing project** → **GitHub** → репозиторий `AIBot`. Настройки подтянутся из `netlify.toml`. Нажмите **Deploy**. Адрес можно сменить в **Project configuration** → **Change project name**.
5. На iPhone откройте адрес в Safari → **Share** → **Add to Home Screen**. Страница будет открываться как приложение.

## Настройки

Задаются в **Settings** → **Secrets and variables** → **Actions** → вкладка **Variables** (необязательно):

| Переменная | По умолчанию | Что делает |
|---|---|---|
| `SHOW_MIN_SCORE` | 6 | С какой оценки (1–10) пост попадает на страницу |
| `TRANSLATE_MIN_SCORE` | 6 | С какой оценки делается полный перевод |
| `MAX_TRANSLATIONS_PER_RUN` | 4 | Сколько переводов за один запуск (ограничивает расходы) |
| `MAX_TRANSLATE_CHARS` | 70000 | Тексты длиннее — только абстракт |
| `MODEL_TRANSLATE` | claude-opus-5-5 | Модель переводчика |
| `INTERESTS` | список в `build.py` | Ваши темы для отбора (строки через `\n`) |

## Локальная проверка

```bash
pip install -r requirements.txt playwright && playwright install chromium
python tests/test_build.py    # сборщик с имитацией Claude API → tests/out/data
python tests/test_site.py     # страница в браузере → скриншоты в tests/out
```
