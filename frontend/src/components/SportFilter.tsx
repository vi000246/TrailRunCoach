import { useSportsFacets } from '../api/hooks'

interface Props {
  /** Currently selected sport keys. Empty array = all sports. */
  selected: string[]
  onChange: (sports: string[]) => void
  athleteId?: number
}

export function SportFilter({ selected, onChange, athleteId = 1 }: Props) {
  const { data } = useSportsFacets(athleteId)
  const sports = data?.sports ?? []

  const toggle = (key: string) => {
    onChange(
      selected.includes(key)
        ? selected.filter(k => k !== key)
        : [...selected, key],
    )
  }

  if (!sports.length) return null

  return (
    <div className="flex flex-wrap gap-3">
      {sports.map(s => (
        <label
          key={s.key}
          className="flex items-center gap-1.5 text-xs text-[#a9b6c8] cursor-pointer select-none"
        >
          <input
            type="checkbox"
            checked={selected.includes(s.key)}
            onChange={() => toggle(s.key)}
            className="accent-[#7c3aed]"
          />
          {s.label} <span className="text-[#3e4e63]">({s.count})</span>
        </label>
      ))}
    </div>
  )
}
