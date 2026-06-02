export type Language = 'zh' | 'en'

const translations: Record<Language, Record<string, string>> = {
  zh: {
    appTitle: 'VergeX Trading System',
    subtitle: '三省六部量化交易系统',
    dashboard: 'Dashboard',
    strategyStudio: 'Strategy Studio',
    agent: 'AI Agent',
    settings: 'Settings',
    currentTraders: '运行交易员',
    positions: '持仓',
    pnl: '收益',
    trades: '交易',
  },
  en: {
    appTitle: 'VergeX Trading System',
    subtitle: 'Three Provinces Six Ministries Trading System',
    dashboard: 'Dashboard',
    strategyStudio: 'Strategy Studio',
    agent: 'AI Agent',
    settings: 'Settings',
    currentTraders: 'Active Traders',
    positions: 'Positions',
    pnl: 'P&L',
    trades: 'Trades',
  },
}

export function t(key: string, lang: Language): string {
  return translations[lang][key] || key
}