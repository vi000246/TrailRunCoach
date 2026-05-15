interface Props {
  label: string
  value: string | number
  unit?: string
  accent?: boolean
}

export function MetricCard({ label, value, unit, accent }: Props) {
  return (
    <div className="bg-[#0e1117] border border-[#1c2333] rounded-lg px-4 py-3 min-w-0">
      <div className="text-[10px] font-medium text-[#3e4e63] uppercase tracking-wide mb-1">{label}</div>
      <div className={`text-lg font-semibold tabular-nums ${accent ? 'text-[#a78bfa]' : 'text-[#e8edf5]'}`}>
        {value}
        {unit && <span className="text-xs font-normal text-[#7d8fa6] ml-1">{unit}</span>}
      </div>
    </div>
  )
}
