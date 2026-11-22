// Счётчик просмотров дайджеста: POST /api/views засчитывает открытие страницы, GET только читает.
// Ответ: { day, today, total, since } — страница показывает «сегодня/всего» справа под шестерёнкой.
// Числа лежат в Netlify Blobs (хранилище сайта, строгая согласованность); о читателях ничего не хранится.
// Стоимость: один вызов функции на открытие приложения — доли кредита в месяц.

import { getStore } from "@netlify/blobs";
import { createViewCounter, blobViewStore, botAgent } from "../views-core.mjs";

export const config = { path: "/api/views" };

const json = (body, status = 200) => new Response(JSON.stringify(body), {
  status,
  headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" },
});

export default async (request) => {
  if (request.method !== "GET" && request.method !== "POST") return json({ error: "method" }, 405);
  const tz = new URL(request.url).searchParams.get("tz");
  try {
    const counter = createViewCounter(blobViewStore(getStore({ name: "digest-views", consistency: "strong" })));
    const count = request.method === "POST" && !botAgent(request.headers.get("user-agent"));
    return json(count ? await counter.count(tz) : await counter.read(tz));
  } catch (e) {
    console.log(`views: ${e && e.message}`);
    return json({ error: "store" }, 503);
  }
};
