import { useState } from 'react'
import { ChevronLeft, ChevronRight } from 'lucide-react'
import { useWorkouts } from '../api/hooks'
import { ActivityRow } from '../components/ActivityRow'
import { Button } from '../components/ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../components/ui/select'
import { DateRangePicker, type DateRange } from '../components/DateRangePicker'

const toIso = (d: Date) => d.toISOString().slice(0, 10)
const daysAgo = (n: number) => { const d = new Date(); d.setDate(d.getDate() - n); return toIso(d) }

export function ActivityListPage() {
  const [page, setPage] = useState(1)
  const [perPage] = useState(20)
  const [sport, setSport] = useState<string>('')
  const [range, setRange] = useState<DateRange>({ from: daysAgo(365), to: toIso(new Date()) })

  const params = {
    page,
    per_page: perPage,
    ...(sport && sport !== 'all' ? { sport } : {}),
    date_from: range.from,
    date_to: range.to,
  }

  const { data, isLoading } = useWorkouts(params)
  const total = data?.total ?? 0
  const totalPages = Math.ceil(total / perPage)

  return (
    <div className="p-5 max-w-6xl mx-auto space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-sm font-semibold text-[#7d8fa6] uppercase tracking-wide">Activities</h1>
        <div className="flex items-center gap-3">
          <Select value={sport || 'all'} onValueChange={v => { setSport(v === 'all' ? '' : v); setPage(1) }}>
            <SelectTrigger className="w-36">
              <SelectValue placeholder="All sports" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All sports</SelectItem>
              <SelectItem value="cycling">Cycling</SelectItem>
              <SelectItem value="running">Running</SelectItem>
              <SelectItem value="trail_running">Trail Run</SelectItem>
              <SelectItem value="swimming">Swimming</SelectItem>
              <SelectItem value="strength_training">Strength</SelectItem>
            </SelectContent>
          </Select>
          <DateRangePicker value={range} onChange={r => { setRange(r); setPage(1) }} />
        </div>
      </div>

      <div className="bg-[#0e1117] border border-[#1c2333] rounded-lg overflow-hidden">
        <table className="w-full">
          <thead>
            <tr className="border-b border-[#1c2333]">
              <th className="px-4 py-2.5 text-left text-xs font-medium text-[#3e4e63] uppercase tracking-wide">Date</th>
              <th className="px-4 py-2.5 text-left text-xs font-medium text-[#3e4e63] uppercase tracking-wide">Sport</th>
              <th className="px-4 py-2.5 text-left text-xs font-medium text-[#3e4e63] uppercase tracking-wide">Duration</th>
              <th className="px-4 py-2.5 text-left text-xs font-medium text-[#3e4e63] uppercase tracking-wide">NP</th>
              <th className="px-4 py-2.5 text-left text-xs font-medium text-[#3e4e63] uppercase tracking-wide">TSS</th>
              <th className="px-4 py-2.5 text-left text-xs font-medium text-[#3e4e63] uppercase tracking-wide">Source</th>
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              <tr>
                <td colSpan={6} className="px-4 py-12 text-center text-sm text-[#3e4e63]">
                  Loading...
                </td>
              </tr>
            ) : data?.items?.length ? (
              data.items.map(w => <ActivityRow key={w.id} workout={w} />)
            ) : (
              <tr>
                <td colSpan={6} className="px-4 py-12 text-center text-sm text-[#3e4e63]">
                  No activities found
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {totalPages > 1 && (
        <div className="flex items-center justify-between text-xs text-[#3e4e63]">
          <span>{total} activities · page {page} of {totalPages}</span>
          <div className="flex items-center gap-2">
            <Button variant="ghost" size="icon" onClick={() => setPage(p => p - 1)} disabled={page <= 1}>
              <ChevronLeft className="h-4 w-4" />
            </Button>
            <Button variant="ghost" size="icon" onClick={() => setPage(p => p + 1)} disabled={page >= totalPages}>
              <ChevronRight className="h-4 w-4" />
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}
