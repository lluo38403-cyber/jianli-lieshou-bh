import { describe, expect, it } from 'vitest'
import { describeGreetTaskOutcome, greetPauseReasonLabel } from './greetTask'
import type { WorkbenchTask } from '@/hooks/useDashboard'

function buildTask(overrides: Partial<WorkbenchTask>): WorkbenchTask {
  return {
    id: 'task-1',
    mode: 'greet',
    label: '生成招呼语',
    status: 'completed',
    logs: [],
    stop_requested: false,
    ...overrides,
  }
}

describe('greetPauseReasonLabel', () => {
  it('maps known pause reason kinds to friendly labels', () => {
    expect(greetPauseReasonLabel('AI Token 额度或账户余额不足 (token_quota, status=402)')).toBe('额度或余额不够了')
    expect(greetPauseReasonLabel('AI 服务鉴权失败：服务未授权或当前模型没有访问权限 (auth, status=401)')).toBe('服务没通过验证')
    expect(greetPauseReasonLabel('AI 服务触发请求或 Token 频率限制 (rate_limit, status=429)')).toBe('打分太频繁，先缓一缓')
    expect(greetPauseReasonLabel('AI 服务连接失败或超时 (network)')).toBe('网络连不上或超时了')
    expect(greetPauseReasonLabel('请求内容超过当前模型的上下文限制 (context_limit)')).toBe('内容太长，装不下了')
  })

  it('falls back to the raw reason for unknown kinds', () => {
    expect(greetPauseReasonLabel('某个未知服务异常 (new_kind, status=500)')).toBe('某个未知服务异常 (new_kind, status=500)')
    expect(greetPauseReasonLabel('没有 kind 后缀的原始错误')).toBe('没有 kind 后缀的原始错误')
  })

  it('returns empty string for missing reasons', () => {
    expect(greetPauseReasonLabel(undefined)).toBe('')
    expect(greetPauseReasonLabel('')).toBe('')
    expect(greetPauseReasonLabel(42)).toBe('')
  })
})

describe('describeGreetTaskOutcome', () => {
  it('summarizes a fully completed greet task', () => {
    const task = buildTask({
      metrics: { greet_generated: 5, greet_preserved: 2, greet_failed: 1 },
    })
    expect(describeGreetTaskOutcome(task)).toBe('打招呼写完了：新写了 5 条，沿用以前的 2 条，失败 1 条')
  })

  it('appends conflict count when jobs changed state', () => {
    const task = buildTask({
      metrics: { greet_generated: 3, greet_preserved: 0, greet_failed: 0 },
      progress: { conflict_ids: ['job-1', 'job-2'] },
    })
    expect(describeGreetTaskOutcome(task)).toBe('打招呼写完了：新写了 3 条，沿用以前的 0 条，失败 0 条；2 个岗位状态变了，没保存')
  })

  it('explains the pause reason for partial success', () => {
    const task = buildTask({
      metrics: {
        greet_generated: 3,
        greet_preserved: 0,
        greet_failed: 0,
        greet_paused: 1,
        greet_pause_reason: 'AI Token 额度或账户余额不足 (token_quota, status=402)',
      },
    })
    expect(describeGreetTaskOutcome(task)).toBe(
      '打招呼写完了：新写了 3 条，沿用以前的 0 条，失败 0 条；因为额度或余额不够了提前停下了，写好的都留着，剩下的下次继续'
    )
  })

  it('uses a generic fallback when the pause reason is missing', () => {
    const task = buildTask({
      metrics: { greet_generated: 1, greet_preserved: 0, greet_failed: 0, greet_paused: 1 },
    })
    expect(describeGreetTaskOutcome(task)).toBe(
      '打招呼写完了：新写了 1 条，沿用以前的 0 条，失败 0 条；因为打分服务出了点问题提前停下了，写好的都留着，剩下的下次继续'
    )
  })
})
