import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import '../styles/monitor.css'

export default function MonitorShell({title, children}: {title: string; children: ReactNode}) {
  return <div className="app-shell">
    <aside className="rail"><Link className="brand-mark brand-link" to="/">E<span>●</span></Link><div className="rail-divider" />
      <Link className="rail-icon" to="/" title="Content Studio" aria-label="Content Studio">▦</Link>
      <Link className="rail-icon" to="/audio" title="Audio Studio" aria-label="Audio Studio">♫</Link>
      <Link className={`rail-icon ${title === 'Job Monitor' ? 'active' : ''}`} to="/jobs" title="Job Monitor" aria-label="Job Monitor">☷</Link>
      <Link className={`rail-icon ${title === 'Dashboard' ? 'active' : ''}`} to="/dashboard" title="Dashboard" aria-label="Dashboard">◫</Link>
      <div className="rail-spacer" /><div className="rail-bottom">05</div>
    </aside><main className="main"><header className="topbar"><div className="brand-name">EchoFlow <span>/ {title}</span></div><div className="stage-pill"><span className="stage-dot" />Stage 05 · Operations</div></header>
      <div className="workspace monitor-workspace">{children}</div></main>
  </div>
}

export const displayTime = (value: string | null) => value ? new Date(value).toLocaleString() : '—'
export const label = (value: string) => value.replaceAll('_', ' ')
export const activeJob = (status: string) => ['queued','running','retry_scheduled'].includes(status)
