import { useCallback, useEffect, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { ArrowLeft, ArrowRight, X } from 'lucide-react'

export const START_ONBOARDING_EVENT = 'jianli-lieshou:start-onboarding'
/** 走完或退出一次就记下来，不再自动弹；侧栏“新手教程”随时能重开。 */
export const TOUR_DONE_KEY = 'jianli-lieshou:tour-done'
/** 配置示例里的占位关键词：不算真的填过。 */
const EXAMPLE_KEYWORDS = ['关键词1', '关键词2']

type TourStep = { route: string; target: string; title: string; body: string; click: boolean; waitForSave?: boolean; placement?: 'above'; scroll?: false }

const steps: TourStep[] = [
  { route: '/', target: 'nav-config', title: '先找到设置', body: '点左边的“我的设置”，先从设置页面开始。', click: true },
  { route: '/config', target: 'resume-upload', title: '上传简历', body: '点一下发光的地方，或者把简历拖进去。已经传过的直接下一步。', click: false },
  { route: '/config', target: 'search-keywords', title: '填想找的岗位', body: '在打开的平台里填你想找的岗位，输完按回车。平台都没打开的话，先打开一个。', click: false },
  { route: '/config', target: 'search-city', title: '选想去的城市', body: '搜一下、选上你想去的城市。平台都没打开的话，先打开一个。', click: false, placement: 'above' },
  { route: '/config', target: 'config-save', title: '保存一下', body: '改完点一下发光的“保存”。点完按钮变灰了，就是保存好了。', click: false, waitForSave: true, scroll: false },
  { route: '/config', target: 'nav-workbench', title: '回到首页', body: '点左边的“首页”，回去检查能不能正常跑。', click: true },
  { route: '/', target: 'chrome-guide', title: '连上浏览器', body: '先在浏览器里打开远程调试、登录好招聘网站，再回到这里。', click: false },
  { route: '/', target: 'check-preflight', title: '检查能不能跑', body: '点一下发光的按钮做检查，结果会直接显示在上面。', click: true },
  // 后半程：从“能跑”到“真正用起来”。目标都在真按钮/真区域上；某步的
  // 按钮在当前状态下没有（比如还没岗位），右下角的“跳过这步”能直接过。
  { route: '/', target: 'nav-jobs', title: '找工作', body: '点左边的“找工作”，找到的岗位都在这儿，可以筛、可以排序。', click: true },
  { route: '/jobs', target: 'score-open', title: '给岗位打分', body: '点「打分设置」，选给哪些岗位打分；分够了的岗位才值得投。', click: false },
  { route: '/jobs', target: 'deliver-selected', title: '把看中的投了', body: '勾上想投的岗位，点「把选中的都投了」；够分的会自己投出去，不用你一个个点。', click: false },
  { route: '/jobs', target: 'nav-monitor', title: '投递跟进', body: '点左边的“投递跟进”，投出去的岗位和 HR 的回复都在这儿。', click: true },
  { route: '/', target: 'auth-activate', title: '卡密激活', body: '卡密填在首页顶上的输入框里，点「激活」就加上有效期；试用快到期了也是在这儿续。', click: false },
  { route: '/', target: 'update-check', title: '检查更新', body: '点左上角版本号旁边的「检查更新」，有新版会告诉你，点一下就能换上。', click: false },
  { route: '/config', target: 'greeting-ai', title: '智能打招呼', body: '在这儿打开「智能定制打招呼」，发给 HR 的话会结合你的简历和岗位来写；关掉就用你自己写的固定招呼语。', click: false },
]

type Highlight = { x: number; y: number; width: number; height: number }

export function OnboardingTour() {
  const [index, setIndex] = useState<number | null>(null)
  const [highlight, setHighlight] = useState<Highlight | null>(null)
  const [savePending, setSavePending] = useState(false)
  const [viewport, setViewport] = useState({ width: window.innerWidth, height: window.innerHeight })
  const location = useLocation()
  const navigate = useNavigate()
  const step = index === null ? null : steps[index]

  /** 走完或退出都记下来：下次打开不再自动弹。 */
  const finishTour = useCallback(() => {
    try {
      window.localStorage.setItem(TOUR_DONE_KEY, '1')
    } catch {
      // 记不住就算了：下次打开再弹一次，也不致命。
    }
    setIndex(null)
  }, [])

  useEffect(() => {
    const start = () => setIndex(0)
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') finishTour()
    }
    window.addEventListener(START_ONBOARDING_EVENT, start)
    window.addEventListener('keydown', escape)
    return () => {
      window.removeEventListener(START_ONBOARDING_EVENT, start)
      window.removeEventListener('keydown', escape)
    }
  }, [finishTour])

  // 新用户第一次打开自动带一遍：漏斗全是 0、也没填过真实关键词才算
  // 新用户；老用户（有岗位、有关键词）绝不突然弹出来打扰。
  // 查不到就当老用户，不打扰。
  useEffect(() => {
    let cancelled = false
    try {
      if (window.localStorage.getItem(TOUR_DONE_KEY)) return
    } catch {
      return
    }
    const check = async () => {
      try {
        const [funnelRes, configRes] = await Promise.all([fetch('/api/funnel'), fetch('/api/config')])
        if (cancelled || !funnelRes.ok || !configRes.ok) return
        const funnel = (await funnelRes.json()) as Record<string, unknown>
        const config = (await configRes.json()) as { search?: { keywords?: unknown } }
        const funnelEmpty = Object.values(funnel).every(value => Number(value) === 0)
        const keywords = Array.isArray(config?.search?.keywords) ? config.search.keywords : []
        const hasRealKeyword = keywords.some(keyword => {
          const text = String(keyword).trim()
          return text !== '' && !EXAMPLE_KEYWORDS.includes(text)
        })
        if (!cancelled && funnelEmpty && !hasRealKeyword) setIndex(0)
      } catch {
        // 查不到就当老用户，不打扰。
      }
    }
    void check()
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    if (!step) return
    let target: HTMLElement | null = null
    let resizeObserver: ResizeObserver | undefined
    let lookupObserver: MutationObserver | undefined
    const measure = () => {
      if (!target) return
      const bounds = target.getBoundingClientRect()
      const x = Math.max(6, bounds.left - 6)
      const y = Math.max(6, bounds.top - 6)
      const width = Math.max(12, Math.min(bounds.right + 6, window.innerWidth - 6) - x)
      const height = Math.max(12, Math.min(bounds.bottom + 6, window.innerHeight - 6) - y)
      setHighlight(previous => previous?.x === x && previous.y === y && previous.width === width && previous.height === height
        ? previous : { x, y, width, height })
      setViewport({ width: window.innerWidth, height: window.innerHeight })
      if (step.waitForSave) setSavePending(!(target as HTMLButtonElement).disabled || target.textContent?.includes('保存中') === true)
    }
    const locate = () => {
      if (location.pathname !== step.route) return
      target = document.querySelector<HTMLElement>(`[data-tour="${step.target}"]`)
      if (!target) return
      lookupObserver?.disconnect()
      if (step.scroll !== false) target.scrollIntoView({ block: 'center', behavior: 'auto' })
      measure()
      resizeObserver = new ResizeObserver(measure)
      resizeObserver.observe(target)
      if (step.waitForSave) {
        lookupObserver = new MutationObserver(measure)
        lookupObserver.observe(target, { attributes: true, attributeFilter: ['disabled'], childList: true, subtree: true })
      }
    }
    setHighlight(null)
    locate()
    if (!target) {
      lookupObserver = new MutationObserver(locate)
      lookupObserver.observe(document.body, { childList: true, subtree: true })
    }
    window.addEventListener('scroll', measure, true)
    window.addEventListener('resize', measure)
    return () => {
      lookupObserver?.disconnect()
      resizeObserver?.disconnect()
      window.removeEventListener('scroll', measure, true)
      window.removeEventListener('resize', measure)
    }
  }, [index, location.pathname, step])

  useEffect(() => {
    if (!step?.click || !highlight) return
    const onClick = (event: MouseEvent) => {
      const target = document.querySelector(`[data-tour="${step.target}"]`)
      if (!target?.contains(event.target as Node)) return
      if (index !== null && index + 1 >= steps.length) finishTour()
      else setIndex(current => (current === null ? null : current + 1))
    }
    document.addEventListener('click', onClick, true)
    return () => document.removeEventListener('click', onClick, true)
  }, [highlight, step, index, finishTour])

  if (!step || index === null) return null
  const next = () => {
    if (index + 1 >= steps.length) finishTour()
    else setIndex(index + 1)
  }
  const tooltipWidth = Math.min(340, viewport.width - 24)
  const tooltipLeft = highlight ? Math.max(12, Math.min(highlight.x, viewport.width - tooltipWidth - 12)) : 12
  const tooltipTop = highlight
    ? step.placement !== 'above' && highlight.y + highlight.height + 220 < viewport.height
      ? highlight.y + highlight.height + 12
      : Math.max(12, highlight.y - 212)
    : Math.max(12, viewport.height / 2 - 110)
  const hole = highlight
    ? `M${highlight.x} ${highlight.y}h${highlight.width}v${highlight.height}h-${highlight.width}z`
    : ''

  return <>
    <svg aria-hidden="true" className="pointer-events-none fixed inset-0 z-50 h-full w-full" viewBox={`0 0 ${viewport.width} ${viewport.height}`} preserveAspectRatio="none">
      <path d={`M0 0H${viewport.width}V${viewport.height}H0z ${hole}`} fill="rgba(0, 0, 0, 0.68)" fillRule="evenodd" />
    </svg>
    {highlight && <div aria-hidden="true" className="pointer-events-none fixed z-[51] rounded-xl border-2 border-primary shadow-[0_0_0_4px_rgba(13,116,144,0.28)]" style={{ left: highlight.x, top: highlight.y, width: highlight.width, height: highlight.height }} />}
    <div role="dialog" aria-label="交互引导" className="fixed z-[52] rounded-2xl border border-primary/40 bg-card p-4 text-foreground shadow-2xl" style={{ top: tooltipTop, left: tooltipLeft, width: tooltipWidth }}>
      <div className="flex items-start justify-between gap-3">
        <span className="text-[11px] font-bold text-primary">新手引导 · {index + 1}/{steps.length}</span>
        <button type="button" onClick={finishTour} aria-label="退出引导" className="rounded p-1 text-muted hover:bg-surface hover:text-foreground"><X className="h-4 w-4" /></button>
      </div>
      <h2 className="mt-1 text-base font-black">{step.title}</h2>
      <p className="mt-2 text-sm leading-6 text-muted">{step.body}</p>
      {!highlight && <p className="mt-2 text-xs text-warning">这一步不在这一页。<button type="button" className="font-bold underline" onClick={() => navigate(step.route)}>去那一页</button><button type="button" className="ml-3 font-bold underline" onClick={next}>跳过这步</button></p>}
      <div className="mt-4 flex items-center justify-between border-t border-card-border pt-3">
        <button type="button" onClick={() => setIndex(Math.max(0, index - 1))} disabled={index === 0} className="inline-flex items-center gap-1 text-xs text-muted disabled:opacity-40"><ArrowLeft className="h-3 w-3" />上一步</button>
        {step.click ? <span className="text-xs font-semibold text-primary">请点击高亮位置</span> : (
          <button type="button" onClick={next} disabled={step.waitForSave && savePending} className="inline-flex items-center gap-1 rounded-lg bg-primary px-3 py-2 text-xs font-bold text-primary-foreground disabled:opacity-50">
            下一步<ArrowRight className="h-3 w-3" />
          </button>
        )}
      </div>
    </div>
  </>
}
