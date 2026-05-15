import { useState } from 'react'
import { DateRangePicker, type DateRange } from '../components/DateRangePicker'
import { PmcChart } from '../components/PmcChart'
import { RunLoadChart } from '../components/charts/RunLoadChart'
import { DailyPctCtlChart } from '../components/charts/DailyPctCtlChart'
import { RampRateChart } from '../components/charts/RampRateChart'
import { IntensityLoadChart } from '../components/charts/IntensityLoadChart'
import { RunVolumeLog } from '../components/charts/RunVolumeLog'

const toIso = (d: Date) => d.toISOString().slice(0, 10)
const daysAgo = (n: number) => {
  const d = new Date()
  d.setDate(d.getDate() - n)
  return toIso(d)
}

interface SectionProps {
  title: string
  children: React.ReactNode
  defaultOpen?: boolean
}

function CollapsibleSection({ title, children, defaultOpen = true }: SectionProps) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="space-y-3">
      <button
        onClick={() => setOpen(o => !o)}
        className="flex items-center gap-2 text-sm font-medium text-[#7d8fa6] hover:text-white transition-colors"
      >
        <span className="text-xs">{open ? '▼' : '▶'}</span>
        {title}
      </button>
      {open && children}
    </div>
  )
}

export function SeasonTab() {
  const [range, setRange] = useState<DateRange>({
    from: daysAgo(365),
    to: toIso(new Date()),
  })

  return (
    <div className="space-y-6">
      <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
        <DateRangePicker value={range} onChange={setRange} />
      </div>

      <CollapsibleSection title="Overall Performance Management">
        <PmcChart dateFrom={range.from} dateTo={range.to} />
      </CollapsibleSection>

      <CollapsibleSection title="Run Training Load">
        <RunLoadChart dateFrom={range.from} dateTo={range.to} />
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <DailyPctCtlChart dateFrom={range.from} dateTo={range.to} />
          <RampRateChart dateFrom={range.from} dateTo={range.to} />
        </div>
      </CollapsibleSection>

      <CollapsibleSection title="Intensity Load">
        <IntensityLoadChart dateFrom={range.from} dateTo={range.to} />
      </CollapsibleSection>

      <CollapsibleSection title="Running Volume Log">
        <RunVolumeLog dateFrom={range.from} dateTo={range.to} />
      </CollapsibleSection>
    </div>
  )
}
