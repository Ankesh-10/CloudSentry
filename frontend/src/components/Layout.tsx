import { ListIcon, MagnifyingGlassIcon, ShieldCheckIcon, SignOutIcon, StopCircleIcon, WarningIcon, XIcon } from '@phosphor-icons/react'
import { Suspense, useEffect, useState } from 'react'
import { Link, NavLink, Outlet, useLocation } from 'react-router-dom'

import { useAuth } from '../auth/AuthProvider'
import { useActions, useDashboardSummary, useEmergencyStop, useSystemHealth } from '../hooks/queries'
import { isDemo } from '../lib/env'
import { useTheme, type ThemePref } from '../theme/ThemeProvider'
import CommandPalette from './CommandPalette'
import { ConfirmDialog } from './Dialog'
import { NAV } from './nav'
import { toast } from './Toast'
import { Button, Loading, Segmented, Tip } from './ui'

function Brand() {
  return (
    <Link to="/" className="flex items-center gap-2.5 px-5 pt-6 pb-5">
      <span className="grid h-7 w-7 place-items-center rounded-md bg-primary text-on-primary">
        <ShieldCheckIcon size={16} weight="fill" />
      </span>
      <span className="text-[15px] font-semibold tracking-[-0.01em] text-fg">CloudSentry</span>
    </Link>
  )
}

function ThemeSwitch() {
  const { pref, setPref } = useTheme()
  const opts: { value: ThemePref; label: string }[] = [
    { value: 'light', label: 'Light' },
    { value: 'system', label: 'Auto' },
    { value: 'dark', label: 'Dark' },
  ]
  return <Segmented label="Theme" value={pref} onChange={setPref} options={opts} />
}

function Sidebar({ onNavigate }: { onNavigate?: () => void }) {
  const { email, roleHint, signOut } = useAuth()
  const summary = useDashboardSummary()
  const pending = useActions({ status: 'pending_approval', limit: 100 })
  const counts: Record<string, number | undefined> = {
    '/anomalies': summary.data?.active_anomalies,
    '/actions': pending.data?.length,
  }
  return (
    <div className="flex h-full flex-col">
      <Brand />
      <nav aria-label="Main" className="space-y-0.5 px-3">
        {NAV.map(({ to, label, end, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            onClick={onNavigate}
            className={({ isActive }) =>
              `flex h-9 items-center gap-2.5 rounded-md px-2.5 text-[13.5px] transition-colors ${
                isActive ? 'bg-surface font-medium text-fg ring-1 ring-line' : 'text-fg-2 hover:bg-subtle hover:text-fg'
              }`
            }
          >
            {({ isActive }) => (
              <>
                <Icon size={16} className={isActive ? 'text-fg' : 'text-muted'} />
                <span className="flex-1">{label}</span>
                {!!counts[to] && (
                  <span className={`rounded-full px-1.5 text-[11px] font-semibold tabular-nums ${to === '/anomalies' ? 'bg-accent-soft text-accent' : 'bg-subtle text-muted'}`}>
                    {counts[to]}
                  </span>
                )}
              </>
            )}
          </NavLink>
        ))}
      </nav>
      <div className="mt-auto space-y-4 border-t border-line px-5 py-5">
        <ThemeSwitch />
        <div className="min-w-0">
          <div className="truncate text-[13px] font-medium text-fg">{email}</div>
          <div className="text-[12px] text-muted">{isDemo ? 'Demo data' : `Role: ${roleHint ?? 'set by server'}`}</div>
        </div>
        <Button variant="ghost" size="sm" onClick={signOut} className="-ml-3">
          <SignOutIcon size={14} /> Sign out
        </Button>
      </div>
    </div>
  )
}

function Clock() {
  const [now, setNow] = useState(() => new Date())
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(t)
  }, [])
  return <time className="tabular-nums">{now.toISOString().slice(11, 19)} UTC</time>
}

function Readouts() {
  const { data } = useDashboardSummary()
  const health = useSystemHealth()
  const status = health.data?.status
  const pill = 'flex h-8 items-center gap-2 rounded-full border border-line bg-surface px-3 text-[12.5px] whitespace-nowrap hover:border-line-strong'
  // Automation state stays visible at every width; health and the clock are extras.
  return (
    <div className="flex items-center gap-2">
      <Tip content={status ? `System ${status}${health.data?.issues.length ? ` — ${health.data.issues.join(', ')}` : ''}` : 'Checking system health'} side="bottom">
        <Link to="/system" className={`${pill} max-md:hidden`}>
          <span className={`h-1.5 w-1.5 rounded-full ${status === 'ok' ? 'bg-signal' : status === 'unhealthy' ? 'bg-accent' : status ? 'bg-warn' : 'bg-muted'}`} />
          <span className={status === 'unhealthy' ? 'text-accent' : 'text-fg-2'}>{status === 'ok' ? 'Online' : status ? status[0].toUpperCase() + status.slice(1) : 'Checking'}</span>
        </Link>
      </Tip>
      {data && (
        <Tip
          side="bottom"
          content={
            data.automation_enabled && !data.dry_run
              ? 'Automation is on and live: approved actions change real cloud resources.'
              : data.automation_enabled
                ? 'Automation is on in dry-run mode: actions are logged, nothing is changed.'
                : 'Automation is off: nothing runs automatically. Change it on the System page.'
          }
        >
          <Link to="/system" className={pill} aria-label={`Automation ${data.automation_enabled ? 'on' : 'off'}, ${data.dry_run ? 'dry run' : 'live'}`}>
            <span className="text-muted max-md:hidden">Automation</span>
            <span className="font-medium text-fg">{data.automation_enabled ? 'On' : 'Off'}</span>
            <span className="text-line-strong">·</span>
            <span className={`font-medium ${data.dry_run ? 'text-rehearsal' : 'text-accent'}`}>{data.dry_run ? 'Dry run' : 'Live'}</span>
          </Link>
        </Tip>
      )}
      <div className="hidden px-2 text-[12.5px] text-muted lg:block">
        <Clock />
      </div>
    </div>
  )
}

function EmergencyStop() {
  const [open, setOpen] = useState(false)
  const stop = useEmergencyStop()
  const { data } = useDashboardSummary()
  const alreadyOff = data && !data.automation_enabled
  return (
    <>
      <Button variant="danger-outline" size="sm" onClick={() => setOpen(true)} aria-label="Emergency stop">
        <StopCircleIcon size={15} weight="fill" aria-hidden />
        <span className="sm:hidden">Stop</span>
        <span className="max-sm:hidden">Emergency stop</span>
      </Button>
      <ConfirmDialog
        open={open}
        onOpenChange={setOpen}
        tone="safety"
        title="Stop all automation"
        description="Any signed-in user can do this. It takes effect immediately and changes no cloud resource."
        consequence={
          alreadyOff ? (
            <>Automation is already off. Stopping again cancels any pending request to turn it back on, and is written to the audit log.</>
          ) : (
            <>
              Sets <code className="rounded bg-surface px-1 font-mono text-[12px]">GLOBAL_AUTOMATION_ENABLED</code> to false: nothing new runs, and
              the kill-switch blocks any action that tries. Rollbacks stay available. Turning it back on is a separate change on the System page.
            </>
          )
        }
        confirmLabel="Stop automation"
        pending={stop.isPending}
        onConfirm={() =>
          stop.mutate(undefined, {
            onSuccess: (r) => {
              setOpen(false)
              toast.success('Automation stopped', r.persisted ? undefined : 'Saved in memory only — repeat once the database recovers.')
            },
            onError: (e) => toast.error('Emergency stop failed', e.message),
          })
        }
      />
    </>
  )
}

function LiveBanner() {
  const { data } = useDashboardSummary()
  if (!data?.automation_enabled || data.dry_run) return null
  return (
    <div role="status" className="flex items-center justify-center gap-2 bg-accent-soft px-4 py-2 text-[13px] font-medium text-accent">
      <WarningIcon size={15} weight="fill" aria-hidden />
      Live mode — approved actions change real cloud resources
    </div>
  )
}

export default function Layout() {
  const [paletteOpen, setPaletteOpen] = useState(false)
  const [mobileNav, setMobileNav] = useState(false)
  const location = useLocation()

  useEffect(() => setMobileNav(false), [location.pathname])
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setPaletteOpen((o) => !o)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const mac = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform)

  return (
    <div className="min-h-screen">
      <aside className="fixed inset-y-0 left-0 hidden w-60 border-r border-line bg-page lg:block">
        <Sidebar />
      </aside>

      {mobileNav && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <button type="button" className="absolute inset-0 bg-[var(--scrim)]" aria-label="Close menu" onClick={() => setMobileNav(false)} />
          <aside className="absolute inset-y-0 left-0 w-72 border-r border-line bg-page shadow-pop [animation:fade-in_120ms_ease-out]">
            <button type="button" onClick={() => setMobileNav(false)} className="absolute top-5 right-4 z-10 rounded-md p-1.5 text-muted hover:bg-subtle hover:text-fg" aria-label="Close menu">
              <XIcon size={16} />
            </button>
            <Sidebar onNavigate={() => setMobileNav(false)} />
          </aside>
        </div>
      )}

      <div className="lg:pl-60">
        <header className="sticky top-0 z-30 border-b border-line bg-page/85 backdrop-blur-md">
          <LiveBanner />
          <div className="flex h-14 items-center gap-3 px-4 sm:px-8">
            <button type="button" onClick={() => setMobileNav(true)} className="rounded-md p-1.5 text-fg hover:bg-subtle lg:hidden" aria-label="Open menu">
              <ListIcon size={18} />
            </button>
            <button
              type="button"
              onClick={() => setPaletteOpen(true)}
              className="flex h-9 min-w-0 flex-1 items-center gap-2.5 rounded-md border border-line bg-surface px-3 text-[13px] text-muted transition-colors hover:border-line-strong sm:max-w-sm"
            >
              <MagnifyingGlassIcon size={15} className="shrink-0" aria-hidden />
              <span className="truncate">Search or jump to…</span>
              <kbd className="ml-auto hidden sm:inline-flex">{mac ? '⌘' : 'Ctrl'} K</kbd>
            </button>
            <div className="ml-auto flex items-center gap-2">
              <Readouts />
              <EmergencyStop />
            </div>
          </div>
        </header>
        <main className="mx-auto max-w-[1320px] px-4 py-10 sm:px-8 lg:px-10">
          {isDemo && (
            <div className="mb-8 flex items-center gap-3 rounded-lg bg-info-soft px-4 py-2.5 text-[13px] text-fg-2">
              <span className="eyebrow rounded-full bg-surface px-2 py-0.5 text-info">Demo</span>
              Sample data — nothing is sent to the API. Unset VITE_DEMO to use the real backend.
            </div>
          )}
          <Suspense fallback={<Loading />}>
            <Outlet />
          </Suspense>
        </main>
      </div>
      <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} />
    </div>
  )
}
