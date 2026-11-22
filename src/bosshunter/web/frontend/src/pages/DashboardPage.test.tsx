import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import DashboardPage from './DashboardPage'
import { UpdateProvider } from '@/hooks/useUpdate'
import type { WorkbenchTask } from '@/hooks/useDashboard'

let workbenchPayload: Record<string, unknown> = {}
let stopResponse: () => Response = () => jsonResponse({})
let preflightResponse: () => Response = () => jsonResponse({})

function jsonResponse(body: unknown, ok = true) {
  return new Response(JSON.stringify(body), {
    status: ok ? 200 : 500,
    headers: { 'Content-Type': 'application/json' },
  })
}

function buildTask(overrides: Partial<WorkbenchTask>): WorkbenchTask {
  return {
    id: 'task-1',
    mode: 'greet',
    label: '生成招呼语',
    status: 'running',
    logs: [],
    stop_requested: false,
    ...overrides,
  }
}

function baseWorkbench(overrides: Record<string, unknown> = {}) {
  return {
    funnel: {},
    funnel_today: {},
    pending_confirmation: [],
    pending_greetings: [],
    send_errors: [],
    needs_resume: [],
    send_quota: { daily_limit: 30, sent: 0, remaining: 30, exhausted: false },
    task: null,
    last_task: null,
    ...overrides,
  }
}

const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = String(input)
  if (url === '/api/workbench' && (!init?.method || init.method === 'GET')) {
    return jsonResponse(workbenchPayload)
  }
  if (url.includes('/stop') && init?.method === 'POST') {
    return stopResponse()
  }
  if (url.startsWith('/api/workbench/preflight')) {
    return preflightResponse()
  }
  return jsonResponse({})
})

describe('DashboardPage workbench task panel', () => {
  it('shows scoring progress for standalone scoring even before the first job completes', async () => {
    workbenchPayload = baseWorkbench({ task: buildTask({ mode: 'score', metrics: { ai_completed: 0, ai_total: 12 } }) })
    render(<UpdateProvider><DashboardPage view="workbench" /></UpdateProvider>)
    expect((await screen.findByRole('progressbar', { name: '打分进度' })).getAttribute('aria-valuenow')).toBe('0')
    expect(screen.getByText('已打 0 / 12 个 · 0%')).toBeTruthy()
  })

  it('groups all collection counters with separate parsing and saving failure records', async () => {
    workbenchPayload = baseWorkbench({ task: buildTask({ mode: 'full',
      metrics: { collect_seen: 8, collect_new: 2, collect_parse_failed: 1, collect_save_failed: 1 },
      progress: { outcome: 'running', platforms: { boss: {
        status: 'running', new: 2, target: null, seen: 8, duplicate: 2, filtered: 2, parse_failed: 1, save_failed: 1,
        message: '保存岗位失败：RuntimeError', error_records: [
          { kind: 'parse', title: '', company: '', city: '深圳', keyword: 'AI', page: 2, reason: '列表缺少有效链接' },
          { kind: 'save', title: '测试岗位', company: '测试公司', city: '深圳', keyword: 'AI', page: 2, reason: '保存岗位失败：RuntimeError' },
        ],
      } } },
    }) })
    render(<UpdateProvider><DashboardPage view="workbench" /></UpdateProvider>)
    fireEvent.click(await screen.findByText('找岗位的详情'))
    expect(within(screen.getByLabelText('任务状态摘要')).queryByText(/本轮扫描|本轮新增|保存失败/)).toBeNull()
    expect(screen.getByText('看了 8 · 新增 2 · 重复 2 · 筛掉的 2 · 没看懂 1 · 没保存上 1')).toBeTruthy()
    const records = screen.getByRole('list', { name: 'BOSS 直聘出问题的记录' })
    expect(records.classList.contains('max-h-60')).toBe(true)
    expect(within(records).getByText('解析失败')).toBeTruthy()
    expect(within(records).getByText('没存上')).toBeTruthy()
    expect(within(records).getByText('测试岗位 · 测试公司')).toBeTruthy()
    expect(within(records).getAllByText('深圳 · AI · 第 2 页')).toHaveLength(2)
    expect(screen.getAllByText('保存岗位失败：RuntimeError')).toHaveLength(1)
  })

  it('keeps scoring details and a collection warning that differs from filtering records', async () => {
    workbenchPayload = baseWorkbench({ task: buildTask({ mode: 'full',
      metrics: { ai_completed: 2, ai_total: 3, ai_passed: 1, ai_filtered: 1 },
      progress: { outcome: 'scoring', platforms: { boss: {
        status: 'completed_with_shortage', new: 3, target: null, filtered: 1,
        message: '翻页未生效，已停止采集',
        filter_records: [{ title: '岗位', company: '公司', source_job_id: '1', reason: '薪资低于要求' }],
      } } },
    }) })
    render(<UpdateProvider><DashboardPage view="workbench" /></UpdateProvider>)
    fireEvent.click(await screen.findByText('看看详情'))
    const metrics = screen.getByLabelText('任务详细统计')
    expect(within(metrics).getByText('已打分')).toBeTruthy()
    expect(within(metrics).getByText('要打分的')).toBeTruthy()
    expect(within(metrics).getByText('打分筛掉的')).toBeTruthy()
    expect(within(metrics).queryByText('打分通过的')).toBeNull()
    fireEvent.click(screen.getByText('找岗位的详情'))
    expect(screen.getByText('翻页未生效，已停止采集')).toBeTruthy()
    expect(screen.getByText('薪资低于要求')).toBeTruthy()
  })

  it('shows bounded filtering details for a finished task without exposing job descriptions', async () => {
    workbenchPayload = baseWorkbench({ last_task: buildTask({ mode: 'full', status: 'completed',
      progress: { outcome: 'completed_with_shortage', platforms: { boss: {
        status: 'completed', new: 0, target: null, filtered: 201, message: '薪资低于硬性要求: 10K < 12K',
        filter_records: [{ title: '测试岗位', company: '测试公司', source_job_id: 'filtered-1', reason: '薪资低于硬性要求: 10K < 12K' }],
      } } },
    }) })
    render(<UpdateProvider><DashboardPage view="workbench" /></UpdateProvider>)
    fireEvent.click(await screen.findByText('找岗位的详情'))
    const records = screen.getByRole('list', { name: 'BOSS 直聘筛掉的记录' })
    expect(records.classList.contains('max-h-60')).toBe(true)
    expect(records.classList.contains('overflow-y-auto')).toBe(true)
    expect(records.tabIndex).toBe(0)
    expect(within(records).getByText('测试岗位 · 测试公司')).toBeTruthy()
    expect(screen.getAllByText('薪资低于硬性要求: 10K < 12K')).toHaveLength(1)
    expect(screen.getByText('留了 1 条，剩下的没留下来，或者是老版本找的')).toBeTruthy()
    expect(screen.getByText('筛掉的记录 · 201 条')).toBeTruthy()
    fireEvent.click(screen.getByText('筛掉的记录 · 201 条'))
    expect(records.closest('details')?.open).toBe(false)
  })

  it.each([false, true])('keeps collection statistics in their proper scope (multiple platforms: %s)', async multiple => {
    const state = { status: 'running', new: 32, seen: 42, target: null, filtered: 9,
      city: '深圳', keyword: 'AI', page: 3, max_pages: 6,
      message: 'BOSS 本轮搜索已结束；本轮新增 32 条，读取 42 条，重复 0 条，过滤 9 条，解析失败 0 条，保存失败 0 条' }
    workbenchPayload = baseWorkbench({ task: buildTask({ mode: 'full', logs: ['开始采集岗位'],
      metrics: { collect_seen: 42, collect_new: 32, collect_filtered: 9 },
      progress: { outcome: 'running', platforms: multiple ? { boss: state, zhilian: state } : { boss: state } },
    }) })
    render(<UpdateProvider><DashboardPage view="workbench" /></UpdateProvider>)
    await screen.findByLabelText('任务状态摘要')
    expect(screen.queryByText('看看详情')).toBeNull()
    expect(screen.queryByText('浏览器无反应时，请检查 BOSS 登录状态和 Chrome 连接。')).toBeNull()
    const details = screen.getByText('找岗位的详情').closest('details')!
    expect(details.open).toBe(false)
    expect(within(details.querySelector('summary')!).getAllByText(/新找到 32 条/)).toHaveLength(multiple ? 2 : 1)
    fireEvent.click(screen.getByText('找岗位的详情'))
    expect(details.open).toBe(true)
    expect(within(details).getAllByText('BOSS 本轮搜索已结束')).toHaveLength(multiple ? 2 : 1)
    expect(within(details).queryAllByText(/看了 42 · 新增 32/)).toHaveLength(multiple ? 2 : 1)
    expect(within(details).getAllByText(/筛掉的 9/)).toHaveLength(multiple ? 2 : 1)
  })

  beforeEach(() => {
    workbenchPayload = baseWorkbench()
    stopResponse = () => jsonResponse({})
    preflightResponse = () => jsonResponse({})
    vi.stubGlobal('fetch', fetchMock)
    fetchMock.mockClear()
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('starts an interactive guide from an empty workbench and reuses preflight', async () => {
    render(<UpdateProvider><DashboardPage view="workbench" /></UpdateProvider>)

    const guide = await screen.findByRole('region', { name: '开始使用' })
    expect(within(guide).getByText(/先在浏览器里登录好招聘网站/)).toBeTruthy()
    expect(within(guide).getByText(/只想先搜搜岗位/)).toBeTruthy()
    const started = vi.fn()
    window.addEventListener('jianli-lieshou:start-onboarding', started, { once: true })
    fireEvent.click(within(guide).getByRole('button', { name: '带我走一遍' }))
    expect(started).toHaveBeenCalledOnce()

    fireEvent.click(within(guide).getByRole('button', { name: '检查一下能不能跑' }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/workbench/preflight?mode=full', expect.anything()))
  })

  it('does not keep the getting started card above the workbench after jobs have been collected', async () => {
    workbenchPayload = baseWorkbench({ funnel: { collected: 2 } })
    render(<UpdateProvider><DashboardPage view="workbench" /></UpdateProvider>)

    expect(await screen.findByRole('heading', { name: '今天要做的事' })).toBeTruthy()
    expect(screen.queryByRole('region', { name: '开始使用' })).toBeNull()
  })

  it('shows setup check failures next to the getting started steps', async () => {
    preflightResponse = () => jsonResponse({
      ok: false,
      checks: [{
        id: 'resume', title: '简历文件', status: 'error', message: '尚未配置有效简历',
        detail: '打开“配置 → 个人信息”，上传简历。', action: 'config',
      }],
    })
    render(<UpdateProvider><DashboardPage view="workbench" /></UpdateProvider>)

    const guide = await screen.findByRole('region', { name: '开始使用' })
    fireEvent.click(within(guide).getByRole('button', { name: '检查一下能不能跑' }))
    const results = await within(guide).findByRole('region', { name: '启动检查结果' })
    expect(within(results).getByRole('status').textContent).toContain('尚未配置有效简历')
  })

  it('keeps the preflight check reachable after the getting started card disappears', async () => {
    workbenchPayload = baseWorkbench({ funnel: { collected: 2 } })
    render(<UpdateProvider><DashboardPage view="workbench" /></UpdateProvider>)

    expect(await screen.findByRole('heading', { name: '今天要做的事' })).toBeTruthy()
    expect(screen.queryByRole('region', { name: '开始使用' })).toBeNull()
    // 引导区没了，标题栏里只剩一份检查入口，点的还是同一个预检。
    const checks = screen.getAllByRole('button', { name: '检查一下能不能跑' })
    expect(checks).toHaveLength(1)
    fireEvent.click(checks[0])
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/workbench/preflight?mode=full', expect.anything()))
  })

  it('points at the platform login buttons instead of a one-click launch when the browser runtime is down', async () => {
    // v27：“一键启动浏览器”按钮已删，拉起统一走各平台的“去登录”。
    preflightResponse = () => jsonResponse({
      ok: false,
      checks: [{
        id: 'browser_runtime', title: '浏览器运行组件', status: 'error',
        message: '简历猎手浏览器运行组件未启动',
        detail: '点任意一个平台的“去登录”，程序会自动打开工作浏览器并连上；还不行就点“再检查一次”。',
        action: 'browser',
      }],
    })
    render(<UpdateProvider><DashboardPage view="workbench" /></UpdateProvider>)

    const guide = await screen.findByRole('region', { name: '开始使用' })
    fireEvent.click(within(guide).getByRole('button', { name: '检查一下能不能跑' }))
    const results = await within(guide).findByRole('region', { name: '启动检查结果' })
    expect(within(results).getByRole('status').textContent).toContain('浏览器运行组件未启动')
    expect(within(results).queryByRole('button', { name: '一键启动浏览器' })).toBeNull()
    expect(within(results).getByText(/点任意一个平台的“去登录”/)).toBeTruthy()
    const preflightCalls = () => fetchMock.mock.calls.filter(call => String(call[0]) === '/api/workbench/preflight?mode=full').length
    expect(preflightCalls()).toBe(1)
    fireEvent.click(within(results).getByRole('button', { name: '再检查一次' }))
    await waitFor(() => expect(preflightCalls()).toBeGreaterThanOrEqual(2))
    expect(fetchMock).not.toHaveBeenCalledWith('/api/browser/launch', expect.anything())
  })

  it('reports login-button feedback in the notice when the work browser starts', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) === '/api/browser/open') {
        return jsonResponse({ ok: true, target_id: 't-1', browser_started: true, reused_tab: false, message: '已在工作浏览器打开。' })
      }
      return fetchMock(input, init)
    }))
    const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null)
    render(<UpdateProvider><DashboardPage view="workbench" /></UpdateProvider>)

    const guide = await screen.findByRole('region', { name: '开始使用' })
    fireEvent.click(within(guide).getByRole('button', { name: '去BOSS 直聘登录' }))
    await waitFor(() => expect(
      screen.getAllByText('工作浏览器已启动，正在打开BOSS 直聘登录页。').length,
    ).toBeGreaterThanOrEqual(1))
    expect(fetchMock).not.toHaveBeenCalledWith('/api/browser/launch', expect.anything())
    expect(openSpy).not.toHaveBeenCalled()
  })

  it('shows per-job greet progress from task logs', async () => {
    workbenchPayload = baseWorkbench({
      task: buildTask({
        status: 'running',
        logs: ['开始为 3 个岗位生成招呼语', '生成招呼语 (2/3)：字节跳动｜后端工程师'],
      }),
    })
    render(<UpdateProvider><DashboardPage view="workbench" /></UpdateProvider>)
    expect(within(await screen.findByLabelText('任务状态摘要')).getByText('生成招呼语 (2/3)：字节跳动｜后端工程师')).toBeTruthy()
  })

  it('keeps outsourcing evidence visible before greeting review and sending', async () => {
    workbenchPayload = baseWorkbench({ pending_greetings: [{
      id: 'outsourcing-preview', company: '合成供应商', title: '测试岗位', status: 'ready',
      greeting: '原始招呼语', greeting_original: '原始招呼语', greeting_optimized: '优化招呼语',
      greeting_selection: 'pending', greeting_style_issues: ['表达可以更简洁'],
      outsourcing_level: 'confirmed', outsourcing_matches: ['合成供应商'],
    }] })
    render(<UpdateProvider><DashboardPage view="workbench" /></UpdateProvider>)
    const toggle = await screen.findByRole('button', { name: '展开打招呼的话：合成供应商｜测试岗位' })
    expect(within(toggle).getByLabelText('外包；命中：合成供应商')).toBeTruthy()
    fireEvent.click(toggle)
    expect(screen.getByLabelText('外包；命中：合成供应商')).toBeTruthy()
    expect(screen.getByText('表达可以更简洁')).toBeTruthy()
    expect((screen.getByRole('button', { name: '发这条打招呼' }) as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getByRole('button', { name: '就用改完的' })).toBeTruthy()
  })

  it('shows the latest queue summary and preserves full progress in expandable details', async () => {
    const latestProgress = '招呼语进度：2/3\n成功 1，失败 1，待处理 1'
    workbenchPayload = baseWorkbench({
      task: buildTask({
        mode: 'full',
        label: '全流程',
        logs: ['招呼语进度：1/3', latestProgress, '正在等待下一次发送窗口'],
      }),
    })
    render(<UpdateProvider><DashboardPage view="workbench" /></UpdateProvider>)
    const summary = await screen.findByLabelText('任务状态摘要')
    expect(within(summary).getByText('招呼语进度：2/3')).toBeTruthy()
    fireEvent.click(screen.getByText('看看详情'))
    const progress = screen.getByText(latestProgress, { normalizer: text => text })
    expect(progress.textContent).toBe(latestProgress)
    expect(progress.classList.contains('whitespace-pre-line')).toBe(true)
    expect(progress.closest('details:not([open]), [hidden], [aria-hidden="true"]')).toBeNull()
    expect(screen.queryByText('招呼语进度：1/3')).toBeNull()
    expect(screen.getByRole('button', { name: '停下' })).toBeTruthy()
  })

  it('shows the pause reason when a greet task completed with partial success', async () => {
    workbenchPayload = baseWorkbench({
      last_task: buildTask({
        status: 'completed',
        metrics: {
          greet_generated: 3,
          greet_preserved: 1,
          greet_failed: 0,
          greet_paused: 1,
          greet_pause_reason: 'AI Token 额度或账户余额不足 (token_quota, status=402)',
        },
      }),
    })
    render(<UpdateProvider><DashboardPage view="workbench" /></UpdateProvider>)
    expect(await screen.findByText('提前停下的原因：额度或余额不够了。写好的都留着，剩下的岗位下次继续。')).toBeTruthy()
    expect(screen.getByText('提前停下的')).toBeTruthy()
  })

  it('shows failure feedback and the raw error for a zero-output greet task', async () => {
    workbenchPayload = baseWorkbench({
      last_task: buildTask({
        status: 'failed',
        error: '招呼语生成已安全暂停：AI 服务触发请求或 Token 频率限制 (rate_limit, status=429)',
      }),
    })
    const { container } = render(<UpdateProvider><DashboardPage view="workbench" /></UpdateProvider>)
    expect(await screen.findByText('任务运行失败')).toBeTruthy()
    expect(container.textContent).toContain('招呼语生成已安全暂停：AI 服务触发请求或 Token 频率限制 (rate_limit, status=429)')
  })

  it('recovers the notice and surfaces the real error when stopping fails', async () => {
    workbenchPayload = baseWorkbench({
      task: buildTask({ mode: 'collect', label: '单独采集', status: 'running' }),
    })
    stopResponse = () => jsonResponse({ error: '任务已结束，无法停止' }, false)
    const confirmSpy = vi.fn(() => true)
    vi.stubGlobal('confirm', confirmSpy)
    render(<UpdateProvider><DashboardPage view="workbench" /></UpdateProvider>)
    const stopButton = await screen.findByRole('button', { name: '停下' })
    fireEvent.click(stopButton)
    expect(confirmSpy).toHaveBeenCalled()
    await waitFor(() => {
      expect(screen.getAllByText('单独采集没停下来：任务已结束，无法停止').length).toBeGreaterThan(0)
    })
  })

  it('confirms the stop request when the backend accepts it', async () => {
    workbenchPayload = baseWorkbench({
      task: buildTask({ mode: 'collect', label: '单独采集', status: 'running' }),
    })
    stopResponse = () => jsonResponse(buildTask({ mode: 'collect', label: '单独采集', status: 'stopping', stop_requested: true }))
    vi.stubGlobal('confirm', () => true)
    render(<UpdateProvider><DashboardPage view="workbench" /></UpdateProvider>)
    const stopButton = await screen.findByRole('button', { name: '停下' })
    fireEvent.click(stopButton)
    await waitFor(() => {
      expect(screen.getAllByText('单独采集已经让它停下了。').length).toBeGreaterThan(0)
    })
  })

  it('lists the actual failing preflight items in the notice and expands the panel with a fix button', async () => {
    preflightResponse = () => jsonResponse({
      ok: false,
      checks: [
        { id: 'resume', title: '简历文件', status: 'error', message: '尚未配置有效简历', detail: '打开“配置 → 个人信息”，上传简历。', action: 'config' },
        { id: 'search_keywords', title: '搜索关键词', status: 'error', message: '尚未填写搜索关键词', detail: '打开“配置 → 搜索设置”，至少添加一个岗位关键词。', action: 'config' },
      ],
    })
    render(<UpdateProvider><DashboardPage view="workbench" /></UpdateProvider>)

    const guide = await screen.findByRole('region', { name: '开始使用' })
    fireEvent.click(within(guide).getByRole('button', { name: '检查一下能不能跑' }))
    const results = await within(guide).findByRole('region', { name: '启动检查结果' })
    // 失败提示列具体项，不用点“看看详情”就能看到详情和去修复的按钮。
    await waitFor(() => expect(screen.getAllByText('启动前预检未通过：尚未配置有效简历；尚未填写搜索关键词。').length).toBeGreaterThan(0))
    expect(within(results).getByText('打开“配置 → 个人信息”，上传简历。')).toBeTruthy()
    expect(within(results).getByRole('button', { name: '去传简历' })).toBeTruthy()
    expect(within(results).getAllByRole('button', { name: '去设置' }).length).toBeGreaterThan(0)
  })

  it('tells the user to wait instead of silently swallowing a second mode click', async () => {
    render(<UpdateProvider><DashboardPage view="workbench" /></UpdateProvider>)
    const monitorCard = await screen.findByRole('button', { name: /只看 HR 回复/ })
    fireEvent.click(monitorCard)
    fireEvent.click(monitorCard)
    expect(screen.getAllByText('正在处理上一个操作，请稍等一下。').length).toBeGreaterThan(0)
  })

  it('shows a force-stop button after stopping for 10 seconds and reports success', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      workbenchPayload = baseWorkbench({
        task: buildTask({ mode: 'collect', label: '单独采集', status: 'stopping', stop_requested: true }),
      })
      vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        if (String(input) === '/api/workbench/force-stop' && init?.method === 'POST') {
          return jsonResponse({ ok: true, status: 'stopped' })
        }
        return fetchMock(input, init)
      }))
      render(<UpdateProvider><DashboardPage view="workbench" /></UpdateProvider>)
      expect(await screen.findByRole('button', { name: '正在停下...' })).toBeTruthy()
      expect(screen.queryByRole('button', { name: '强制停止' })).toBeNull()
      await act(async () => { vi.advanceTimersByTime(10000) })
      const forceButton = await screen.findByRole('button', { name: '强制停止' })
      fireEvent.click(forceButton)
      await waitFor(() => {
        expect(screen.getAllByText('单独采集已经停下了。').length).toBeGreaterThan(0)
      })
    } finally {
      vi.useRealTimers()
    }
  })
  it('displays the final edited version alongside both generated candidates', async () => {
    workbenchPayload = baseWorkbench({ pending_greetings: [{
      id: 'final-preview', company: '测试公司', title: '测试岗位', status: 'ready',
      greeting: '手动修改后最终发送的文字', greeting_original: 'AI 原始候选',
      greeting_optimized: 'AI 优化候选', greeting_selection: 'edited',
      greeting_reviewed_at: '2026-09-09 12:00:00',
    }] })
    render(<UpdateProvider><DashboardPage view="workbench" /></UpdateProvider>)
    fireEvent.click(await screen.findByRole('button', { name: '展开打招呼的话：测试公司｜测试岗位' }))
    const finalVersion = await screen.findByLabelText('最后发出去的版本')
    expect(within(finalVersion).getByText('手动修改后最终发送的文字')).toBeTruthy()
    expect(screen.getByText('AI 原始候选')).toBeTruthy()
    expect(screen.getByText('AI 优化候选')).toBeTruthy()
  })

  it('blocks individual and batch sends until an edit is saved and refreshed', async () => {
    const job = {
      id: 'saving-preview', company: '测试公司', title: '测试岗位', status: 'ready',
      greeting: '原始版本', greeting_original: '原始版本', greeting_optimized: '优化版本',
      greeting_selection: 'original', greeting_reviewed_at: '2026-09-09 12:00:00',
    }
    workbenchPayload = baseWorkbench({ pending_greetings: [job] })
    render(<UpdateProvider><DashboardPage view="workbench" /></UpdateProvider>)
    fireEvent.click(await screen.findByRole('button', { name: '展开打招呼的话：测试公司｜测试岗位' }))
    fireEvent.click(await screen.findByRole('button', { name: '手动改一改' }))
    const send = screen.getByRole('button', { name: '发这条打招呼' }) as HTMLButtonElement
    const batchSend = screen.getByRole('button', { name: '发出去 1 个' }) as HTMLButtonElement
    expect(send.disabled).toBe(true)
    expect(batchSend.disabled).toBe(true)
    fireEvent.change(screen.getByLabelText('自己改改最后发的版本'), { target: { value: '保存后真正发送的文字' } })
    let finishSave!: (response: Response) => void
    fetchMock.mockImplementationOnce(() => new Promise<Response>(resolve => { finishSave = resolve }))
    fireEvent.click(screen.getByRole('button', { name: '保存我改的' }))
    expect(send.disabled).toBe(true)
    expect(batchSend.disabled).toBe(true)
    const saved = { ...job, greeting: '保存后真正发送的文字', greeting_selection: 'edited' }
    workbenchPayload = baseWorkbench({ pending_greetings: [saved] })
    finishSave(jsonResponse(saved))
    await waitFor(() => expect(send.disabled).toBe(false))
    expect(batchSend.disabled).toBe(false)
    expect(within(screen.getByLabelText('最后发出去的版本')).getByText(saved.greeting)).toBeTruthy()
  })

  it('keeps candidates collapsed and prevents sending before a version is chosen', async () => {
    workbenchPayload = baseWorkbench({ pending_greetings: [{
      id: 'pending-preview', company: '测试公司', title: '测试岗位', status: 'ready',
      greeting: '待确认原文', greeting_original: '待确认原文',
      greeting_optimized: '优化候选', greeting_selection: 'pending',
    }] })
    render(<UpdateProvider><DashboardPage view="workbench" /></UpdateProvider>)
    const toggle = await screen.findByRole('button', { name: '展开打招呼的话：测试公司｜测试岗位' })
    expect(toggle.getAttribute('aria-expanded')).toBe('false')
    expect(screen.queryByText('优化候选')).toBeNull()
    expect(screen.queryByRole('button', { name: '发这条打招呼' })).toBeNull()
    expect((screen.getByRole('button', { name: '发出去 0 个' }) as HTMLButtonElement).disabled).toBe(true)
    fireEvent.click(toggle)
    expect(screen.getByText('优化候选')).toBeTruthy()
    expect((screen.getByRole('button', { name: '发这条打招呼' }) as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getByRole('button', { name: '就用原来的' })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: '收起来打招呼的话：测试公司｜测试岗位' }))
    expect(screen.queryByText('优化候选')).toBeNull()
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(0)
  })

  it.each(['full', 'monitor', 'greet', 'deliver'] as const)('allows version selection during an unrelated %s task without stopping or sending', async (mode) => {
    const job = {
      id: 'editable-preview', company: '测试公司', title: '测试岗位', status: 'ready',
      greeting: '待确认原文', greeting_original: '待确认原文',
      greeting_optimized: '优化候选', greeting_selection: 'pending',
    }
    workbenchPayload = baseWorkbench({ task: buildTask({ mode }), pending_greetings: [job] })
    render(<UpdateProvider><DashboardPage view="workbench" /></UpdateProvider>)
    fireEvent.click(await screen.findByRole('button', { name: '展开打招呼的话：测试公司｜测试岗位' }))
    expect((screen.getByRole('button', { name: '就用原来的' }) as HTMLButtonElement).disabled).toBe(false)
    expect((screen.getByRole('button', { name: '手动改一改' }) as HTMLButtonElement).disabled).toBe(false)
    expect((screen.getByRole('button', { name: '发这条打招呼' }) as HTMLButtonElement).disabled).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: '就用改完的' }))
    await waitFor(() => expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST').length).toBe(1))
    const posts = fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST')
    expect(String(posts[0][0])).toBe('/api/jobs/editable-preview/greeting-selection')
    expect(JSON.parse(String(posts[0][1]?.body))).toEqual({ selection: 'optimized', greeting: '', confirmed: true })
    expect(screen.queryByRole('button', { name: '停止任务后选择' })).toBeNull()
  })

  it.each(['sending', 'generating'] as const)('locks only the greeting currently %s and explains why', async (activity) => {
    const job = {
      id: 'busy-preview', company: '当前公司', title: '测试岗位', status: 'ready',
      greeting: '原文', greeting_original: '原文', greeting_optimized: '候选', greeting_selection: 'pending',
    }
    workbenchPayload = baseWorkbench({ task: buildTask({ mode: 'full' }), pending_greetings: [
      { ...job, greeting_activity: activity }, { ...job, id: 'free-preview', company: '其他公司' },
    ] })
    render(<UpdateProvider><DashboardPage view="workbench" /></UpdateProvider>)
    fireEvent.click(await screen.findByRole('button', { name: '展开打招呼的话：当前公司｜测试岗位' }))
    expect((screen.getByRole('button', { name: '就用原来的' }) as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getByText(activity === 'sending' ? '这条正在发，现在改不了。' : '这条正在写，写好就能挑。')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: '收起来打招呼的话：当前公司｜测试岗位' }))
    fireEvent.click(screen.getByRole('button', { name: '展开打招呼的话：其他公司｜测试岗位' }))
    expect((screen.getByRole('button', { name: '就用原来的' }) as HTMLButtonElement).disabled).toBe(false)
    expect((screen.getByRole('button', { name: '手动改一改' }) as HTMLButtonElement).disabled).toBe(false)
  })

  it('summarizes collection limits while keeping full diagnostics in closed details', async () => {
    workbenchPayload = baseWorkbench({ task: buildTask({
      mode: 'full', metrics: { collect_seen: 327, collect_new: 150, collect_parse_failed: 0, collect_save_failed: 2 },
      progress: { outcome: 'completed_with_shortage', platforms: { boss: {
        status: 'completed', new: 150, target: null, seen: 327,
        reason_code: 'safety_limit', message: 'BOSS 采集已达安全上限：daily_detail_page_limit；读取 327 条',
      } } },
    }) })
    render(<UpdateProvider><DashboardPage view="workbench" /></UpdateProvider>)
    const reason = await screen.findByText('今天能打开的岗位详情用完了')
    expect(reason.closest('details')?.open).toBe(false)
    expect(screen.getByText('找完了，但新岗位不多')).toBeTruthy()
    expect(screen.queryByText('completed_with_shortage')).toBeNull()
    const metrics = screen.getByLabelText('任务关键统计')
    expect(within(metrics).queryByText('保存失败 2')).toBeNull()
    expect(within(metrics).queryByText('解析失败')).toBeNull()
    expect(screen.queryByText('看看详情')).toBeNull()
  })

})

describe('DashboardPage scoring dialog confirm behaviour', () => {
  const scoringPreview = {
    eligible_jobs: 3,
    skipped_jobs: 0,
    first_attempt_requests: 3,
    max_attempts_per_job: 2,
    max_possible_requests: 6,
    note: '',
  }

  function stubDashboardFetch(overrides: Record<string, () => Response | undefined> = {}) {
    const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const override = overrides[url]
      if (override) {
        const response = override()
        if (response) return response
      }
      if (url === '/api/workbench' && (!init?.method || init.method === 'GET')) return jsonResponse(workbenchPayload)
      if (url.startsWith('/api/jobs/search')) return jsonResponse({ items: [], total: 0, all_total: 0 })
      if (url === '/api/scoring/preview' && init?.method === 'POST') return jsonResponse(scoringPreview)
      if (url === '/api/scoring/runs') return jsonResponse([])
      if (url === '/api/scoring/start' && init?.method === 'POST') {
        return jsonResponse({ run: { remaining_job_ids: ['j1', 'j2', 'j3'] } })
      }
      return jsonResponse({})
    })
    vi.stubGlobal('fetch', fn)
    return fn
  }

  beforeEach(() => {
    workbenchPayload = baseWorkbench()
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  async function openScoringDialog() {
    render(<UpdateProvider><DashboardPage view="jobs" /></UpdateProvider>)
    fireEvent.click(await screen.findByText('打分设置'))
    expect(await screen.findByText('符合条件的')).toBeTruthy()
  }

  it('closes the scoring dialog and shows the started notice after scoring starts', async () => {
    stubDashboardFetch()
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    await openScoringDialog()
    fireEvent.click(screen.getByText('开始打分'))
    expect((await screen.findAllByText('打分开始了，一共 3 个岗位。')).length).toBeGreaterThan(0)
    await waitFor(() => expect(screen.queryByText('开始打分')).toBeNull())
  })

  it('keeps the progress visible in the jobs pool after the scoring dialog closes', async () => {
    stubDashboardFetch({
      '/api/scoring/start': () => {
        workbenchPayload = baseWorkbench({ task: buildTask({ mode: 'score', metrics: { ai_total: 3, ai_completed: 0 } }) })
        return jsonResponse({ run: { remaining_job_ids: ['a', 'b', 'c'] } })
      },
    })
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    await openScoringDialog()
    fireEvent.click(screen.getByText('开始打分'))
    await waitFor(() => expect(screen.queryByText('开始打分')).toBeNull())
    expect((await screen.findByRole('progressbar', { name: '打分进度' })).getAttribute('aria-valuenow')).toBe('0')
  })

  it('refreshes progress, job scores and counts without losing the selection or requiring a page reload', async () => {
    vi.useFakeTimers()
    try {
      let completed = 0
      const job = { id: 'live-job', company: '测试公司', title: '测试岗位', city: '上海', score: 0, status: 'pending', salary: '20-30K', created_at: '' }
      const normalFetch = stubDashboardFetch()
      vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input)
        if (url.startsWith('/api/jobs/search')) return jsonResponse({
          items: [{ ...job, score: completed ? 88 : 0, status: completed ? 'scored' : 'pending' }],
          total: completed ? 1 : 2, all_total: 2,
        })
        if (url === '/api/workbench') return jsonResponse(baseWorkbench({
          task: completed === 2 ? null : buildTask({ mode: 'score', metrics: { ai_completed: completed, ai_total: 2 } }),
          last_task: completed === 2 ? buildTask({ mode: 'score', status: 'completed', metrics: { ai_completed: 2, ai_total: 2 } }) : null,
        }))
        return normalFetch(input, init)
      }))
      await act(async () => { render(<UpdateProvider><DashboardPage view="jobs" /></UpdateProvider>) })
      fireEvent.click(screen.getByRole('checkbox', { name: '选这个 测试公司 测试岗位' }))
      expect(screen.getByText('找到 2 个 / 一共 2 个')).toBeTruthy()
      completed = 1
      await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
      expect(screen.getByRole('progressbar', { name: '打分进度' }).getAttribute('aria-valuenow')).toBe('50')
      expect(screen.getByText('88')).toBeTruthy()
      expect(screen.getByText('找到 1 个 / 一共 2 个')).toBeTruthy()
      expect((screen.getByRole('checkbox', { name: '选这个 测试公司 测试岗位' }) as HTMLInputElement).checked).toBe(true)
      expect(screen.queryByText('加载中…')).toBeNull()
      completed = 2
      await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
      expect(screen.getByRole('progressbar', { name: '打分进度' }).getAttribute('aria-valuenow')).toBe('100')
      expect(screen.getByText('这轮打分 · 这轮打完了')).toBeTruthy()
    } finally {
      cleanup()
      vi.useRealTimers()
    }
  })

  it('keeps the scoring dialog open and shows the failure inside it when the scoring start fails', async () => {
    stubDashboardFetch({
      '/api/scoring/start': () => jsonResponse({ error: 'AI 额度不足' }, false),
    })
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    await openScoringDialog()
    fireEvent.click(screen.getByText('开始打分'))
    expect(await screen.findByText('AI 额度不足')).toBeTruthy()
    expect(screen.getByText('开始打分')).toBeTruthy()
  })

  it('quick scoring submits exactly the selected jobs, never all pending jobs', async () => {
    // v38 回归：选 15 个点“给这些岗位打分”却跑出 25 个——快捷按钮写死
    // scope 'pending'。这里用 2 个岗位复现同一口径：提交的 job_ids 必须
    // 严格等于勾选集合，提示里的总数也必须等于勾选数。
    const jobs = [
      { id: 'job-a', company: '甲公司', title: '岗位A', city: '上海', score: 0, status: 'pending', salary: '20-30K', created_at: '' },
      { id: 'job-b', company: '乙公司', title: '岗位B', city: '上海', score: 0, status: 'pending', salary: '20-30K', created_at: '' },
    ]
    const fetchFn = stubDashboardFetch({
      '/api/scoring/start': () => jsonResponse({ run: { remaining_job_ids: ['job-a', 'job-b'] } }),
    })
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.startsWith('/api/jobs/search')) return jsonResponse({ items: jobs, total: 2, all_total: 2 })
      return fetchFn(input, init)
    }))
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    render(<UpdateProvider><DashboardPage view="jobs" /></UpdateProvider>)
    fireEvent.click(await screen.findByRole('checkbox', { name: '选这个 甲公司 岗位A' }))
    fireEvent.click(await screen.findByRole('checkbox', { name: '选这个 乙公司 岗位B' }))
    expect(await screen.findByText('已选 2 个')).toBeTruthy()
    fireEvent.click(screen.getByText('给选中的岗位打分'))
    expect((await screen.findAllByText('打分开始了，一共 2 个岗位。')).length).toBeGreaterThan(0)
    const startCalls = fetchFn.mock.calls.filter(call => String(call[0]) === '/api/scoring/start')
    expect(startCalls).toHaveLength(1)
    expect(JSON.parse(String(startCalls[0][1]?.body)).options).toEqual({
      scope: 'selected', limit: null, job_ids: ['job-a', 'job-b'], force_rescore: false,
    })
  })

  it('quick scoring stays disabled until jobs are selected', async () => {
    const jobs = [
      { id: 'job-a', company: '甲公司', title: '岗位A', city: '上海', score: 0, status: 'pending', salary: '20-30K', created_at: '' },
    ]
    const fetchFn = stubDashboardFetch()
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.startsWith('/api/jobs/search')) return jsonResponse({ items: jobs, total: 1, all_total: 1 })
      return fetchFn(input, init)
    }))
    render(<UpdateProvider><DashboardPage view="jobs" /></UpdateProvider>)
    const button = await screen.findByText('给选中的岗位打分')
    expect((button.closest('button') as HTMLButtonElement | null)?.disabled).toBe(true)
    expect(button.closest('button')?.getAttribute('title')).toBe('先勾选要打分的岗位')
  })
})

describe('DashboardPage persisted preflight and platform login status', () => {
  beforeEach(() => {
    workbenchPayload = baseWorkbench()
    preflightResponse = () => jsonResponse({})
    vi.stubGlobal('fetch', fetchMock)
    fetchMock.mockClear()
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('reuses the last preflight result instead of showing “not checked yet”', async () => {
    workbenchPayload = baseWorkbench({
      last_preflight: {
        ran_at: '2026-11-19T08:00:00',
        checks: [{
          id: 'resume', title: '简历文件', status: 'error', message: '尚未配置有效简历',
          detail: '打开“配置 → 个人信息”，上传简历。', action: 'config',
        }],
      },
    })
    render(<UpdateProvider><DashboardPage view="workbench" /></UpdateProvider>)
    const guide = await screen.findByRole('region', { name: '开始使用' })
    const results = await within(guide).findByRole('region', { name: '启动检查结果' })
    expect(within(results).getByRole('status').textContent).toContain('尚未配置有效简历')
    expect(fetchMock.mock.calls.some(call => String(call[0]) === '/api/workbench/preflight?mode=full')).toBe(false)
  })

  it('shows the always-on platform login status and a relogin shortcut for the logged-out one', async () => {
    const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null)
    // 行必须用新鲜时间戳：超过 10 分钟没更新的旧值会被卡片按“没查过”处理。
    const checkedAt = new Date().toISOString()
    workbenchPayload = baseWorkbench({
      platform_login: {
        boss: { status: 'logged_in', checked_at: checkedAt, detail: '' },
        zhilian: { status: 'logged_out', checked_at: checkedAt, detail: '登录过期了' },
        '51job': { status: 'unknown' },
        liepin: { status: 'logged_in', checked_at: checkedAt, detail: '' },
      },
    })
    render(<UpdateProvider><DashboardPage view="workbench" /></UpdateProvider>)
    const statusCard = await screen.findByRole('region', { name: '各平台登录状态' })
    expect(within(statusCard).getByText('已掉线')).toBeTruthy()
    expect(within(statusCard).getByText('还没查过')).toBeTruthy()
    fireEvent.click(within(statusCard).getByRole('button', { name: '重新登录智联招聘' }))
    // v27：后端没接通不再回退到 window.open——失败原因直接显示在卡片上，
    // 让用户再点一次。fetchMock 对未知地址返回 {}，后端 ok 为假。
    await waitFor(() => expect(within(statusCard).getByText('浏览器没连上，再点一次试试。')).toBeTruthy())
    expect(openSpy).not.toHaveBeenCalled()
  })

  it('replaces stale logged-out rows with fresh diagnostics on mount', async () => {
    // v37②b：按后端真实信封形状 mock（原始诊断在 diagnostics 里）。
    const diagnostics = {
      ok: true,
      diagnostics: {
        chrome: true,
        boss_tab: { targetId: '1', url: 'https://www.zhipin.com/web/geek/job' },
        zhilian_tab: { targetId: '2', url: 'https://www.zhaopin.com/' },
        zhilian_page: { status: 'ready', message: '已发现智联搜索框，未发现登录墙' },
        job51_tab: { targetId: '3', url: 'https://we.51job.com/pc/search' },
        job51_page: { status: 'ready', message: '已发现前程无忧搜索框，未发现登录墙' },
        liepin_tab: { targetId: '4', url: 'https://www.liepin.com/zhaopin/' },
        liepin_page: { status: 'ready', message: '已发现猎聘搜索框，未发现登录墙' },
      },
    }
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) === '/api/browser/diagnostics') return jsonResponse(diagnostics)
      return fetchMock(input, init)
    }))
    // 库里是上次启动留下的旧“掉线”：挂载诊断没回来之前不能当成事实。
    const stale = { status: 'logged_out', checked_at: '2026-11-19T08:00:00', detail: '登录过期了' }
    workbenchPayload = baseWorkbench({
      platform_login: { boss: stale, zhilian: stale, '51job': stale, liepin: stale },
    })
    render(<UpdateProvider><DashboardPage view="workbench" /></UpdateProvider>)
    const statusCard = await screen.findByRole('region', { name: '各平台登录状态' })
    await waitFor(() => expect(within(statusCard).getAllByText('已登录')).toHaveLength(4))
    expect(within(statusCard).queryByText('已掉线')).toBeNull()
  })

  it('refreshes only the login states from the card button', async () => {
    // v37②b：按后端真实信封形状 mock（原始诊断在 diagnostics 里）。
    const diagnostics = {
      ok: true,
      diagnostics: {
        chrome: true,
        boss_tab: { targetId: '1', url: 'https://www.zhipin.com/web/geek/job' },
        zhilian_tab: { targetId: '2', url: 'https://www.zhaopin.com/' },
        zhilian_page: { status: 'login_required', message: '智联页面要求登录' },
        job51_tab: { targetId: '3', url: 'https://we.51job.com/pc/search' },
        job51_page: { status: 'ready', message: '已发现前程无忧搜索框，未发现登录墙' },
        liepin_tab: { targetId: '4', url: 'https://www.liepin.com/zhaopin/' },
        liepin_page: { status: 'ready', message: '已发现猎聘搜索框，未发现登录墙' },
      },
    }
    const stubFetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) === '/api/browser/diagnostics' && init?.method === 'POST') return jsonResponse(diagnostics)
      return fetchMock(input, init)
    })
    vi.stubGlobal('fetch', stubFetch)
    workbenchPayload = baseWorkbench({ platform_login: { boss: { status: 'unknown' }, zhilian: { status: 'unknown' }, '51job': { status: 'unknown' }, liepin: { status: 'unknown' } } })
    render(<UpdateProvider><DashboardPage view="workbench" /></UpdateProvider>)
    const statusCard = await screen.findByRole('region', { name: '各平台登录状态' })
    expect(within(statusCard).getAllByText('还没查过')).toHaveLength(4)
    fireEvent.click(within(statusCard).getByRole('button', { name: '刷新登录状态' }))
    await waitFor(() => expect(within(statusCard).getByText(/已刷新/)).toBeTruthy())
    expect(within(statusCard).getByText('已掉线')).toBeTruthy()
    expect(within(statusCard).getByText('1 个平台掉线了，先重新登录再跑')).toBeTruthy()
    expect(stubFetch.mock.calls.filter(([url, init]) =>
      String(url) === '/api/browser/diagnostics' && (init as RequestInit)?.method === 'POST')).toHaveLength(1)
    // 没走预检接口（那会跑全套检查还改写上次检查结果），也没触发整页
    // 刷新：没因为这一次点刷新去读历史。
    expect(stubFetch.mock.calls.filter(([url]) => String(url).startsWith('/api/workbench/preflight'))).toHaveLength(0)
    expect(stubFetch.mock.calls.filter(([url]) => String(url).startsWith('/api/history?limit=50'))).toHaveLength(0)
  })

  it('offers one-tap login jumps for all four platforms in the getting-started card', async () => {
    // v27：登录按钮走后端 /api/browser/open（工作浏览器里打开），不再
    // window.open；点一次就有反馈，失败也不回退。
    const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null)
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) === '/api/browser/open') {
        return jsonResponse({ ok: true, target_id: 't-1', browser_started: false, reused_tab: false, message: '已在工作浏览器打开。' })
      }
      return fetchMock(input, init)
    }))
    render(<UpdateProvider><DashboardPage view="workbench" /></UpdateProvider>)
    const guide = await screen.findByRole('region', { name: '开始使用' })
    expect(within(guide).getByText('点击后会打开工作浏览器并跳转到对应招聘网站登录页。')).toBeTruthy()
    fireEvent.click(within(guide).getByRole('button', { name: '去BOSS 直聘登录' }))
    await waitFor(() => expect(
      screen.getAllByText('已在工作浏览器打开BOSS 直聘登录页。').length,
    ).toBeGreaterThanOrEqual(1))
    fireEvent.click(within(guide).getByRole('button', { name: '去猎聘登录' }))
    await waitFor(() => expect(
      screen.getAllByText('已在工作浏览器打开猎聘登录页。').length,
    ).toBeGreaterThanOrEqual(1))
    expect(fetchMock).not.toHaveBeenCalledWith('/api/browser/launch', expect.anything())
    expect(openSpy).not.toHaveBeenCalled()
  })

  it('tells the not-found and connection-failed browser cases apart', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url === '/api/browser/diagnostics') {
        return jsonResponse({ ok: false, error_kind: 'not_found', message: '电脑上没找到 Chrome 或 Edge。', next_steps: ['先装一个 Chrome 再回来。'] })
      }
      return fetchMock(input, init)
    }))
    render(<UpdateProvider><DashboardPage view="workbench" /></UpdateProvider>)
    const panel = await screen.findByRole('region', { name: '浏览器诊断' })
    expect(within(panel).getByText('没找到能用的浏览器')).toBeTruthy()
    expect(within(panel).getByText('先装一个 Chrome 再回来。')).toBeTruthy()
    expect(within(panel).getByRole('button', { name: '重新检测' })).toBeTruthy()
    expect(within(panel).getByRole('button', { name: '一键复制诊断信息' })).toBeTruthy()
  })
})

describe('DashboardPage survives a hanging browser diagnostics request', () => {
  type FetchImpl = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>
  let originalImpl: FetchImpl | undefined
  beforeEach(() => {
    workbenchPayload = baseWorkbench()
    originalImpl = fetchMock.getMockImplementation() as FetchImpl | undefined
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url === '/api/browser/diagnostics') return new Promise<Response>(() => {})
      if (url === '/api/workbench' && (!init?.method || init.method === 'GET')) return jsonResponse(workbenchPayload)
      return jsonResponse({})
    })
    vi.stubGlobal('fetch', fetchMock)
  })
  afterEach(() => {
    if (originalImpl) fetchMock.mockImplementation(originalImpl)
    cleanup()
    vi.unstubAllGlobals()
  })
  it('renders the workbench even when diagnostics never answers', async () => {
    render(<UpdateProvider><DashboardPage view="workbench" /></UpdateProvider>)
    await waitFor(() => expect(screen.queryByLabelText('正在打开')).toBeNull(), { timeout: 10000 })
    expect(screen.getByRole('region', { name: '开始使用' })).toBeTruthy()
  })
})

describe('DashboardPage manual update check (v29)', () => {
  type FetchImpl = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>
  let originalImpl: FetchImpl | undefined
  beforeEach(() => {
    workbenchPayload = baseWorkbench()
    originalImpl = fetchMock.getMockImplementation() as FetchImpl | undefined
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.startsWith('/api/update/check')) throw new Error('offline')
      if (url === '/api/workbench' && (!init?.method || init.method === 'GET')) return jsonResponse(workbenchPayload)
      if (url === '/api/health') return jsonResponse({ status: 'ok', version: '1.0.1' })
      return jsonResponse({})
    })
    vi.stubGlobal('fetch', fetchMock)
  })
  afterEach(() => {
    if (originalImpl) fetchMock.mockImplementation(originalImpl)
    cleanup()
    vi.unstubAllGlobals()
  })

  it('手动检查没查到：留“没查到，点再试一次。”+ 再试一次按钮，点它能再查', async () => {
    render(<UpdateProvider><DashboardPage view="workbench" /></UpdateProvider>)
    const button = await screen.findByRole('button', { name: '检查更新' }, { timeout: 5000 })
    // 挂载的自动检查先失败（静默），等转圈停了按钮才能点。
    await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false), { timeout: 5000 })
    fireEvent.click(button)
    expect(await screen.findByText('没查到，点再试一次。', {}, { timeout: 5000 })).toBeTruthy()
    const retry = screen.getByRole('button', { name: '再试一次' })
    expect(retry).toBeTruthy()
    // 再点一次：还是没查到，提示还在（不被后返回的过期响应盖掉）。
    fireEvent.click(retry)
    expect(await screen.findByText('没查到，点再试一次。', {}, { timeout: 5000 })).toBeTruthy()
  })

  it('手动检查已是最新：留“已经是最新版，不用换。”，没有再试一次按钮', async () => {
    render(<UpdateProvider><DashboardPage view="workbench" /></UpdateProvider>)
    const button = await screen.findByRole('button', { name: '检查更新' }, { timeout: 5000 })
    await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false), { timeout: 5000 })
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url === '/api/update/check?force=1') {
        return jsonResponse({ update_available: false, current_version: '1.0.1', status: 'idle' })
      }
      if (url === '/api/workbench' && (!init?.method || init.method === 'GET')) return jsonResponse(workbenchPayload)
      if (url === '/api/health') return jsonResponse({ status: 'ok', version: '1.0.1' })
      return jsonResponse({})
    })
    fireEvent.click(button)
    expect(await screen.findByText('已经是最新版，不用换。', {}, { timeout: 5000 })).toBeTruthy()
    expect(screen.queryByRole('button', { name: '再试一次' })).toBeNull()
  })
})

describe('DashboardPage delivery follow-up (v33 task 1)', () => {
  beforeEach(() => {
    workbenchPayload = baseWorkbench()
    vi.stubGlobal('fetch', fetchMock)
    fetchMock.mockClear()
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  function stubMonitorFetch() {
    const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url === '/api/workbench' && (!init?.method || init.method === 'GET')) return jsonResponse(workbenchPayload)
      if (url.startsWith('/api/history')) return jsonResponse([])
      if (url.startsWith('/api/jobs/search')) return jsonResponse({ items: [], total: 0, all_total: 0 })
      return jsonResponse({})
    })
    vi.stubGlobal('fetch', fn)
    return fn
  }

  it('shows the running delivery task and pending jobs on the monitor page', async () => {
    workbenchPayload = baseWorkbench({
      tasks: [
        buildTask({ id: 'task-deliver', mode: 'deliver', label: '确认投递', status: 'running',
          logs: ['招呼语发送结果：成功 2，失败 1，待下次发送 0（共 3）'],
          metrics: { send_requested: 3, send_success: 2, send_failed: 1, send_deferred: 0 } }),
      ],
      delivery_jobs: [
        { id: 'job-error', company: '失败公司', title: '测试岗位', source_platform: 'boss', status: 'error',
          last_error: '对方暂时不接受新消息', step_action: 'error', step_created_at: '2026-11-21 15:02:00' },
        { id: 'job-queued', company: '排队公司', title: '测试岗位', source_platform: 'boss', status: 'approved',
          step_action: 'approved', step_created_at: '2026-11-21 15:03:00' },
      ],
    })
    stubMonitorFetch()
    render(<MemoryRouter><UpdateProvider><DashboardPage view="monitor" /></UpdateProvider></MemoryRouter>)
    expect(await screen.findByRole('region', { name: '投递任务' })).toBeTruthy()
    expect(screen.getByText('确认投递')).toBeTruthy()
    expect(screen.getByText('投递中')).toBeTruthy()
    expect(screen.getByText('共 3 个 · 已投出 2 个 · 没投出去 1 个 · 等下次 0 个')).toBeTruthy()
    expect(screen.getByText('失败公司｜测试岗位')).toBeTruthy()
    expect(screen.getByText('对方暂时不接受新消息')).toBeTruthy()
    expect(screen.getByText('排队公司｜测试岗位')).toBeTruthy()
    expect(screen.getByText('正在排队，等着投')).toBeTruthy()
  })

  it('points the jobs-page delivery card at the real monitor entry', async () => {
    workbenchPayload = baseWorkbench({
      task: buildTask({ id: 'task-deliver', mode: 'deliver', label: '确认投递', status: 'running', logs: ['正在投第 1 个'] }),
      last_task: null,
    })
    const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url === '/api/workbench' && (!init?.method || init.method === 'GET')) return jsonResponse(workbenchPayload)
      if (url.startsWith('/api/jobs/search')) return jsonResponse({ items: [], total: 0, all_total: 0 })
      return jsonResponse({})
    })
    vi.stubGlobal('fetch', fn)
    render(<MemoryRouter><UpdateProvider><DashboardPage view="jobs" /></UpdateProvider></MemoryRouter>)
    expect(await screen.findByText(/投递进度与结果在左侧“投递跟进”里查看/)).toBeTruthy()
    expect(screen.queryByText(/投递记录里查看/)).toBeNull()
    expect(screen.getByRole('link', { name: '去投递跟进看看' }).getAttribute('href')).toBe('/monitor')
  })
})
