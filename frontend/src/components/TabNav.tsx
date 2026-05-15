import { useTabStore, type Tab } from '../store/tabStore'

const TABS: { id: Tab; label: string }[] = [
  { id: 'season',     label: 'Season' },
  { id: 'activities', label: 'Activities' },
  { id: 'config',     label: 'Config' },
  { id: 'ai',         label: 'AI' },
]

export function TabNav() {
  const { activeTab, setTab } = useTabStore()
  return (
    <nav className="flex gap-1 bg-gray-900 border-b border-gray-800 px-6">
      {TABS.map(t => (
        <button
          key={t.id}
          onClick={() => setTab(t.id)}
          className={`px-4 py-2 text-sm font-medium transition border-b-2 -mb-px ${
            activeTab === t.id
              ? 'border-purple-500 text-purple-400'
              : 'border-transparent text-gray-500 hover:text-gray-300'
          }`}
        >
          {t.label}
        </button>
      ))}
    </nav>
  )
}
