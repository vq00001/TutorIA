import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import type { ResolvedTheme, ThemePreference } from '../types'

interface ThemeContextValue { theme: ResolvedTheme; preference: ThemePreference; setPreference: (theme: ThemePreference) => void; toggleTheme: () => void }
const ThemeContext = createContext<ThemeContextValue | null>(null)
const STORAGE_KEY = 'tutoria-theme'

function initialPreference(): ThemePreference {
  const stored = localStorage.getItem(STORAGE_KEY)
  return stored === 'latte' || stored === 'mocha' ? stored : 'system'
}
function systemTheme(): ResolvedTheme { return matchMedia('(prefers-color-scheme: dark)').matches ? 'mocha' : 'latte' }

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [preference, setPreferenceState] = useState<ThemePreference>(initialPreference)
  const [system, setSystem] = useState<ResolvedTheme>(systemTheme)
  const theme = preference === 'system' ? system : preference
  useEffect(() => {
    const media = matchMedia('(prefers-color-scheme: dark)')
    const update = () => setSystem(media.matches ? 'mocha' : 'latte')
    media.addEventListener('change', update)
    return () => media.removeEventListener('change', update)
  }, [])
  useEffect(() => { document.documentElement.dataset.theme = theme }, [theme])
  const setPreference = (value: ThemePreference) => {
    setPreferenceState(value)
    if (value === 'system') localStorage.removeItem(STORAGE_KEY)
    else localStorage.setItem(STORAGE_KEY, value)
  }
  const value = useMemo(() => ({ theme, preference, setPreference, toggleTheme: () => setPreference(theme === 'latte' ? 'mocha' : 'latte') }), [theme, preference])
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
}
export function useTheme() { const value = useContext(ThemeContext); if (!value) throw new Error('useTheme debe usarse dentro de ThemeProvider'); return value }
