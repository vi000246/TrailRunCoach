import {
  ComposedChart, Bar, Line, XAxis, YAxis, CartesianGrid,
  Tooltip, Legend, ResponsiveContainer,
} from 'recharts'
import { useRunVolume } from '../../api/hooks'
import { BASE_AXIS_PROPS, BASE_GRID_PROPS, BASE_TOOLTIP_STYLE } from '../../lib/chartTheme'
import { Card, CardHeader, CardTitle, CardContent } from '../ui/card'

interface Props {
  dateFrom?: string
  dateTo?: string
}

export function RunVolumeLog({ dateFrom, dateTo }: Props = {}) {
  const params = dateFrom || dateTo ? { date_from: dateFrom, date_to: dateTo } : undefined
  const { data, isLoading, isError } = useRunVolume(params)

  if (isLoading) {
    return (
      <Card>
        <CardContent className="flex items-center justify-center h-64 text-[#3e4e63] text-sm">
          Loading run volume...
        </CardContent>
      </Card>
    )
  }

  if (isError || !data?.weeks?.length) {
    return (
      <Card>
        <CardContent className="flex items-center justify-center h-64 text-[#3e4e63] text-sm">
          No running volume data available
        </CardContent>
      </Card>
    )
  }

  const weeks = data.weeks.map(w => ({
    ...w,
    label: w.week_start ? w.week_start.slice(5) : '',
  }))

  const tickCount = Math.min(weeks.length, 14)
  const step = Math.max(1, Math.floor(weeks.length / tickCount))
  const ticks = weeks.filter((_, i) => i % step === 0).map(w => w.label)

  return (
    <Card>
      <CardHeader>
        <CardTitle>Running Volume Log</CardTitle>
      </CardHeader>
      <CardContent className="pt-0 space-y-4">
        <ResponsiveContainer width="100%" height={200}>
          <ComposedChart data={weeks} margin={{ top: 4, right: 40, left: -12, bottom: 0 }}>
            <CartesianGrid {...BASE_GRID_PROPS} />
            <XAxis dataKey="label" ticks={ticks} {...BASE_AXIS_PROPS} />
            <YAxis yAxisId="km" {...BASE_AXIS_PROPS} tickFormatter={(v: number) => `${v}km`} />
            <YAxis yAxisId="elev" orientation="right" {...BASE_AXIS_PROPS} tickFormatter={(v: number) => `${v}m`} />
            <Tooltip
              {...BASE_TOOLTIP_STYLE}
              formatter={(v: unknown, name: unknown) => {
                const n = String(name)
                if (n === 'Distance') return [`${Number(v).toFixed(1)} km`, n]
                if (n === 'Elevation') return [`${Number(v).toFixed(0)} m`, n]
                if (n === 'Hours') return [`${Number(v).toFixed(1)} h`, n]
                return [String(v), n]
              }}
            />
            <Legend wrapperStyle={{ fontSize: 11, color: '#7d8fa6', paddingTop: 8 }} />
            <Bar yAxisId="km" dataKey="distance_km" name="Distance" fill="#3b82f6" opacity={0.85} radius={[2, 2, 0, 0]} />
            <Bar yAxisId="elev" dataKey="elevation_m" name="Elevation" fill="#f59e0b" opacity={0.6} radius={[2, 2, 0, 0]} />
            <Line yAxisId="km" type="monotone" dataKey="hours" name="Hours" stroke="#06b6d4" dot={false} strokeWidth={2} />
          </ComposedChart>
        </ResponsiveContainer>

        {/* Monthly summary table */}
        {data.months.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-xs text-[#7d8fa6]">
              <thead>
                <tr className="border-b border-[#1c2333]">
                  <th className="text-left py-1 pr-3">Month</th>
                  <th className="text-right py-1 pr-3">Distance</th>
                  <th className="text-right py-1 pr-3">Hours</th>
                  <th className="text-right py-1 pr-3">Elevation</th>
                  <th className="text-right py-1">Runs</th>
                </tr>
              </thead>
              <tbody>
                {data.months.map(m => (
                  <tr key={m.month} className="border-b border-[#1c2333] hover:bg-[#1c2333]/40">
                    <td className="py-1 pr-3">{m.month}</td>
                    <td className="text-right py-1 pr-3">{m.distance_km.toFixed(1)} km</td>
                    <td className="text-right py-1 pr-3">{m.hours.toFixed(1)} h</td>
                    <td className="text-right py-1 pr-3">{m.elevation_m.toFixed(0)} m</td>
                    <td className="text-right py-1">{m.count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
