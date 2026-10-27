import { WarningIcon, XIcon } from '@phosphor-icons/react'
import * as RD from '@radix-ui/react-dialog'
import type { ReactNode } from 'react'

import { Button } from './ui'

const OVERLAY = 'fixed inset-0 z-40 bg-[var(--scrim)] [animation:fade-in_120ms_ease-out]'

function Close() {
  return (
    <RD.Close className="rounded-md p-1.5 text-muted hover:bg-subtle hover:text-fg" aria-label="Close">
      <XIcon size={16} />
    </RD.Close>
  )
}

export function Dialog({ open, onOpenChange, title, description, children, footer, wide = false, eyebrow }: {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: ReactNode
  description?: ReactNode
  children?: ReactNode
  footer?: ReactNode
  wide?: boolean
  eyebrow?: ReactNode
}) {
  return (
    <RD.Root open={open} onOpenChange={onOpenChange}>
      <RD.Portal>
        <RD.Overlay className={OVERLAY} />
        <RD.Content
          className={`fixed top-1/2 left-1/2 z-50 w-[calc(100vw-2rem)] -translate-x-1/2 -translate-y-1/2 rounded-xl border border-line bg-surface shadow-pop [animation:pop-in_160ms_cubic-bezier(0.16,1,0.3,1)] ${wide ? 'max-w-2xl' : 'max-w-lg'}`}
        >
          <div className="flex items-start justify-between gap-4 px-6 pt-6">
            <div className="min-w-0">
              {eyebrow && <div className="eyebrow mb-2 text-muted">{eyebrow}</div>}
              <RD.Title className="display text-[1.65rem] text-fg">{title}</RD.Title>
            </div>
            <Close />
          </div>
          {description ? (
            <RD.Description className="px-6 pt-2 text-[13.5px] text-muted">{description}</RD.Description>
          ) : (
            <RD.Description className="sr-only">Dialog</RD.Description>
          )}
          {children && <div className="px-6 pt-5">{children}</div>}
          <div className="mt-6 flex justify-end gap-2 border-t border-line px-6 py-4">{footer}</div>
        </RD.Content>
      </RD.Portal>
    </RD.Root>
  )
}

// Every state-changing action goes through this: it states the consequence
// before the user commits. `live` is reserved for operations that really change
// a cloud resource; `safety` is for stopping things (red button, calm framing).
// Neither tone focuses the confirm button, so a stray Enter only cancels.
export type ConfirmTone = 'confirm' | 'live' | 'safety'

const TONE: Record<ConfirmTone, { eyebrow: string; heading: string; box: string; headingColor: string; button: 'primary' | 'danger' }> = {
  confirm: { eyebrow: 'Confirm', heading: 'What happens', box: 'bg-subtle', headingColor: 'text-fg', button: 'primary' },
  live: { eyebrow: 'Live change', heading: 'This changes real cloud resources', box: 'bg-accent-soft', headingColor: 'text-accent', button: 'danger' },
  safety: { eyebrow: 'Safety control', heading: 'What happens', box: 'bg-subtle', headingColor: 'text-fg', button: 'danger' },
}

export function ConfirmDialog({ open, onOpenChange, title, description, consequence, tone = 'confirm', confirmLabel, pending, onConfirm, children }: {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: ReactNode
  description?: ReactNode
  consequence?: ReactNode
  tone?: ConfirmTone
  confirmLabel: string
  pending?: boolean
  onConfirm: () => void
  children?: ReactNode
}) {
  const t = TONE[tone]
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={title}
      description={description}
      eyebrow={t.eyebrow}
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)} autoFocus={tone !== 'confirm'}>Cancel</Button>
          <Button variant={t.button} disabled={pending} onClick={onConfirm} autoFocus={tone === 'confirm'}>
            {pending ? 'Working…' : confirmLabel}
          </Button>
        </>
      }
    >
      {children}
      {consequence && (
        <div className={`flex gap-3 rounded-lg px-4 py-3 text-[13px] text-fg-2 ${t.box} ${children ? 'mt-3' : ''}`}>
          {tone === 'live' && <WarningIcon size={16} className="mt-0.5 shrink-0 text-accent" aria-hidden />}
          <div>
            <div className={`mb-0.5 font-medium ${t.headingColor}`}>{t.heading}</div>
            {consequence}
          </div>
        </div>
      )}
    </Dialog>
  )
}

export function Drawer({ open, onOpenChange, title, subtitle, children, eyebrow }: {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: ReactNode
  subtitle?: ReactNode
  children: ReactNode
  eyebrow?: ReactNode
}) {
  return (
    <RD.Root open={open} onOpenChange={onOpenChange}>
      <RD.Portal>
        <RD.Overlay className={OVERLAY} />
        <RD.Content className="fixed inset-y-2 right-2 z-50 flex w-[calc(100%-1rem)] max-w-xl flex-col rounded-xl border border-line bg-surface shadow-pop [animation:slide-in_200ms_cubic-bezier(0.16,1,0.3,1)]">
          <div className="flex items-start justify-between gap-4 border-b border-line px-6 py-5">
            <div className="min-w-0">
              {eyebrow && <div className="eyebrow mb-2 text-muted">{eyebrow}</div>}
              <RD.Title className="display truncate text-[1.65rem] text-fg">{title}</RD.Title>
              <RD.Description className={subtitle ? 'mt-1 text-[13px] text-muted' : 'sr-only'}>{subtitle ?? 'Details'}</RD.Description>
            </div>
            <Close />
          </div>
          <div className="flex-1 overflow-y-auto px-6 py-5">{children}</div>
        </RD.Content>
      </RD.Portal>
    </RD.Root>
  )
}
