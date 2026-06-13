import { NavLink, Outlet } from 'react-router'
import { CorosSyncButton } from '../components/CorosSyncButton'

// Primary training views are grouped first, utilities after a subtle divider.
const NAV_ITEMS = [
  { to: '/overview',   label: '總體' },
  { to: '/running',    label: '跑步' },
  { to: '/trail',      label: '越野跑' },
  { to: '/achievements', label: '運動成就' },
  { to: '/activities', label: '活動' },
  { to: '/ai',         label: 'AI 教練' },
  { to: '/sync',       label: '同步' },
  { to: '/config',     label: '設定' },
]

export function AppShell() {
  return (
    <div className="min-h-screen bg-[#07090f] text-[#e8edf5]">
      <header className="fixed top-0 left-0 right-0 z-50 h-14 bg-[#0b0e16]/85 backdrop-blur-md border-b border-[#1c2333]/80 flex items-center px-6 gap-5 shadow-[0_1px_0_0_rgba(124,58,237,0.08)]">
        {/* Brand */}
        <div className="flex items-center gap-2 shrink-0">
          <span className="inline-block w-2.5 h-2.5 rounded-sm bg-gradient-to-br from-[#a78bfa] to-[#7c3aed] shadow-[0_0_8px_rgba(124,58,237,0.6)]" />
          <span className="text-[15px] font-bold tracking-tight text-[#e8edf5]">
            WKO5 <span className="text-[#a78bfa]">Coach</span>
          </span>
        </div>

        {/* Nav */}
        <nav className="flex items-center flex-1 gap-1">
          {NAV_ITEMS.map(item => (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) =>
                `px-3.5 py-1.5 rounded-lg text-[13px] font-medium transition-all duration-150 ${
                  isActive
                    ? 'bg-[#7c3aed]/15 text-[#c4b5fd] shadow-[inset_0_0_0_1px_rgba(124,58,237,0.35)]'
                    : 'text-[#8b9bb4] hover:text-[#e8edf5] hover:bg-white/[0.04]'
                }`
              }
            >
              {item.label}
            </NavLink>
          ))}
        </nav>

        <CorosSyncButton />
      </header>
      <main className="pt-14">
        <Outlet />
      </main>
    </div>
  )
}
