import { Link, useLocation } from 'react-router-dom'

const items = [
  { to: '/', label: 'Content', icon: '▦' },
  { to: '/audio', label: 'Audio', icon: '♫' },
  { to: '/jobs', label: 'Jobs', icon: '☷' },
  { to: '/dashboard', label: 'Dashboard', icon: '◫' },
]

export default function AppNavigation() {
  const { pathname } = useLocation()
  return <aside className="rail app-navigation">
    <Link className="brand-mark brand-link" to="/" aria-label="EchoFlow home">E<span>●</span></Link>
    <div className="rail-divider" />
    <nav aria-label="Main navigation">
      {items.map(item => {
        const selected = item.to === '/' ? pathname === '/' || pathname.startsWith('/review/') : pathname === item.to || pathname.startsWith(item.to + '/')
        return <Link key={item.to} to={item.to} className={`rail-icon${selected ? ' active' : ''}`} aria-current={selected ? 'page' : undefined}>
          <span aria-hidden="true">{item.icon}</span><span className="nav-label">{item.label}</span>
        </Link>
      })}
    </nav>
  </aside>
}
