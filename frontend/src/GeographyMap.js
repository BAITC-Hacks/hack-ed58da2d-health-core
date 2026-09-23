import { onBeforeUnmount, onMounted, ref } from 'vue'
import { request } from './model.js'

const demoMapId = 'DEMO_MAP_ID'

function loadGoogleMaps(key) {
  if (window.google?.maps?.importLibrary) return Promise.resolve(window.google.maps)
  if (window.__windSightMapsPromise) return window.__windSightMapsPromise
  window.__windSightMapsPromise = new Promise((resolve, reject) => {
    const script = document.createElement('script')
    script.src = `https://maps.googleapis.com/maps/api/js?key=${encodeURIComponent(key)}&v=weekly&loading=async`
    script.async = true
    script.onload = () => resolve(window.google?.maps)
    script.onerror = () => reject(new Error('Не удалось загрузить Google Maps. Проверьте API-ключ и ограничения ключа.'))
    document.head.append(script)
  })
  return window.__windSightMapsPromise
}

export default {
  name: 'GeographyMap',
  setup() {
    const mapElement = ref(null)
    const turbines = ref([])
    const loading = ref(true)
    const error = ref('')
    let map
    let markers = []
    let infoWindow

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
      if (!key) {
        error.value = 'Не найден VITE_GOOGLE_MAPS_API_KEY. Проверьте корневой .env и перезапустите Vite.'
        loading.value = false
        return
      }
      try {
        const rows = await request('/turbines')
        if (!Array.isArray(rows) || !rows.length) throw new Error('Backend не вернул координаты турбин.')
        turbines.value = rows
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
          const googleMarker = new AdvancedMarkerElement({ map, position, title: turbine.name, content: pin.element, gmpClickable: true })
          const info = document.createElement('div')
          info.className = 'geo-info'
          const title = document.createElement('strong')
          title.textContent = turbine.name
          const coordinates = document.createElement('span')
          coordinates.textContent = `${turbine.latitude.toFixed(6)}, ${turbine.longitude.toFixed(6)}`
          info.append(title, coordinates)
          googleMarker.addEventListener('gmp-click', () => {
            infoWindow.setContent(info)
            infoWindow.open({ map, anchor: googleMarker })
          })
          bounds.extend(position)
          return { turbine, googleMarker, info }
        })
        if (rows.length > 1) map.fitBounds(bounds, 80)
        else map.setCenter(center)
      } catch (reason) {
        error.value = reason.message || 'Не удалось открыть карту.'
      } finally {
        loading.value = false
      }
    })

    onBeforeUnmount(() => {
      markers.forEach(({ googleMarker }) => { googleMarker.map = null })
      markers = []
      map = undefined
    })

    return { mapElement, turbines, loading, error, focusTurbine }
  },
  template: `
    <section class="geo-page">
      <div class="geo-intro">
        <div><p class="eyebrow">РАСПОЛОЖЕНИЕ ОБЪЕКТОВ</p><h2>География ВЭС</h2><p>Турбины и их точные координаты на карте Google Maps.</p></div>
        <span class="geo-provider">Google Maps · Рельеф</span>
      </div>
      <div v-if="error" class="notice error" role="alert">{{error}}</div>
      <div v-else-if="loading" class="notice" role="status"><span class="spinner"></span> Загружаем координаты и карту…</div>
      <div class="geo-layout">
        <div ref="mapElement" class="geo-map" role="region" aria-label="Карта расположения турбин"></div>
        <aside class="geo-list panel">
          <h3>Турбины</h3>
          <p class="geo-hint">Выберите объект, чтобы приблизить карту.</p>
          <button v-for="turbine in turbines" :key="turbine.id" class="geo-turbine" @click="focusTurbine(turbine)">
            <span class="geo-pin">{{turbine.id}}</span><span class="geo-turbine-copy"><strong>{{turbine.name}}</strong><small>{{turbine.latitude.toFixed(6)}}, {{turbine.longitude.toFixed(6)}}</small></span><span aria-hidden="true">↗</span>
          </button>
          <p class="geo-attribution">Подложка и управление картой предоставлены Google Maps. Внешняя карта не изменяет и не подменяет данные прогноза ВЭС.</p>
        </aside>
      </div>
    </section>
  `
}
