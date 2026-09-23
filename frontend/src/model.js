const base = (import.meta.env?.VITE_API_URL || 'http://127.0.0.1:8000').replace(/\/$/, '')
export async function request(path, options = {}) {
  let response
  const headers = new Headers(options.headers || {})
  if (options.method === 'POST') {
    const key = globalThis.sessionStorage?.getItem('healthcore-operator-key')
    if (key) headers.set('X-Admin-Key', key)
  }
  try { response = await fetch(base + path, { ...options, headers, signal: AbortSignal.timeout(options.method === 'POST' ? 300000 : 15000) }) }
  catch { throw new Error('API не ответил. Проверьте сервер. Запущенный расчёт может продолжаться на сервере; проверьте его перед повтором.') }
  let data
  try { data = await response.json() } catch { throw new Error('API вернул ответ в неподдерживаемом формате.') }
  if (!response.ok) { const detail = data.detail || data.error?.message; throw new Error(response.status === 401 ? 'Для записи нужен ключ оператора. Введите его в верхней панели.' : detail === 'Train the model first' ? 'Сначала обучите модель на сервере.' : typeof detail === 'string' ? detail : `Ошибка API (${response.status}). Проверьте параметры запроса.`) }
  return data
}
// Backend contract: naive valid_at/weather_run_at are UTC; naive issued_at is UTC+5.
export function utc(value) { return new Date(/(?:Z|[+-]\d\d:\d\d)$/i.test(value) ? value : value + 'Z') }
export function issueTime(value) { return new Date(/(?:Z|[+-]\d\d:\d\d)$/i.test(value) ? value : value + '+05:00') }
export function pointsFor(run, turbine, horizon) { return (run?.points || []).filter(p => p.turbine_id === Number(turbine)).slice().sort((a,b) => utc(a.valid_at) - utc(b.valid_at)).slice(0,horizon) }
export function summarize(rows) {
  if (!rows.length) return {}
  const powers = rows.filter(p => Number.isFinite(p.normalized_power))
  if (!powers.length) return {}
  const peak = powers.reduce((a,b) => a.normalized_power > b.normalized_power ? a : b)
  const low = powers.reduce((a,b) => a.normalized_power < b.normalized_power ? a : b)
  const winds = rows.map(p => p.wind_speed_ms).filter(Number.isFinite)
  return { mean:powers.reduce((s,p) => s+p.normalized_power,0)/powers.length, peak, low, wind:winds.length ? winds.reduce((a,b)=>a+b,0)/winds.length : undefined }
}
export function validatePoints(rows,horizon,issued) {
  const errors=[]
  if(rows.length !== horizon) errors.push(`Неполный горизонт: ${rows.length} из ${horizon} часов.`)
  if(rows.some(p=>!Number.isFinite(p.normalized_power)||p.normalized_power<0||p.normalized_power>1)) errors.push('Мощность содержит недопустимые значения.')
  if(rows.some((p,i)=>+utc(p.valid_at)!==+issueTime(issued)+(i+1)*3600000)) errors.push('Нарушена последовательность почасовых значений относительно выпуска.')
  return errors
}
