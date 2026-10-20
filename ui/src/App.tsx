import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import { NavLink, Route, Routes, useLocation } from 'react-router-dom'
import { Activity, BrainCircuit, ChevronLeft, ChevronRight, Command, Cpu, FolderSearch, Home, Settings2, ShieldCheck, Sparkles, X } from 'lucide-react'
import { api, type AppState } from './api'
import { type FaceState } from './DexterFace'
import { ChatPage, FilesPage, HomePage, MemoryPage, SettingsPage, ToolsPage, TrainingPage } from './pages'

type ContextValue = {
  appState: AppState | null
  refresh: () => Promise<void>
  faceState: FaceState
  setFaceState: (state: FaceState) => void
}

const AppContext = createContext<ContextValue | null>(null)
export function useApp() {
  const value = useContext(AppContext)
  if (!value) throw new Error('App context is not available')
  return value
}

const navigation = [
  { to: '/', label: 'Home', icon: Home, end: true },
  { to: '/chat', label: 'Dexter Chat', icon: Sparkles },
  { to: '/training', label: 'Training', icon: Activity },
  { to: '/memory', label: 'Memory', icon: BrainCircuit },
  { to: '/files', label: 'Files', icon: FolderSearch },
  { to: '/tools', label: 'Tools', icon: Command },
  { to: '/settings', label: 'Settings', icon: Settings2 },
]

const pageNames: Record<string, string> = {
  '/': 'Command overview', '/chat': 'Conversation workspace', '/training': 'Learning laboratory',
  '/memory': 'Owner context', '/files': 'Local discovery', '/tools': 'Capabilities', '/settings': 'Preferences & diagnostics',
}

export default function App() {
  const [appState, setAppState] = useState<AppState | null>(null)
  const [backendError, setBackendError] = useState('')
  const [faceState, setFaceState] = useState<FaceState>('idle')
  const [collapsed, setCollapsed] = useState(localStorage.getItem('devil-sidebar-collapsed') === 'true')
  const [pending, setPending] = useState<{ id: string; action: string } | null>(null)
  const [confirmationBusy, setConfirmationBusy] = useState(false)
  const location = useLocation()

  const refresh = useCallback(async () => {
    try {
      const next = await api.state()
      setAppState(next)
      if (typeof next.preferences?.reduced_motion === 'boolean') {
        document.documentElement.dataset.reducedMotion = String(next.preferences.reduced_motion)
      }
      setBackendError('')
    } catch (error) {
      setBackendError(error instanceof Error ? error.message : 'Local backend is unavailable')
    }
  }, [])

  useEffect(() => {
    void refresh()
    const timer = window.setInterval(() => void refresh(), 6000)
    return () => window.clearInterval(timer)
  }, [refresh])

  useEffect(() => {
    let stopped = false
    const poll = async () => {
      try {
        const result = await api.confirmations()
        if (!stopped) setPending(result.pending[0] || null)
      } catch { /* backend startup and shutdown are reflected in the header */ }
    }
    void poll()
    const timer = window.setInterval(poll, 900)
    return () => { stopped = true; window.clearInterval(timer) }
  }, [])

  const context = useMemo(() => ({ appState, refresh, faceState, setFaceState }), [appState, refresh, faceState])
  const configuredProviders = appState ? Object.values(appState.providers).filter(p => p.configured).length : 0
  const pageTitle = pageNames[location.pathname] || 'DEVIL'

  const resolveConfirmation = async (approve: boolean) => {
    if (!pending) return
    setConfirmationBusy(true)
    try {
      await api.answerConfirmation(pending.id, approve)
      setPending(null)
    } finally {
      setConfirmationBusy(false)
    }
  }

  return <AppContext.Provider value={context}>
    <div className={`app-shell ${collapsed ? 'sidebar-collapsed' : ''}`}>
      <aside className="sidebar">
        <div className="brand-lockup">
          <div className="brand-mark"><span>D</span><i /></div>
          {!collapsed && <div className="brand-copy"><strong>DEVIL</strong><small>DEXTER SYSTEMS</small></div>}
        </div>
        <button className="workspace-pill" title="Local personal workspace"><span className="workspace-glyph">D</span>{!collapsed && <><span><b>Devil's workspace</b><small>PERSONAL INSTANCE</small></span><ChevronRight size={14} /></>}</button>
        <div className="nav-label">{!collapsed && 'WORKSPACE'}</div>
        <nav className="primary-nav" aria-label="Main navigation">
          {navigation.map(({ to, label, icon: Icon, end }) => <NavLink key={to} to={to} end={end} title={collapsed ? label : undefined} className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}>
            <Icon size={18} strokeWidth={1.8} /><span>{label}</span>
            {label === 'Training' && <i className="nav-state" title="No trainer configured" />}
          </NavLink>)}
        </nav>
        <div className="sidebar-spacer" />
        <div className="sidebar-footer">
          <div className="user-avatar">D</div>
          {!collapsed && <div className="user-meta"><strong>Devil</strong><small>OWNER · LOCAL</small></div>}
          {!collapsed && <ShieldCheck size={16} className="owner-shield" />}
        </div>
        <button className="collapse-toggle" onClick={() => { const next = !collapsed; setCollapsed(next); localStorage.setItem('devil-sidebar-collapsed', String(next)) }} title={collapsed ? 'Expand navigation' : 'Collapse navigation'}>
          {collapsed ? <ChevronRight size={16} /> : <ChevronLeft size={16} />} {!collapsed && <span>Collapse sidebar</span>}
        </button>
      </aside>

      <main className="main-shell">
        <header className="topbar">
          <div className="crumbs"><span>DEVIL</span><i>/</i><strong>{pageTitle}</strong></div>
          <div className="topbar-right">
            <div className={`connection-pill ${backendError ? 'offline' : appState ? 'online' : ''}`}><i />{backendError ? 'Backend unavailable' : appState ? 'Local backend ready' : 'Connecting'}</div>
            <span className="provider-summary"><Cpu size={14} />{appState ? `${configuredProviders}/3 configured` : 'Providers'}</span>
            <div className="top-avatar">D</div>
          </div>
        </header>
        {backendError && <div className="backend-alert"><span>{backendError}. Start the local server with <code>python web_server.py</code>.</span><button onClick={() => void refresh()}>Retry</button></div>}
        <div className="page-content" key={location.pathname}>
          <Routes>
            <Route path="/" element={<HomePage />} />
            <Route path="/chat" element={<ChatPage />} />
            <Route path="/training" element={<TrainingPage />} />
            <Route path="/memory" element={<MemoryPage />} />
            <Route path="/files" element={<FilesPage />} />
            <Route path="/tools" element={<ToolsPage />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="*" element={<HomePage />} />
          </Routes>
        </div>
        <footer className="app-footer"><span><span className="footer-key">⌘</span> DEVIL // PERSONAL AI COMMAND CENTER</span><span><i className={`status-dot ${faceState}`} /> DEXTER · {faceState.toUpperCase()}</span></footer>
      </main>
    </div>

    {pending && <div className="modal-scrim" role="presentation">
      <section className="confirm-modal" role="dialog" aria-modal="true" aria-labelledby="approval-heading">
        <div className="modal-icon"><ShieldCheck size={22} /></div>
        <div className="eyebrow">ACTION APPROVAL REQUIRED</div>
        <h2 id="approval-heading">Allow Dexter to change a file?</h2>
        <p>This request is waiting for your approval. Review the exact target and change before allowing it.</p>
        <pre className="approval-detail">{pending.action}</pre>
        <div className="modal-actions"><button className="button secondary" disabled={confirmationBusy} onClick={() => void resolveConfirmation(false)}><X size={15} /> Decline</button><button className="button primary" disabled={confirmationBusy} onClick={() => void resolveConfirmation(true)}><ShieldCheck size={15} /> Approve change</button></div>
      </section>
    </div>}
  </AppContext.Provider>
}
