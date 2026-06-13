import type { ReactNode } from 'react'
import { useInterpretation } from '../api/hooks'

// Status signal colours mirror SmartDashboardSection's TSB language.
const DOT: Record<string, string> = {
  green: 'bg-green-400',
  blue: 'bg-blue-400',
  yellow: 'bg-yellow-400',
  red: 'bg-red-400',
  gray: 'bg-gray-500',
}

interface Props {
  title: string
  /** When set, fetches a rule-based plain-language reading + status signal. */
  chart?: string
  athleteId?: number
  children: ReactNode
}

export function ChartCard({ title, chart, athleteId = 1, children }: Props) {
  const { data } = useInterpretation(chart ?? '', athleteId)
  const showSignal = !!chart && !!data

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-xs font-semibold text-[#7d8fa6] uppercase tracking-wide">
          {title}
        </span>
        {showSignal && (
          <>
            <span className={`w-2 h-2 rounded-full ${DOT[data.color] ?? 'bg-gray-500'}`} />
            <span className="text-xs text-gray-400">{data.summary}</span>
          </>
        )}
      </div>
      {children}
    </div>
  )
}
