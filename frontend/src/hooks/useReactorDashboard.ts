import { useEffect, useState, useCallback, useRef } from 'react'
import {
  WS_URL,
  API_URL,
  type DashboardPayload,
  type ReactorState,
  type Alert,
  type DSSRec,
  type PredictionTte,
} from '../lib/api'

const EMPTY: DashboardPayload = {
  reactor: null as unknown as ReactorState,
  mode: 'normal',
  alerts: [],
  dss: [],
  heatmap: undefined,
  prediction: undefined,
  connected: false,
}

const WS_RECONNECT_MS = 3000

export function useReactorDashboard() {
  const [data, setData] = useState<DashboardPayload>(EMPTY)
  const [pinnMeta, setPinnMeta] = useState({
    loaded: false,
    errorPct: null as number | null,
    inferenceMs: null as number | null,
  })
  const [lastWsAt, setLastWsAt] = useState<number | null>(null)
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const applyPayload = useCallback((payload: Partial<DashboardPayload>) => {
    setData(prev => ({
      ...prev,
      ...payload,
      reactor: payload.reactor ?? prev.reactor,
      mode: payload.mode ?? prev.mode,
      alerts: payload.alerts ?? prev.alerts,
      dss: payload.dss ?? prev.dss,
      heatmap: payload.heatmap ?? prev.heatmap,
      prediction: payload.prediction ?? prev.prediction,
    }))
  }, [])

  const fetchRest = useCallback(async () => {
    try {
      const [status, alerts, dss, heatmap, pinn, prediction] = await Promise.all([
        fetch(`${API_URL}/api/v1/status`).then(r => r.ok ? r.json() : null),
        fetch(`${API_URL}/api/v1/alerts`).then(r => r.ok ? r.json() : []),
        fetch(`${API_URL}/api/v1/dss/recommendations`).then(r => r.ok ? r.json() : []),
        fetch(`${API_URL}/api/v1/reactor/heatmap`).then(r => r.ok ? r.json() : null).catch(() => null),
        fetch(`${API_URL}/api/v1/pinn/status`).then(r => r.ok ? r.json() : null).catch(() => null),
        fetch(`${API_URL}/api/v1/prediction`).then(r => r.ok ? r.json() : null).catch(() => null),
      ])

      applyPayload({
        reactor: status?.reactor_state,
        mode: status?.mode,
        alerts: alerts as Alert[],
        dss: dss as DSSRec[],
        heatmap: heatmap ?? undefined,
        prediction: prediction as PredictionTte,
      })

      if (pinn) {
        setPinnMeta({
          loaded: pinn.model_loaded ?? false,
          errorPct: pinn.val_temp_error_pct ?? null,
          inferenceMs: heatmap?.inference_ms ?? pinn.inference_ms ?? null,
        })
      }
    } catch { /* offline */ }
  }, [applyPayload])

  const connectWs = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return

    const ws = new WebSocket(WS_URL)
    wsRef.current = ws

    ws.onopen = () => setData(prev => ({ ...prev, connected: true }))
    ws.onclose = () => {
      setData(prev => ({ ...prev, connected: false }))
      reconnectRef.current = setTimeout(connectWs, WS_RECONNECT_MS)
    }
    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data)
        setLastWsAt(Date.now())
        applyPayload({
          reactor: msg.reactor,
          mode: msg.mode,
          heatmap: msg.heatmap,
          prediction: msg.prediction,
          alerts: msg.alerts,
          dss: msg.dss,
          connected: true,
        })
        if (msg.heatmap?.inference_ms != null) {
          setPinnMeta(prev => ({ ...prev, inferenceMs: msg.heatmap.inference_ms }))
        }
      } catch { /* ignore */ }
    }
  }, [applyPayload])

  useEffect(() => {
    fetchRest()
    const interval = setInterval(fetchRest, 5000)
    connectWs()

    return () => {
      clearInterval(interval)
      if (reconnectRef.current) clearTimeout(reconnectRef.current)
      wsRef.current?.close()
    }
  }, [fetchRest, connectWs])

  const wsLatencyMs = lastWsAt ? Date.now() - lastWsAt : null

  return { data, pinnMeta, refresh: fetchRest, wsLatencyMs }
}
