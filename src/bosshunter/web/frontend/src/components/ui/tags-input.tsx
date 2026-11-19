import { useState, KeyboardEvent } from 'react'
import { X } from 'lucide-react'
import { cn } from '@/lib/utils'

interface TagsInputProps {
  value: string[]
  onChange: (tags: string[]) => void
  placeholder?: string
  className?: string
  onAdd?: (tag: string) => void
}

export function TagsInput({ value, onChange, placeholder = '输入后按回车添加', className, onAdd }: TagsInputProps) {
  const [input, setInput] = useState('')

  const commitInput = () => {
    // 中英文逗号、顿号、分号都能切分，输完按回车、点“添加”或点别处都会提交。
    const tags = input.split(/[,，、;；]/).map(tag => tag.trim()).filter(Boolean)
    if (!tags.length) return
    if (onAdd) {
      tags.forEach(onAdd)
    } else {
      onChange([...new Set([...value, ...tags])])
    }
    setInput('')
  }

  const handleKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.nativeEvent.isComposing || e.nativeEvent.keyCode === 229) return
    if (e.key === 'Enter') {
      e.preventDefault()
      commitInput()
    } else if (e.key === 'Backspace' && !input && value.length > 0) {
      onChange(value.slice(0, -1))
    }
  }

  const removeTag = (index: number) => {
    onChange(value.filter((_, i) => i !== index))
  }

  return (
    <div className={cn('flex items-start gap-2', className)}>
      <div className="flex min-w-0 flex-1 flex-wrap gap-1.5 rounded-md border border-card-border bg-card p-2 focus-within:border-primary focus-within:ring-2 focus-within:ring-primary/30">
        {value.map((tag, i) => (
          <span
            key={i}
            className="inline-flex items-center gap-1 rounded-md bg-secondary px-2 py-0.5 text-xs font-bold text-primary"
          >
            {tag}
            <button
              type="button"
              onClick={() => removeTag(i)}
              className="text-primary/70 hover:text-primary"
            >
              <X className="w-3 h-3" />
            </button>
          </span>
        ))}
        <input
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          onBlur={commitInput}
          placeholder={value.length === 0 ? placeholder : ''}
          className="min-h-[36px] min-w-[80px] flex-1 bg-transparent text-sm text-foreground outline-none placeholder:text-muted/60"
        />
      </div>
      <button
        type="button"
        onClick={commitInput}
        disabled={!input.trim()}
        className="mt-1 h-9 shrink-0 rounded-md border border-card-border bg-surface px-3 text-xs font-bold text-primary hover:border-primary/40 disabled:cursor-not-allowed disabled:opacity-45"
      >
        添加
      </button>
    </div>
  )
}
