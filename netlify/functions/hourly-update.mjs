// Запуск сборщика раз в час (Netlify Scheduled Function).
// Расписание GitHub Actions ненадёжно: 18–19.11.2026 из 24 ежечасных запусков в сутки проходило 3–4,
// а 19.11 после 13:16 UTC не было ни одного. Функция просто просит GitHub запустить workflow «Update digest»;
// работа идёт там же, где и раньше.
// Нужна переменная GITHUB_DISPATCH_TOKEN (Project configuration → Environment variables, Contains secret values,
// scope Functions; задана Валерием 19.11.2026): fine-grained токен GitHub только на репозиторий VBalik/AIBot
// с правом Actions: Read and write. Переменные подхватываются только при деплое.
const WORKFLOW = "https://api.github.com/repos/VBalik/AIBot/actions/workflows/update.yml/dispatches";
const FEED = "https://raw.githubusercontent.com/VBalik/AIBot/data/feed.json";
const FRESH_MINUTES = 50; // если сервер недавно уже собирал (например, ожило расписание GitHub), второй раз не нужно

async function minutesSinceBuild() {
  try {
    const r = await fetch(`${FEED}?t=${Date.now()}`, { signal: AbortSignal.timeout(10000) });
    const built = Date.parse((await r.json()).built_at);
    return Number.isFinite(built) ? (Date.now() - built) / 60000 : Infinity;
  } catch {
    return Infinity; // не удалось узнать — лучше запустить
  }
}

export default async () => {
  const token = Netlify.env.get("GITHUB_DISPATCH_TOKEN");
  if (!token) {
    console.log("GITHUB_DISPATCH_TOKEN не задан — запуск пропущен");
    return;
  }
  const age = await minutesSinceBuild();
  if (age < FRESH_MINUTES) {
    console.log(`Данные обновлялись ${Math.round(age)} мин назад — запуск не нужен`);
    return;
  }
  const r = await fetch(WORKFLOW, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "ai-digest-hourly",
    },
    body: JSON.stringify({ ref: "main" }),
  });
  // 204 — запуск принят; новые версии API могут отвечать 200 с номером запуска
  console.log(r.ok ? "Сборщик запущен" : `GitHub ответил ${r.status}: ${(await r.text()).slice(0, 300)}`);
};

export const config = { schedule: "@hourly" };
