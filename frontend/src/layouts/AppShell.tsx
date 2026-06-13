import { NavLink, Outlet } from 'react-router'
import { CorosSyncButton } from '../components/CorosSyncButton'

const NAV_ITEMS = [
  { to: '/overview',   label: '總體' },
  { to: '/running',    label: '跑步' },
  { to: '/trail',      label: '越野跑' },
  { to: '/activities', label: 'Activities' },
  { to: '/config',     label: 'Config' },
  { to: '/ai',         label: 'AI' },
  { to: '/sync',       label: '同步' },
]

export function AppShell() {
  return (
    <div className="min-h-screen bg-[#07090f] text-[#e8edf5]">
      <header className="fixed top-0 left-0 right-0 z-50 h-11 bg-[#0e1117] border-b border-[#1c2333] flex items-center px-5 gap-6">
        <span className="text-sm font-semibold text-[#a78bfa] tracking-wide shrink-0">
          WKO5 Coach
        </span>
        <nav className="flex items-stretch flex-1 h-full">
          {NAV_ITEMS.map(item => (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) =>
                `flex items-center px-3 text-sm font-medium transition-colors border-b-2 ${
                  isActive
                    ? 'border-[#7c3aed] text-[#a78bfa]'
                    : 'border-transparent text-[#7d8fa6] hover:text-[#e8edf5] hover:border-[#3e4e63]'
                }`
              }
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
        <CorosSyncButton />
      </header>
      <main className="pt-11">
        <Outlet />
      </main>
    </div>
  )
}
