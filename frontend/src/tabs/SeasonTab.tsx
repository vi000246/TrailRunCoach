import { useState } from 'react'
import { DateRangePicker, type DateRange } from '../components/DateRangePicker'
import { PmcChart } from '../components/PmcChart'

const toIso = (d: Date) => d.toISOString().slice(0, 10)
const daysAgo = (n: number) => {
  const d = new Date()
  d.setDate(d.getDate() - n)
  return toIso(d)
}

export function SeasonTab() {
  const [range, setRange] = useState<DateRange>({
    from: daysAgo(365),
    to: toIso(new Date()),
  })

  return (
    <div className="space-y-4">
      <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
        <DateRangePicker value={range} onChange={setRange} />
      </div>
      <PmcChart dateFrom={range.from} dateTo={range.to} />
    </div>
  )
}
