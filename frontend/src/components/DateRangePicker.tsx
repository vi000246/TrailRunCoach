import { Button } from './ui/button'

export interface DateRange {
  from: string  // YYYY-MM-DD
  to: string
}

interface Props {
  value: DateRange
  onChange: (range: DateRange) => void
}

const toIso = (d: Date) => d.toISOString().slice(0, 10)
const today = () => toIso(new Date())

function calendarPresets(): Array<{ label: string; from: string; to: string }> {
  const now = new Date()
  const y = now.getFullYear()
  const m = now.getMonth()

  const firstOfMonth = (year: number, month: number) =>
    toIso(new Date(year, month, 1))
  const lastOfMonth = (year: number, month: number) =>
    toIso(new Date(year, month + 1, 0))

  const prevM = m === 0 ? 11 : m - 1
  const prevY = m === 0 ? y - 1 : y

  const monthsMinus = (n: number) => {
    const d = new Date(y, m - n, 1)
    return toIso(d)
  }

  return [
    { label: '本月', from: firstOfMonth(y, m), to: today() },
    { label: '上月', from: firstOfMonth(prevY, prevM), to: lastOfMonth(prevY, prevM) },
    { label: '近3月', from: monthsMinus(3), to: today() },
    { label: '近6月', from: monthsMinus(6), to: today() },
    { label: '今年', from: `${y}-01-01`, to: today() },
    { label: '去年', from: `${y - 1}-01-01`, to: `${y - 1}-12-31` },
  ]
}

const ROLLING_PRESETS = [
  { label: '30天', days: 30 },
  { label: '90天', days: 90 },
  { label: '180天', days: 180 },
  { label: '1年', days: 365 },
] as const

function daysAgo(n: number) {
  const d = new Date()
  d.setDate(d.getDate() - n)
  return toIso(d)
}

export function DateRangePicker({ value, onChange }: Props) {
  const calPresets = calendarPresets()

  const isCalActive = (p: { from: string; to: string }) =>
    value.from === p.from && value.to === p.to

  const isRollingActive = (days: number) =>
    value.from === daysAgo(days) && value.to === today()

  const activeClass = 'bg-[#7c3aed] text-white border-[#7c3aed] hover:bg-[#6d28d9]'
  const inactiveClass = ''

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-1.5 flex-wrap">
        {calPresets.map(p => (
          <Button
            key={p.label}
            variant="secondary"
            size="sm"
            onClick={() => onChange({ from: p.from, to: p.to })}
            className={`text-xs h-7 px-2.5 ${isCalActive(p) ? activeClass : inactiveClass}`}
          >
            {p.label}
          </Button>
        ))}
        <span className="text-[#3e4e63] text-xs mx-1">|</span>
        {ROLLING_PRESETS.map(p => (
          <Button
            key={p.label}
            variant="secondary"
            size="sm"
            onClick={() => onChange({ from: daysAgo(p.days), to: today() })}
            className={`text-xs h-7 px-2.5 ${isRollingActive(p.days) ? activeClass : inactiveClass}`}
          >
            {p.label}
          </Button>
        ))}
      </div>
      <div className="flex items-center gap-2">
        <input
          type="date"
          value={value.from}
          max={value.to}
          onChange={e => onChange({ ...value, from: e.target.value })}
          className="h-7 px-2 text-xs bg-[#141922] border border-[#1c2333] rounded text-[#e8edf5] focus:outline-none focus:ring-1 focus:ring-[#7c3aed]"
          style={{ colorScheme: 'dark' }}
        />
        <span className="text-xs text-[#3e4e63]">—</span>
        <input
          type="date"
          value={value.to}
          min={value.from}
          max={today()}
          onChange={e => onChange({ ...value, to: e.target.value })}
          className="h-7 px-2 text-xs bg-[#141922] border border-[#1c2333] rounded text-[#e8edf5] focus:outline-none focus:ring-1 focus:ring-[#7c3aed]"
          style={{ colorScheme: 'dark' }}
        />
      </div>
    </div>
  )
}
