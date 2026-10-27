import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'

export type ThemePref = 'light' | 'dark' | 'system'
export type ResolvedTheme = 'light' | 'dark'

const STORAGE_KEY = 'cloudsentry-theme'

function readPref(): ThemePref {
  try {
    const v = localStorage.getItem(STORAGE_KEY)
    return v === 'light' || v === 'dark' ? v : 'system'
  } catch {
    return 'system'
  }
}

function systemTheme(): ResolvedTheme {
  return typeof window !== 'undefined' && window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

// index.html applies the stored preference before first paint; this keeps it in
// sync afterwards. "system" removes the attribute so the CSS media query decides.
function apply(pref: ThemePref) {
  const root = document.documentElement
  if (pref === 'system') root.removeAttribute('data-theme')
  else root.setAttribute('data-theme', pref)
}

interface ThemeState {
  pref: ThemePref
  resolved: ResolvedTheme
  setPref: (p: ThemePref) => void
}

const ThemeContext = createContext<ThemeState | null>(null)

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [pref, setPrefState] = useState<ThemePref>(readPref)
  const [system, setSystem] = useState<ResolvedTheme>(systemTheme)

  useEffect(() => {
    const mq = window.matchMedia?.('(prefers-color-scheme: dark)')
    if (!mq) return
    const onChange = () => setSystem(mq.matches ? 'dark' : 'light')
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [])

  useEffect(() => apply(pref), [pref])

  const value = useMemo<ThemeState>(
    () => ({
      pref,
      resolved: pref === 'system' ? system : pref,
      setPref(p) {
        // Apply before the re-render: useChartColors reads computed styles
        // during render, and an effect would run too late (stale colours).
        apply(p)
        setPrefState(p)
        try {
          if (p === 'system') localStorage.removeItem(STORAGE_KEY)
          else localStorage.setItem(STORAGE_KEY, p)
        } catch {
          /* storage blocked: preference lasts for this tab only */
        }
      },
    }),
    [pref, system],
  )

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
}

export function useTheme(): ThemeState {
  const ctx = useContext(ThemeContext)
  if (!ctx) throw new Error('useTheme must be used inside <ThemeProvider>')
  return ctx
}

// Recharts writes colours into SVG attributes, so charts get resolved values
// (re-read whenever the theme changes) rather than var() references.
export function useChartColors() {
  const { resolved } = useTheme()
  return useMemo(() => {
    const css = getComputedStyle(document.documentElement)
    const v = (name: string) => css.getPropertyValue(name).trim()
    return {
      theme: resolved,
      surface: v('--surface'),
      grid: v('--chart-grid'),
      axis: v('--chart-axis'),
      text: v('--fg'),
      text2: v('--fg-2'),
      muted: v('--muted'),
      series1: v('--series-1'),
      series2: v('--series-2'),
      prev: v('--series-prev'),
      sevHigh: v('--sev-high'),
      sevMed: v('--sev-med'),
      sevLow: v('--sev-low'),
    }
  }, [resolved])
}
