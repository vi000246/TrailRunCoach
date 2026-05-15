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
const daysAgo = (n: number) => {
  const d = new Date()
  d.setDate(d.getDate() - n)
  return toIso(d)
}

const PRESETS = [
  { label: '1M',  days: 30 },
  { label: '3M',  days: 90 },
  { label: '6M',  days: 180 },
  { label: '1Y',  days: 365 },
  { label: 'All', days: 365 * 10 },
] as const

export function DateRangePicker({ value, onChange }: Props) {
  return (
    <div className="flex items-center gap-2 flex-wrap">
      {PRESETS.map(p => (
        <Button
          key={p.label}
          variant="secondary"
          size="sm"
          onClick={() => onChange({ from: daysAgo(p.days), to: today() })}
          className="text-xs h-7 px-2.5"
        >
          {p.label}
        </Button>
      ))}
      <div className="flex items-center gap-2 ml-2">
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
