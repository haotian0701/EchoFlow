import AppNavigation from './AppNavigation'
import type { ReactNode } from 'react'
import '../styles/monitor.css'

export default function MonitorShell({title, children}: {title: string; children: ReactNode}) {
  return <div className="app-shell">
    <AppNavigation /><main className="main"><header className="topbar"><div className="brand-name">EchoFlow <span>/ {title}</span></div><div className="stage-pill"><span className="stage-dot" />{title}</div></header>
      <div className="workspace monitor-workspace">{children}</div></main>
  </div>
}

export const displayTime = (value: string | null) => value ? new Date(value).toLocaleString() : '—'
export const label = (value: string) => value.replaceAll('_', ' ')
export const activeJob = (status: string) => ['queued','running','retry_scheduled'].includes(status)
