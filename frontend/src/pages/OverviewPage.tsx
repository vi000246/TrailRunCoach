import { useState } from 'react'
import { DateRangePicker, type DateRange } from '../components/DateRangePicker'

const toIso = (d: Date) => d.toISOString().slice(0, 10)
const daysAgo = (n: number) => { const d = new Date(); d.setDate(d.getDate() - n); return toIso(d) }

export function OverviewPage() {
  const [range, setRange] = useState<DateRange>({ from: daysAgo(365), to: toIso(new Date()) })

  return (
    <div className="p-5 space-y-6 max-w-6xl mx-auto">
      <div className="flex items-center justify-between">
        <h1 className="text-sm font-semibold text-[#7d8fa6] uppercase tracking-wide">總體訓練</h1>
        <DateRangePicker value={range} onChange={setRange} />
      </div>
    </div>
  )
}
