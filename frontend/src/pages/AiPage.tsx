import { AiChat } from '../components/AiChat'
import { useAiChatStore } from '../store/aiChatStore'

export function AiPage() {
  const clear = useAiChatStore(s => s.clear)

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center justify-between px-5 py-3 border-b border-gray-800">
        <h1 className="text-sm font-semibold text-gray-500 uppercase tracking-wide">AI 教練</h1>
        <button
          onClick={clear}
          className="text-xs text-gray-600 hover:text-gray-400 transition"
        >
          清除對話
        </button>
      </div>
      <div className="flex-1 min-h-0">
        <AiChat athleteId={1} />
      </div>
    </div>
  )
}
