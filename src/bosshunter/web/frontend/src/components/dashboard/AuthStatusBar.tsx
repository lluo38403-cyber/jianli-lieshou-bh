import { useState } from 'react'
import { createPortal } from 'react-dom'
import { Check, Copy, MessageCircle, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { copyText, formatRemaining, type AuthHook } from '@/hooks/useAuth'
import { cn } from '@/lib/utils'

interface AuthStatusBarProps {
  auth: AuthHook
}

/** 首页顶部的授权状态栏：倒计时、卡密激活、购买、机器码、联系客服。 */
export function AuthStatusBar({ auth }: AuthStatusBarProps) {
  const { snapshot, display, remainingSeconds } = auth
  const [cardCode, setCardCode] = useState('')
  const [message, setMessage] = useState('')
  const [activating, setActivating] = useState(false)
  const [serviceOpen, setServiceOpen] = useState(false)
  const [qrFailed, setQrFailed] = useState(false)
  const [copied, setCopied] = useState<'machine' | 'wechat' | null>(null)

  const status = snapshot?.status ?? 'unknown'
  const statusText = formatRemaining(remainingSeconds, status)
  const dotClass =
    status === 'expired'
      ? 'bg-danger-solid'
      : status === 'unknown'
        ? 'bg-muted'
        : 'bg-primary'

  const flashCopied = (which: 'machine' | 'wechat') => {
    setCopied(which)
    window.setTimeout(() => setCopied((prev) => (prev === which ? null : prev)), 2000)
  }

  const handleCopyMachine = async () => {
    const short = snapshot?.machineIdShort || ''
    if (!short) return
    if (await copyText(short)) flashCopied('machine')
  }

  const handleCopyWechat = async () => {
    if (!display.wechatId) return
    if (await copyText(display.wechatId)) flashCopied('wechat')
  }

  const handleActivate = async () => {
    setMessage('')
    setActivating(true)
    try {
      const result = await auth.activate(cardCode)
      if (result.ok) {
        setMessage('好了，有效期加上了。')
        setCardCode('')
      } else {
        setMessage(result.error || '这张卡用不了。')
      }
    } finally {
      setActivating(false)
    }
  }

  const handleBuy = () => {
    if (display.buyUrl) window.open(display.buyUrl, '_blank', 'noopener,noreferrer')
  }

  // 注意：弹窗必须和带入场动画（transform）的状态栏平级，不能嵌在
  // 动画 div 里面——否则 fixed 会以状态栏为参照，弹窗跑偏、被截断。
  // 更进一步：整页外层还有个 animate-page-in 动画容器（也带 transform），
  // 所以弹窗用 Portal 直接挂到 body 上，彻底脱离所有动画祖先。
  return (
    <>
      <div
        data-testid="auth-status-bar"
        className="animate-fade-up rounded-2xl border border-card-border bg-card p-4 shadow-sm"
      >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <span className="inline-flex items-center gap-2 text-base font-semibold text-foreground">
          <span className={cn('h-2 w-2 animate-pulse rounded-full', dotClass)} />
          {statusText}
        </span>
        {snapshot?.offline ? (
          <span className="text-xs text-muted">网络断了，先用上次的状态顶着</span>
        ) : null}
        <span className="ml-auto inline-flex items-center gap-1 text-xs text-muted">
          机器码 {snapshot?.machineIdShort || '……'}
          <Button
            variant="ghost"
            size="sm"
            data-testid="auth-copy-machine"
            onClick={() => void handleCopyMachine()}
            disabled={!snapshot?.machineIdShort}
            title="复制机器码"
          >
            {copied === 'machine' ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
            {copied === 'machine' ? '已复制' : '复制'}
          </Button>
        </span>
        <Button
          variant="secondary"
          size="sm"
          data-testid="auth-service-open"
          onClick={() => setServiceOpen(true)}
        >
          <MessageCircle className="mr-1 h-3.5 w-3.5" />
          联系客服
        </Button>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Input
          data-testid="auth-card-input"
          value={cardCode}
          onChange={(event) => setCardCode(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') void handleActivate()
          }}
          placeholder="输入卡密，如 JL-D-XXXX-XXXX-XXXX"
          className="min-w-52 flex-1"
          maxLength={24}
        />
        <Button data-testid="auth-activate" onClick={() => void handleActivate()} disabled={activating}>
          {activating ? '正在激活…' : '激活'}
        </Button>
        {display.buyUrl ? (
          <Button variant="secondary" data-testid="auth-buy" onClick={handleBuy}>
            购买天卡/周卡
          </Button>
        ) : null}
        {message ? (
          <span data-testid="auth-message" className="text-sm text-muted">
            {message}
          </span>
        ) : null}
      </div>
    </div>

      {serviceOpen
        ? createPortal(
            <div
              data-testid="auth-service-modal"
              role="dialog"
              aria-label="联系客服"
              className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
              onClick={() => setServiceOpen(false)}
            >
          <div
            className="w-full max-w-sm rounded-2xl border border-card-border bg-card p-5 text-center shadow-lg"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="mb-3 flex items-center justify-between">
              <h3 className="text-base font-semibold text-foreground">联系客服</h3>
              <Button variant="ghost" size="sm" data-testid="auth-service-close" onClick={() => setServiceOpen(false)}>
                <X className="h-4 w-4" />
              </Button>
            </div>
            {display.qrPath && !qrFailed ? (
              <img
                src={display.qrPath}
                alt="客服微信二维码"
                className="mx-auto h-56 w-56 rounded-xl border border-card-border bg-white object-contain"
                onError={() => setQrFailed(true)}
              />
            ) : (
              <div className="mx-auto flex h-56 w-56 flex-col items-center justify-center rounded-xl border border-card-border bg-surface px-4 text-sm text-muted">
                二维码图还没换好
                <span className="mt-1">先加微信号也行</span>
              </div>
            )}
            <p className="mt-3 text-base font-semibold text-foreground">
              微信号：{display.wechatId || '……'}
            </p>
            <Button
              variant="secondary"
              className="mt-2"
              data-testid="auth-copy-wechat"
              onClick={() => void handleCopyWechat()}
              disabled={!display.wechatId}
            >
              {copied === 'wechat' ? <Check className="mr-1 h-3.5 w-3.5" /> : <Copy className="mr-1 h-3.5 w-3.5" />}
              {copied === 'wechat' ? '已复制' : '一键复制微信号'}
            </Button>
            <p className="mt-3 text-sm text-muted">使用问题、购买咨询、售后答疑请添加微信</p>
          </div>
            </div>,
            document.body,
          )
        : null}
    </>
  )
}
