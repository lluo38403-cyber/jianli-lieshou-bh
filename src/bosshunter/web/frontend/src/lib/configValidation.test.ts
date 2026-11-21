import { describe, expect, it } from 'vitest'
import { validateConfigNumbers, validateNumberInput } from './configValidation'

describe('validateNumberInput（逐框失焦校验，H-6）', () => {
  it('空值就地报错，而不是写成 0', () => {
    expect(validateNumberInput('', '最多翻几页', 1, 10)).toBe('最多翻几页不能为空，请填写数字')
    expect(validateNumberInput('   ', '最多翻几页', 1, 10)).toContain('不能为空')
  })

  it('非数字与小数就地报错', () => {
    expect(validateNumberInput('e', '最多翻几页', 1, 10)).toBe('最多翻几页请填写数字')
    expect(validateNumberInput('-', '最多翻几页', 1, 10)).toBe('最多翻几页请填写数字')
    expect(validateNumberInput('1.5', '最多翻几页', 1, 10)).toBe('最多翻几页请填写整数')
  })

  it('范围外就地报错，范围内通过；小数框允许小数', () => {
    expect(validateNumberInput('0', '最多翻几页', 1, 10)).toBe('最多翻几页应在 1–10 之间')
    expect(validateNumberInput('11', '最多翻几页', 1, 10)).toBe('最多翻几页应在 1–10 之间')
    expect(validateNumberInput('3', '最多翻几页', 1, 10)).toBeNull()
    expect(validateNumberInput('1.5', '动作快慢', 1, 5, { integer: false })).toBeNull()
  })
})

describe('validateConfigNumbers（保存前集中校验，H-6）', () => {
  it('空值写成的 0 与 NaN 存成的 null 都拦住', () => {
    const errors = validateConfigNumbers({
      platforms: { boss: { search: { max_pages: 0 } } },
      scoring: { max_candidates: null },
    })
    expect(errors.map(error => error.path)).toContain('platforms.boss.search.max_pages')
    expect(errors.map(error => error.path)).toContain('scoring.max_candidates')
  })

  it('最小值大于最大值拦住（薪资、歇几分钟两组）', () => {
    const errors = validateConfigNumbers({
      profile: { salary_min: 10, salary_max: 5 },
      collection: { risk_pause_min_minutes: 20, risk_pause_max_minutes: 5 },
    })
    expect(errors.map(error => error.message)).toContain('我想要的工资：最少不能比最多大')
    expect(errors.map(error => error.message)).toContain('出问题先歇几分钟：最少不能比最多大')
  })

  it('缺失字段按默认处理、不报错；合法配置通过', () => {
    expect(validateConfigNumbers({})).toEqual([])
    // 薪资最多填 0 表示不限：最少 5、最多 0 不算“最少比最多大”。
    expect(validateConfigNumbers({ profile: { salary_min: 5, salary_max: 0 } })).toEqual([])
    expect(validateConfigNumbers({
      platforms: { boss: { search: { max_pages: 3 } }, zhilian: { search: { max_pages: 1 } } },
      scoring: { max_candidates: 20, threshold: 60 },
      collection: { daily_search_page_limit: 60, collection_delay_multiplier: 1.5 },
      profile: { salary_min: 0, salary_max: 0 },
      monitor: { interval: 30 },
      follow_up: { interval_hours: 48 },
    })).toEqual([])
  })
})
