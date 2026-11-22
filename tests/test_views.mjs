// Счётчик просмотров (netlify/views-core.mjs и функция /api/views): node tests/test_views.mjs
import assert from "node:assert/strict";
import { countView, viewStats, createViewCounter, memoryViewStore, botAgent, clockOffset } from "../netlify/views-core.mjs";

const HOUR = 3600e3;
const t0 = Date.UTC(2026, 10, 22, 10, 30); // 22.11.2026 10:30 UTC

// счёт и «сегодня» по часам читателя
let s = null;
for (const dt of [-30 * HOUR, -12 * HOUR, -2 * HOUR, 0, 0]) s = countView(s, t0 + dt);
assert.equal(s.total, 5);
assert.equal(Object.keys(s.hours).length, 3, "часы старше суток выброшены");
let v = viewStats(s, t0, 0);
assert.deepEqual([v.day, v.today, v.total], [4, 3, 5], "UTC: сегодня — с 00:00, 12 часов назад было вчера (22:30)");
v = viewStats(s, t0, 11 * 60); // UTC+11: у читателя 21:30, его полночь — 13:00 UTC накануне
assert.equal(v.today, 4);
v = viewStats(s, t0, -10 * 60); // UTC−10: у читателя 00:30, сегодня — только два последних
assert.equal(v.today, 2);
assert.equal(viewStats(s, t0).today, undefined, "без часового пояса — только сутки");
assert.equal(clockOffset("abc"), null);
assert.equal(clockOffset("60"), 60);

// одновременные просмотры: запись по etag, повтор после конфликта
const store = memoryViewStore();
const counter = createViewCounter(store, { now: () => t0, pause: async () => {} });
const results = await Promise.all(Array.from({ length: 5 }, () => counter.count(0)));
assert.equal((await counter.read(0)).total, 5, "ни один просмотр не потерян");
assert.ok(results.every((r) => r.total >= 1 && r.today >= 1));

// программы не считаются
assert.ok(botAgent("Mozilla/5.0 (compatible; Googlebot/2.1)"));
assert.ok(botAgent("Mozilla/5.0 HeadlessChrome/140"));
assert.ok(botAgent(""));
assert.ok(!botAgent("Mozilla/5.0 (iPhone; CPU iPhone OS 26_1 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148"));

// функция без хранилища (не на Netlify) отвечает 503, а не падает
const fn = (await import("../netlify/functions/views.mjs")).default;
let r = await fn(new Request("https://x/api/views?tz=60"));
assert.equal(r.status, 503);
assert.equal(r.headers.get("cache-control"), "no-store");
r = await fn(new Request("https://x/api/views", { method: "DELETE" }));
assert.equal(r.status, 405);

console.log("СЧЁТЧИК ПРОСМОТРОВ: ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ");
