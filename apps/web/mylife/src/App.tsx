import { HashRouter, NavLink, Route, Routes } from 'react-router'
import GraphView from './views/GraphView'
import ReplayView from './views/ReplayView'
import TimelineView from './views/TimelineView'

const tabs = [
  { to: '/timeline', label: 'Timeline', icon: '◷' },
  { to: '/graph', label: 'Graph', icon: '✦' },
  { to: '/replay', label: 'Replay', icon: '▶' },
]

function TabLinks({ desktop }: { desktop?: boolean }) {
  return (
    <>
      {tabs.map((t) => (
        <NavLink
          key={t.to}
          to={t.to}
          className={({ isActive }) =>
            `flex flex-col items-center justify-center gap-0.5 text-xs transition-colors ${
              isActive ? 'text-gold' : 'text-dust hover:text-star'
            } ${desktop ? 'px-6 py-1 text-sm' : 'min-h-12 flex-1'}`
          }
        >
          <span className="text-base leading-none">{t.icon}</span>
          {t.label}
        </NavLink>
      ))}
    </>
  )
}

export default function App() {
  return (
    <HashRouter>
      <div className="flex h-full flex-col">
        {/* 桌面顶部导航（≥768px） */}
        <nav className="hidden shrink-0 items-center gap-1 border-b border-line bg-void/90 px-6 backdrop-blur md:flex">
          <span className="mr-4 text-lg font-semibold tracking-wide text-gold">✦ MyLife</span>
          <TabLinks desktop />
        </nav>
        <main className="min-h-0 flex-1 overflow-y-auto">
          <Routes>
            <Route path="/" element={<TimelineView />} />
            <Route path="/timeline" element={<TimelineView />} />
            <Route path="/graph" element={<GraphView />} />
            <Route path="/replay" element={<ReplayView />} />
          </Routes>
        </main>
        {/* 移动端底部 tab */}
        <nav className="pb-safe flex shrink-0 border-t border-line bg-void/90 backdrop-blur md:hidden">
          <TabLinks />
        </nav>
      </div>
    </HashRouter>
  )
}
