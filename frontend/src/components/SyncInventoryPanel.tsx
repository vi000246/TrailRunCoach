import { useSyncInventory } from '../api/hooks'
import { Card, CardHeader, CardTitle, CardContent } from './ui/card'

export function SyncInventoryPanel({ athleteId = 1 }: { athleteId?: number }) {
  const { data } = useSyncInventory(athleteId)
  if (!data) return null

  const sources = Object.entries(data.by_source).map(([k, v]) => `${k} ${v}`).join(' · ')
  const sports = Object.entries(data.by_sport).slice(0, 8).map(([k, v]) => `${k} ${v}`).join(' · ')

  return (
    <Card>
      <CardHeader>
        <CardTitle>已載入資料</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2 text-xs text-[#a9b6c8]">
        <div>
          總 <b className="text-[#e8edf5]">{data.total}</b> 筆
          {data.date_min && data.date_max && <> · {data.date_min} → {data.date_max}</>}
        </div>
        {sources && <div>來源：{sources}</div>}
        {sports && <div>運動：{sports}</div>}
        <div>
          最後同步：COROS {data.last_sync.coros ? new Date(data.last_sync.coros).toLocaleDateString() : '從未'}
          {' · '}TP {data.last_sync.tp ? new Date(data.last_sync.tp).toLocaleDateString() : '從未'}
        </div>
      </CardContent>
    </Card>
  )
}
