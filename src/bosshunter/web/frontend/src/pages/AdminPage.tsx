import { useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Switch } from '@/components/ui/switch'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card'

type RelayStatus = {
  url?: string
  reachable?: boolean
  provider?: string | null
  model?: string | null
  ai_enabled?: boolean | null
}

type AdminAi = {
  relay?: RelayStatus
  thinking?: string
  thinking_budget?: number
  timeout_seconds?: number
  scoring_max_tokens?: number
  scoring_max_attempts?: number
  scoring_concurrency?: number
  scoring_second_review?: boolean
  greeting_max_tokens?: number
  greeting_review_max_tokens?: number
  greeting_max_attempts?: number
  greeting_review_threshold?: number
  greeting_style_suggestions?: boolean
  resume_tailor_enabled?: boolean
}

type AdminProfile = {
  ai_greeting_enabled?: boolean
  fixed_greeting?: string
  greeting_preference?: string
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="mb-1.5">
        <label className="block text-xs text-foreground">{label}</label>
      </div>
      {children}
    </div>
  )
}

function SectionCard({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="space-y-4">{children}</div>
      </CardContent>
    </Card>
  )
}

function StatusRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-lg border border-card-border bg-surface px-3 py-2">
      <span className="text-xs text-muted">{label}</span>
      <span className="text-xs font-bold text-foreground break-all text-right">{value}</span>
    </div>
  )
}

export default function AdminPage() {
  const [status, setStatus] = useState<{ authorized: boolean; password_configured: boolean } | null>(null)
  const [loading, setLoading] = useState(true)
  const [password, setPassword] = useState('')
  const [loginError, setLoginError] = useState('')
  const [loggingIn, setLoggingIn] = useState(false)

  const [ai, setAi] = useState<AdminAi | null>(null)
  const [profile, setProfile] = useState<AdminProfile | null>(null)
  const [aiTest, setAiTest] = useState<{ testing: boolean; ok?: boolean; message?: string }>({ testing: false })
  const [saving, setSaving] = useState(false)
  const [saveMessage, setSaveMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null)
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [passwordMessage, setPasswordMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null)

  const loadAdminConfig = async () => {
    const res = await fetch('/api/admin/config', { cache: 'no-store' })
    const data = await res.json()
    if (!res.ok) throw new Error(data.error || '加载管理员配置失败')
    setAi(data.ai || {})
    setProfile(data.profile || {})
  }

  useEffect(() => {
    fetch('/api/admin/status', { cache: 'no-store' })
      .then(r => r.json())
      .then(async data => {
        setStatus(data)
        if (data.authorized) {
          try {
            await loadAdminConfig()
          } catch (error) {
            setSaveMessage({ type: 'error', text: error instanceof Error ? error.message : '加载管理员配置失败' })
          }
        }
      })
      .catch(() => setStatus({ authorized: false, password_configured: false }))
      .finally(() => setLoading(false))
  }, [])

  const handleLogin = async () => {
    if (!password) {
      setLoginError('请输入管理员密码')
      return
    }
    setLoggingIn(true)
    setLoginError('')
    try {
      const res = await fetch('/api/admin/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password }),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.error || '登录失败')
      setPassword('')
      setStatus({ authorized: true, password_configured: true })
      await loadAdminConfig()
    } catch (error) {
      setLoginError(error instanceof Error ? error.message : '登录失败')
    } finally {
      setLoggingIn(false)
    }
  }

  const handleLogout = async () => {
    await fetch('/api/admin/logout', { method: 'POST' })
    setStatus({ authorized: false, password_configured: status?.password_configured ?? true })
    setAi(null)
    setProfile(null)
  }

  const updateAi = (key: string, value: unknown) => {
    setAi(prev => prev ? { ...prev, [key]: value } : prev)
  }

  const draftAi = (): AdminAi => {
    // 中转状态只用来显示，绝不发回后端；其余调参字段原样发回。
    const { relay: _relay, ...rest } = ai || {}
    return rest
  }

  const handleAiTest = async () => {
    if (saving) return
    setAiTest({ testing: true })
    try {
      const res = await fetch('/api/diagnostics/ai', { cache: 'no-store' })
      const data = await res.json()
      const check = Array.isArray(data.checks) ? data.checks[0] : null
      setAiTest({
        testing: false,
        ok: Boolean(res.ok && data.ok),
        message: check ? `${check.message}：${check.detail}` : (data.messages?.[0] || 'AI 接口未返回检测结果'),
      })
    } catch {
      setAiTest({ testing: false, ok: false, message: '无法连接本地检测接口，请确认后端正在运行。' })
    }
  }

  const handleSave = async () => {
    if (!ai || !profile) return
    setSaving(true)
    setSaveMessage(null)
    try {
      const res = await fetch('/api/admin/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ai: draftAi(), profile }),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.error || '保存失败')
      await loadAdminConfig()
      setSaveMessage({ type: 'success', text: '已保存，全局生效，用户直接体验结果。' })
    } catch (error) {
      setSaveMessage({ type: 'error', text: error instanceof Error ? error.message : '保存失败' })
    } finally {
      setSaving(false)
    }
  }

  const handleChangePassword = async () => {
    if (newPassword.length < 6) {
      setPasswordMessage({ type: 'error', text: '管理员密码至少 6 位' })
      return
    }
    if (newPassword !== confirmPassword) {
      setPasswordMessage({ type: 'error', text: '两次输入的密码不一致' })
      return
    }
    try {
      const res = await fetch('/api/admin/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ admin: { new_password: newPassword } }),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.error || '修改密码失败')
      setNewPassword('')
      setConfirmPassword('')
      setPasswordMessage({ type: 'success', text: '管理员密码已修改' })
    } catch (error) {
      setPasswordMessage({ type: 'error', text: error instanceof Error ? error.message : '修改密码失败' })
    }
  }

  if (loading || !status) {
    return <div className="flex items-center justify-center h-full text-muted text-sm">加载中...</div>
  }

  if (!status.password_configured) {
    return (
      <div className="flex h-full items-center justify-center">
        <div className="max-w-md rounded-2xl border border-card-border bg-surface p-6 text-center">
          <div className="text-sm font-black text-foreground">未设置管理员密码</div>
          <p className="mt-2 text-xs leading-6 text-muted">
            请在配置文件里设置 admin.password 后重启服务，管理员面板才能使用。
          </p>
          <a className="mt-4 inline-block text-xs text-primary underline" href="/">返回工作台</a>
        </div>
      </div>
    )
  }

  if (!status.authorized || !ai || !profile) {
    return (
      <div className="flex h-full items-center justify-center">
        <div className="w-full max-w-sm rounded-2xl border border-card-border bg-surface p-6">
          <div className="text-sm font-black text-foreground">管理员登录</div>
          <p className="mt-2 text-xs leading-6 text-muted">只有管理员能进这里，改动会全局生效。</p>
          <Input
            type="password"
            aria-label="管理员密码"
            className="mt-4"
            value={password}
            onChange={e => setPassword(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') handleLogin() }}
            placeholder="输入管理员密码"
          />
          {loginError && <p className="mt-2 rounded-lg bg-danger-soft px-3 py-2 text-xs text-danger">{loginError}</p>}
          <Button className="mt-4 w-full" size="sm" onClick={handleLogin} disabled={loggingIn}>
            {loggingIn ? '登录中...' : '登录'}
          </Button>
          <a className="mt-4 block text-center text-xs text-primary underline" href="/">返回工作台</a>
        </div>
      </div>
    )
  }

  const relay = ai.relay || {}
  const relayAiState = relay.ai_enabled === true ? '开启' : relay.ai_enabled === false ? '关闭' : '未知'

  return (
    <div className="mx-auto h-full max-w-3xl space-y-4 overflow-y-auto pr-4">
      <div className="flex items-center justify-between sticky top-0 bg-background z-10 py-2">
        <div>
          <div className="text-base font-black text-foreground">管理员面板</div>
          <p className="mt-1 text-xs text-muted">改动全局生效，用户直接体验结果。用户界面里不放这里的入口。</p>
        </div>
        <div className="flex items-center gap-2">
          {saveMessage && (
            <span className={`text-xs ${saveMessage.type === 'success' ? 'text-success' : 'text-danger'}`}>
              {saveMessage.text}
            </span>
          )}
          <Button variant="ghost" size="sm" onClick={handleLogout}>退出登录</Button>
          <Button size="sm" onClick={handleSave} disabled={saving}>{saving ? '保存中...' : '保存'}</Button>
        </div>
      </div>

      <SectionCard title="云端中转">
        <p className="rounded-xl border border-card-border bg-surface px-3 py-2 text-xs leading-5 text-muted">
          AI 走云端中转：Key 与模型由云端统一管理，安装包里不再带 Key。中转连不上时评分走本地规则，招呼语用固定模板，不会卡住。
        </p>
        <StatusRow label="中转地址" value={relay.url || '未配置'} />
        <StatusRow label="中转状态" value={relay.reachable ? '连接正常' : '连接失败'} />
        <StatusRow label="服务端服务商" value={relay.provider || '未知'} />
        <StatusRow label="服务端模型" value={relay.model || '未知'} />
        <StatusRow label="服务端 AI 开关" value={relayAiState} />
        <div className="rounded-2xl border border-card-border bg-surface p-3">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <div className="text-sm font-black text-foreground">AI 连接检测</div>
              <p className="mt-1 text-xs text-muted">不会消耗对话 Token；只检测中转健康接口。</p>
            </div>
            <Button variant="secondary" size="sm" onClick={handleAiTest} disabled={aiTest.testing}>
              {aiTest.testing ? '检测中...' : '测试连接'}
            </Button>
          </div>
          {aiTest.message && (
            <p className={`mt-2 rounded-lg px-3 py-2 text-xs ${aiTest.ok ? 'bg-success-soft text-success' : 'bg-danger-soft text-danger'}`}>
              {aiTest.message}
            </p>
          )}
        </div>
      </SectionCard>

      <SectionCard title="AI 功能开关">
        <div className="flex items-center justify-between rounded-lg border border-card-border bg-surface p-3">
          <div>
            <label className="text-xs font-bold text-foreground">按岗位定制简历</label>
            <p className="mt-1 text-xs text-muted">默认关闭；开启后投递时用 AI 按岗位改简历，失败发原简历。</p>
          </div>
          <Switch
            aria-label="按岗位定制简历"
            checked={ai.resume_tailor_enabled ?? false}
            onChange={v => updateAi('resume_tailor_enabled', v)}
          />
        </div>
        <div className="flex items-center justify-between gap-3">
          <div>
            <label className="text-xs text-foreground">AI 生成招呼语</label>
            <p className="mt-1 text-xs text-muted">关闭后使用你填写的固定文案，全自动投递时直接发送。</p>
          </div>
          <Switch
            aria-label="AI 生成招呼语"
            checked={profile.ai_greeting_enabled ?? true}
            onChange={value => setProfile({ ...profile, ai_greeting_enabled: value })}
          />
        </div>
        {(profile.ai_greeting_enabled ?? true) === false && (
          <Field label="固定招呼语">
            <textarea
              aria-label="固定招呼语"
              value={profile.fixed_greeting || ''}
              onChange={e => setProfile({ ...profile, fixed_greeting: e.target.value })}
              placeholder="填写你希望使用的招呼语"
              rows={3}
              maxLength={300}
              className="w-full resize-y rounded-md border border-card-border bg-surface px-3 py-2 text-sm text-foreground outline-none focus:border-primary"
            />
            <p className="mt-1 text-xs text-muted">保存后，新生成时原样使用，不调用 AI。已有文案保留，可逐条编辑。</p>
          </Field>
        )}
        {(profile.ai_greeting_enabled ?? true) && (
          <Field label="招呼语偏好">
            <textarea
              value={profile.greeting_preference || ''}
              onChange={e => setProfile({ ...profile, greeting_preference: e.target.value })}
              placeholder="例如：语气简洁；不要主动询问薪资；不要提能否出差"
              rows={3}
              maxLength={500}
              className="w-full resize-y rounded-md border border-card-border bg-surface px-3 py-2 text-sm text-foreground outline-none placeholder:text-muted focus:border-primary"
            />
            <p className="mt-1 text-xs text-muted">仅补充语气和内容偏好，不能覆盖真实简历与安全规则。</p>
          </Field>
        )}
        {(profile.ai_greeting_enabled ?? true) && (
          <div className="rounded-2xl border border-primary/20 bg-surface-muted p-4">
            <div className="flex items-center justify-between gap-4">
              <div>
                <label className="text-sm font-black text-foreground">招呼语质量提醒</label>
                <p className="mt-1 text-xs leading-5 text-muted">可选检查一次，只提供建议，不拒绝或自动改写文案。默认关闭。</p>
              </div>
              <Switch
                aria-label="招呼语质量提醒"
                checked={ai.greeting_style_suggestions ?? false}
                onChange={value => updateAi('greeting_style_suggestions', value)}
              />
            </div>
          </div>
        )}
      </SectionCard>

      <SectionCard title="管理员密码">
        <Field label="新密码（至少 6 位）">
          <Input
            type="password"
            aria-label="新管理员密码"
            value={newPassword}
            onChange={e => setNewPassword(e.target.value)}
            placeholder="输入新密码"
          />
        </Field>
        <Field label="确认新密码">
          <Input
            type="password"
            aria-label="确认新管理员密码"
            value={confirmPassword}
            onChange={e => setConfirmPassword(e.target.value)}
            placeholder="再输入一次"
          />
        </Field>
        {passwordMessage && (
          <p className={`rounded-lg px-3 py-2 text-xs ${passwordMessage.type === 'success' ? 'bg-success-soft text-success' : 'bg-danger-soft text-danger'}`}>
            {passwordMessage.text}
          </p>
        )}
        <Button variant="secondary" size="sm" onClick={handleChangePassword}>修改密码</Button>
      </SectionCard>
    </div>
  )
}
