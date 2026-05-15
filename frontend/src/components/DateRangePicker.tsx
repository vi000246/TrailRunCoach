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
  { label: '1M', days: 30 },
  { label: '3M', days: 90 },
  { label: '6M', days: 180 },
  { label: '1Y', days: 365 },
] as const

export function DateRangePicker({ value, onChange }: Props) {
  return (
    <div className="flex items-center gap-3 flex-wrap">
      {PRESETS.map(p => (
        <button
          key={p.label}
          onClick={() => onChange({ from: daysAgo(p.days), to: today() })}
          className="px-3 py-1 text-xs bg-gray-800 hover:bg-gray-700 rounded transition text-gray-300"
        >
          {p.label}
        </button>
      ))}
      <input
        type="date"
        value={value.from}
        max={value.to}
        onChange={e => onChange({ ...value, from: e.target.value })}
        className="px-2 py-1 text-xs bg-gray-800 border border-gray-700 rounded text-gray-300"
      />
      <span className="text-xs text-gray-500">—</span>
      <input
        type="date"
        value={value.to}
        min={value.from}
        max={today()}
        onChange={e => onChange({ ...value, to: e.target.value })}
        className="px-2 py-1 text-xs bg-gray-800 border border-gray-700 rounded text-gray-300"
      />
    </div>
  )
}
