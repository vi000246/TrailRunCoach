import { useState } from 'react'
import { DateRangePicker, type DateRange } from '../components/DateRangePicker'
import { ChartCard } from '../components/ChartCard'
import { RunLoadChart } from '../components/charts/RunLoadChart'
import { DailyPctCtlChart } from '../components/charts/DailyPctCtlChart'
import { RampRateChart } from '../components/charts/RampRateChart'
import { IntensityLoadChart } from '../components/charts/IntensityLoadChart'
import { RunVolumeLog } from '../components/charts/RunVolumeLog'

const toIso = (d: Date) => d.toISOString().slice(0, 10)
const daysAgo = (n: number) => { const d = new Date(); d.setDate(d.getDate() - n); return toIso(d) }

export function RunningPage() {
  const [range, setRange] = useState<DateRange>({ from: daysAgo(365), to: toIso(new Date()) })

  return (
    <div className="p-5 space-y-6 max-w-6xl mx-auto">
      <div className="flex items-center justify-between">
        <h1 className="text-sm font-semibold text-[#7d8fa6] uppercase tracking-wide">跑步訓練</h1>
        <DateRangePicker value={range} onChange={setRange} />
      </div>

      <ChartCard title="跑步訓練負荷" chart="pmc">
        <RunLoadChart dateFrom={range.from} dateTo={range.to} />
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mt-4">
          <DailyPctCtlChart dateFrom={range.from} dateTo={range.to} />
          <RampRateChart dateFrom={range.from} dateTo={range.to} />
        </div>
      </ChartCard>

      <ChartCard title="強度負荷">
        <IntensityLoadChart dateFrom={range.from} dateTo={range.to} />
      </ChartCard>

      <ChartCard title="跑量日誌">
        <RunVolumeLog dateFrom={range.from} dateTo={range.to} />
      </ChartCard>
    </div>
  )
}
