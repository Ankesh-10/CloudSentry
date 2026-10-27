import { HardDrivesIcon, ListChecksIcon, PulseIcon, ScrollIcon, SlidersHorizontalIcon, SquaresFourIcon, type Icon } from '@phosphor-icons/react'

export const NAV: { to: string; label: string; icon: Icon; end?: boolean }[] = [
  { to: '/', label: 'Overview', icon: SquaresFourIcon, end: true },
  { to: '/resources', label: 'Resources', icon: HardDrivesIcon },
  { to: '/anomalies', label: 'Anomalies', icon: PulseIcon },
  { to: '/actions', label: 'Actions', icon: ListChecksIcon },
  { to: '/audit', label: 'Audit log', icon: ScrollIcon },
  { to: '/system', label: 'System', icon: SlidersHorizontalIcon },
]
