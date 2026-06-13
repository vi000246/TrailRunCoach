import { useState } from 'react'
import { DateRangePicker, type DateRange } from '../components/DateRangePicker'
import { ChartCard } from '../components/ChartCard'
import { SportFilter } from '../components/SportFilter'
import { SmartDashboardSection } from '../components/charts/SmartDashboardSection'
import { PmcChart } from '../components/charts/PmcChart'
import { WeeklyLoadChart } from '../components/charts/WeeklyLoadChart'
import { useSportsFacets } from '../api/hooks'

const toIso = (d: Date) => d.toISOString().slice(0, 10)
const daysAgo = (n: number) => { const d = new Date(); d.setDate(d.getDate() - n); return toIso(d) }

export function OverviewPage() {
  const [range, setRange] = useState<DateRange>({ from: daysAgo(365), to: toIso(new Date()) })

  // Sport filter state is local to this page only (does not leak to other pages).
  // null = "not yet touched" → treated as all sports selected (derived during
  // render from facets, so no setState-in-effect is needed).
  const { data: facets } = useSportsFacets(1)
  const [selected, setSelected] = useState<string[] | null>(null)
  const allKeys = facets?.sports.map(s => s.key) ?? []
  const effectiveSelected = selected ?? allKeys
  const sports = selected ?? undefined

  return (
    <div className="p-5 space-y-6 max-w-6xl mx-auto">
      <div className="flex items-center justify-between">
        <h1 className="text-sm font-semibold text-[#7d8fa6] uppercase tracking-wide">總體訓練</h1>
        <DateRangePicker value={range} onChange={setRange} />
      </div>

      <SmartDashboardSection />

      <div className="border border-[#1c2333] rounded-lg p-3">
        <div className="text-xs font-semibold text-[#7d8fa6] uppercase tracking-wide mb-2">運動篩選</div>
        <SportFilter selected={effectiveSelected} onChange={setSelected} />
      </div>

      <ChartCard title="體能管理圖 (所有運動)" chart="pmc">
        <PmcChart dateFrom={range.from} dateTo={range.to} sports={sports} />
      </ChartCard>

      <ChartCard title="每週負荷">
        <WeeklyLoadChart dateFrom={range.from} dateTo={range.to} sports={sports} />
      </ChartCard>
    </div>
  )
}
