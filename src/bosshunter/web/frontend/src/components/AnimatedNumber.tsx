import { useCountUp } from '@/hooks/useCountUp'

export function AnimatedNumber({ value, className }: { value: number; className?: string }) {
  const display = useCountUp(value)
  return <span className={className}>{display}</span>
}
