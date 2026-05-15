import { create } from 'zustand'

export type Tab = 'season' | 'activities' | 'config' | 'ai'

interface TabStore {
  activeTab: Tab
  setTab: (tab: Tab) => void
}

export const useTabStore = create<TabStore>((set) => ({
  activeTab: 'season',
  setTab: (tab) => set({ activeTab: tab }),
}))
