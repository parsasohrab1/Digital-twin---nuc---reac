import { useEffect, useState } from 'react'

import { API_URL, apiGet, apiPost, apiPut, type SafetyThresholds } from '../lib/api'



interface EventLogEntry {

  id: string

  level: string

  message?: string

  title?: string

  created_at: string

}



export function SettingsPage() {

  const [thresholds, setThresholds] = useState<SafetyThresholds>({

    max_clad_temp_c: 620,

    min_dnbr: 1.3,

    max_roughness_rate: 0.001,

  })

  const [pinnLambda, setPinnLambda] = useState({ lambda_ns: 0.1, lambda_energy: 0.1, lambda_neutronics: 0.01 })

  const [events, setEvents] = useState<EventLogEntry[]>([])

  const [status, setStatus] = useState('')

  const [exportHours, setExportHours] = useState(24)

  const [exportFormat, setExportFormat] = useState<'csv' | 'parquet'>('csv')



  useEffect(() => {

    apiGet<{ thresholds?: SafetyThresholds; pinn?: Record<string, number> }>('/api/v1/config/ui')

      .then(cfg => {

        if (cfg.thresholds) setThresholds(cfg.thresholds)

        if (cfg.pinn) {

          setPinnLambda({

            lambda_ns: cfg.pinn.lambda_ns ?? 0.1,

            lambda_energy: cfg.pinn.lambda_energy ?? 0.1,

            lambda_neutronics: cfg.pinn.lambda_neutronics ?? 0.01,

          })

        }

      })

      .catch(() => {})



    apiGet<EventLogEntry[]>('/api/v1/events/log?days=30')

      .then(setEvents)

      .catch(() => {})

  }, [])



  const saveThresholds = async () => {

    try {

      await apiPut('/api/v1/config/thresholds', thresholds)

      setStatus('آستانه‌های ایمنی ذخیره شد')

    } catch {

      setStatus('خطا در ذخیره آستانه‌ها')

    }

  }



  const savePinnLambda = async () => {

    try {

      await apiPut('/api/v1/config/pinn-lambda', pinnLambda)

      setStatus('ضرایب λ PINN ذخیره شد')

    } catch {

      setStatus('خطا در ذخیره تنظیمات PINN')

    }

  }



  const reloadPinn = async () => {

    try {

      await apiPost('/api/v1/pinn/reload')

      setStatus('بارگذاری مجدد PINN زمان‌بندی شد')

    } catch {

      setStatus('خطا در reload مدل PINN')

    }

  }



  const downloadExport = () => {

    window.open(`${API_URL}/api/v1/export/download?format=${exportFormat}&hours=${exportHours}`, '_blank')

    setStatus(`خروجی ${exportFormat} برای ${exportHours} ساعت شروع شد`)

  }



  return (

    <div className="settings-page">

      <h1>تنظیمات و گزارش‌ها</h1>



      {status && <div className="settings-status">{status}</div>}



      <section className="card settings-section">

        <h2>آستانه‌های ایمنی</h2>

        <div className="form-grid">

          <label>

            حداکثر دمای غلاف (°C)

            <input

              type="number"

              value={thresholds.max_clad_temp_c}

              onChange={e => setThresholds(t => ({ ...t, max_clad_temp_c: +e.target.value }))}

            />

          </label>

          <label>

            حداقل DNBR

            <input

              type="number"

              step="0.1"

              value={thresholds.min_dnbr}

              onChange={e => setThresholds(t => ({ ...t, min_dnbr: +e.target.value }))}

            />

          </label>

          <label>

            حداکثر نرخ زبری

            <input

              type="number"

              step="0.0001"

              value={thresholds.max_roughness_rate}

              onChange={e => setThresholds(t => ({ ...t, max_roughness_rate: +e.target.value }))}

            />

          </label>

        </div>

        <button type="button" className="confirm-btn" onClick={saveThresholds}>ذخیره آستانه‌ها</button>

      </section>



      <section className="card settings-section">

        <h2>مدل PINN</h2>

        <div className="form-grid">

          <label>λ NS <input type="number" step="0.01" value={pinnLambda.lambda_ns} onChange={e => setPinnLambda(p => ({ ...p, lambda_ns: +e.target.value }))} /></label>

          <label>λ Energy <input type="number" step="0.01" value={pinnLambda.lambda_energy} onChange={e => setPinnLambda(p => ({ ...p, lambda_energy: +e.target.value }))} /></label>

          <label>λ Neutronics <input type="number" step="0.01" value={pinnLambda.lambda_neutronics} onChange={e => setPinnLambda(p => ({ ...p, lambda_neutronics: +e.target.value }))} /></label>

        </div>

        <div className="btn-row">

          <button type="button" className="confirm-btn" onClick={savePinnLambda}>ذخیره λ</button>

          <button type="button" className="secondary-btn" onClick={reloadPinn}>Hot-Reload PINN</button>

        </div>

      </section>



      <section className="card settings-section">

        <h2>خروجی تاریخچه</h2>

        <div className="form-grid">

          <label>

            ساعات

            <input type="number" min={1} max={720} value={exportHours} onChange={e => setExportHours(+e.target.value)} />

          </label>

          <label>

            فرمت

            <select value={exportFormat} onChange={e => setExportFormat(e.target.value as 'csv' | 'parquet')}>

              <option value="csv">CSV</option>

              <option value="parquet">Parquet</option>

            </select>

          </label>

        </div>

        <button type="button" className="confirm-btn" onClick={downloadExport}>دانلود</button>

      </section>



      <section className="card settings-section">

        <h2>لاگ رویدادها – ۳۰ روز گذشته</h2>

        <div className="event-log">

          {events.length === 0 ? (

            <p className="empty-msg">رویدادی ثبت نشده</p>

          ) : (

            <table>

              <thead>

                <tr><th>زمان</th><th>سطح</th><th>پیام</th></tr>

              </thead>

              <tbody>

                {events.map(ev => (

                  <tr key={ev.id} className={`log-${ev.level}`}>

                    <td>{new Date(ev.created_at).toLocaleString('fa-IR')}</td>

                    <td>{ev.level}</td>

                    <td>{ev.title ?? ev.message}</td>

                  </tr>

                ))}

              </tbody>

            </table>

          )}

        </div>

      </section>

    </div>

  )

}

