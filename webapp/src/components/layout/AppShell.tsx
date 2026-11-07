import { useState, type ReactNode } from 'react'
import { Plus } from 'lucide-react'
import { Link, NavLink } from 'react-router-dom'

import { useTheme } from '../../contexts/ThemeContext'
import { useAppData } from '../../contexts/AppDataContext'

export function AppShell({ children }: { children: ReactNode }) {
  const [mobileOpen, setMobileOpen] = useState(false)

  const { theme, toggleTheme } = useTheme()
  const { profile, courses, materialsByCourse } = useAppData()

  const name = profile?.displayName ?? 'Cargando perfil…'

  const initials = name
    .split(/\s+/)
    .filter(Boolean)
    .map((part) => part[0])
    .slice(0, 2)
    .join('')
    .toUpperCase()

  const materialCount = Object.values(materialsByCourse).reduce(
    (sum, items) => sum + items.length,
    0
  )

  const close = () => setMobileOpen(false)

  return (
    <div className="app-shell">
      <aside className={`sidebar ${mobileOpen ? 'sidebar-open' : ''}`}>
        <Link to="/" className="brand" onClick={close}>
          <span className="brand-mark">
            t<span>·</span>
          </span>

          <span className="brand-name">
            tutor<span>ia</span>
            <small>Aprende a tu ritmo</small>
          </span>
        </Link>

        <nav className="main-nav" aria-label="Navegación principal">
          <NavLink to="/" end onClick={close}>
            ⌂ <span>Inicio</span>
          </NavLink>

          <NavLink
            to={courses[0] ? `/cursos/${courses[0].id}` : '/'}
            onClick={close}
          >
            ◉ <span>Mi tutor</span>
          </NavLink>

          <NavLink to="/materiales" onClick={close}>
            ▤ <span>Materiales</span>
            <b className="nav-count">{materialCount}</b>
          </NavLink>
        </nav>

        <div className="side-label courses-label">
          Mis cursos

          <NavLink to="/cursos/crear" onClick={close}>
            <Plus size={16} strokeWidth={2} />
          </NavLink>
        </div>

        <nav className="course-nav">
          {courses.map((course) => (
            <NavLink
              to={`/cursos/${course.id}`}
              key={course.id}
              onClick={close}
            >
              <i className={`course-dot ${course.color}-dot`} />
              {course.name}
            </NavLink>
          ))}
        </nav>

        <div className="sidebar-spacer" />

        <div className="sidebar-profile">
          <div className="avatar">
            {initials || '·'}
          </div>

          <div>
            <strong>{name}</strong>
            <small>{profile?.role}</small>
          </div>
        </div>
      </aside>

      {mobileOpen && (
        <button
          className="mobile-scrim"
          aria-label="Cerrar menú"
          onClick={close}
        />
      )}

      <main className="main-area">
        <header className="topbar">
          <button
            className="mobile-menu"
            aria-label="Abrir menú"
            onClick={() => setMobileOpen((open) => !open)}
          >
            ☰
          </button>

          <div className="breadcrumb">
            Mi espacio <b>/</b> <strong>Aprendizaje</strong>
          </div>

          <div className="top-actions">
            <button
              className="icon-button"
              onClick={toggleTheme}
              aria-label="Cambiar tema"
              title={`Tema ${theme}`}
            >
              {theme === 'latte' ? '☾' : '☼'}
            </button>

            <span className="avatar">
              {initials || '·'}
            </span>
          </div>
        </header>

        <div className="page-content">
          {children}
        </div>
      </main>
    </div>
  )
}
