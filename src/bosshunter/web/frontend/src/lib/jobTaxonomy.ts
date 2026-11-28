/**
 * v54 图2-6：岗位选择器的内置岗位表。
 *
 * 招聘平台（BOSS/智联/前程无忧/猎聘）没有公开的岗位类目接口，类目
 * 数据是登录后 JS 渲染的，程序拉不到最新列表——所以这里用一份内置
 * 的常用岗位表（按职责区分，如“国内电商运营”和“电商客服”是两份
 * 不同的工作），关键词照样可以手填。选了方向后，搜岗和打分只认这些
 * 方向里的岗位（见 prefilter.title_matches_role_picks）。
 */

export interface JobRolePick {
  /** 展示名，如“国内电商运营”。 */
  label: string
  /** 该方向的细分岗位词，如淘宝运营、天猫运营、京东运营、拼多多运营。 */
  keywords: string[]
}

export interface JobCategory {
  category: string
  roles: JobRolePick[]
}

export const JOB_TAXONOMY: JobCategory[] = [
  {
    category: '电商',
    roles: [
      { label: '国内电商运营', keywords: ['淘宝运营', '天猫运营', '京东运营', '拼多多运营', '电商运营'] },
      { label: '跨境电商运营', keywords: ['亚马逊运营', 'Shopee运营', '速卖通运营', '独立站运营', '跨境电商运营', '电商运营'] },
      { label: '电商客服', keywords: ['电商客服', '淘宝客服', '天猫客服', '京东客服', '拼多多客服', '售前客服', '售后客服'] },
      { label: '电商美工', keywords: ['电商美工', '淘宝美工', '电商设计', '店铺装修'] },
      { label: '电商推广', keywords: ['电商推广', '直通车推广', '淘宝推广', '拼多多推广', '直播运营'] },
    ],
  },
  {
    category: '运营',
    roles: [
      { label: '新媒体运营', keywords: ['新媒体运营', '公众号运营', '抖音运营', '小红书运营', '短视频运营'] },
      { label: '内容运营', keywords: ['内容运营', '编辑', '文案策划', '新媒体编辑'] },
      { label: '用户运营', keywords: ['用户运营', '社群运营', '会员运营', '私域运营'] },
      { label: '活动运营', keywords: ['活动运营', '活动策划', '市场活动'] },
      { label: '产品运营', keywords: ['产品运营', '产品助理', '产品经理助理'] },
    ],
  },
  {
    category: '销售',
    roles: [
      { label: '销售代表', keywords: ['销售代表', '销售', '业务员', '客户代表'] },
      { label: '电话销售', keywords: ['电话销售', '电销', '电话客服销售'] },
      { label: '客户经理', keywords: ['客户经理', '大客户经理', '渠道经理'] },
      { label: '销售助理', keywords: ['销售助理', '销售内勤', '商务助理'] },
    ],
  },
  {
    category: '客服',
    roles: [
      { label: '客服专员', keywords: ['客服专员', '客服', '在线客服', '售前客服', '售后客服'] },
      { label: '售后支持', keywords: ['售后支持', '售后专员', '客服主管'] },
    ],
  },
  {
    category: '仓库物流',
    roles: [
      { label: '仓库管理员', keywords: ['仓库管理员', '仓管', '仓库管理', '库房管理员'] },
      { label: '仓库主管', keywords: ['仓库主管', '仓储主管', '物流主管'] },
      { label: '物流专员', keywords: ['物流专员', '物流跟单', '调度员'] },
      { label: '分拣员', keywords: ['分拣员', '打包员', '理货员'] },
      { label: '快递员', keywords: ['快递员', '配送员', '外卖骑手'] },
    ],
  },
  {
    category: '财务',
    roles: [
      { label: '出纳', keywords: ['出纳', '收银出纳'] },
      { label: '会计', keywords: ['会计', '财务会计', '总账会计'] },
      { label: '财务助理', keywords: ['财务助理', '财务文员'] },
      { label: '电商财务', keywords: ['电商财务', '电商会计'] },
    ],
  },
  {
    category: '人事行政',
    roles: [
      { label: '人事专员', keywords: ['人事专员', '人力资源专员', 'HR专员'] },
      { label: '招聘专员', keywords: ['招聘专员', '招聘助理'] },
      { label: '行政专员', keywords: ['行政专员', '行政助理', '前台'] },
    ],
  },
  {
    category: '开发',
    roles: [
      { label: '前端开发', keywords: ['前端开发', '前端工程师', 'Web前端'] },
      { label: '后端开发', keywords: ['后端开发', '后端工程师', 'Java开发', 'Python开发', 'Go开发'] },
      { label: '测试工程师', keywords: ['测试工程师', '软件测试', 'QA'] },
      { label: '运维工程师', keywords: ['运维工程师', '系统运维'] },
    ],
  },
  {
    category: '设计',
    roles: [
      { label: '平面设计', keywords: ['平面设计', '平面设计师', '视觉设计'] },
      { label: 'UI设计', keywords: ['UI设计', 'UI设计师', '交互设计'] },
      { label: '室内设计', keywords: ['室内设计', '室内设计师', '家装设计'] },
    ],
  },
  {
    category: '生产制造',
    roles: [
      { label: '生产工人', keywords: ['生产工人', '操作工', '普工', '组装工'] },
      { label: '质检员', keywords: ['质检员', '质量检验', 'QC'] },
      { label: '设备维修', keywords: ['设备维修', '维修工', '机修工', '电工'] },
    ],
  },
  {
    category: '餐饮服务',
    roles: [
      { label: '服务员', keywords: ['服务员', '餐厅服务员'] },
      { label: '厨师', keywords: ['厨师', '厨师长', '帮厨'] },
      { label: '收银员', keywords: ['收银员', '收银'] },
    ],
  },
  {
    category: '司机',
    roles: [
      { label: '货车司机', keywords: ['货车司机', '卡车司机', '司机'] },
      { label: '网约车司机', keywords: ['网约车司机', '代驾司机'] },
    ],
  },
]

/** 按输入联想细分岗位：名字、细分词、分类里任意一个含关键词都算命中。 */
export function searchRolePicks(query: string): JobRolePick[] {
  const text = query.trim().toLowerCase()
  if (!text) {
    return JOB_TAXONOMY.flatMap(group => group.roles)
  }
  const hits: JobRolePick[] = []
  for (const group of JOB_TAXONOMY) {
    const categoryHit = group.category.toLowerCase().includes(text)
    for (const role of group.roles) {
      if (
        categoryHit ||
        role.label.toLowerCase().includes(text) ||
        role.keywords.some(keyword => keyword.toLowerCase().includes(text))
      ) {
        hits.push(role)
      }
    }
  }
  return hits
}

/** 某个岗位方向属于哪个分类（下拉里展示用）。 */
export function roleCategory(label: string): string {
  for (const group of JOB_TAXONOMY) {
    if (group.roles.some(role => role.label === label)) {
      return group.category
    }
  }
  return ''
}
