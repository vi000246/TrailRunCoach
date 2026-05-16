import {
  ComposedChart, Line, Bar, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer, ScatterChart, Scatter,
} from 'recharts'
import { useTrailAnalysis } from '../../api/hooks'
import { CHART_COLORS, BASE_GRID_PROPS, BASE_TOOLTIP_STYLE } from '../../lib/chartTheme'
import { Card, CardHeader, CardTitle, CardContent } from '../ui/card'

const AXIS = { tick: { fontSize: 11, fill: '#3e4e63' }, tickLine: false, axisLine: false } as const

function fmtPace(sPerKm: number): string {
  if (!sPerKm || sPerKm <= 0 || sPerKm > 1800) return '-'
  const m = Math.floor(sPerKm / 60)
  const s = Math.round(sPerKm % 60)
  return `${m}:${String(s).padStart(2, '0')}`
}

function fmtDist(m: number): string {
  return m >= 1000 ? `${(m / 1000).toFixed(1)}km` : `${Math.round(m)}m`
}

interface Props {
  workoutId: number
}

export function TrailAnalysisSection({ workoutId }: Props) {
  const { data, isLoading, isError } = useTrailAnalysis(workoutId)

  if (isLoading) return <div className="text-xs text-gray-600 py-2">Loading trail data...</div>
  if (isError || !data?.is_trail) return null

  const elevPaceData = data.series.map(p => ({
    dist: +(p.dist_m / 1000).toFixed(2),
    alt: p.alt_m,
    gap: p.gap_s_km > 0 && p.gap_s_km < 1800 ? p.gap_s_km : null,
  }))

  return (
    <div className="space-y-4 mt-4">
      <div className="text-xs font-semibold text-gray-500 uppercase tracking-wide">
        越野跑分析 — 總爬升 {data.total_gain_m.toFixed(0)}m
      </div>

      {/* Elevation + GAP pace overlay */}
      <Card>
        <CardHeader><CardTitle>海拔 & 坡度調整配速 (GAP)</CardTitle></CardHeader>
        <CardContent>
          <ResponsiveContainer width="100%" height={200}>
            <ComposedChart data={elevPaceData} margin={{ left: 0, right: 0 }}>
              <CartesianGrid {...BASE_GRID_PROPS} />
              <XAxis dataKey="dist" tickFormatter={v => `${v}km`} {...AXIS} />
              <YAxis yAxisId="alt" orientation="left" {...AXIS} unit="m" />
              <YAxis
                yAxisId="gap"
                orientation="right"
                {...AXIS}
                tickFormatter={fmtPace}
                domain={['auto', 'auto']}
              />
              <Tooltip
                {...BASE_TOOLTIP_STYLE}
                formatter={(val: unknown, name: unknown) => {
                  const v = Number(val)
                  return name === 'alt' ? [`${v.toFixed(0)}m`, '海拔'] : [fmtPace(v), 'GAP配速']
                }}
                labelFormatter={v => `${v}km`}
              />
              <Bar yAxisId="alt" dataKey="alt" fill="#1c2333" opacity={0.6} />
              <Line
                yAxisId="gap"
                type="monotone"
                dataKey="gap"
                stroke={CHART_COLORS.pace}
                dot={false}
                strokeWidth={1.5}
                connectNulls
              />
            </ComposedChart>
          </ResponsiveContainer>
        </CardContent>
      </Card>

      {/* Grade vs Cadence scatter */}
      {data.grade_cadence.length > 0 && (
        <Card>
          <CardHeader><CardTitle>坡度 vs 步頻</CardTitle></CardHeader>
          <CardContent>
            <ResponsiveContainer width="100%" height={180}>
              <ScatterChart margin={{ left: 0, right: 0 }}>
                <CartesianGrid {...BASE_GRID_PROPS} />
                <XAxis dataKey="grade_pct" name="坡度" {...AXIS} unit="%" />
                <YAxis dataKey="cadence" name="步頻" {...AXIS} unit="spm" />
                <Tooltip
                  {...BASE_TOOLTIP_STYLE}
                  formatter={(val: unknown, name: unknown) => {
                    const v = Number(val)
                    const label = String(name)
                    return label === '坡度' ? [`${v}%`, label] : [`${v}spm`, label]
                  }}
                />
                <Scatter data={data.grade_cadence} fill={CHART_COLORS.cadence} opacity={0.5} />
              </ScatterChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>
      )}

      {/* Climb segments table */}
      {data.climb_segments.length > 0 && (
        <Card>
          <CardHeader><CardTitle>爬坡段分析</CardTitle></CardHeader>
          <CardContent>
            <table className="w-full text-xs">
              <thead>
                <tr className="text-gray-500">
                  <th className="text-left py-1 px-2 font-normal">#</th>
                  <th className="text-left py-1 px-2 font-normal">起點</th>
                  <th className="text-left py-1 px-2 font-normal">距離</th>
                  <th className="text-left py-1 px-2 font-normal">爬升</th>
                  <th className="text-left py-1 px-2 font-normal">坡度</th>
                  <th className="text-left py-1 px-2 font-normal">VAM</th>
                </tr>
              </thead>
              <tbody>
                {data.climb_segments.map((seg, i) => (
                  <tr key={i} className="border-t border-gray-800">
                    <td className="py-1 px-2 text-gray-500">{i + 1}</td>
                    <td className="py-1 px-2 text-gray-300">{fmtDist(seg.start_m)}</td>
                    <td className="py-1 px-2 text-gray-300">{fmtDist(seg.distance_m)}</td>
                    <td className="py-1 px-2 text-gray-300">{seg.gain_m.toFixed(0)}m</td>
                    <td className="py-1 px-2 text-gray-300">{seg.grade_pct.toFixed(1)}%</td>
                    <td className="py-1 px-2 text-gray-300">{seg.vam > 0 ? `${seg.vam}m/h` : '-'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </CardContent>
        </Card>
      )}

      {/* HR drift card */}
      {data.hr_drift && (
        <Card>
          <CardHeader><CardTitle>心率漂移 (解耦程度)</CardTitle></CardHeader>
          <CardContent>
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-3 text-sm">
              <div>
                <div className="text-xs text-gray-500">前半 HR</div>
                <div className="text-gray-100">{data.hr_drift.hr_first_half.toFixed(0)} bpm</div>
              </div>
              <div>
                <div className="text-xs text-gray-500">後半 HR</div>
                <div className="text-gray-100">{data.hr_drift.hr_second_half.toFixed(0)} bpm</div>
              </div>
              <div>
                <div className="text-xs text-gray-500">前半 GAP配速</div>
                <div className="text-gray-100">{fmtPace(data.hr_drift.gap_first_half_s_per_km)}</div>
              </div>
              <div>
                <div className="text-xs text-gray-500">後半 GAP配速</div>
                <div className="text-gray-100">{fmtPace(data.hr_drift.gap_second_half_s_per_km)}</div>
              </div>
              <div className="col-span-2 sm:col-span-1">
                <div className="text-xs text-gray-500">解耦程度</div>
                <div className={`font-semibold ${Math.abs(data.hr_drift.decoupling_pct) > 5 ? 'text-yellow-400' : 'text-green-400'}`}>
                  {data.hr_drift.decoupling_pct > 0 ? '+' : ''}{data.hr_drift.decoupling_pct.toFixed(1)}%
                </div>
                <div className="text-xs text-gray-600">{Math.abs(data.hr_drift.decoupling_pct) > 5 ? '超過5%建議加強有氧基礎' : '耦合良好'}</div>
              </div>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  )
}
