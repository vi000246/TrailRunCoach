import {
  ComposedChart, Bar, Line, XAxis, YAxis, CartesianGrid,
  Tooltip, Legend, ResponsiveContainer,
} from 'recharts'
import { useWeeklyLoad } from '../../api/hooks'
import { BASE_AXIS_PROPS, BASE_GRID_PROPS, BASE_TOOLTIP_STYLE } from '../../lib/chartTheme'
import { Card, CardHeader, CardTitle, CardContent } from '../ui/card'

interface Props {
  dateFrom?: string
  dateTo?: string
  sports?: string[]
}

export function WeeklyLoadChart({ dateFrom, dateTo, sports }: Props = {}) {
  const params = dateFrom || dateTo || sports
    ? { date_from: dateFrom, date_to: dateTo, sports }
    : undefined
  const { data, isLoading } = useWeeklyLoad(params)

  if (isLoading) {
    return (
      <Card>
        <CardContent className="flex items-center justify-center h-48 text-[#3e4e63] text-sm">
          Loading weekly load...
        </CardContent>
      </Card>
    )
  }

  if (!data?.weeks?.length) {
    return (
      <Card>
        <CardContent className="flex items-center justify-center h-48 text-[#3e4e63] text-sm">
          No weekly data available
        </CardContent>
      </Card>
    )
  }

  const weeks = data.weeks.map(w => ({
    ...w,
    label: w.week_start ? w.week_start.slice(5) : '',
  }))

  return (
    <Card>
      <CardHeader>
        <CardTitle>Weekly Load</CardTitle>
      </CardHeader>
      <CardContent className="pt-0">
        <ResponsiveContainer width="100%" height={200}>
          <ComposedChart data={weeks} margin={{ top: 4, right: 32, left: -12, bottom: 0 }}>
            <CartesianGrid {...BASE_GRID_PROPS} />
            <XAxis dataKey="label" {...BASE_AXIS_PROPS} />
            <YAxis yAxisId="tss" {...BASE_AXIS_PROPS} />
            <YAxis yAxisId="hours" orientation="right" {...BASE_AXIS_PROPS} />
            <Tooltip
              {...BASE_TOOLTIP_STYLE}
              formatter={(value: unknown, name: unknown) => {
                const n = String(name)
                const label = n === 'Hours' ? `${Number(value)}h` : String(value)
                return [label, n] as [string, string]
              }}
            />
            <Legend wrapperStyle={{ fontSize: 11, color: '#7d8fa6', paddingTop: 8 }} />
            <Bar yAxisId="tss" dataKey="tss" name="TSS" fill="#f59e0b" opacity={0.8} radius={[2, 2, 0, 0]} />
            <Line yAxisId="hours" type="monotone" dataKey="hours" name="Hours" stroke="#06b6d4" dot={false} strokeWidth={2} />
          </ComposedChart>
        </ResponsiveContainer>
      </CardContent>
    </Card>
  )
}
