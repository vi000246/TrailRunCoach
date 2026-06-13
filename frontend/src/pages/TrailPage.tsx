import { useState } from 'react'
import { DateRangePicker, type DateRange } from '../components/DateRangePicker'
import { ChartCard } from '../components/ChartCard'
import { TrailPmcChart } from '../components/charts/TrailPmcChart'
import { ClimbLoadChart } from '../components/charts/ClimbLoadChart'

const toIso = (d: Date) => d.toISOString().slice(0, 10)
const daysAgo = (n: number) => { const d = new Date(); d.setDate(d.getDate() - n); return toIso(d) }

export function TrailPage() {
  const [range, setRange] = useState<DateRange>({ from: daysAgo(365), to: toIso(new Date()) })

  return (
    <div className="p-5 space-y-6 max-w-6xl mx-auto">
      <div className="flex items-center justify-between">
        <h1 className="text-sm font-semibold text-[#7d8fa6] uppercase tracking-wide">越野跑訓練</h1>
        <DateRangePicker value={range} onChange={setRange} />
      </div>

      <ChartCard title="越野訓練負荷" chart="trail-pmc">
        <TrailPmcChart dateFrom={range.from} dateTo={range.to} />
      </ChartCard>

      <ChartCard title="爬升能力">
        <ClimbLoadChart dateFrom={range.from} dateTo={range.to} />
      </ChartCard>
    </div>
  )
}
