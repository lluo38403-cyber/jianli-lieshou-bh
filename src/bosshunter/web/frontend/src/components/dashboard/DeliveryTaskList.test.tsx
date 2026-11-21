import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { DeliveryTaskList } from './DeliveryTaskList'
import type { DeliveryJob, WorkbenchTask } from '@/hooks/useDashboard'

afterEach(cleanup)

function buildTask(overrides: Partial<WorkbenchTask>): WorkbenchTask {
  return {
    id: 'task-1',
    mode: 'deliver',
    label: '确认投递',
    status: 'running',
    logs: ['招呼语发送结果：成功 2，失败 1，待下次发送 0（共 3）'],
    stop_requested: false,
    created_at: '2026-11-21 15:00:00',
    ...overrides,
  }
}

function buildJob(overrides: Partial<DeliveryJob>): DeliveryJob {
  return {
    id: 'job-1',
    company: '测试公司',
    title: '测试岗位',
    source_platform: 'boss',
    status: 'sent',
    updated_at: '2026-11-21 15:00:00',
    ...overrides,
  }
}

function renderList(tasks: WorkbenchTask[], deliveryJobs: DeliveryJob[]) {
  return render(
    <MemoryRouter>
      <DeliveryTaskList tasks={tasks} deliveryJobs={deliveryJobs} />
    </MemoryRouter>,
  )
}

describe('DeliveryTaskList', () => {
  it('shows a running delivery task with success/failure/deferred counts and the last log', () => {
    renderList(
      [buildTask({ metrics: { send_requested: 3, send_success: 2, send_failed: 1, send_deferred: 0 } })],
      [],
    )
    expect(screen.getByText('确认投递')).toBeTruthy()
    expect(screen.getByText('投递中')).toBeTruthy()
    expect(screen.getByText('共 3 个 · 已投出 2 个 · 没投出去 1 个 · 等下次 0 个')).toBeTruthy()
    expect(screen.getByText(/招呼语发送结果：成功 2/)).toBeTruthy()
  })

  it('only shows delivery modes, not scoring or collection tasks', () => {
    renderList(
      [buildTask({ id: 'task-score', mode: 'score', label: '单独 AI 评分', status: 'running', logs: [] })],
      [],
    )
    expect(screen.queryByText('单独 AI 评分')).toBeNull()
    expect(screen.getByText(/最近没有投递任务/)).toBeTruthy()
  })

  it('shows a failed task with its stop reason', () => {
    renderList(
      [buildTask({ status: 'failed', stop_reason: '发送已安全暂停：检测到验证码', logs: ['正在投第 2 个'] })],
      [],
    )
    expect(screen.getByText('失败了')).toBeTruthy()
    expect(screen.getByText(/停下来的原因：发送已安全暂停：检测到验证码/)).toBeTruthy()
  })

  it('shows queued and failed jobs with flow steps and the persisted failure reason, and skips already-sent jobs', () => {
    renderList(
      [buildTask({ status: 'completed', metrics: { send_requested: 3, send_success: 1, send_failed: 1, send_deferred: 1 } })],
      [
        buildJob({ id: 'job-sent', company: '已投公司', status: 'sent', step_action: 'sent', step_created_at: '2026-11-21 15:01:00' }),
        buildJob({ id: 'job-error', company: '失败公司', status: 'error', last_error: '对方暂时不接受新消息', step_action: 'error', step_created_at: '2026-11-21 15:02:00' }),
        buildJob({ id: 'job-queued', company: '排队公司', status: 'approved', step_action: 'approved', step_created_at: '2026-11-21 15:03:00' }),
      ],
    )
    // 已投出的不在这个分区重复列，由下面“已投出的岗位”展示。
    expect(screen.queryByText('已投公司')).toBeNull()
    expect(screen.getByText(/已确认 → 没投出去/)).toBeTruthy()
    expect(screen.getByText('对方暂时不接受新消息')).toBeTruthy()
    expect(screen.getByText(/那里再发一次/)).toBeTruthy()
    expect(screen.getByRole('link', { name: '首页“这几个没投出去”' }).getAttribute('href')).toBe('/')
    expect(screen.getByText(/已确认 → 等着投/)).toBeTruthy()
    expect(screen.getByText(/已经投出去的岗位在下面“已投出的岗位”里/)).toBeTruthy()
  })

  it('labels an approved job as queued while a delivery is running, and as waiting when none is', () => {
    const { rerender } = renderList(
      [buildTask({ status: 'running' })],
      [buildJob({ id: 'job-queued', company: '排队公司', status: 'approved', step_action: 'approved' })],
    )
    expect(screen.getByText('正在排队，等着投')).toBeTruthy()
    rerender(
      <MemoryRouter>
        <DeliveryTaskList tasks={[buildTask({ status: 'completed' })]} deliveryJobs={[buildJob({ id: 'job-queued', company: '排队公司', status: 'approved', step_action: 'approved' })]} />
      </MemoryRouter>,
    )
    expect(screen.getByText('已确认，等下次投递')).toBeTruthy()
  })

  it('shows empty states that point to the real entries', () => {
    renderList([], [])
    expect(screen.getByText(/最近没有投递任务/)).toBeTruthy()
    expect(screen.getByRole('link', { name: '找工作' }).getAttribute('href')).toBe('/jobs')
    expect(screen.getByText(/没有正在排队、也没投失败的岗位/)).toBeTruthy()
  })
})
