import { useState } from 'react'
import { useAthleteSettings, useUpdateSettings, useAiModels } from '../api/hooks'
import type { PowerZone, HrZone } from '../api/client'
import { CLAUDE_MODELS, OPENAI_MODELS } from '../engine/ai/models'

function ZoneTable({ zones, unitKey }: {
  zones: (PowerZone | HrZone)[]
  unitKey: 'min_w' | 'min_bpm'
}) {
  const maxKey = unitKey === 'min_w' ? 'max_w' : 'max_bpm'
  const unit = unitKey === 'min_w' ? 'w' : 'bpm'
  return (
    <table className="w-full text-xs">
      <thead>
        <tr className="text-gray-500">
          <th className="text-left py-1 px-2 font-normal">Zone</th>
          <th className="text-left py-1 px-2 font-normal">Label</th>
          <th className="text-left py-1 px-2 font-normal">Min</th>
          <th className="text-left py-1 px-2 font-normal">Max</th>
        </tr>
      </thead>
      <tbody>
        {zones.map(z => (
          <tr key={z.zone} className="border-t border-gray-800">
            <td className="py-1 px-2 text-gray-400">Z{z.zone}</td>
            <td className="py-1 px-2 text-gray-300">{z.label}</td>
            <td className="py-1 px-2 text-gray-300">{(z as PowerZone & HrZone)[unitKey]}{unit}</td>
            <td className="py-1 px-2 text-gray-300">
              {(z as PowerZone & HrZone)[maxKey] != null
                ? `${(z as PowerZone & HrZone)[maxKey]}${unit}`
                : '∞'}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

export function ConfigTab() {
  const { data: settings, isLoading } = useAthleteSettings()
  const update = useUpdateSettings()
  const { data: aiModels } = useAiModels()
  const [saved, setSaved] = useState(false)
  const [aiSaved, setAiSaved] = useState(false)

  const [provider, setProvider] = useState<string>(settings?.ai_provider ?? 'claude')

  const handleSubmit = (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    const fd = new FormData(e.currentTarget)
    const ftpVal = fd.get('ftp') as string
    const lthrVal = fd.get('lthr') as string
    update.mutate(
      {
        ftp_w: ftpVal ? parseFloat(ftpVal) : undefined,
        lthr: lthrVal ? parseInt(lthrVal, 10) : undefined,
      },
      {
        onSuccess: () => {
          setSaved(true)
          setTimeout(() => setSaved(false), 2000)
        },
      },
    )
  }

  const handleAiSubmit = (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    const fd = new FormData(e.currentTarget)
    const prov = fd.get('ai_provider') as string
    const key = fd.get('ai_api_key') as string
    const model = fd.get('ai_model') as string
    update.mutate(
      {
        ai_provider: prov || undefined,
        ai_api_key: key || undefined,
        ai_model: model || undefined,
      },
      {
        onSuccess: () => {
          setAiSaved(true)
          setTimeout(() => setAiSaved(false), 2000)
        },
      },
    )
  }

  const modelOptions = provider === 'claude'
    ? (aiModels?.claude ?? CLAUDE_MODELS)
    : (aiModels?.openai ?? OPENAI_MODELS)

  return (
    <div className="space-y-4 max-w-lg">
      <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
        <h2 className="text-sm font-semibold text-gray-400 mb-3">Athlete Settings</h2>
        {isLoading ? (
          <div className="text-gray-500 text-xs">Loading settings...</div>
        ) : (
          <form key={settings?.effective_date ?? 'empty'} onSubmit={handleSubmit} className="space-y-3">
            <div className="flex flex-col gap-1">
              <label className="text-xs text-gray-500">FTP (watts)</label>
              <input
                name="ftp"
                type="number"
                min="1"
                max="600"
                defaultValue={settings?.ftp_w ?? ''}
                placeholder="e.g. 250"
                className="px-2 py-1 text-sm bg-gray-800 border border-gray-700 rounded text-gray-100 w-32"
              />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-xs text-gray-500">LTHR (bpm)</label>
              <input
                name="lthr"
                type="number"
                min="1"
                max="220"
                defaultValue={settings?.lthr ?? ''}
                placeholder="e.g. 162"
                className="px-2 py-1 text-sm bg-gray-800 border border-gray-700 rounded text-gray-100 w-32"
              />
            </div>
            <button
              type="submit"
              disabled={update.isPending}
              className="px-4 py-1.5 text-sm bg-purple-700 hover:bg-purple-600 rounded transition disabled:opacity-50"
            >
              {update.isPending ? 'Saving...' : saved ? '✓ Saved' : 'Save'}
            </button>
          </form>
        )}
      </div>

      <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
        <h2 className="text-sm font-semibold text-gray-400 mb-3">AI Coach Settings</h2>
        <form onSubmit={handleAiSubmit} className="space-y-3">
          <div className="flex flex-col gap-1">
            <label className="text-xs text-gray-500">Provider</label>
            <select
              name="ai_provider"
              value={provider}
              onChange={e => setProvider(e.target.value)}
              className="px-2 py-1 text-sm bg-gray-800 border border-gray-700 rounded text-gray-100 w-40"
            >
              <option value="claude">Claude (Anthropic)</option>
              <option value="openai">OpenAI</option>
            </select>
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-xs text-gray-500">Model</label>
            <select
              name="ai_model"
              defaultValue={settings?.ai_model ?? modelOptions[1]}
              key={provider}
              className="px-2 py-1 text-sm bg-gray-800 border border-gray-700 rounded text-gray-100 w-56"
            >
              {modelOptions.map(m => (
                <option key={m} value={m}>{m}</option>
              ))}
            </select>
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-xs text-gray-500">API Key</label>
            <input
              name="ai_api_key"
              type="password"
              placeholder={settings?.ai_provider ? '••••••••• (已設定)' : '輸入 API Key'}
              className="px-2 py-1 text-sm bg-gray-800 border border-gray-700 rounded text-gray-100 w-full font-mono"
            />
            <span className="text-xs text-gray-600">Key 僅儲存在本機 SQLite，不會上傳</span>
          </div>
          <button
            type="submit"
            disabled={update.isPending}
            className="px-4 py-1.5 text-sm bg-blue-700 hover:bg-blue-600 rounded transition disabled:opacity-50"
          >
            {update.isPending ? 'Saving...' : aiSaved ? '✓ Saved' : 'Save AI Settings'}
          </button>
        </form>
        {settings?.ai_provider && (
          <div className="mt-2 text-xs text-green-500">
            已設定: {settings.ai_provider} / {settings.ai_model}
          </div>
        )}
      </div>

      {settings?.power_zones && settings.power_zones.length > 0 && (
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
          <h2 className="text-sm font-semibold text-gray-400 mb-2">
            Power Zones — FTP {settings.ftp_w}w
          </h2>
          <ZoneTable zones={settings.power_zones} unitKey="min_w" />
        </div>
      )}

      {settings?.hr_zones && settings.hr_zones.length > 0 && (
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
          <h2 className="text-sm font-semibold text-gray-400 mb-2">
            HR Zones — LTHR {settings.lthr}bpm
          </h2>
          <ZoneTable zones={settings.hr_zones} unitKey="min_bpm" />
        </div>
      )}
    </div>
  )
}
