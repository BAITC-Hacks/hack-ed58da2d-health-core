import { createApp } from 'vue'
import './style.css'

const api = import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000'

createApp({
  data: () => ({ date: '2026-01-31', loading: false, error: '', run: null, rows: [] }),
  methods: {
    async generate() {
      this.error = ''
      this.loading = true
      try {
        const created = await fetch(`${api}/forecasts/${this.date}`, { method: 'POST' })
        const result = await created.json()
        if (!created.ok) throw new Error(result.detail || 'Ошибка расчёта')
        const response = await fetch(`${api}/forecasts/${result.id}`)
        if (!response.ok) throw new Error('Прогноз не загрузился')
        this.run = await response.json()
        this.rows = this.run.points
      } catch (error) {
        this.error = error.message
      } finally {
        this.loading = false
      }
    },
    displayTime(value) { return `${value.replace('T', ' ')} UTC` }
  },
  template: `
    <main>
      <header><p class="eyebrow">Health Core · ВЭС</p><h1>Почасовой прогноз выработки</h1>
        <p>Две турбины · ECMWF IFS · 48 часов · нормализованная мощность</p></header>
      <section class="panel">
        <label for="date">Дата расчёта, Казахстан (UTC+5)</label>
        <div class="controls"><input id="date" v-model="date" type="date" /><button @click="generate" :disabled="loading">{{ loading ? 'Считаем…' : 'Сформировать прогноз' }}</button></div>
        <p v-if="error" class="error" role="alert">{{ error }}</p>
        <p class="hint">Для расчёта нужна обученная модель на сервере. Выбранный выпуск погоды определяется датой расчёта.</p>
      </section>
      <section v-if="run" class="panel">
        <h2>Прогноз #{{ run.id }}</h2>
        <p>Выпуск погоды: {{ displayTime(run.weather_run_at) }} · модель: {{ run.model_version }} · {{ rows.length }} точек</p>
        <div class="table-wrap"><table><thead><tr><th>Турбина</th><th>Время прогноза</th><th>Ветер, м/с</th><th>Температура, °C</th><th>Мощность, 0–1</th></tr></thead>
          <tbody><tr v-for="point in rows" :key="point.turbine_id + point.valid_at"><td>{{ point.turbine_id }}</td><td>{{ displayTime(point.valid_at) }}</td><td>{{ point.wind_speed_ms.toFixed(2) }}</td><td>{{ point.temperature_c.toFixed(1) }}</td><td>{{ point.normalized_power.toFixed(3) }}</td></tr></tbody></table></div>
      </section>
    </main>`
}).mount('#app')
