import {
  LineChart, Line, XAxis, YAxis, CartesianGrid,
  Tooltip, Legend, ResponsiveContainer, ReferenceLine,
} from 'recharts'
import { usePmc } from '../../api/hooks'
import { CHART_COLORS, BASE_AXIS_PROPS, BASE_GRID_PROPS, BASE_TOOLTIP_STYLE } from '../../lib/chartTheme'
import { Card, CardHeader, CardTitle, CardContent } from '../ui/card'

interface Props {
  dateFrom?: string
  dateTo?: string
}

export function PmcChart({ dateFrom, dateTo }: Props = {}) {
  const params = dateFrom || dateTo ? { date_from: dateFrom, date_to: dateTo } : undefined
  const { data, isLoading, isError } = usePmc(params)

  if (isLoading) {
    return (
      <Card>
        <CardContent className="flex items-center justify-center h-64 text-[#3e4e63] text-sm">
          Loading PMC...
        </CardContent>
      </Card>
    )
  }

  if (isError || !data?.series?.length) {
    return (
      <Card>
        <CardContent className="flex items-center justify-center h-64 text-[#3e4e63] text-sm">
          No PMC data — sync workouts to populate
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
        <CardTitle>Performance Management Chart</CardTitle>
      </CardHeader>
      <CardContent className="pt-0">
        <ResponsiveContainer width="100%" height={260}>
          <LineChart data={series} margin={{ top: 4, right: 16, left: -12, bottom: 0 }}>
            <CartesianGrid {...BASE_GRID_PROPS} />
            <XAxis dataKey="date" ticks={ticks} {...BASE_AXIS_PROPS} />
            <YAxis {...BASE_AXIS_PROPS} />
            <ReferenceLine y={0} stroke="#1c2333" />
            <Tooltip {...BASE_TOOLTIP_STYLE} />
            <Legend
              wrapperStyle={{ fontSize: 11, color: '#7d8fa6', paddingTop: 8 }}
            />
            <Line type="monotone" dataKey="ctl" name="CTL (Fitness)" stroke={CHART_COLORS.ctl} dot={false} strokeWidth={2} />
            <Line type="monotone" dataKey="atl" name="ATL (Fatigue)" stroke={CHART_COLORS.atl} dot={false} strokeWidth={2} />
            <Line type="monotone" dataKey="tsb" name="TSB (Form)" stroke={CHART_COLORS.tsb} dot={false} strokeWidth={1.5} strokeDasharray="4 2" />
          </LineChart>
        </ResponsiveContainer>
      </CardContent>
    </Card>
  )
}
