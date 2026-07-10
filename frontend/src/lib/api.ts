export const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000'
export const WS_URL = import.meta.env.VITE_WS_URL || 'ws://localhost:8000/ws/reactor'

export interface ReactorState {
  power_mwth?: number
  pressure_mpa?: number
  temp_inlet_c?: number
  temp_outlet_c?: number
  mass_flow_kg_s?: number
  temp_hot_channel_c?: number
  temp_cladding_max_c?: number
  dnbr?: number
}

export interface Alert {
  id?: string
  level: string
  title: string
  description?: string
  tte_sec?: number
  created_at?: string
}

export interface DSSRec {
  rank: number
  action: string
  safety_score: number
  success_probability: number
  execution_time_sec?: number
}

export interface PredictionTte {
  dnb_p50?: number
  clad_p50?: number
  anomaly_detected?: boolean
}

export interface HeatmapGrid {
  temp_coolant: number[][]
  grid?: { r_steps: number; z_steps: number }
  inference_ms?: number
}

export interface DashboardPayload {
  reactor?: ReactorState
  mode?: string
  heatmap?: HeatmapGrid
  prediction?: PredictionTte
  alerts?: Alert[]
  dss?: DSSRec[]
  connected?: boolean
}

export interface SafetyThresholds {
  max_clad_temp_c: number
  min_dnbr: number
  max_roughness_rate: number
}

export async function apiGet<T>(path: string): Promise<T> {
  const res = await fetch(`${API_URL}${path}`)
  if (!res.ok) throw new Error(`${path}: ${res.status}`)
  return res.json()
}

export async function apiPost<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    method: 'POST',
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) throw new Error(`${path}: ${res.status}`)
  return res.json()
}

export async function apiPut<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error(`${path}: ${res.status}`)
  return res.json()
}
