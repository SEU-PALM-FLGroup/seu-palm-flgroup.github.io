from dataclasses import dataclass


@dataclass(frozen=True)
class CreditItem:
    key: str
    group: str
    label: str
    rule: str


PROJECTS = '组内项目参与'
RESOURCES = '公共资源维护'
MENTORING = '新人培训'
ACADEMIC = '学术活动'
APPLICATIONS = '项目申请材料撰写'
OUTREACH = '宣传与资源获取'
OPERATIONS = '组务轮值与运行协调'

RESOURCE_RATE = '每人参考基础分 2 分/月'
OPERATIONS_RATE = '每人参考基础分 4 分/月'
OUTREACH_RATE = '2–5 分/项'

# 对应课题组管理细则的各类服务评分细则及服务范围总览。
CREDIT_ITEMS = (
    CreditItem('engineering-campus-online', PROJECTS, '工程项目 · 校内或线上', '6 分/有效参与月'),
    CreditItem('engineering-city-offsite', PROJECTS, '工程项目 · 同城校外线下', '8 分/有效参与月'),
    CreditItem('engineering-away', PROJECTS, '工程项目 · 异地线下', '10 分/有效参与月'),
    CreditItem('mixed-campus-online', PROJECTS, '混合项目 · 校内或线上', '4 分/有效参与月'),
    CreditItem('mixed-city-offsite', PROJECTS, '混合项目 · 同城校外线下', '6 分/有效参与月'),
    CreditItem('mixed-away', PROJECTS, '混合项目 · 异地线下', '8 分/有效参与月'),
    CreditItem('academic-campus-online', PROJECTS, '学术项目 · 校内或线上', '2 分/有效参与月'),
    CreditItem('academic-city-offsite', PROJECTS, '学术项目 · 同城校外线下', '4 分/有效参与月'),
    CreditItem('academic-away', PROJECTS, '学术项目 · 异地线下', '6 分/有效参与月'),
    CreditItem('academic-strategy-submission', PROJECTS, '校内或线上学术项目 · 战略方向年度投稿', '符合条件计 10 个有效参与月'),
    CreditItem('academic-strategy-publication', PROJECTS, '校内或线上学术项目 · 战略方向论文发表', '符合条件另加 10 分'),
    CreditItem('mixed-publication', PROJECTS, '线上混合项目 · 论文满足项目指标或结题', '符合条件另加 20 分'),
    CreditItem('engineering-delivery', PROJECTS, '线上工程项目 · 成果满足项目指标或结题', '符合条件另加 30 分'),
    CreditItem('frontier-publication', PROJECTS, '学术项目 · 前沿重要方向论文发表', '学术项目相关积分翻倍'),
    CreditItem('project-final-bonus', PROJECTS, '项目最终奖励', '按分工、贡献、发表及项目完成度评定'),
    CreditItem('publication-extra-credit', PROJECTS, '学术论文额外加分', '按论文价值、影响力或时间精力投入评定'),
    CreditItem('maintain-servers', RESOURCES, '组内服务器和账号管理', RESOURCE_RATE),
    CreditItem('maintain-computing-center', RESOURCES, '学校计算中心相关事宜', RESOURCE_RATE),
    CreditItem('maintain-equipment', RESOURCES, '共享设备、电脑、机械臂及公用耗材', RESOURCE_RATE),
    CreditItem('maintain-shared-materials', RESOURCES, '共享代码、数据、文档和操作手册', RESOURCE_RATE),
    CreditItem('mentor-monthly', MENTORING, '带教本科生或研零生', '0.5 分/人/月'),
    CreditItem('recruitment-interview', MENTORING, '面试有意向进组的学生', '0.5 分/人次，原则上每次至少半小时'),
    CreditItem('mentored-paper', MENTORING, '带教发表 CCF-A 类或相当认可度论文', '10 分/篇，团队分配按细则'),
    CreditItem('mentored-frontier-paper', MENTORING, '带教发表前沿重要方向论文', '20 分/篇，团队分配按细则'),
    CreditItem('lecture-setup', ACADEMIC, '专家讲座设备调试与准备', '1 分/次'),
    CreditItem('lecture-attendance', ACADEMIC, '全程参加课题组邀请的专家讲座', '0.5 分/次'),
    CreditItem('expert-pickup', ACADEMIC, '专家接待 · 机场或火车站接至酒店', '1 分/次'),
    CreditItem('expert-hosting', ACADEMIC, '专家接待 · 较长时间的接待活动', '3 分/天'),
    CreditItem('event-organizing-short', ACADEMIC, '组织组会或学术活动 · 不超过 3 小时', '1 分/次'),
    CreditItem('event-organizing-day', ACADEMIC, '组织组会或学术活动 · 按天', '3 分/天'),
    CreditItem('project-proposal', APPLICATIONS, '参与项目申请材料撰写、整理或统稿', '3–20 分，参考 3 分/天'),
    CreditItem('homepage-maintenance', OUTREACH, '组内主页维护', OUTREACH_RATE),
    CreditItem('research-publicity', OUTREACH, '成果报道或招生介绍', OUTREACH_RATE),
    CreditItem('collaboration-demo', OUTREACH, '合作展示或演示材料', OUTREACH_RATE),
    CreditItem('industry-partnership', OUTREACH, '由学生发起的校企合作项目', '分值等于项目总经费（万元）'),
    CreditItem('operations-rota', OPERATIONS, '组务轮值或排班协调', OPERATIONS_RATE),
    CreditItem('operations-expenses', OPERATIONS, '财务报销和发票整理', OPERATIONS_RATE),
    CreditItem('operations-procurement', OPERATIONS, '公共采购或招标', OPERATIONS_RATE),
    CreditItem('operations-signatures', OPERATIONS, '文件签署管理与对接', OPERATIONS_RATE),
    CreditItem('operations-activity-coordination', OPERATIONS, '组内活动安排组织与人员通知', '参考 3 分/月'),
    CreditItem('other', '未列明事项', '其他', '请说明具体事项和申请积分的依据'),
)

CREDIT_ITEMS_BY_KEY = {item.key: item for item in CREDIT_ITEMS}
