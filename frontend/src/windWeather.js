const hourMs = 60 * 60 * 1000
const kztOffsetMs = 5 * hourMs
const compass = ['С', 'ССВ', 'СВ', 'ВСВ', 'В', 'ВЮВ', 'ЮВ', 'ЮЮВ', 'Ю', 'ЮЮЗ', 'ЮЗ', 'ЗЮЗ', 'З', 'ЗСЗ', 'СЗ', 'ССЗ']

export function weatherTimes(issueDate) {
  if (!/^2026-(01-31|02-(0[1-9]|1[0-9]|2[0-8]))$/.test(issueDate)) {
    throw new Error('Выберите дату выпуска с 31 января по 28 февраля 2026 года.')
  }
  const issuedAt = new Date(`${issueDate}T00:00:00+05:00`)
  const runAt = new Date(issuedAt)
  runAt.setUTCHours(12, 0, 0, 0)
  return { issuedAt, runAt }
}

export function formatKzt(instant) {
  const date = new Date(new Date(instant).getTime() + kztOffsetMs)
  const two = value => String(value).padStart(2, '0')
  return `${two(date.getUTCDate())}.${two(date.getUTCMonth() + 1)}.${date.getUTCFullYear()} ${two(date.getUTCHours())}:${two(date.getUTCMinutes())} UTC+5`
}

export function compassFrom(degrees) {
  return compass[Math.round(((degrees % 360) + 360) % 360 / 22.5) % 16]
}

export function flowBearing(fromDegrees) {
  return (fromDegrees + 180) % 360
}

export function parseWindSeries(data, issueDate) {
  const { issuedAt } = weatherTimes(issueDate)
  const hourly = data?.hourly
  if (data?.error || !Array.isArray(hourly?.time) ||
      !Array.isArray(hourly?.wind_speed_100m) ||
      !Array.isArray(hourly?.wind_direction_100m)) {
    throw new Error(data?.reason || 'Погодный источник не вернул почасовой ветер.')
  }
  const units = data.hourly_units || {}
  if ((units.wind_speed_100m && !['m/s', 'ms'].includes(units.wind_speed_100m)) ||
      (units.wind_direction_100m && units.wind_direction_100m !== '°')) {
    throw new Error('Погодный источник вернул неожиданные единицы ветра.')
  }
  const byTime = new Map(hourly.time.map((stamp, index) => [stamp, index]))
  return Array.from({ length: 48 }, (_, index) => {
    const validAt = new Date(issuedAt.getTime() + (index + 1) * hourMs)
    const key = validAt.toISOString().slice(0, 16)
    const sourceIndex = byTime.get(key)
    const speed = hourly.wind_speed_100m[sourceIndex]
    const fromDegrees = hourly.wind_direction_100m[sourceIndex]
    if (sourceIndex === undefined || !Number.isFinite(speed) || speed < 0 ||
        !Number.isFinite(fromDegrees) || fromDegrees < 0 || fromDegrees > 360) {
      throw new Error(`Нет корректных данных ветра на ${formatKzt(validAt)}.`)
    }
    return { validAt: validAt.toISOString(), speed, fromDegrees: fromDegrees % 360 }
  })
}

export async function fetchWindSeries(turbine, issueDate, signal) {
  const { runAt } = weatherTimes(issueDate)
  const params = new URLSearchParams({
    latitude: String(turbine.latitude),
    longitude: String(turbine.longitude),
    run: runAt.toISOString().slice(0, 16),
    models: 'ecmwf_ifs',
    hourly: 'wind_speed_100m,wind_direction_100m',
    wind_speed_unit: 'ms',
    timezone: 'UTC',
    forecast_days: '4'
  })
  const response = await fetch(`https://single-runs-api.open-meteo.com/v1/forecast?${params}`, { signal })
  if (!response.ok) throw new Error(`Погодный источник ответил HTTP ${response.status}.`)
  return parseWindSeries(await response.json(), issueDate)
}
