import { useState } from 'react'

interface HeatmapProps {
  grid: number[][]
  rSteps?: number
  zSteps?: number
}

function tempToColor(temp: number, min = 290, max = 380): string {
  const t = Math.max(0, Math.min(1, (temp - min) / (max - min)))
  const r = Math.round(255 * t)
  const g = Math.round(100 * (1 - t))
  const b = Math.round(255 * (1 - t))
  return `rgb(${r},${g},${b})`
}

export function Heatmap({ grid, rSteps = 20, zSteps = 30 }: HeatmapProps) {
  const [hover, setHover] = useState<{ r: number; z: number; temp: number } | null>(null)
  const flat = grid.length > 0 ? grid : Array.from({ length: rSteps }, () => Array(zSteps).fill(330))

  return (
    <div className="heatmap-wrap">
      <div className="heatmap-labels">
        <span>r (radial) →</span>
        <span>z (axial) ↓</span>
      </div>
      <div
        className="heatmap"
        style={{ gridTemplateColumns: `repeat(${zSteps}, 1fr)` }}
        onMouseLeave={() => setHover(null)}
      >
        {flat.map((row, ri) =>
          row.map((temp, zi) => (
            <div
              key={`${ri}-${zi}`}
              className={`heatmap-cell${hover?.r === ri && hover?.z === zi ? ' heatmap-cell-active' : ''}`}
              style={{ background: tempToColor(temp) }}
              onMouseEnter={() => setHover({ r: ri, z: zi, temp })}
              title={`r=${(ri / Math.max(rSteps - 1, 1)).toFixed(2)}, z=${(zi / Math.max(zSteps - 1, 1)).toFixed(2)}, T=${temp.toFixed(1)}°C`}
            />
          ))
        )}
      </div>
      {hover && (
        <div className="heatmap-tooltip">
          <strong>(r, θ, z)</strong> = ({(hover.r / Math.max(rSteps - 1, 1)).toFixed(2)}, 0, {(hover.z / Math.max(zSteps - 1, 1)).toFixed(2)})
          <br />
          T_coolant = {hover.temp.toFixed(1)} °C
        </div>
      )}
    </div>
  )
}
