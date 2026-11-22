// Просмотры дайджеста: сколько раз его открыли сегодня и всего. Логика взята из «Глобуса Воланда»
// (VBalik/Globus, views.mjs), чтобы счётчики считали одинаково.
//
// Запись одна и маленькая: всего, просмотры каждого из последних 24 часов и момент начала счёта.
// «Сегодня» — день читателя: часы с полуночи по часам страницы, которая спрашивает (она сообщает,
// насколько её часы впереди UTC), — каждую полночь число начинается с нуля. Страница, не сообщившая
// часовой пояс, получает последние 24 часа. О читателях не хранится ничего: ни адреса, ни cookie,
// ни идентификатора — только числа.
//
// Несколько страниц могут считать одновременно: запись пишется, только если она всё ещё та,
// что была прочитана (etag хранилища), иначе её читают и считают заново.

const HOUR = 3600e3;
export const VIEW_HOURS = 24;

/** Запись без просмотров. */
export function emptyViews() {
  return { total: 0, hours: {}, since: null };
}

function valid(state) {
  return state && typeof state === "object" && Number.isFinite(state.total) && state.hours && typeof state.hours === "object";
}

/** Запись после ещё одного просмотра в момент now (мс): часы старше суток отбрасываются. */
export function countView(state, now) {
  const base = valid(state) ? state : emptyViews();
  const hour = Math.floor(now / HOUR);
  const hours = {};
  for (const [k, n] of Object.entries(base.hours)) {
    const h = Number(k);
    if (h > hour - VIEW_HOURS && h <= hour && Number.isFinite(n) && n > 0) hours[h] = n;
  }
  hours[hour] = (hours[hour] || 0) + 1;
  return { total: Math.max(0, Math.floor(base.total)) + 1, hours, since: base.since || new Date(now).toISOString() };
}

/** Насколько часы могут отличаться от UTC (минуты): от UTC−12 до UTC+14. */
export function clockOffset(value) {
  const tz = Number(value);
  return value !== null && value !== undefined && value !== "" && Number.isInteger(tz) && tz >= -720 && tz <= 840 ? tz : null;
}

/**
 * { day, today?, total, since }: просмотры за последние 24 часа и за всё время; с tz (на сколько минут
 * часы читателя впереди UTC) — ещё и сегодня: с полуночи по этим часам (час относится к тому дню,
 * на который приходится его середина).
 */
export function viewStats(state, now, tz = null) {
  const offset = clockOffset(tz);
  if (!valid(state)) return { day: 0, ...(offset === null ? {} : { today: 0 }), total: 0, since: null };
  const hour = Math.floor(now / HOUR);
  const midnight = offset === null ? 0 : Math.floor((now + offset * 60e3) / (24 * HOUR)) * 24 * HOUR - offset * 60e3;
  let day = 0;
  let today = 0;
  for (const [k, n] of Object.entries(state.hours)) {
    const h = Number(k);
    if (!(h > hour - VIEW_HOURS && h <= hour && Number.isFinite(n) && n > 0)) continue;
    day += n;
    if (h * HOUR + HOUR / 2 >= midnight) today += n;
  }
  const total = Math.max(day, Math.floor(state.total));
  return { day, ...(offset === null ? {} : { today }), total, since: state.since || null };
}

// поисковые роботы, проверялки страниц, браузеры под управлением программ и программы, спрашивающие
// сами; не встроенные браузеры приложений (Telegram, WhatsApp): их читатели — читатели
const BOT_RE = /(?<!cu)bot(?:[/\-\s;)_+]|$)|crawl|spider|slurp|headless|lighthouse|pagespeed|inspectiontool|mediapartners|googleother|read-aloud|bingpreview|python|curl\/|wget\/|node-fetch|undici|axios|go-http|java\/|okhttp|libwww|httpclient/i;

/** User-Agent программы, а не читателя. */
export function botAgent(ua) {
  return !ua || BOT_RE.test(String(ua));
}

/**
 * Счётчик над хранилищем { get(): {data, etag}, put(data, etag): boolean } — put пишет, только если
 * запись всё ещё с этим etag (без etag — только если записи ещё нет). deps: { now, tries, pause }.
 */
export function createViewCounter(store, deps = {}) {
  const now = deps.now || (() => Date.now());
  const tries = deps.tries || 6;
  const pause = deps.pause || ((k) => new Promise((r) => setTimeout(r, 15 + Math.random() * 40 * (k + 1))));
  return {
    /** Засчитать просмотр: числа после него. */
    async count(tz = null) {
      let last = null;
      for (let k = 0; k < tries; k++) {
        const { data, etag } = await store.get();
        last = data;
        const next = countView(data, now());
        if (await store.put(next, etag || null)) return viewStats(next, now(), tz);
        await pause(k);
      }
      // слишком много страниц сразу: просмотр не записан, числа показываются с ним
      return { ...viewStats(countView(last, now()), now(), tz), stored: false };
    },
    /** Числа без счёта. */
    async read(tz = null) {
      const { data } = await store.get();
      return viewStats(data, now(), tz);
    },
  };
}

/** Хранилище в памяти (тесты). */
export function memoryViewStore(initial = null) {
  let data = initial ? JSON.parse(JSON.stringify(initial)) : null;
  let version = data ? 1 : 0;
  return {
    async get() {
      return { data: data ? JSON.parse(JSON.stringify(data)) : null, etag: data ? `v${version}` : null };
    },
    async put(next, etag) {
      const current = data ? `v${version}` : null;
      if (etag !== current) return false;
      data = JSON.parse(JSON.stringify(next));
      version += 1;
      return true;
    },
  };
}

/** Хранилище в Netlify Blobs (store из @netlify/blobs: getWithMetadata, setJSON с условиями). */
export function blobViewStore(blobs, key = "views") {
  return {
    async get() {
      const found = await blobs.getWithMetadata(key, { type: "json" });
      // запись, которую нельзя отличить от её следующих версий, нельзя безопасно перезаписать
      if (found && !found.etag) throw new Error("the store gives no etag: views cannot be counted");
      return found ? { data: found.data, etag: found.etag } : { data: null, etag: null };
    },
    async put(next, etag) {
      const result = await blobs.setJSON(key, next, etag ? { onlyIfMatch: etag } : { onlyIfNew: true });
      return Boolean(result && result.modified);
    },
  };
}
