import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import type { ResolvedTheme, ThemePreference } from '../types'

export interface ThemeContextValue {
  theme: ResolvedTheme
  preference: ThemePreference
  setPreference: (theme: ThemePreference) => void
  toggleTheme: () => void
}

// Context and constants

const ThemeContext = createContext<ThemeContextValue | null>(null)

const STORAGE_KEY = 'tutoria-theme'

// Helper functions

function initialPreference(): ThemePreference {
  const stored = localStorage.getItem(STORAGE_KEY)

  return stored === 'latte' || stored === 'mocha'
    ? stored
    : 'system'
}

function systemTheme(): ResolvedTheme {
  return matchMedia('(prefers-color-scheme: dark)').matches
    ? 'mocha'
    : 'latte'
}

// Theme provider

export function ThemeProvider({
  children,
}: {
  children: ReactNode
}) {
  // State

  const [preference, setPreferenceState] =
    useState<ThemePreference>(initialPreference)

  const [system, setSystem] =
    useState<ResolvedTheme>(systemTheme)

  const theme = preference === 'system'
    ? system
    : preference

  // Effects

  useEffect(() => {
    const media = matchMedia('(prefers-color-scheme: dark)')

    const update = () => {
      setSystem(media.matches ? 'mocha' : 'latte')
    }

    media.addEventListener('change', update)

    return () => {
      media.removeEventListener('change', update)
    }
  }, [])

  useEffect(() => {
    document.documentElement.dataset.theme = theme
  }, [theme])

  // Theme preference management

  const setPreference = (value: ThemePreference) => {
    setPreferenceState(value)

    if (value === 'system') {
      localStorage.removeItem(STORAGE_KEY)
    } else {
      localStorage.setItem(STORAGE_KEY, value)
    }
  }

  // Context value

  const value = useMemo(
    () => ({
      theme,
      preference,
      setPreference,
      toggleTheme: () => {
        setPreference(theme === 'latte' ? 'mocha' : 'latte')
      },
    }),
    [theme, preference]
  )

  // Provider render

  return (
    <ThemeContext.Provider value={value}>
      {children}
    </ThemeContext.Provider>
  )
}

// Custom hook

export function useTheme() {
  const value = useContext(ThemeContext)

  if (!value) {
    throw new Error(
      'useTheme debe usarse dentro de ThemeProvider'
    )
  }

  return value
}
