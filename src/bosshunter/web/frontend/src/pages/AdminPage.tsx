import { useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card'
import { Loader2 } from 'lucide-react'

const AI_SERVICES = {
  anthropic: {
    label: 'Claude / Anthropic',
    provider: 'anthropic',
    baseUrl: '',
    defaultModel: 'claude-sonnet-4-6',
  },
  deepseek: {
    label: 'DeepSeek',
    provider: 'openai_compatible',
    baseUrl: 'https://api.deepseek.com',
    defaultModel: '',
  },
  doubao: {
    label: '豆包 / 火山方舟',
    provider: 'openai_compatible',
    baseUrl: 'https://ark.cn-beijing.volces.com/api/v3',
    defaultModel: '',
  },
  zhipu: {
    label: '智谱免费 (GLM-4.7-Flash)',
    provider: 'openai_compatible',
    baseUrl: 'https://open.bigmodel.cn/api/paas/v4',
    defaultModel: 'glm-4.7-flash',
  },
  custom: {
    label: '其他 OpenAI 兼容接口',
    provider: 'openai_compatible',
    baseUrl: '',
    defaultModel: '',
  },
  local: {
    label: '本地规则（不用 AI）',
    provider: 'local',
    baseUrl: '',
    defaultModel: '',
  },
} as const

type AiService = keyof typeof AI_SERVICES

type AdminAi = {
  service?: string
  provider?: string
  base_url?: string
  model?: string
  thinking?: string
  thinking_budget?: number
  timeout_seconds?: number
  scoring_concurrency?: number
  scoring_second_review?: boolean
  greeting_style_suggestions?: boolean
  resume_tailor_enabled?: boolean
  services?: Record<string, { api_key?: string; auth_token?: string; api_key_masked?: string; auth_token_masked?: string }>
}

type AdminProfile = {
  ai_greeting_enabled?: boolean
  fixed_greeting?: string
  greeting_preference?: string
}

type KeyInputs = Record<string, { api_key: string; auth_token: string }>

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

export default function AdminPage() {
  const [status, setStatus] = useState<{ authorized: boolean; password_configured: boolean } | null>(null)
  const [loading, setLoading] = useState(true)
  const [password, setPassword] = useState('')
  const [loginError, setLoginError] = useState('')
  const [loggingIn, setLoggingIn] = useState(false)

  const [ai, setAi] = useState<AdminAi | null>(null)
  const [profile, setProfile] = useState<AdminProfile | null>(null)
  const [keyInputs, setKeyInputs] = useState<KeyInputs>({})
  const [modelList, setModelList] = useState<{ loading: boolean; models: string[]; message?: string; error?: boolean }>({ loading: false, models: [] })
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
    setKeyInputs({})
  }

  const aiService = (ai?.service || (ai?.provider === 'openai_compatible' ? 'custom' : 'anthropic')) as AiService

  const updateAi = (key: string, value: unknown) => {
    setAi(prev => prev ? { ...prev, [key]: value } : prev)
  }

  const handleAiServiceChange = (service: AiService) => {
    if (!ai || service === aiService) return
    const preset = AI_SERVICES[service]
    setAi({ ...ai, service, provider: preset.provider, base_url: preset.baseUrl, model: preset.defaultModel })
    setModelList({ loading: false, models: [] })
    setAiTest({ testing: false })
  }

  const setKeyInput = (service: string, field: 'api_key' | 'auth_token', value: string) => {
    setKeyInputs(prev => {
      const current = prev[service] || { api_key: '', auth_token: '' }
      return { ...prev, [service]: { ...current, [field]: value } }
    })
  }

  const buildServices = (): Record<string, { api_key?: string; auth_token?: string }> => {
    const services: Record<string, { api_key?: string; auth_token?: string }> = {}
    for (const [service, typed] of Object.entries(keyInputs)) {
      const entry: { api_key?: string; auth_token?: string } = {}
      if (typed.api_key.trim()) entry.api_key = typed.api_key.trim()
      if (typed.auth_token.trim()) entry.auth_token = typed.auth_token.trim()
      if (Object.keys(entry).length) services[service] = entry
    }
    return services
  }

  const draftAi = (): AdminAi => {
    // 掩码字段只用来显示，绝不发回后端；只发管理员新填的 Key。
    const { services: _maskedServices, ...rest } = ai || {}
    const services = buildServices()
    return { ...rest, ...(Object.keys(services).length ? { services } : {}) }
  }

  const handleFetchModels = async () => {
    setModelList({ loading: true, models: [] })
    try {
      const res = await fetch('/api/admin/config/models', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ai: draftAi() }),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.error || '获取模型列表失败')
      if (!Array.isArray(data.models) || !data.models.every((model: unknown) => typeof model === 'string')) {
        throw new Error('模型列表格式不正确，可手动填写模型 ID')
      }
      setModelList({
        loading: false,
        models: data.models,
        message: data.models.length ? `已获取 ${data.models.length} 个模型，请选择或继续手动填写。` : '服务商未返回可用模型，可手动填写模型 ID。',
      })
    } catch (error) {
      setModelList({ loading: false, models: [], error: true, message: error instanceof Error ? error.message : '获取模型列表失败，请重试' })
    }
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
      setKeyInputs({})
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

  const maskedKey = ai.services?.[aiService]?.api_key_masked || ''
  const maskedToken = ai.services?.[aiService]?.auth_token_masked || ''

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

      <SectionCard title="AI 服务">
        <p className="rounded-xl border border-card-border bg-surface px-3 py-2 text-xs leading-5 text-muted">
          不用配也能跑：未配置 AI 服务时评分走本地规则，招呼语用固定模板。HR 自动回复和按岗位定制简历需要 AI 服务。
        </p>
        <Field label="提供商">
          <Select aria-label="提供商" value={aiService} onChange={e => handleAiServiceChange(e.target.value as AiService)}>
            {Object.entries(AI_SERVICES).map(([value, preset]) => (
              <option key={value} value={value}>{preset.label}</option>
            ))}
          </Select>
          <p className="mt-1 text-xs text-muted">
            {aiService === 'local'
              ? '不用联网等 AI：评分走本地规则，招呼语用固定模板。'
              : '切换提供商会自动填好协议、服务地址和默认模型。'}
          </p>
        </Field>
        {aiService === 'zhipu' && (
          <p className="rounded-xl border border-card-border bg-surface px-3 py-2 text-xs leading-5 text-muted">
            智谱免费（GLM-4.7-Flash）：模型长期免费；去 open.bigmodel.cn 注册拿 Key，新用户另有约 2000 万 token 额度可用更强模型；免费政策可能变，以官网为准；免费模型限并发，一天最多 120 次、一个一个调的量够用；限流或调用失败时自动回落本地规则，不会卡住。
          </p>
        )}
        {aiService === 'local' && (
          <p className="rounded-xl border border-card-border bg-surface px-3 py-2 text-xs leading-5 text-muted">
            本地规则：不调用任何 AI。评分按关键词、薪资、城市、学历这些规则算，招呼语用固定模板，定制简历发原简历。不用联网等 AI。
          </p>
        )}
        {aiService !== 'local' && (
          <>
            <Field label="API Key">
              <Input
                type="password"
                aria-label="API Key"
                value={keyInputs[aiService]?.api_key || ''}
                onChange={e => setKeyInput(aiService, 'api_key', e.target.value)}
                placeholder={maskedKey ? `已保存：${maskedKey}（留空保持不变）` : '填写该服务商的 API Key'}
              />
              <p className="mt-1 text-xs text-muted">
                Key 只存在后端配置文件里，不会完整显示在任何界面；留空表示沿用已保存的。注意：Key 随安装包分发，有心人能从程序里翻出来，只用免费模型，被刷就换 Key。
              </p>
            </Field>
            {aiService === 'anthropic' && (
              <Field label="Auth Token（可选）">
                <Input
                  type="password"
                  aria-label="Auth Token"
                  value={keyInputs[aiService]?.auth_token || ''}
                  onChange={e => setKeyInput(aiService, 'auth_token', e.target.value)}
                  placeholder={maskedToken ? `已保存：${maskedToken}（留空保持不变）` : '也可通过 Auth Token 授权'}
                />
              </Field>
            )}
            <Field label="模型名称">
              <div className="flex flex-wrap gap-2">
                <Input
                  aria-label="模型名称"
                  className="min-w-0 flex-1 basis-40"
                  value={ai.model || ''}
                  onChange={e => updateAi('model', e.target.value)}
                  placeholder="填写服务商当前支持的模型 ID"
                />
                <Button type="button" variant="secondary" disabled={modelList.loading} onClick={handleFetchModels}>
                  {modelList.loading && <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden="true" />}
                  {modelList.loading ? '获取中…' : '获取模型列表'}
                </Button>
              </div>
              {modelList.models.length > 0 && (
                <Select
                  aria-label="可用模型"
                  className="mt-2 pr-8 appearance-auto"
                  value={modelList.models.includes(ai.model || '') ? (ai.model || '') : ''}
                  onChange={e => updateAi('model', e.target.value)}
                >
                  <option value="" disabled>请选择模型</option>
                  {modelList.models.map(model => <option key={model} value={model}>{model}</option>)}
                </Select>
              )}
              <p role="status" className={`mt-1 text-xs ${modelList.error ? 'text-danger' : 'text-muted'}`}>
                {modelList.message || '按当前服务商与已保存的 Key 获取，无需先保存配置。'}
              </p>
            </Field>
            <Field label="Base URL">
              <Input
                value={ai.base_url || ''}
                onChange={e => updateAi('base_url', e.target.value)}
                placeholder="留空使用默认"
              />
              <p className="mt-1 text-xs text-muted">填写后优先生效；留空时使用服务商默认地址。</p>
            </Field>
          </>
        )}
        <div className="grid gap-4 md:grid-cols-2">
          <Field label="Thinking 模式">
            <Select value={ai.thinking || 'auto'} onChange={e => updateAi('thinking', e.target.value)}>
              <option value="auto">自动兼容（推荐）</option>
              <option value="disabled">强制关闭</option>
              <option value="enabled">强制开启</option>
              <option value="off">不发送参数</option>
            </Select>
            <p className="mt-1 text-xs text-muted">自动模式优先获取纯文本；接口不支持 thinking 参数时会安全回退。</p>
          </Field>
          <Field label="Thinking 预算 Token">
            <Input
              type="number"
              value={ai.thinking_budget || 2048}
              onChange={e => updateAi('thinking_budget', Number(e.target.value))}
              min={1024}
              max={32768}
              disabled={(ai.thinking || 'auto') !== 'enabled'}
            />
          </Field>
        </div>
        <Field label="AI 请求超时 (秒)">
          <Input
            type="number"
            value={ai.timeout_seconds || 180}
            onChange={e => updateAi('timeout_seconds', Number(e.target.value))}
            min={5}
            max={600}
          />
        </Field>
        <Field label="AI 评分并发数">
          <Select value={String(ai.scoring_concurrency || 1)} onChange={e => updateAi('scoring_concurrency', Number(e.target.value))}>
            {[1, 2, 3].map(value => <option key={value} value={value}>{value}</option>)}
          </Select>
          <p className="mt-1 text-xs text-muted">默认 1；提高并发会增加 API 限流风险。</p>
        </Field>
        <div className="flex items-center justify-between rounded-lg border border-card-border bg-surface p-3">
          <div>
            <label className="text-xs font-bold text-foreground">临界评分二次复核</label>
            <p className="mt-1 text-xs text-muted">默认关闭；开启后会增加 AI 调用次数。</p>
          </div>
          <Switch checked={ai.scoring_second_review ?? false} onChange={v => updateAi('scoring_second_review', v)} />
        </div>
        <div className="rounded-2xl border border-card-border bg-surface p-3">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <div className="text-sm font-black text-foreground">AI 连接检测</div>
              <p className="mt-1 text-xs text-muted">不会消耗对话 Token；检测已保存的 Key、Base URL 和服务可用性。</p>
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
