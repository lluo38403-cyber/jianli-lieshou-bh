/**
 * 设置页数字校验（H-6）：前后端共用同一套区间口径。
 *
 * 背景：数字框以前是 onChange 直接 Number(...) 写进配置，清空变 0、
 * 输 e/- 变 NaN（JSON 里成 null），保存时不拦；显示层又用 || 回退
 * 默认值把存坏的 0 盖住，故障拖到启动任务时才报。这里的三层防线是：
 * 1. 逐框失焦校验（ValidatedNumberInput）：空值/非法值就地红字报错并
 *    恢复上一次合法值；2. 保存前集中校验（useConfig.saveConfig）：
 *    整份快照过一遍 validateConfigNumbers，有错不发请求；
 * 3. 后端 POST 校验（config.py validate_config_numbers）：
 *    直接调接口也拦得住。区间与后端保持一致，改一处要同步另一处。
 */

export type ConfigNumberError = {
  path: string
  label: string
  message: string
}

type NumberRule = {
  /** 精确路径；平台搜索页数用前缀通配，见 ruleForPath。 */
  path: string
  label: string
  min: number
  max: number
  integer?: boolean
}

const RULES: NumberRule[] = [
  { path: 'search.max_pages', label: '最多翻几页', min: 1, max: 10, integer: true },
  { path: 'platforms.*.search.max_pages', label: '最多翻几页', min: 1, max: 10, integer: true },
  { path: 'platforms.boss.search.city_yield_policy.warmup_minutes', label: '过几分钟先看一次', min: 1, max: 60, integer: true },
  { path: 'platforms.boss.search.city_yield_policy.window_minutes', label: '隔几分钟看一次', min: 1, max: 30, integer: true },
  { path: 'platforms.boss.search.city_yield_policy.min_new_jobs', label: '一次至少新找到几个', min: 1, max: 1000, integer: true },
  { path: 'scoring.max_candidates', label: '一次最多看多少个', min: 1, max: 100, integer: true },
  { path: 'scoring.threshold', label: '多少分以上才投', min: 0, max: 100, integer: true },
  { path: 'collection.daily_search_page_limit', label: 'BOSS 每天最多翻多少页', min: 1, max: 200, integer: true },
  { path: 'collection.daily_detail_page_limit', label: 'BOSS 每天最多打开多少个岗位', min: 1, max: 1000, integer: true },
  { path: 'collection.max_consecutive_page_failures', label: 'BOSS 页面连续打不开几次就停', min: 1, max: 10, integer: true },
  { path: 'collection.collection_delay_multiplier', label: '动作快慢', min: 1, max: 5 },
  { path: 'collection.risk_pause_min_minutes', label: '出问题先歇几分钟（最少）', min: 1, max: 60, integer: true },
  { path: 'collection.risk_pause_max_minutes', label: '出问题先歇几分钟（最多）', min: 1, max: 60, integer: true },
  { path: 'collection.delivery_cooldown_min_minutes', label: '找完先歇几分钟再投（最少）', min: 0, max: 240, integer: true },
  { path: 'collection.delivery_cooldown_max_minutes', label: '找完先歇几分钟再投（最多）', min: 0, max: 240, integer: true },
  { path: 'safety.daily_platform_page_limit', label: 'BOSS 每天最多打开多少个页面', min: 1, max: 2000, integer: true },
  { path: 'throttle.daily_cap', label: '每日投递上限', min: 1, max: 500, integer: true },
  { path: 'monitor.interval', label: '隔几分钟看一次', min: 1, max: 120, integer: true },
  { path: 'monitor.initial_cooldown_minutes', label: '投完先歇几分钟再看回复', min: 0, max: 120, integer: true },
  { path: 'monitor.max_conversations_per_cycle', label: '一次最多处理几段对话', min: 1, max: 20, integer: true },
  { path: 'monitor.max_consecutive_page_failures', label: '页面连续打不开几次就停', min: 1, max: 10, integer: true },
  { path: 'monitor.max_resume_sends_per_cycle', label: '一次最多发几份简历', min: 1, max: 100, integer: true },
  { path: 'follow_up.interval_hours', label: '过几小时没回就问一次', min: 12, max: 168, integer: true },
  { path: 'profile.salary_min', label: '我想要的工资（最少）', min: 0, max: 200, integer: true },
  { path: 'profile.salary_max', label: '我想要的工资（最多）', min: 0, max: 200, integer: true },
  { path: 'profile.salary_ceil_ratio', label: '工资高一点也能接受', min: 1, max: 5 },
  { path: 'profile.posted_days', label: '只要最近发布的', min: 0, max: 30, integer: true },
]

/** min<=max 成对校验的区间。薪资 max=0 表示不限：与后端既有口径一致，max>0 时才比大小。 */
const RANGE_PAIRS: Array<{ minPath: string; maxPath: string; label: string; onlyWhenMaxPositive?: boolean }> = [
  { minPath: 'profile.salary_min', maxPath: 'profile.salary_max', label: '我想要的工资', onlyWhenMaxPositive: true },
  { minPath: 'collection.risk_pause_min_minutes', maxPath: 'collection.risk_pause_max_minutes', label: '出问题先歇几分钟' },
  { minPath: 'collection.delivery_cooldown_min_minutes', maxPath: 'collection.delivery_cooldown_max_minutes', label: '找完先歇几分钟再投' },
]

export function getConfigValue(config: unknown, path: string): unknown {
  let current: any = config
  for (const key of path.split('.')) {
    if (current == null || typeof current !== 'object') return undefined
    current = current[key]
  }
  return current
}

function ruleForPath(path: string): NumberRule | undefined {
  const exact = RULES.find(rule => rule.path === path)
  if (exact) return exact
  // platforms.<平台>.search.max_pages：四个平台共用一条规则。
  if (/^platforms\.[^.]+\.search\.max_pages$/.test(path)) {
    return RULES.find(rule => rule.path === 'platforms.*.search.max_pages')
  }
  return undefined
}

/** 校验单个输入框里的原始字符串；合法返回 null，否则返回就地显示的中文原因。 */
export function validateNumberInput(
  raw: string,
  label: string,
  min: number,
  max: number,
  options?: { integer?: boolean },
): string | null {
  const text = raw.trim()
  if (text === '') return `${label}不能为空，请填写数字`
  const value = Number(text)
  if (!Number.isFinite(value)) return `${label}请填写数字`
  if (options?.integer !== false && !Number.isInteger(value)) return `${label}请填写整数`
  if (value < min || value > max) return `${label}应在 ${min}–${max} 之间`
  return null
}

function checkRuleValue(path: string, rule: NumberRule, value: unknown): ConfigNumberError | null {
  if (value === undefined) return null
  if (value === null || typeof value === 'boolean' || typeof value !== 'number' || !Number.isFinite(value)) {
    return { path, label: rule.label, message: `${rule.label}请填写数字` }
  }
  if (rule.integer && !Number.isInteger(value)) {
    return { path, label: rule.label, message: `${rule.label}请填写整数` }
  }
  if (value < rule.min || value > rule.max) {
    return { path, label: rule.label, message: `${rule.label}应在 ${rule.min}–${rule.max} 之间` }
  }
  return null
}

/** 校验整份配置快照里的全部数字区间；返回所有错误（空数组表示通过）。 */
export function validateConfigNumbers(config: unknown): ConfigNumberError[] {
  const errors: ConfigNumberError[] = []
  const seen = new Set<string>()
  const check = (path: string) => {
    if (seen.has(path)) return
    seen.add(path)
    const rule = ruleForPath(path)
    if (!rule) return
    const error = checkRuleValue(path, rule, getConfigValue(config, path))
    if (error) errors.push(error)
  }
  for (const rule of RULES) {
    if (rule.path.includes('*')) {
      const platforms = getConfigValue(config, 'platforms')
      if (platforms && typeof platforms === 'object') {
        for (const platform of Object.keys(platforms)) {
          check(rule.path.replace('*', platform))
        }
      }
      continue
    }
    check(rule.path)
  }
  for (const pair of RANGE_PAIRS) {
    const minValue = getConfigValue(config, pair.minPath)
    const maxValue = getConfigValue(config, pair.maxPath)
    if (
      typeof minValue === 'number' && typeof maxValue === 'number'
      && (!pair.onlyWhenMaxPositive || maxValue > 0)
      && minValue > maxValue
    ) {
      errors.push({
        path: pair.minPath,
        label: pair.label,
        message: `${pair.label}：最少不能比最多大`,
      })
    }
  }
  return errors
}
