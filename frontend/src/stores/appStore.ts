import { create } from 'zustand'
import type { Trader, Position, StrategyConfig, Exchange, AIModel } from '@/types'

interface AppState {
  // Traders
  traders: Trader[]
  setTraders: (traders: Trader[]) => void
  
  // Positions
  positions: Position[]
  setPositions: (positions: Position[]) => void
  
  // Exchanges
  exchanges: Exchange[]
  setExchanges: (exchanges: Exchange[]) => void
  
  // Models
  models: AIModel[]
  setModels: (models: AIModel[]) => void
  
  // Strategies
  strategies: StrategyConfig[]
  setStrategies: (strategies: StrategyConfig[]) => void
  
  // UI State
  sidebarOpen: boolean
  toggleSidebar: () => void
}

export const useAppStore = create<AppState>((set) => ({
  traders: [],
  positions: [],
  exchanges: [],
  models: [],
  strategies: [],
  sidebarOpen: false,
  
  setTraders: (traders) => set({ traders }),
  setPositions: (positions) => set({ positions }),
  setExchanges: (exchanges) => set({ exchanges }),
  setModels: (models) => set({ models }),
  setStrategies: (strategies) => set({ strategies }),
  toggleSidebar: () => set((state) => ({ sidebarOpen: !state.sidebarOpen })),
}))