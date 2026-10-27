import { useSyncExternalStore } from 'react'

// The browser cannot know whether the API grants operator rights (the
// OPERATOR_USER_IDS list is server-side). We assume it might, and the first
// 403 on a state-changing call flips the UI to read-only for the session, so
// operator controls are disabled with an explanation instead of failing again.

let readOnly = false
const listeners = new Set<() => void>()

export function markReadOnly() {
  if (readOnly) return
  readOnly = true
  listeners.forEach((l) => l())
}

export function resetRole() {
  readOnly = false
  listeners.forEach((l) => l())
}

function subscribe(l: () => void) {
  listeners.add(l)
  return () => listeners.delete(l)
}

export function useCanOperate(): boolean {
  return !useSyncExternalStore(subscribe, () => readOnly, () => readOnly)
}

export const READ_ONLY_HINT = 'Your account has the viewer role — operator rights are needed for this.'
