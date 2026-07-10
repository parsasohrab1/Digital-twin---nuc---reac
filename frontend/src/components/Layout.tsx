import { Link, Outlet, useLocation } from 'react-router-dom'

import { useTheme } from '../hooks/useTheme'



export function Layout() {

  const { theme, toggle } = useTheme()

  const loc = useLocation()



  return (

    <div className="app-shell">

      <nav className="top-nav">

        <div className="nav-brand">اتاق کنترل NDT</div>

        <div className="nav-links">

          <Link to="/" className={loc.pathname === '/' ? 'active' : ''}>داشبورد</Link>

          <Link to="/settings" className={loc.pathname === '/settings' ? 'active' : ''}>تنظیمات</Link>

        </div>

        <button type="button" className="theme-toggle" onClick={toggle} title="حالت شب (UI-RQ-07)">

          {theme === 'dark' ? 'روشن' : 'تاریک'}

        </button>

      </nav>

      <Outlet />

    </div>

  )

}

