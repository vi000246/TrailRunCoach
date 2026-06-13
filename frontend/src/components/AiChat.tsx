import { useState, useRef, useEffect } from 'react'
import { useAiChatStore } from '../store/aiChatStore'
import { useAiStatus } from '../api/hooks'

interface AiChatProps {
  athleteId?: number
  initialMessage?: string
}

export function AiChat({ athleteId = 1, initialMessage }: AiChatProps) {
  const { data: status } = useAiStatus(athleteId)
  const { messages, isStreaming, addUserMessage, startStreaming, appendAssistantChunk, finalizeAssistant, setError } =
    useAiChatStore()
  const [input, setInput] = useState(initialMessage ?? '')
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const QUICK_PROMPTS = ['今天該練什麼？', '我的間歇怎麼排？', '現在該做 zone 幾？']

  const send = async (override?: string) => {
    const msg = (override ?? input).trim()
    if (!msg || isStreaming) return
    setInput('')
    addUserMessage(msg)
    startStreaming()

    try {
      const res = await fetch('/api/v1/ai/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ athlete_id: athleteId, message: msg }),
      })
      if (!res.ok || !res.body) {
        const err = await res.json().catch(() => ({ detail: res.statusText }))
        setError(err.detail ?? 'Request failed')
        return
      }
      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buf = ''
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buf += decoder.decode(value, { stream: true })
        const lines = buf.split('\n')
        buf = lines.pop() ?? ''
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          try {
            const payload = JSON.parse(line.slice(6))
            if (payload.chunk) appendAssistantChunk(payload.chunk)
            if (payload.done) finalizeAssistant()
            if (payload.error) setError(payload.error)
          } catch {}
        }
      }
      finalizeAssistant()
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Network error')
    }
  }

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      send()
    }
  }

  if (!status?.configured) {
    return (
      <div className="flex flex-col items-center justify-center h-48 gap-2 text-gray-500 text-sm">
        <span>尚未設定 AI Coach</span>
        <span className="text-xs">請在「設定」頁面輸入 API Key</span>
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full min-h-0">
      <div className="flex-1 overflow-y-auto space-y-3 p-3 min-h-0">
        {messages.length === 0 && (
          <div className="text-gray-600 text-sm text-center mt-8">
            問我任何訓練問題，我會根據你的數據分析回答
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div
              className={`max-w-[85%] rounded-lg px-3 py-2 text-sm whitespace-pre-wrap ${
                m.role === 'user'
                  ? 'bg-blue-700 text-white'
                  : 'bg-gray-800 text-gray-100'
              }`}
            >
              {m.content}
              {m.role === 'assistant' && i === messages.length - 1 && isStreaming && (
                <span className="inline-block w-1 h-4 bg-gray-400 ml-1 animate-pulse align-middle" />
              )}
            </div>
          </div>
        ))}
        <div ref={bottomRef} />
      </div>

      <div className="border-t border-gray-800 px-3 pt-2 flex flex-wrap gap-2">
        {QUICK_PROMPTS.map(q => (
          <button
            key={q}
            type="button"
            onClick={() => send(q)}
            disabled={isStreaming}
            className="text-xs px-2 py-1 rounded border border-gray-700 text-gray-400 hover:text-gray-200 hover:border-gray-500 transition disabled:opacity-40"
          >
            {q}
          </button>
        ))}
      </div>

      <div className="px-3 pb-3 pt-2 flex gap-2">
        <textarea
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder="輸入訓練問題... (Enter 送出)"
          rows={2}
          className="flex-1 resize-none px-3 py-2 text-sm bg-gray-800 border border-gray-700 rounded text-gray-100 focus:outline-none focus:border-blue-500"
          disabled={isStreaming}
        />
        <button
          onClick={() => send()}
          disabled={isStreaming || !input.trim()}
          className="px-4 py-2 bg-blue-700 hover:bg-blue-600 rounded text-sm disabled:opacity-40 transition self-end"
        >
          {isStreaming ? '...' : '送出'}
        </button>
      </div>
    </div>
  )
}
