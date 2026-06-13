import {
  ComposedChart, Bar, Line, XAxis, YAxis, CartesianGrid,
  Tooltip, Legend, ResponsiveContainer,
} from 'recharts'
import { useTrailSummary } from '../../api/hooks'
import { BASE_AXIS_PROPS, BASE_GRID_PROPS, BASE_TOOLTIP_STYLE } from '../../lib/chartTheme'
import { Card, CardHeader, CardTitle, CardContent } from '../ui/card'

interface Props {
  dateFrom?: string
  dateTo?: string
}

export function ClimbLoadChart({ dateFrom, dateTo }: Props = {}) {
  const params = dateFrom || dateTo ? { date_from: dateFrom, date_to: dateTo } : undefined
  const { data, isLoading } = useTrailSummary(params)

  if (isLoading) {
    return (
      <Card>
        <CardContent className="flex items-center justify-center h-64 text-[#3e4e63] text-sm">
          Loading climb load...
        </CardContent>
      </Card>
    )
  }

  if (!data?.recent?.length) {
    return (
      <Card>
        <CardContent className="flex items-center justify-center h-64 text-[#3e4e63] text-sm">
          尚無越野跑爬升資料
        </CardContent>
      </Card>
    )
  }

  // oldest → newest for a left-to-right time axis
  const series = [...data.recent].reverse().map(r => ({
    date: (r.date ?? '').slice(5),
    gain_m: r.gain_m,
    vam: r.vam,
  }))

  return (
    <Card>
      <CardHeader>
        <CardTitle>
          爬升負荷 / 垂直速度 — 期間總爬升 {Math.round(data.total_gain_m)} m · {data.activity_count} 次
        </CardTitle>
      </CardHeader>
      <CardContent className="pt-0">
        <ResponsiveContainer width="100%" height={260}>
          <ComposedChart data={series} margin={{ top: 4, right: 40, left: -12, bottom: 0 }}>
            <CartesianGrid {...BASE_GRID_PROPS} />
            <XAxis dataKey="date" {...BASE_AXIS_PROPS} />
            <YAxis yAxisId="gain" {...BASE_AXIS_PROPS} />
            <YAxis yAxisId="vam" orientation="right" {...BASE_AXIS_PROPS} />
            <Tooltip {...BASE_TOOLTIP_STYLE} />
            <Legend wrapperStyle={{ fontSize: 11, color: '#7d8fa6', paddingTop: 8 }} />
            <Bar yAxisId="gain" dataKey="gain_m" name="爬升 (m)" fill="#7c3aed" fillOpacity={0.7} />
            <Line yAxisId="vam" type="monotone" dataKey="vam" name="VAM (m/hr)" stroke="#f59e0b" dot={false} strokeWidth={1.5} />
          </ComposedChart>
        </ResponsiveContainer>
      </CardContent>
    </Card>
  )
}
