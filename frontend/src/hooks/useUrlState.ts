import { useCallback } from 'react'
import { useSearchParams } from 'react-router-dom'

// Filters live in the URL so a filtered view can be shared, bookmarked and
// restored with the back button. Empty/default values are removed.
export function useUrlState(key: string, fallback = ''): [string, (v: string) => void] {
  const [params, setParams] = useSearchParams()
  const value = params.get(key) ?? fallback
  const set = useCallback(
    (v: string) =>
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev)
          if (!v || v === fallback) next.delete(key)
          else next.set(key, v)
          // Changing a filter returns to the first page of results.
          if (key !== 'page') next.delete('page')
          return next
        },
        { replace: true },
      ),
    [key, fallback, setParams],
  )
  return [value, set]
}
