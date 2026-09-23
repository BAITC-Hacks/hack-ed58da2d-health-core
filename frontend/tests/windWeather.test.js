import test from 'node:test'
import assert from 'node:assert/strict'
import {
  compassFrom, fetchWindSeries, flowBearing, formatKzt, parseWindSeries, weatherTimes
} from '../src/windWeather.js'

function fixture() {
  const start = Date.parse('2026-01-30T12:00:00Z')
  return {
    hourly_units: { wind_speed_100m: 'm/s', wind_direction_100m: '°' },
    hourly: {
      time: Array.from({ length: 96 }, (_, index) => new Date(start + index * 3600000).toISOString().slice(0, 16)),
      wind_speed_100m: Array.from({ length: 96 }, (_, index) => index + 0.5),
      wind_direction_100m: Array.from({ length: 96 }, (_, index) => index * 3)
    }
  }
}

test('selects the archived 12 UTC run before the Kazakhstan issue time', () => {
  const { issuedAt, runAt } = weatherTimes('2026-01-31')
  assert.equal(issuedAt.toISOString(), '2026-01-30T19:00:00.000Z')
  assert.equal(runAt.toISOString(), '2026-01-30T12:00:00.000Z')
  assert.equal(formatKzt(new Date('2026-01-30T20:00:00Z')), '31.01.2026 01:00 UTC+5')
  assert.throws(() => weatherTimes('2026-03-01'))
})

test('reads 48 exact hours without inventing missing wind values', () => {
  const series = parseWindSeries(fixture(), '2026-01-31')
  assert.equal(series.length, 48)
  assert.deepEqual(series[0], { validAt: '2026-01-30T20:00:00.000Z', speed: 8.5, fromDegrees: 24 })
  assert.equal(series[47].validAt, '2026-02-01T19:00:00.000Z')
  const missing = fixture()
  missing.hourly.wind_direction_100m[8] = null
  assert.throws(() => parseWindSeries(missing, '2026-01-31'), /Нет корректных данных/)
})

test('arrow points where air flows, opposite to meteorological from-direction', () => {
  assert.equal(flowBearing(90), 270)
  assert.equal(flowBearing(350), 170)
  assert.equal(compassFrom(71), 'ВСВ')
})

test('queries the same ECMWF run and height for turbine coordinates', async () => {
  const originalFetch = globalThis.fetch
  let called
  globalThis.fetch = async (url, options) => {
    called = { url: new URL(url), signal: options.signal }
    return { ok: true, json: async () => fixture() }
  }
  try {
    const controller = new AbortController()
    const rows = await fetchWindSeries({ latitude: 43.64515, longitude: 78.535604 }, '2026-01-31', controller.signal)
    assert.equal(called.url.searchParams.get('run'), '2026-01-30T12:00')
    assert.equal(called.url.searchParams.get('hourly'), 'wind_speed_100m,wind_direction_100m')
    assert.equal(called.url.searchParams.get('models'), 'ecmwf_ifs')
    assert.equal(called.signal, controller.signal)
    assert.equal(rows.length, 48)
  } finally {
    globalThis.fetch = originalFetch
  }
})
