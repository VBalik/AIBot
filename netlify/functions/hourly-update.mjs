// Запуск сборщика раз в час (Netlify Scheduled Function).
// Расписание GitHub Actions ненадёжно: 18–19.11.2026 из 24 ежечасных запусков в сутки проходило 3–4.
// Функция просто просит GitHub запустить workflow «Update digest»; работа идёт там же, где и раньше.
// Нужна переменная GITHUB_DISPATCH_TOKEN (Project configuration → Environment variables):
// fine-grained токен GitHub только на репозиторий VBalik/AIBot с правом Actions: Read and write.
const WORKFLOW = "https://api.github.com/repos/VBalik/AIBot/actions/workflows/update.yml/dispatches";

export default async () => {
  const token = Netlify.env.get("GITHUB_DISPATCH_TOKEN");
  if (!token) {
    console.log("GITHUB_DISPATCH_TOKEN не задан — запуск пропущен");
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
  console.log(r.status === 204 ? "Сборщик запущен" : `GitHub ответил ${r.status}: ${(await r.text()).slice(0, 300)}`);
};

export const config = { schedule: "@hourly" };
