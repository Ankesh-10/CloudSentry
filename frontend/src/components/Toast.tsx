import { CheckCircleIcon, InfoIcon, WarningCircleIcon, XIcon } from '@phosphor-icons/react'
import { useSyncExternalStore } from 'react'

type Tone = 'success' | 'error' | 'info'
interface ToastItem {
  id: number
  tone: Tone
  title: string
  detail?: string
}

let items: ToastItem[] = []
let nextId = 1
const listeners = new Set<() => void>()
const emit = () => listeners.forEach((l) => l())

export function dismiss(id: number) {
  items = items.filter((t) => t.id !== id)
  emit()
}

function push(tone: Tone, title: string, detail?: string) {
  const id = nextId++
  items = [...items.slice(-3), { id, tone, title, detail }]
  emit()
  setTimeout(() => dismiss(id), tone === 'error' ? 8000 : 4500)
}

export const toast = {
  success: (title: string, detail?: string) => push('success', title, detail),
  error: (title: string, detail?: string) => push('error', title, detail),
  info: (title: string, detail?: string) => push('info', title, detail),
}

const ICON = {
  success: <CheckCircleIcon size={18} weight="fill" className="text-success" aria-hidden />,
  error: <WarningCircleIcon size={18} weight="fill" className="text-accent" aria-hidden />,
  info: <InfoIcon size={18} weight="fill" className="text-info" aria-hidden />,
}

export function Toaster() {
  const list = useSyncExternalStore(
    (l) => {
      listeners.add(l)
      return () => listeners.delete(l)
    },
    () => items,
  )
  return (
    <div className="pointer-events-none fixed right-4 bottom-4 z-50 flex w-[23rem] max-w-[calc(100vw-2rem)] flex-col gap-2" aria-live="polite">
      {list.map((t) => (
        <div
          key={t.id}
          role={t.tone === 'error' ? 'alert' : 'status'}
          className="pointer-events-auto flex items-start gap-3 rounded-xl border border-line bg-surface py-3 pr-2 pl-4 shadow-pop [animation:slide-in_200ms_cubic-bezier(0.16,1,0.3,1)]"
        >
          <span className="mt-0.5 shrink-0">{ICON[t.tone]}</span>
          <div className="min-w-0 flex-1">
            <div className="text-[13.5px] font-medium text-fg">{t.title}</div>
            {t.detail && <div className="mt-0.5 text-[12.5px] break-words text-muted">{t.detail}</div>}
          </div>
          <button type="button" onClick={() => dismiss(t.id)} className="rounded-md p-1.5 text-muted hover:bg-subtle hover:text-fg" aria-label="Dismiss">
            <XIcon size={14} />
          </button>
        </div>
      ))}
    </div>
  )
}
