import { useMemo, useRef, useState } from 'react'
import { X } from 'lucide-react'
import { roleCategory, searchRolePicks, type JobRolePick } from '@/lib/jobTaxonomy'

/**
 * v54 图2-6：岗位方向多选器（参考招聘平台的职位选择体验）。
 * 输入关键词后下拉联想出职责区分明确的细分岗位（如输“电商”出
 * “国内电商运营 / 跨境电商运营 / 电商客服 / 电商美工”），点一下
 * 加一个，可多选，点 × 拿掉。选中的方向写进 search.role_picks：
 * 选了方向后，搜岗和打分只认这些方向里的岗位；没选方向时按原来
 * 的关键词搜。下拉是内置的常用岗位表——招聘平台没有公开的类目
 * 接口，拉不到最新列表。
 */
export function RolePicker({
  value,
  onChange,
}: {
  value: JobRolePick[]
  onChange: (next: JobRolePick[]) => void
}) {
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)
  const picked = useMemo(() => new Set(value.map(pick => pick.label)), [value])
  const suggestions = useMemo(
    () => searchRolePicks(query).filter(pick => !picked.has(pick.label)).slice(0, 8),
    [query, picked],
  )

  const addPick = (pick: JobRolePick) => {
    onChange([...value, pick])
    setQuery('')
    setOpen(false)
    inputRef.current?.focus()
  }

  const removePick = (label: string) => {
    onChange(value.filter(pick => pick.label !== label))
  }

  return (
    <div>
      <div className="flex flex-wrap items-center gap-2 rounded-xl border border-card-border bg-surface px-3 py-2">
        {value.map(pick => (
          <span
            key={pick.label}
            className="flex items-center gap-1 rounded-full bg-primary/10 px-2.5 py-1 text-xs font-bold text-primary"
          >
            {pick.label}
            <button
              type="button"
              aria-label={`去掉${pick.label}`}
              className="flex h-4 w-4 items-center justify-center rounded-full hover:bg-primary/20"
              onClick={() => removePick(pick.label)}
            >
              <X className="h-3 w-3" />
            </button>
          </span>
        ))}
        <input
          ref={inputRef}
          value={query}
          onChange={event => {
            setQuery(event.target.value)
            setOpen(true)
          }}
          onFocus={() => setOpen(true)}
          onBlur={() => {
            // 失焦后留一点时间，让下拉里的点击先跑完再合上。
            window.setTimeout(() => setOpen(false), 150)
          }}
          onKeyDown={event => {
            if (event.key === 'Enter' && suggestions.length > 0) {
              event.preventDefault()
              addPick(suggestions[0])
            }
          }}
          placeholder={value.length === 0 ? '比如：电商，会下拉出国内电商运营、跨境电商运营、电商客服…' : '继续输关键词加方向…'}
          className="min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-muted"
          aria-label="选岗位方向"
        />
      </div>
      {open && suggestions.length > 0 && (
        <ul className="mt-1 max-h-64 overflow-auto rounded-xl border border-card-border bg-surface py-1 shadow-lg" role="listbox" aria-label="岗位方向建议">
          {suggestions.map(pick => (
            <li key={pick.label}>
              <button
                type="button"
                role="option"
                aria-selected={false}
                className="flex w-full flex-col items-start gap-0.5 px-3 py-2 text-left hover:bg-primary/5"
                onMouseDown={event => {
                  // 用 mousedown 而不是 click：input 失焦合上之前先把选择收了。
                  event.preventDefault()
                  addPick(pick)
                }}
              >
                <span className="text-sm font-bold text-foreground">
                  {pick.label}
                  <span className="ml-2 text-xs font-normal text-muted">{roleCategory(pick.label)}</span>
                </span>
                <span className="text-xs text-muted">{pick.keywords.join('、')}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {open && query.trim() && suggestions.length === 0 && (
        <p className="mt-1 rounded-xl border border-card-border bg-surface px-3 py-2 text-xs text-muted">
          没找到“{query.trim()}”对应的方向，换个词试试，或者直接在下面“想找的岗位”里手填关键词。
        </p>
      )}
      <p className="mt-1 text-xs text-muted">
        {value.length === 0
          ? '没选方向时按关键词搜，搜得宽；选了方向后只找这些方向里的岗位，比如“电商财务”就不会再混进来了。'
          : `已选 ${value.length} 个方向：搜岗和打分只认这些方向里的岗位。`}
      </p>
    </div>
  )
}
