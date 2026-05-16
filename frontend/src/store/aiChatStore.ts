import { create } from 'zustand'
import type { ChatMessage } from '../api/client'

interface AiChatStore {
  messages: ChatMessage[]
  isStreaming: boolean
  addUserMessage: (content: string) => void
  appendAssistantChunk: (chunk: string) => void
  finalizeAssistant: () => void
  startStreaming: () => void
  setError: (msg: string) => void
  clear: () => void
}

export const useAiChatStore = create<AiChatStore>((set) => ({
  messages: [],
  isStreaming: false,

  addUserMessage: (content) =>
    set((s) => ({
      messages: [...s.messages, { role: 'user', content }],
    })),

  startStreaming: () =>
    set((s) => ({
      isStreaming: true,
      messages: [...s.messages, { role: 'assistant', content: '' }],
    })),

  appendAssistantChunk: (chunk) =>
    set((s) => {
      const msgs = [...s.messages]
      const last = msgs[msgs.length - 1]
      if (last && last.role === 'assistant') {
        msgs[msgs.length - 1] = { ...last, content: last.content + chunk }
      }
      return { messages: msgs }
    }),

  finalizeAssistant: () => set({ isStreaming: false }),

  setError: (msg) =>
    set((s) => {
      const msgs = [...s.messages]
      const last = msgs[msgs.length - 1]
      if (last && last.role === 'assistant' && last.content === '') {
        msgs[msgs.length - 1] = { role: 'assistant', content: `⚠️ ${msg}` }
      } else {
        msgs.push({ role: 'assistant', content: `⚠️ ${msg}` })
      }
      return { messages: msgs, isStreaming: false }
    }),

  clear: () => set({ messages: [], isStreaming: false }),
}))
