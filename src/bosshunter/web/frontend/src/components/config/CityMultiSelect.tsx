import { useMemo, useRef, useState } from 'react'
import { RefreshCw } from 'lucide-react'

export interface CityOption {
  name: string
  code: string
  first_char?: string
  hot?: boolean
}

export function CityMultiSelect({
  options,
  value,
  onChange,
  onRefresh,
  refreshing,
  message,
  tourTarget,
}: {
  options: CityOption[]
  value: string[]
  onChange: (cities: string[]) => void
  onRefresh: () => void
  refreshing: boolean
  message: string
  tourTarget?: string
}) {
  const [query, setQuery] = useState('')
  const [dropdownOpen, setDropdownOpen] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)
  // 只显示城市名，不显示内部编码。
  const suggestions = useMemo(() => {
    const needle = query.trim().toLowerCase()
    if (!needle) return []
    return options
      .filter(city => city.name.toLowerCase().includes(needle) && !value.includes(city.name))
      .slice(0, 8)
  }, [options, query, value])
  const hotCities = useMemo(() => {
    const hot = options.filter(city => city.hot)
    return (hot.length ? hot : options).slice(0, 8)
  }, [options])
  const toggle = (name: string) => {
    onChange(value.includes(name) ? value.filter(item => item !== name) : [...value, name])
    setQuery('')
    inputRef.current?.focus()
  }
  return (
    <div className="space-y-2">
      <div data-tour={tourTarget} className="relative flex gap-2">
        <input
          ref={inputRef}
          value={query}
          onChange={event => { setQuery(event.target.value); setDropdownOpen(true) }}
          onFocus={() => setDropdownOpen(true)}
          onBlur={() => window.setTimeout(() => setDropdownOpen(false), 150)}
          placeholder="搜索城市名"
          aria-label="搜索城市"
          className="min-w-0 flex-1 rounded-xl border border-card-border bg-surface px-3 py-2 text-xs outline-none focus:border-primary"
        />
        <button
          type="button"
          onClick={onRefresh}
          disabled={refreshing}
          aria-label="刷新城市列表"
          title="刷新城市列表"
          className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-card-border bg-surface text-muted hover:border-primary/40 hover:text-primary disabled:opacity-45"
        >
          <RefreshCw className={`h-4 w-4 ${refreshing ? 'animate-spin' : ''}`} />
        </button>
        {dropdownOpen && suggestions.length > 0 && (
          <ul className="absolute left-0 right-12 top-full z-20 mt-1 overflow-hidden rounded-xl border border-card-border bg-card shadow-lg">
            {suggestions.map(city => (
              <li key={city.code}>
                <button
                  type="button"
                  onMouseDown={event => { event.preventDefault(); toggle(city.name) }}
                  className="w-full px-3 py-2 text-left text-xs text-foreground hover:bg-surface"
                >
                  {city.name}
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
        <span>已选 {value.length} 个</span>
        {value.map(name => (
          <button key={name} type="button" onClick={() => toggle(name)} className="rounded-full bg-secondary px-2 py-1 text-primary" title="点击移除">
            {name} ×
          </button>
        ))}
        {!value.length && <span>还没选城市。</span>}
      </div>
      {hotCities.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-xs text-muted">热门：</span>
          {hotCities.map(city => {
            const selected = value.includes(city.name)
            return (
              <button
                key={city.code}
                type="button"
                onClick={() => toggle(city.name)}
                aria-pressed={selected}
                className={`rounded-full border px-2.5 py-1 text-xs font-bold ${selected ? 'border-primary bg-secondary text-primary' : 'border-card-border bg-card text-muted hover:border-primary/40'}`}
              >
                {city.name}
              </button>
            )
          })}
        </div>
      )}
      {query.trim() && !suggestions.length && (
        <p className="text-xs text-muted">没找到“{query.trim()}”，换个写法试试。</p>
      )}
      {message && <p className="rounded-lg bg-warning-soft px-3 py-2 text-xs text-warning">{message}</p>}
    </div>
  )
}
