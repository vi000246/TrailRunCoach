import {
  ComposedChart, Line, XAxis, YAxis, CartesianGrid,
  Tooltip, Legend, ResponsiveContainer, ReferenceLine,
} from 'recharts'
import { useTrailLoad } from '../../api/hooks'
import { CHART_COLORS, BASE_AXIS_PROPS, BASE_GRID_PROPS, BASE_TOOLTIP_STYLE } from '../../lib/chartTheme'
import { Card, CardHeader, CardTitle, CardContent } from '../ui/card'

interface Props {
  dateFrom?: string
  dateTo?: string
}

export function TrailPmcChart({ dateFrom, dateTo }: Props = {}) {
  const params = dateFrom || dateTo ? { date_from: dateFrom, date_to: dateTo } : undefined
  const { data, isLoading, isError } = useTrailLoad(params)

  if (isLoading) {
    return (
      <Card>
        <CardContent className="flex items-center justify-center h-64 text-[#3e4e63] text-sm">
          Loading trail load...
        </CardContent>
      </Card>
    )
  }

  if (isError || !data?.series?.length) {
    return (
      <Card>
        <CardContent className="flex items-center justify-center h-64 text-[#3e4e63] text-sm">
          尚無越野跑資料 — 同步越野跑活動以填入
        </CardContent>
      </Card>
    )
  }

  const series = data.series.map(p => ({ ...p, date: p.date.slice(5) }))
  const tickCount = Math.min(series.length, 14)
  const step = Math.max(1, Math.floor(series.length / tickCount))
  const ticks = series.filter((_, i) => i % step === 0).map(p => p.date)

  return (
    <Card>
      <CardHeader>
        <CardTitle>越野 PMC（hrTSS 為主，rTSS 並陳）</CardTitle>
      </CardHeader>
      <CardContent className="pt-0">
        <ResponsiveContainer width="100%" height={280}>
          <ComposedChart data={series} margin={{ top: 4, right: 40, left: -12, bottom: 0 }}>
            <CartesianGrid {...BASE_GRID_PROPS} />
            <XAxis dataKey="date" ticks={ticks} {...BASE_AXIS_PROPS} />
            <YAxis yAxisId="pmc" {...BASE_AXIS_PROPS} />
            <YAxis yAxisId="tss" orientation="right" {...BASE_AXIS_PROPS} />
            <ReferenceLine yAxisId="pmc" y={0} stroke="#1c2333" />
            <Tooltip {...BASE_TOOLTIP_STYLE} />
            <Legend wrapperStyle={{ fontSize: 11, color: '#7d8fa6', paddingTop: 8 }} />
            <Line yAxisId="pmc" type="monotone" dataKey="ctl" name="CTL (體能)" stroke={CHART_COLORS.ctl} dot={false} strokeWidth={2} />
            <Line yAxisId="pmc" type="monotone" dataKey="atl" name="ATL (疲勞)" stroke={CHART_COLORS.atl} dot={false} strokeWidth={2} />
            <Line yAxisId="pmc" type="monotone" dataKey="tsb" name="TSB (狀態)" stroke={CHART_COLORS.tsb} dot={false} strokeWidth={1.5} strokeDasharray="4 2" />
            <Line yAxisId="tss" type="monotone" dataKey="r_tss" name="rTSS (並陳)" stroke="#a78bfa" dot={false} strokeWidth={1.5} strokeDasharray="3 3" />
          </ComposedChart>
        </ResponsiveContainer>
      </CardContent>
    </Card>
  )
}
