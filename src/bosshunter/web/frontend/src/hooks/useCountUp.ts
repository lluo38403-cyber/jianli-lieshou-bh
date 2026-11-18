import { useEffect, useRef, useState } from 'react'

/** 数字滚动动画：目标值变化时从旧值平滑滚到新值（约 600ms）。 */
export function useCountUp(target: number, duration = 600): number {
  const [value, setValue] = useState(target)
  const previousRef = useRef(target)
  const rafRef = useRef(0)

  useEffect(() => {
    const from = previousRef.current
    if (from === target) return
    if (typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      previousRef.current = target
      setValue(target)
      return
    }
    const start = performance.now()
    const tick = (now: number) => {
      const progress = Math.min(1, (now - start) / duration)
      const eased = 1 - Math.pow(1 - progress, 3)
      const current = Math.round(from + (target - from) * eased)
      setValue(current)
      if (progress < 1) {
        rafRef.current = requestAnimationFrame(tick)
      } else {
        previousRef.current = target
      }
    }
    rafRef.current = requestAnimationFrame(tick)
    return () => {
      cancelAnimationFrame(rafRef.current)
      previousRef.current = target
    }
  }, [target, duration])

  return value
}
