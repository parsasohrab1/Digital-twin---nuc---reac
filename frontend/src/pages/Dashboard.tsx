import { useState } from 'react'

import { Heatmap } from '../components/Heatmap'

import { useReactorDashboard } from '../hooks/useReactorDashboard'

import { API_URL } from '../lib/api'



export function DashboardPage() {

  const { data, pinnMeta, wsLatencyMs } = useReactorDashboard()

  const [confirmStep, setConfirmStep] = useState<{ rank: number; step: 1 | 2 } | null>(null)

  const [snoozedIds, setSnoozedIds] = useState<Set<string>>(new Set())



  const r = data.reactor

  const dnbrClass = (r?.dnbr ?? 2) < 1.4 ? 'danger' : (r?.dnbr ?? 2) < 1.6 ? 'warning' : 'ok'

  const grid = data.heatmap?.temp_coolant ?? []

  const rSteps = data.heatmap?.grid?.r_steps ?? 20

  const zSteps = data.heatmap?.grid?.z_steps ?? 30



  const dnbTte = data.prediction?.dnb_p50

  const cladTte = data.prediction?.clad_p50

  const alertTte = data.alerts?.find(a => a.tte_sec != null)?.tte_sec

  const criticalTte = dnbTte ?? alertTte



  const visibleAlerts = (data.alerts ?? []).filter(a => !snoozedIds.has(a.id ?? a.title))



  const startConfirm = (rank: number) => setConfirmStep({ rank, step: 1 })



  const handleConfirm = async () => {

    if (!confirmStep) return

    if (confirmStep.step === 1) {

      setConfirmStep({ ...confirmStep, step: 2 })

      return

    }

    await fetch(`${API_URL}/api/v1/dss/confirm/${confirmStep.rank}?operator_id=operator-001`, { method: 'POST' })

    setConfirmStep(null)

    alert('توصیه DSS تأیید شد – فقط مشاوره است، بدون دستور خودکار به PLC (SAF-01)')

  }



  const snoozeAlert = async (alertId: string) => {

    await fetch(`${API_URL}/api/v1/alerts/${alertId}/snooze?minutes=5`, { method: 'POST' })

    setSnoozedIds(prev => new Set(prev).add(alertId))

  }



  return (

    <div className="dashboard">

      <header className="header">

        <h1>داشبورد دوقلوی دیجیتال هسته‌ای</h1>

        <div className="header-status">

          <span className={`mode-badge ${data.mode === 'safe' ? 'mode-safe' : ''}`}>

            حالت: {data.mode === 'safe' ? 'ایمن (قطع سنسور)' : data.mode ?? 'عادی'}

          </span>

          {data.prediction?.anomaly_detected && (

            <span className="mode-badge mode-transient">گذرا</span>

          )}

          <span className={`status-dot ${data.connected ? 'connected' : 'disconnected'}`} />

          <span className="status-text">

            {data.connected ? 'زنده' : 'قطع'}

            {wsLatencyMs != null && wsLatencyMs < 5000 && ` · WS ${wsLatencyMs}ms`}

          </span>

        </div>

      </header>



      <section className="card">

        <h2>پارامترهای کلیدی</h2>

        <div className="metric-grid">

          <div className="metric">

            <span className="metric-label">توان حرارتی</span>

            <span className="metric-value ok">{r?.power_mwth?.toFixed(0) ?? '—'} MWth</span>

          </div>

          <div className="metric">

            <span className="metric-label">فشار مدار اول</span>

            <span className="metric-value ok">{r?.pressure_mpa?.toFixed(2) ?? '—'} MPa</span>

          </div>

          <div className="metric">

            <span className="metric-label">دمای ورودی / خروجی</span>

            <span className="metric-value ok">

              {r?.temp_inlet_c?.toFixed(1) ?? '—'} / {r?.temp_outlet_c?.toFixed(1) ?? '—'} °C

            </span>

          </div>

          <div className="metric">

            <span className="metric-label">دبی جرمی</span>

            <span className="metric-value ok">{r?.mass_flow_kg_s?.toFixed(0) ?? '—'} kg/s</span>

          </div>

          <div className="metric">

            <span className="metric-label">DNBR (داغ‌ترین کانال)</span>

            <span className={`metric-value ${dnbrClass}`}>{r?.dnbr?.toFixed(2) ?? '—'}</span>

          </div>

          <div className="metric">

            <span className="metric-label">دمای غلاف (حد 620°C)</span>

            <span className={`metric-value ${(r?.temp_cladding_max_c ?? 0) > 550 ? 'warning' : 'ok'}`}>

              {r?.temp_cladding_max_c?.toFixed(1) ?? '—'} °C

            </span>

          </div>

          <div className="metric">

            <span className="metric-label">TTE → DNB</span>

            <span className={`metric-value ${criticalTte != null && criticalTte < 300 ? 'danger' : 'ok'}`}>

              {criticalTte != null ? `${criticalTte.toFixed(0)} s` : '—'}

            </span>

          </div>

          <div className="metric">

            <span className="metric-label">TTE → حد غلاف</span>

            <span className={`metric-value ${cladTte != null && cladTte < 600 ? 'warning' : 'ok'}`}>

              {cladTte != null ? `${cladTte.toFixed(0)} s` : '—'}

            </span>

          </div>

        </div>

      </section>



      <section className="card">

        <h2>هشدارهای فعال</h2>

        <ul className="alert-list">

          {visibleAlerts.length === 0 ? (

            <li className="empty-msg">هشدار فعالی وجود ندارد</li>

          ) : (

            visibleAlerts.map((a, i) => (

              <li key={a.id ?? i} className={`alert-item ${a.level}`}>

                <strong>{a.title}</strong>

                {a.tte_sec != null && <span> – TTE: {a.tte_sec.toFixed(0)}s</span>}

                {a.created_at && <time className="alert-time">{new Date(a.created_at).toLocaleString('fa-IR')}</time>}

                {a.id && a.level !== 'info' && (

                  <button type="button" className="snooze-btn" onClick={() => snoozeAlert(a.id!)}>

                    Snooze 5 دقیقه

                  </button>

                )}

              </li>

            ))

          )}

        </ul>

      </section>



      <section className="card heatmap-card">

        <h2>

          نقشه حرارتی قلب راکتور (PINN)

          {pinnMeta.loaded && (

            <span className="pinn-meta">

              v1 | خطا {pinnMeta.errorPct?.toFixed(2) ?? '—'}% | {pinnMeta.inferenceMs?.toFixed(0) ?? '—'} ms

            </span>

          )}

        </h2>

        <Heatmap grid={grid} rSteps={rSteps} zSteps={zSteps} />

      </section>



      <section className="card">

        <h2>توصیه‌های DSS</h2>

        <ul className="dss-list">

          {(data.dss ?? []).length === 0 ? (

            <li className="empty-msg">توصیه‌ای در دسترس نیست</li>

          ) : (

            data.dss!.map(rec => (

              <li key={rec.rank} className="dss-item">

                <span className="dss-rank">{rec.rank}</span>

                {rec.action}

                <div className="dss-meta">

                  ایمنی: {rec.safety_score}/5 | موفقیت: {rec.success_probability.toFixed(0)}%

                  {rec.execution_time_sec != null && ` | اجرا: ${rec.execution_time_sec.toFixed(0)}s`}

                </div>

                <button type="button" className="confirm-btn" onClick={() => startConfirm(rec.rank)}>

                  تأیید اقدام

                </button>

              </li>

            ))

          )}

        </ul>

      </section>



      {confirmStep && (

        <div className="modal-overlay" role="dialog" aria-modal="true">

          <div className="modal">

            <h3>تأیید دو مرحله‌ای</h3>

            <p>

              مرحله {confirmStep.step}/2: تأیید توصیه رتبه #{confirmStep.rank}؟

              <br />

              <small>فقط مشاوره – بدون دستور خودکار به PLC (SAF-01)</small>

            </p>

            <div className="modal-actions">

              <button type="button" onClick={() => setConfirmStep(null)}>انصراف</button>

              <button type="button" className="confirm-btn" onClick={handleConfirm}>

                {confirmStep.step === 1 ? 'ادامه' : 'تأیید نهایی'}

              </button>

            </div>

          </div>

        </div>

      )}

    </div>

  )

}

