import { ArrowElbowDownLeftIcon, HardDrivesIcon, MagnifyingGlassIcon, MonitorIcon, MoonIcon, SunIcon } from '@phosphor-icons/react'
import * as RD from '@radix-ui/react-dialog'
import { useQuery } from '@tanstack/react-query'
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'

import { api } from '../lib/api'
import type { Resource } from '../lib/types'
import { useTheme, type ThemePref } from '../theme/ThemeProvider'
import { NAV } from './nav'

interface Item {
  id: string
  group: string
  label: string
  hint?: string
  icon: ReactNode
  run: () => void
}

export default function CommandPalette({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const navigate = useNavigate()
  const { setPref } = useTheme()
  const [q, setQ] = useState('')
  const [active, setActive] = useState(0)
  const listRef = useRef<HTMLDivElement>(null)

  // Shares the cache with the Resources page; only fetched once the palette opens.
  const resources = useQuery({
    queryKey: ['resources', {}],
    queryFn: ({ signal }) => api<Resource[]>('/resources/', { query: { limit: 1000 }, signal }),
    enabled: open,
  })

  const items = useMemo<Item[]>(() => {
    const go = (to: string) => () => navigate(to)
    const theme = (p: ThemePref) => () => setPref(p)
    const all: Item[] = [
      ...NAV.map((n) => ({ id: `nav:${n.to}`, group: 'Go to', label: n.label, icon: <n.icon size={16} />, run: go(n.to) })),
      { id: 'theme:light', group: 'Theme', label: 'Light theme', icon: <SunIcon size={16} />, run: theme('light') },
      { id: 'theme:dark', group: 'Theme', label: 'Dark theme', icon: <MoonIcon size={16} />, run: theme('dark') },
      { id: 'theme:system', group: 'Theme', label: 'Match system theme', icon: <MonitorIcon size={16} />, run: theme('system') },
      ...(resources.data ?? []).map((r) => ({
        id: `res:${r.id}`,
        group: 'Resources',
        label: r.name || r.provider_id,
        hint: `${r.resource_type} · ${r.region ?? '—'}`,
        icon: <HardDrivesIcon size={16} />,
        run: go(`/resources/${r.id}`),
      })),
    ]
    const needle = q.trim().toLowerCase()
    if (!needle) return all.filter((i) => i.group !== 'Resources').concat(all.filter((i) => i.group === 'Resources').slice(0, 5))
    return all.filter((i) => `${i.label} ${i.hint ?? ''}`.toLowerCase().includes(needle)).slice(0, 30)
  }, [q, resources.data, navigate, setPref])

  useEffect(() => setActive(0), [q, open])
  useEffect(() => {
    listRef.current?.querySelector(`[data-index="${active}"]`)?.scrollIntoView({ block: 'nearest' })
  }, [active])

  function choose(i: Item | undefined) {
    if (!i) return
    onOpenChange(false)
    setQ('')
    i.run()
  }

  let lastGroup = ''
  return (
    <RD.Root open={open} onOpenChange={(o) => { onOpenChange(o); if (!o) setQ('') }}>
      <RD.Portal>
        <RD.Overlay className="fixed inset-0 z-40 bg-[var(--scrim)] [animation:fade-in_120ms_ease-out]" />
        <RD.Content
          className="fixed top-[14vh] left-1/2 z-50 w-[calc(100vw-2rem)] max-w-xl -translate-x-1/2 overflow-hidden rounded-xl border border-line bg-surface shadow-pop [animation:fade-in_120ms_ease-out]"
          onKeyDown={(e) => {
            if (e.key === 'ArrowDown') { e.preventDefault(); setActive((a) => Math.min(a + 1, items.length - 1)) }
            if (e.key === 'ArrowUp') { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)) }
            if (e.key === 'Enter') { e.preventDefault(); choose(items[active]) }
          }}
        >
          <RD.Title className="sr-only">Search and jump</RD.Title>
          <RD.Description className="sr-only">Type to find a page, resource or setting. Use arrow keys and Enter.</RD.Description>
          <div className="flex items-center gap-3 border-b border-line px-4">
            <MagnifyingGlassIcon size={16} className="text-muted" aria-hidden />
            <input
              autoFocus
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Search pages, resources, settings"
              className="h-12 flex-1 bg-transparent text-[14px] text-fg outline-none placeholder:text-muted"
              aria-label="Search"
              role="combobox"
              aria-expanded
              aria-controls="palette-list"
              aria-activedescendant={items[active] ? `pal-${active}` : undefined}
            />
            <kbd>Esc</kbd>
          </div>
          <div ref={listRef} id="palette-list" role="listbox" className="max-h-[50vh] overflow-y-auto p-1.5">
            {items.length === 0 && <div className="px-3 py-8 text-[13px] text-muted">No matches for “{q}”</div>}
            {items.map((i, idx) => {
              const header = i.group !== lastGroup ? (lastGroup = i.group) : null
              return (
                <div key={i.id}>
                  {header && <div className="eyebrow px-3 pt-3 pb-1.5 text-[10.5px] text-muted">{header}</div>}
                  <div
                    id={`pal-${idx}`}
                    data-index={idx}
                    role="option"
                    aria-selected={idx === active}
                    onMouseMove={() => setActive(idx)}
                    onClick={() => choose(i)}
                    className={`flex cursor-pointer items-center gap-3 rounded-md px-3 py-2 text-[13.5px] ${idx === active ? 'bg-subtle text-fg' : 'text-fg-2'}`}
                  >
                    <span className="text-muted">{i.icon}</span>
                    <span className="flex-1 truncate">{i.label}</span>
                    {i.hint && <span className="truncate text-[12px] text-muted">{i.hint}</span>}
                    {idx === active && <ArrowElbowDownLeftIcon size={14} className="text-muted" aria-hidden />}
                  </div>
                </div>
              )
            })}
          </div>
        </RD.Content>
      </RD.Portal>
    </RD.Root>
  )
}
