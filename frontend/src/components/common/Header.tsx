import { Link, useLocation } from 'react-router-dom'
import { Bot, BarChart3, Settings, BookOpen, Activity } from 'lucide-react'

const navItems = [
  { path: '/dashboard', label: 'Dashboard', icon: BarChart3 },
  { path: '/strategy', label: 'Strategy Studio', icon: BookOpen },
  { path: '/agent', label: 'AI Agent', icon: Bot },
  { path: '/settings', label: 'Settings', icon: Settings },
]

export function Header() {
  const location = useLocation()
  
  return (
    <header className="glass sticky top-0 z-50 backdrop-blur-xl">
      <div className="max-w-[1920px] mx-auto px-6 sm:px-8 lg:px-12 py-4">
        <div className="flex items-center justify-between">
          {/* Logo */}
          <Link to="/" className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg bg-nofx-gold flex items-center justify-center">
              <Activity className="w-5 h-5 text-black" />
            </div>
            <div>
              <h1 className="text-xl font-bold text-[#EAECEF]">VergeX</h1>
              <p className="text-xs text-[#848E9C] font-mono">Trading System</p>
            </div>
          </Link>
          
          {/* Nav */}
          <nav className="flex items-center gap-1">
            {navItems.map(({ path, label, icon: Icon }) => {
              const active = location.pathname === path
              return (
                <Link
                  key={path}
                  to={path}
                  className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-all ${
                    active
                      ? 'bg-[#F0B90B]/10 text-[#F0B90B] border border-[#F0B90B]/20'
                      : 'text-[#848E9C] hover:text-[#EAECEF] hover:bg-[#1E2329]'
                  }`}
                >
                  <Icon className="w-4 h-4" />
                  <span className="hidden sm:inline">{label}</span>
                </Link>
              )
            })}
          </nav>
        </div>
      </div>
    </header>
  )
}