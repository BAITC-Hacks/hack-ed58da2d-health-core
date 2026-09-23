import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { request } from './model.js'
import { compassFrom, fetchWindSeries, flowBearing, formatKzt, weatherTimes } from './windWeather.js'

const demoMapId = 'DEMO_MAP_ID'

function loadGoogleMaps(key) {
  if (typeof window.google?.maps?.importLibrary === 'function') return Promise.resolve(window.google.maps)
  if (window.__windSightMapsPromise) return window.__windSightMapsPromise
  window.__windSightMapsPromise = new Promise((resolve, reject) => {
    const callbackName = '__windSightMapsReady'
    const script = document.createElement('script')
    const timeout = window.setTimeout(() => fail(new Error('Google Maps не ответила вовремя. Проверьте подключение и настройки API-ключа.')), 15000)
    function finish() {
      window.clearTimeout(timeout)
      delete window[callbackName]
    }
    function fail(reason) {
      finish()
      script.remove()
      reject(reason)
    }
    window[callbackName] = () => {
      finish()
      if (typeof window.google?.maps?.importLibrary === 'function') resolve(window.google.maps)
      else reject(new Error('Google Maps загрузилась без необходимых библиотек. Проверьте Maps JavaScript API.'))
    }
    script.src = `https://maps.googleapis.com/maps/api/js?key=${encodeURIComponent(key)}&v=weekly&loading=async&callback=${callbackName}`
    script.async = true
    script.onerror = () => fail(new Error('Не удалось загрузить Google Maps. Проверьте API-ключ и ограничения ключа.'))
    document.head.append(script)
  }).catch(reason => {
    window.__windSightMapsPromise = undefined
    throw reason
  })
  return window.__windSightMapsPromise
}

export default {
  name: 'GeographyMap',
  props: { initialDate: { type: String, default: '2026-02-01' } },
  setup(props) {
    const mapElement = ref(null)
    const turbines = ref([])
    const loading = ref(true)
    const error = ref('')
    const windDate = ref(props.initialDate)
    const windHour = ref(1)
    const windEnabled = ref(true)
    const windLoading = ref(false)
    const windError = ref('')
    const windPlaying = ref(false)
    const windSeries = ref({})
    const windReady = computed(() => turbines.value.length > 0 && turbines.value.every(turbine => !!windSeries.value[turbine.id]))
    const selectedTime = computed(() => {
      try {
        return formatKzt(new Date(weatherTimes(windDate.value).issuedAt.getTime() + windHour.value * 3600000))
      } catch {
        return 'Выберите дату выпуска'
      }
    })
    const runTime = computed(() => {
      try {
        return weatherTimes(windDate.value).runAt.toISOString().slice(0, 16).replace('T', ' ') + ' UTC'
      } catch {
        return '—'
      }
    })
    let map
    let markers = []
    let infoWindow
    let windRequest
    let playTimer
    let disposed = false
    const mapListeners = []

    const windAt = turbine => windSeries.value[turbine.id]?.[windHour.value - 1]
    const speedText = speed => speed.toLocaleString('ru-RU', { minimumFractionDigits: 1, maximumFractionDigits: 1 })

    function renderWind() {
      const heading = map?.getHeading?.() || 0
      const zoom = map?.getZoom?.() ?? 16
      markers.forEach(({ turbine, windBadge, windArrow, windSpeed, windInfo }, index) => {
        const point = windAt(turbine)
        windBadge.hidden = !windEnabled.value || !point
        windInfo.hidden = !windEnabled.value || !point
        if (!point) return
        const speed = speedText(point.speed)
        windArrow.style.transform = `rotate(${(flowBearing(point.fromDegrees) - heading + 360) % 360}deg)`
        windSpeed.textContent = `${speed} м/с`
        windBadge.dataset.band = point.speed < 5 ? 'low' : point.speed < 10 ? 'medium' : 'high'
        windBadge.style.bottom = zoom < 15 && index % 2 ? '-47px' : '52px'
        windBadge.title = `${turbine.name}: ${speed} м/с; из ${compassFrom(point.fromDegrees)} (${Math.round(point.fromDegrees)}°), поток к ${compassFrom(flowBearing(point.fromDegrees))}; ${formatKzt(point.validAt)}`
        windInfo.textContent = `Ветер: ${speed} м/с · из ${compassFrom(point.fromDegrees)} (${Math.round(point.fromDegrees)}°) · 100 м`
      })
    }

    watch([windHour, windEnabled], renderWind)

    function stopPlayback() {
      if (playTimer) window.clearInterval(playTimer)
      playTimer = undefined
      windPlaying.value = false
    }

    function togglePlayback() {
      if (windPlaying.value) return stopPlayback()
      if (!windReady.value || !windEnabled.value) return
      windPlaying.value = true
      playTimer = window.setInterval(() => {
        windHour.value = windHour.value === 48 ? 1 : windHour.value + 1
      }, 900)
    }

    async function loadWind() {
      stopPlayback()
      windRequest?.abort()
      const controller = new AbortController()
      windRequest = controller
      windHour.value = 1
      windSeries.value = {}
      windError.value = ''
      windLoading.value = true
      renderWind()
      try {
        const entries = await Promise.all(turbines.value.map(async turbine => [
          turbine.id, await fetchWindSeries(turbine, windDate.value, controller.signal)
        ]))
        if (!controller.signal.aborted && !disposed) windSeries.value = Object.fromEntries(entries)
      } catch (reason) {
        if (!controller.signal.aborted && !disposed) windError.value = reason.message || 'Не удалось получить данные ветра.'
      } finally {
        if (windRequest === controller && !disposed) {
          windRequest = undefined
          windLoading.value = false
          renderWind()
        }
      }
    }

    function toggleWind() {
      windEnabled.value = !windEnabled.value
      if (!windEnabled.value) stopPlayback()
    }

    async function focusTurbine(turbine) {
      if (!map) return
      map.panTo({ lat: turbine.latitude, lng: turbine.longitude })
      map.setZoom(17)
      const marker = markers.find(item => item.turbine.id === turbine.id)
      if (marker) {
        infoWindow.setContent(marker.info)
        infoWindow.open({ map, anchor: marker.googleMarker })
      }
    }

    onMounted(async () => {
      const key = import.meta.env.VITE_GOOGLE_MAPS_API_KEY
      try {
        const rows = await request('/turbines')
        if (!Array.isArray(rows) || !rows.length) throw new Error('Backend не вернул координаты турбин.')
        turbines.value = rows
        if (!key) {
          error.value = 'Ключ Google Maps не настроен; координаты турбин доступны в списке и по ссылке ниже.'
          return
        }
        const maps = await loadGoogleMaps(key)
        if (!maps) throw new Error('Google Maps не инициализировалась. Проверьте Maps JavaScript API для Demo Key.')
        const [{ Map, InfoWindow }, { AdvancedMarkerElement, PinElement }] = await Promise.all([
          maps.importLibrary('maps'),
          maps.importLibrary('marker')
        ])
        const center = {
          lat: rows.reduce((sum, turbine) => sum + turbine.latitude, 0) / rows.length,
          lng: rows.reduce((sum, turbine) => sum + turbine.longitude, 0) / rows.length
        }
        map = new Map(mapElement.value, {
          center,
          zoom: 16,
          mapId: demoMapId,
          mapTypeId: maps.MapTypeId.TERRAIN,
          mapTypeControl: true,
          mapTypeControlOptions: { mapTypeIds: [maps.MapTypeId.TERRAIN, maps.MapTypeId.ROADMAP, maps.MapTypeId.SATELLITE] },
          streetViewControl: false,
          fullscreenControl: true,
          clickableIcons: false
        })
        infoWindow = new InfoWindow()
        const bounds = new maps.LatLngBounds()
        markers = rows.map(turbine => {
          const position = { lat: turbine.latitude, lng: turbine.longitude }
          const pin = new PinElement({ background: '#117762', borderColor: '#ffffff', glyphColor: '#ffffff', glyph: String(turbine.id), scale: 1.25 })
          const content = document.createElement('div')
          content.className = 'geo-marker'
          const windBadge = document.createElement('div')
          windBadge.className = 'geo-wind-badge'
          windBadge.hidden = true
          const windNumber = document.createElement('span')
          windNumber.className = 'geo-wind-number'
          windNumber.textContent = String(turbine.id).padStart(2, '0')
          const windArrow = document.createElement('span')
          windArrow.className = 'geo-wind-arrow'
          windArrow.textContent = '↑'
          windArrow.setAttribute('aria-hidden', 'true')
          const windSpeed = document.createElement('strong')
          windSpeed.className = 'geo-wind-speed'
          windBadge.append(windNumber, windArrow, windSpeed)
          content.append(windBadge, pin.element)
          const googleMarker = new AdvancedMarkerElement({ map, position, title: turbine.name, content, gmpClickable: true })
          const info = document.createElement('div')
          info.className = 'geo-info'
          const title = document.createElement('strong')
          title.textContent = turbine.name
          const coordinates = document.createElement('span')
          coordinates.textContent = `${turbine.latitude.toFixed(6)}, ${turbine.longitude.toFixed(6)}`
          const windInfo = document.createElement('span')
          windInfo.hidden = true
          info.append(title, coordinates, windInfo)
          googleMarker.addEventListener('gmp-click', () => {
            infoWindow.setContent(info)
            infoWindow.open({ map, anchor: googleMarker })
          })
          bounds.extend(position)
          return { turbine, googleMarker, info, windBadge, windArrow, windSpeed, windInfo }
        })
        if (rows.length > 1) map.fitBounds(bounds, 80)
        else map.setCenter(center)
        if (typeof map.addListener === 'function') {
          mapListeners.push(map.addListener('zoom_changed', renderWind))
          mapListeners.push(map.addListener('heading_changed', renderWind))
        }
        void loadWind()
      } catch (reason) {
        error.value = reason.message || 'Не удалось открыть карту.'
      } finally {
        loading.value = false
      }
    })

    onBeforeUnmount(() => {
      disposed = true
      stopPlayback()
      windRequest?.abort()
      mapListeners.forEach(listener => listener?.remove?.())
      markers.forEach(({ googleMarker }) => { googleMarker.map = null })
      markers = []
      map = undefined
    })

    return {
      mapElement, turbines, loading, error, focusTurbine,
      windDate, windHour, windEnabled, windLoading, windError, windPlaying,
      windReady, selectedTime, runTime, windAt, speedText, compassFrom,
      loadWind, toggleWind, togglePlayback
    }
  },
  template: `
    <section class="geo-page">
      <div class="geo-intro">
        <div><p class="eyebrow">РАСПОЛОЖЕНИЕ ОБЪЕКТОВ</p><h2>География ВЭС</h2><p>Турбины и почасовой ветер на карте Google Maps.</p></div>
        <span class="geo-provider">Google Maps · Рельеф</span>
      </div>
      <div v-if="error" class="notice error" role="alert">{{error}}</div>
      <div v-else-if="loading" class="notice" role="status"><span class="spinner"></span> Загружаем координаты и карту…</div>
      <div v-if="!loading && !error" class="panel geo-wind-panel">
        <div class="geo-wind-head">
          <div><strong>Ветер на высоте 100 м</strong><span>Архивный прогноз ECMWF IFS · Open-Meteo</span></div>
          <button class="geo-wind-toggle" type="button" :aria-pressed="windEnabled" @click="toggleWind">{{windEnabled?'Слой ветра: включён':'Слой ветра: выключен'}}</button>
        </div>
        <div class="geo-wind-tools">
          <label class="geo-wind-date">Дата выпуска<input v-model="windDate" type="date" min="2026-01-31" max="2026-02-28" required @change="loadWind"></label>
          <label class="geo-wind-timeline">Час прогноза <strong>{{windHour}} / 48 · {{selectedTime}}</strong><input v-model.number="windHour" type="range" min="1" max="48" step="1" :disabled="!windReady" :aria-valuetext="selectedTime"></label>
          <button class="geo-wind-play" type="button" :disabled="!windReady || !windEnabled" @click="togglePlayback">{{windPlaying?'Ⅱ  Пауза':'▶  По часам'}}</button>
        </div>
        <p class="geo-wind-legend">Стрелка показывает, <strong>куда дует</strong> ветер; число — скорость в м/с. Выпуск погоды: {{runTime}}. Приближайте карту или выберите турбину для деталей.</p>
        <p v-if="windLoading" class="geo-wind-status" role="status"><span class="spinner"></span> Загружаем ветер у турбин…</p>
        <div v-if="windError" class="geo-wind-status geo-wind-error" role="alert"><span>Слой ветра недоступен: {{windError}} Карта и координаты остаются доступны.</span><button type="button" @click="loadWind">Повторить</button></div>
      </div>
      <div class="geo-layout">
        <div ref="mapElement" class="geo-map" role="region" aria-label="Карта расположения турбин"><span v-if="!loading && error">Карта недоступна. Координаты и ссылки на объекты — справа.</span></div>
        <aside class="geo-list panel">
          <h3>Турбины</h3>
          <p class="geo-hint">Выберите объект, чтобы приблизить карту и открыть данные.</p>
          <button v-for="turbine in turbines" :key="turbine.id" class="geo-turbine" @click="focusTurbine(turbine)">
            <span class="geo-pin">{{turbine.id}}</span><span class="geo-turbine-copy"><strong>{{turbine.name}}</strong><small>{{turbine.latitude.toFixed(6)}}, {{turbine.longitude.toFixed(6)}}</small><small v-if="windEnabled && windAt(turbine)" class="geo-turbine-wind">{{speedText(windAt(turbine).speed)}} м/с · из {{compassFrom(windAt(turbine).fromDegrees)}} ({{Math.round(windAt(turbine).fromDegrees)}}°)</small></span><span aria-hidden="true">↗</span>
          </button>
          <a v-for="turbine in turbines" :key="'link-'+turbine.id" :href="'https://www.google.com/maps/search/?api=1&query='+turbine.latitude+','+turbine.longitude" target="_blank" rel="noopener noreferrer">Открыть {{turbine.name}} в Google Maps</a>
          <p class="geo-attribution">Подложка: Google Maps. Ветер: архивный прогноз ECMWF IFS через Open-Meteo, не фактическое измерение. Значения относятся только к точкам турбин и не образуют поле ветра между ними.</p>
        </aside>
      </div>
    </section>
  `
}
