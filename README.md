# Дайджест ИИ

Личная страница с лучшими англоязычными текстами об ИИ. По каждому тексту есть русский абстракт на 3–5 предложений, а по нажатию открывается полный литературный перевод.

**Источники:** Simon Willison, Import AI, One Useful Thing, Interconnects, Latent Space, Ahead of AI (Raschka), Understanding AI, AI as Normal Technology, Andrej Karpathy, Dwarkesh Podcast, Benedict Evans. Список лежит в `digest/feeds.py`.

## Как это работает

```
GitHub Actions, каждый час — digest/build.py
  ├─ RSS 11 блогов
  ├─ отбор 1–10           GPT-6 Luna   │ Gemini Flash-Lite │ Claude Haiku
  ├─ заголовок + абстракт GPT-6.1 Sol  │ Gemini Pro        │ Claude Haiku
  ├─ полный перевод       GPT-6 Astra  │ Gemini Pro        │ Claude Sonnet
  └─ шифрование перевода паролем
        │
        ▼
  ветка `data` этого репозитория (feed.json, full/*.json)
        ▲
        │ страница сама берёт свежие данные при открытии
Netlify — site/index.html (публикуется только при изменении кода)
```

- **Три движка, выбираются по ключам.** Порядок такой: OpenAI, потом Gemini, потом Claude. Основным становится первый, для которого задан ключ, а переменная `PROVIDER` выбирает движок явно.
  - **OpenAI** оплачивается кредитами ChatGPT Pro ($100 в месяц). Переводит GPT-6 Astra, абстракты пишет GPT-6.1 Sol, отбирает GPT-6 Luna, это примерно $20–35 в месяц из кредитов.
  - **Gemini** оплачивается кредитами Google AI Ultra.
  - **Claude** работает в экономном режиме: переводит Sonnet, отбирает и пишет абстракты Haiku, около $4 в месяц. Если и перевод поручить Haiku (`CLAUDE_MODEL_TRANSLATE=claude-haiku-5-5`), выйдет около $0,3 в месяц.
- **Сравнение качества.** Первые 3 поста дополнительно переводят другие движки, для которых заданы ключи. Если основной движок Claude Sonnet, их переводит ещё и Haiku. В читалке появляется переключатель, например «GPT-6 Astra / Claude Sonnet».
- **Netlify без расхода кредитов.** На бесплатном тарифе каждый production-деплой стоит 15 из 300 месячных кредитов. Поэтому Netlify отдаёт только саму страницу, а данные обновляются в ветке `data` на GitHub и деплоев не вызывают.
- **Полные переводы зашифрованы** (AES-256-GCM, ключ из пароля). Абстракты видны всем, у кого есть адрес. Полный перевод открывается только после ввода пароля, и на устройстве пароль запоминается.
- **Без перевода остаются платные посты и очень длинные тексты.** У них только абстракт и ссылка на оригинал.

## Установка (можно с телефона, в браузере)

1. **Ключ OpenAI и кредиты ChatGPT Pro (рекомендуется).**
   - В ChatGPT откройте **Settings** → **Billing**, найдите бонус API credits и пройдите шаги активации. Кредиты привязываются к организации на platform.openai.com. Email в ChatGPT и в API-аккаунте должен совпадать. Дальше кредиты начисляются каждый месяц сами, а неизрасходованные сгорают в конце периода.
   - Ключ: platform.openai.com/api-keys → **Create new secret key** → имя `AIBot` → скопируйте ключ.
2. **(Необязательно) ключ Gemini и кредиты Ultra.**
   - Откройте aistudio.google.com. Если в левом меню видны невостребованные кредиты, откройте окно **Claim Google Cloud Credits** и нажмите **Redeem and Continue**. В этом же процессе создаётся профиль Google Developer Program и платёжный аккаунт Google Cloud. Для работы через Cloud billing нужна разовая предоплата $5, после этого начинают списываться кредиты подписки.
   - Ключ: aistudio.google.com/app/apikey → **Create API key** → скопируйте его.
3. **(Необязательно) ключ Claude** — для сравнения переводов или как запасной движок. На platform.claude.com/settings/billing → **Buy credits**, затем на platform.claude.com/settings/keys → **Create key**.
4. **Секреты в GitHub.** На github.com/VBalik/AIBot/settings/secrets/actions → **New repository secret** добавьте:
   - `OPENAI_API_KEY` — ключ OpenAI;
   - `GEMINI_API_KEY` — ключ из AI Studio (только когда у проекта заработает оплата: Gemini Pro на бесплатном уровне недоступен);
   - `DIGEST_PASSWORD` — придуманный вами пароль для полных переводов. Лучше длинный, например из четырёх случайных слов;
   - `ANTHROPIC_API_KEY` — по желанию, ключ Claude.
5. **Первый запуск.** github.com/VBalik/AIBot/actions/workflows/update.yml → **Run workflow**. Через несколько минут появится ветка `data`. Дальше сборщик запускается сам каждый час.
6. **Netlify.** На app.netlify.com откройте **Add new project** → **Import an existing project** → **GitHub** → репозиторий `AIBot` → **Deploy**. Настройки подтянутся из `netlify.toml`. Если репозитория нет в списке, откройте Netlify доступ к нему на github.com/settings/installations. Сменить адрес `*.netlify.app` можно в **Project configuration** → **General** → **Project details** → **Project information** → **Manage project name and cover image**.
7. На iPhone откройте адрес страницы в Safari → **Share** → **Add to Home Screen**.

## Настройки

Задаются на github.com/VBalik/AIBot/settings/variables/actions (вкладка **Variables**), все необязательные:

| Переменная | По умолчанию | Что делает |
|---|---|---|
| `PROVIDER` | auto (OpenAI → Gemini → Claude, по наличию ключа) | Основной движок: `openai`, `gemini` или `claude` |
| `COMPARE_PROVIDER` | auto (все остальные движки с ключами) | Чем переводить для сравнения: `openai`, `gemini`, `claude`; `off` — не сравнивать |
| `COMPARE_MODEL` | auto (для Claude — Haiku) | Ещё одна модель того же движка для сравнения переводов; `off` — не сравнивать |
| `COMPARE_LIMIT` | 3 | Сколько постов перевести каждым вариантом для сравнения |
| `OPENAI_MODEL_TRANSLATE` | gpt-6-astra | Модель-переводчик OpenAI: `gpt-6.1-sol` дешевле в 5 раз (и `OPENAI_MODEL_ABSTRACT`, `OPENAI_MODEL_SCORE`) |
| `OPENAI_REASONING_EFFORT` | low | Глубина размышлений моделей OpenAI: low, medium, high, xhigh, max |
| `GEMINI_MODEL_TRANSLATE` | gemini-3.1-pro-preview | Модель-переводчик Gemini (и `GEMINI_MODEL_ABSTRACT`, `GEMINI_MODEL_SCORE`) |
| `GEMINI_THINKING_LEVEL` | LOW | Глубина «размышлений» Gemini: MINIMAL, LOW, MEDIUM, HIGH |
| `CLAUDE_MODEL_TRANSLATE` | claude-sonnet-5-5 | Модель-переводчик Claude: `claude-haiku-5-5` — дешевле, `claude-opus-5-5` — лучше и дороже |
| `CLAUDE_MODEL_ABSTRACT` | claude-haiku-5-5 | Модель для абстрактов (и `CLAUDE_MODEL_SCORE` для отбора) |
| `SHOW_MIN_SCORE` | 6 | С какой оценки (1–10) пост попадает на страницу |
| `TRANSLATE_MIN_SCORE` | 6 | С какой оценки делается полный перевод |
| `MAX_TRANSLATIONS_PER_RUN` | 4 | Сколько переводов за один запуск |
| `MAX_TRANSLATE_CHARS` | 70000 | Тексты длиннее — только абстракт |
| `INTERESTS` | список в `build.py` | Ваши темы для отбора (строки через `\n`) |

## Локальная проверка

```bash
pip install -r requirements.txt playwright && playwright install chromium
python tests/test_build.py    # сборщик с имитацией OpenAI, Gemini и Claude API → tests/out/data
python tests/test_site.py     # страница в браузере → скриншоты в tests/out
```
