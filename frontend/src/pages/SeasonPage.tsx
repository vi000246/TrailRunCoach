import { useState } from 'react'
import { DateRangePicker, type DateRange } from '../components/DateRangePicker'
import { PmcChart } from '../components/charts/PmcChart'
import { WeeklyLoadChart } from '../components/charts/WeeklyLoadChart'

const toIso = (d: Date) => d.toISOString().slice(0, 10)
const daysAgo = (n: number) => { const d = new Date(); d.setDate(d.getDate() - n); return toIso(d) }

export function SeasonPage() {
  const [range, setRange] = useState<DateRange>({
    from: daysAgo(365),
    to: toIso(new Date()),
  })

  return (
    <div className="p-5 space-y-4 max-w-6xl mx-auto">
      <div className="flex items-center justify-between">
        <h1 className="text-sm font-semibold text-[#7d8fa6] uppercase tracking-wide">Season Overview</h1>
        <DateRangePicker value={range} onChange={setRange} />
      </div>
      <PmcChart dateFrom={range.from} dateTo={range.to} />
      <WeeklyLoadChart dateFrom={range.from} dateTo={range.to} />
    </div>
  )
}
