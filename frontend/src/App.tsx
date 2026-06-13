import { useState, useEffect } from 'react'
import { Container } from '@/components/common/Container'
import { TraderDashboardPage } from '@/pages/TraderDashboardPage'
import { StrategyStudioPage } from '@/pages/StrategyStudioPage'
import { CompetitionPage } from '@/pages/CompetitionPage'
import { AgentChatPage } from '@/pages/AgentChatPage'
import { SettingsPage } from '@/pages/SettingsPage'
import { BarChart3, BookOpen, Bot, Settings, Zap, Trophy } from 'lucide-react'

const tabs = [
  { id: 'dashboard', label: 'Dashboard', icon: BarChart3, path: '/dashboard' },
  { id: 'strategy', label: 'Strategy Studio', icon: BookOpen, path: '/strategy' },
  { id: 'competition', label: 'Competition', icon: Trophy, path: '/competition' },
  { id: 'agent', label: 'AI Agent', icon: Bot, path: '/agent' },
  { id: 'light', label: 'Light', icon: Zap, path: '/light', external: true },
  { id: 'settings', label: 'Settings', icon: Settings, path: '/settings' },
]

/** 从当前 URL pathname 解析 active tab */
function getTabFromPath(): string {
  const path = window.location.pathname.replace(/\/$/, '') || '/'
  const tab = tabs.find(t => !t.external && (path === t.path || path === '/'))
  return tab ? tab.id : 'dashboard'
}

export default function App() {
  const [activeTab, setActiveTab] = useState(getTabFromPath)

  // 同步 URL ↔ Tab（浏览器前进/后退）
  useEffect(() => {
    const onPopState = () => setActiveTab(getTabFromPath())
    window.addEventListener('popstate', onPopState)
    return () => window.removeEventListener('popstate', onPopState)
  }, [])

  const switchTab = (id: string) => {
    const tab = tabs.find(t => t.id === id)
    if (!tab) return
    if (tab.external) {
      window.location.href = tab.path
      return
    }
    window.history.pushState(null, '', tab.path)
    setActiveTab(id)
  }

  return (
    <div className="min-h-screen bg-nofx-bg">
      {/* Top Nav */}
      <header className="glass sticky top-0 z-50 backdrop-blur-xl border-b border-[#1E2329]">
        <div className="max-w-[1920px] mx-auto px-6 sm:px-8 lg:px-12 py-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="w-8 h-8 rounded-lg bg-nofx-gold flex items-center justify-center">
                <BarChart3 className="w-5 h-5 text-black" />
              </div>
              <div>
                <h1 className="text-lg font-bold text-[#EAECEF]">VergeX</h1>
                <p className="text-xs text-[#848E9C] font-mono">Trading System</p>
              </div>
            </div>

            {/* Tab Switcher */}
            <nav className="flex items-center gap-1">
              {tabs.map(({ id, label, icon: Icon, external }) => (
                <button
                  key={id}
                  onClick={() => switchTab(id)}
                  className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-all ${
                    activeTab === id
                      ? 'bg-[#F0B90B]/10 text-[#F0B90B] border border-[#F0B90B]/20'
                      : 'text-[#848E9C] hover:text-[#EAECEF] hover:bg-[#1E2329]'
                  }`}
                >
                  <Icon className="w-4 h-4" />
                  <span className="hidden sm:inline">{label}</span>
                  {external && (
                    <span className="text-[10px] text-[#F0B90B]/50 ml-0.5">↗</span>
                  )}
                </button>
              ))}
            </nav>

            {/* Status */}
            <div className="flex items-center gap-2 text-xs">
              <span className="w-2 h-2 rounded-full bg-[#0ECB81] animate-pulse" />
              <span className="text-[#848E9C] font-mono">LIVE</span>
            </div>
          </div>
        </div>
      </header>

      {/* Page Content */}
      <main>
        {activeTab === 'dashboard' && <TraderDashboardPage />}
        {activeTab === 'strategy' && <StrategyStudioPage />}
        {activeTab === 'competition' && <CompetitionPage />}
        {activeTab === 'agent' && <AgentChatPage />}
        {activeTab === 'settings' && <SettingsPage />}
      </main>
    </div>
  )
}
