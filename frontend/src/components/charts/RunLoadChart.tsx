import {
  ComposedChart, Line, XAxis, YAxis, CartesianGrid,
  Tooltip, Legend, ResponsiveContainer, ReferenceArea, ReferenceLine,
} from 'recharts'
import { useRunLoad } from '../../api/hooks'
import { CHART_COLORS, BASE_AXIS_PROPS, BASE_GRID_PROPS, BASE_TOOLTIP_STYLE } from '../../lib/chartTheme'
import { Card, CardHeader, CardTitle, CardContent } from '../ui/card'

interface Props {
  dateFrom?: string
  dateTo?: string
}

export function RunLoadChart({ dateFrom, dateTo }: Props = {}) {
  const params = dateFrom || dateTo ? { date_from: dateFrom, date_to: dateTo } : undefined
  const { data, isLoading, isError } = useRunLoad(params)

  if (isLoading) {
    return (
      <Card>
        <CardContent className="flex items-center justify-center h-64 text-[#3e4e63] text-sm">
          Loading run load...
        </CardContent>
      </Card>
    )
  }

  if (isError || !data?.series?.length) {
    return (
      <Card>
        <CardContent className="flex items-center justify-center h-64 text-[#3e4e63] text-sm">
          No run load data — sync running workouts to populate
        </CardContent>
      </Card>
    )
  }

  const series = data.series.map(p => ({
    ...p,
    date: p.date.slice(5),
  }))

  const tickCount = Math.min(series.length, 14)
  const step = Math.max(1, Math.floor(series.length / tickCount))
  const ticks = series.filter((_, i) => i % step === 0).map(p => p.date)

  return (
    <Card>
      <CardHeader>
        <CardTitle>Run Training Load (PMC)</CardTitle>
      </CardHeader>
      <CardContent className="pt-0">
        <ResponsiveContainer width="100%" height={280}>
          <ComposedChart data={series} margin={{ top: 4, right: 40, left: -12, bottom: 0 }}>
            <CartesianGrid {...BASE_GRID_PROPS} />
            <XAxis dataKey="date" ticks={ticks} {...BASE_AXIS_PROPS} />
            <YAxis yAxisId="pmc" {...BASE_AXIS_PROPS} />
            <YAxis yAxisId="acwr" orientation="right" domain={[0, 2]} {...BASE_AXIS_PROPS} />
            {/* ACWR risk zones on right axis */}
            <ReferenceArea yAxisId="acwr" y1={0} y2={0.8} fill="#3b82f6" fillOpacity={0.06} />
            <ReferenceArea yAxisId="acwr" y1={0.8} y2={1.3} fill="#22c55e" fillOpacity={0.06} />
            <ReferenceArea yAxisId="acwr" y1={1.3} y2={1.5} fill="#f59e0b" fillOpacity={0.10} />
            <ReferenceArea yAxisId="acwr" y1={1.5} y2={2.0} fill="#ef4444" fillOpacity={0.10} />
            <ReferenceLine yAxisId="acwr" y={1.0} stroke="#7d8fa6" strokeDasharray="4 2" strokeWidth={1} />
            <Tooltip {...BASE_TOOLTIP_STYLE} />
            <Legend wrapperStyle={{ fontSize: 11, color: '#7d8fa6', paddingTop: 8 }} />
            <Line yAxisId="pmc" type="monotone" dataKey="ctl" name="CTL (Fitness)" stroke={CHART_COLORS.ctl} dot={false} strokeWidth={2} />
            <Line yAxisId="pmc" type="monotone" dataKey="atl" name="ATL (Fatigue)" stroke={CHART_COLORS.atl} dot={false} strokeWidth={2} />
            <Line yAxisId="pmc" type="monotone" dataKey="tsb" name="TSB (Form)" stroke={CHART_COLORS.tsb} dot={false} strokeWidth={1.5} strokeDasharray="4 2" />
            <Line yAxisId="acwr" type="monotone" dataKey="acwr" name="ACWR" stroke="#f97316" dot={false} strokeWidth={1.5} />
          </ComposedChart>
        </ResponsiveContainer>
      </CardContent>
    </Card>
  )
}
