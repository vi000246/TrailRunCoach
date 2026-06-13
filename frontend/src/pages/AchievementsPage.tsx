import { useState } from 'react'
import { DateRangePicker, type DateRange } from '../components/DateRangePicker'
import { useAchievements } from '../api/hooks'
import { Card, CardHeader, CardTitle, CardContent } from '../components/ui/card'
import { Button } from '../components/ui/button'
import type { AchievementItem } from '../api/client'

const toIso = (d: Date) => d.toISOString().slice(0, 10)
const daysAgo = (n: number) => { const d = new Date(); d.setDate(d.getDate() - n); return toIso(d) }

function buildAiPrompt(items: AchievementItem[], from: string, to: string): string {
  const lines = items.map((a, i) =>
    `${i + 1}. ${a.date} ${a.summary}（負荷 ${a.load}）`).join('\n')
  return `這是我在 ${from} 到 ${to} 期間負荷最大的運動紀錄：\n${lines}\n\n` +
    `請用一段親切、有說服力的文字（繁體中文，約 100 字）描述我的體能與耐力水準，` +
    `讓我可以傳給揪團爬山的夥伴，讓他們了解我的能力。`
}

export function AchievementsPage() {
  const [range, setRange] = useState<DateRange>({ from: daysAgo(90), to: toIso(new Date()) })
  const { data, isLoading } = useAchievements({ date_from: range.from, date_to: range.to, limit: 15 })

  const [aiText, setAiText] = useState('')
  const [aiBusy, setAiBusy] = useState(false)

  const items = data?.items ?? []

  const generateDescription = async () => {
    if (!items.length) return
    setAiText('')
    setAiBusy(true)
    try {
      const res = await fetch('/api/v1/ai/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          athlete_id: 1,
          message: buildAiPrompt(items, range.from, range.to),
        }),
      })
      if (!res.ok || !res.body) {
        const err = await res.json().catch(() => ({ detail: res.statusText }))
        setAiText(`（AI 失敗：${err.detail ?? '請先到設定頁設定 AI key'}）`)
        return
      }
      const reader = res.body.getReader()
      const dec = new TextDecoder()
      let buf = ''
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buf += dec.decode(value, { stream: true })
        const lines = buf.split('\n')
        buf = lines.pop() ?? ''
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          try {
            const p = JSON.parse(line.slice(6))
            if (p.chunk) setAiText(prev => prev + p.chunk)
          } catch { /* skip */ }
        }
      }
    } catch (e) {
      setAiText(`（AI 失敗：${String(e)}）`)
    } finally {
      setAiBusy(false)
    }
  }

  return (
    <div className="p-5 space-y-4 max-w-4xl mx-auto pb-12">
      <div className="flex items-center justify-between">
        <h1 className="text-sm font-semibold text-[#7d8fa6] uppercase tracking-wide">運動成就</h1>
        <DateRangePicker value={range} onChange={setRange} />
      </div>
      <p className="text-xs text-[#7d8fa6]">
        指定期間內負荷最大的運動，可截圖傳給揪團夥伴，或用 AI 轉成文字描述。
      </p>

      <Card>
        <CardHeader>
          <CardTitle>負荷最大的運動（{range.from} → {range.to}）</CardTitle>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <div className="text-[#3e4e63] text-sm">Loading...</div>
          ) : !items.length ? (
            <div className="text-[#3e4e63] text-sm">此期間沒有運動紀錄</div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-[10px] uppercase tracking-wide text-[#3e4e63]">
                    <th className="py-1.5 px-2">日期</th>
                    <th className="py-1.5 px-2">運動</th>
                    <th className="py-1.5 px-2 text-right">距離</th>
                    <th className="py-1.5 px-2 text-right">爬升</th>
                    <th className="py-1.5 px-2 text-right">時間</th>
                    <th className="py-1.5 px-2 text-right">負荷</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map(a => (
                    <tr key={a.id} className="border-t border-[#131824]">
                      <td className="py-1.5 px-2 text-[#7d8fa6] tabular-nums">{a.date}</td>
                      <td className="py-1.5 px-2 text-[#e8edf5]">{a.sport_label}</td>
                      <td className="py-1.5 px-2 text-right tabular-nums text-[#a9b6c8]">{a.distance_km ? `${a.distance_km} km` : '–'}</td>
                      <td className="py-1.5 px-2 text-right tabular-nums text-[#a9b6c8]">{a.elevation_m ? `${a.elevation_m} m` : '–'}</td>
                      <td className="py-1.5 px-2 text-right tabular-nums text-[#a9b6c8]">{a.duration_str}</td>
                      <td className="py-1.5 px-2 text-right tabular-nums text-[#c4b5fd]">{a.load}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>AI 文字描述</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <Button size="sm" onClick={generateDescription} disabled={aiBusy || !items.length}>
            {aiBusy ? '生成中...' : '用 AI 轉成文字敘述'}
          </Button>
          {aiText && (
            <div className="text-sm text-[#e8edf5] bg-[#0e1117] border border-[#1c2333] rounded p-3 whitespace-pre-wrap leading-relaxed">
              {aiText}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
