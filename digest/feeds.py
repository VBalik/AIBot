"""Источники дайджеста. Чтобы добавить блог — допишите словарь с его RSS/Atom-лентой.

Поля:
    id       — короткий латинский идентификатор (используется в данных и фильтре на странице)
    symbol   — двухбуквенный «химический символ» для плитки на странице
    name     — название блога
    author   — автор(ы) по-русски
    about    — одна фраза: почему стоит читать
    url      — RSS/Atom-лента с полным текстом
    site     — главная страница блога
    exclude  — (необязательно) регулярное выражение: посты с таким заголовком пропускаются
    paywall  — (необязательно) False, если у блога нет платных постов: проверка «Read more / paid subscribers»
               нужна только для Substack, а в бесплатных рассылках такие слова встречаются в призывах подписаться
"""

FEEDS = [
    {
        "id": "willison",
        "paywall": False,
        "symbol": "Sw",
        "name": "Simon Willison",
        "author": "Саймон Уиллисон",
        "about": "Сооснователь Django руками тестирует новые модели и агентов, пишет о безопасности и ограничениях",
        "url": "https://simonwillison.net/atom/entries/",  # только длинные статьи
        "site": "https://simonwillison.net",
    },
    {
        "id": "importai",
        "paywall": False,
        "symbol": "Ia",
        "name": "Import AI",
        "author": "Джек Кларк",
        "about": "Сооснователь Anthropic еженедельно разбирает исследования и их последствия для мира",
        # importai.substack.com/feed отдаёт серверам GitHub Actions 403; jack-clark.net — зеркало с полным текстом
        "url": "https://jack-clark.net/feed/",
        "site": "https://importai.substack.com",
    },
    {
        "id": "mollick",
        "symbol": "Mo",
        "name": "One Useful Thing",
        "author": "Итан Моллик",
        "about": "Профессор Уортона: как ИИ меняет работу, обучение и бизнес — с экспериментами",
        "url": "https://www.oneusefulthing.org/feed",
        "site": "https://www.oneusefulthing.org",
    },
    {
        "id": "interconnects",
        "symbol": "Ic",
        "name": "Interconnects",
        "author": "Натан Ламберт",
        "about": "Исследователь AI2 о том, как обучают модели, об открытых моделях и бенчмарках",
        "url": "https://www.interconnects.ai/feed",
        "site": "https://www.interconnects.ai",
    },
    {
        "id": "latentspace",
        "symbol": "Ls",
        "name": "Latent Space",
        "author": "swyx и Алессио Фанелли",
        "about": "AI-инженерия и бизнес: интервью с основателями и инженерами ведущих AI-компаний",
        "url": "https://www.latent.space/feed",
        "site": "https://www.latent.space",
        "exclude": r"^\s*\[AINews\]",  # ежедневные сводки соцсетей — агрегатор, не авторский текст
    },
    {
        "id": "raschka",
        "symbol": "Ra",
        "name": "Ahead of AI",
        "author": "Себастьян Рашка",
        "about": "Глубокие и ясные разборы архитектур LLM и свежих научных работ",
        "url": "https://magazine.sebastianraschka.com/feed",
        "site": "https://magazine.sebastianraschka.com",
    },
    {
        "id": "understandingai",
        "symbol": "Ua",
        "name": "Understanding AI",
        "author": "Тимоти Б. Ли",
        "about": "Технический журналист объясняет, что на самом деле происходит в индустрии ИИ",
        "url": "https://www.understandingai.org/feed",
        "site": "https://www.understandingai.org",
    },
    {
        "id": "normaltech",
        "symbol": "Nt",
        "name": "AI as Normal Technology",
        "author": "Арвинд Нараянан и Саяш Капур",
        "about": "Профессора Принстона — трезвый взгляд на возможности, риски и будущее ИИ",
        "url": "https://www.normaltech.ai/feed",
        "site": "https://www.normaltech.ai",
    },
    {
        "id": "karpathy",
        "paywall": False,
        "symbol": "Ka",
        "name": "Andrej Karpathy",
        "author": "Андрей Карпатый",
        "about": "Сооснователь OpenAI, экс-глава AI в Tesla: редкие, но ключевые тексты",
        "url": "https://karpathy.bearblog.dev/feed/",
        "site": "https://karpathy.bearblog.dev",
    },
    {
        "id": "dwarkesh",
        "symbol": "Dw",
        "name": "Dwarkesh Podcast",
        "author": "Дваркеш Патель",
        "about": "Долгие интервью с ведущими умами ИИ о том, куда всё движется",
        "url": "https://www.dwarkesh.com/feed",
        "site": "https://www.dwarkesh.com",
    },
    {
        "id": "evans",
        "paywall": False,
        "symbol": "Be",
        "name": "Benedict Evans",
        "author": "Бенедикт Эванс",
        "about": "Аналитик технологических рынков: стратегия, бизнес-модели и экономика ИИ",
        "url": "https://www.ben-evans.com/benedictevans?format=rss",
        "site": "https://www.ben-evans.com",
    },
]
