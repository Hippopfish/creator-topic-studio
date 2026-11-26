---
id: profile_memory
title: 记住你是谁
output: json
source: 照搬 account-learning/references/persona.md
---

你在读一个人的账号，目的是让以后每次创作都不用他重新介绍自己。只记**有证据**的东西。

【账号】{{accountName}}｜深读样本 {{nDeep}} 篇｜今天：{{today}}

【主页简介】
{{intro}}

【正文证据（规则只能从这里来）】
{{deepBodies}}

【已经记住的】（同一维度以后更新时修订原记录，不新建重复记录）
{{existingMemory}}

【允许的维度 slot，只能用这些】
- identity 身份与经历：简介、自我介绍、正文里的「我是…／我做了…年」
- expertise 专长与边界：反复讲的领域、从不碰的话题
- stance 一贯观点：**≥2 篇里出现的同一判断**
- story 常讲的故事与素材：反复提到的经历、人物、项目
- offering 产品、服务与变现：只记业务形态，不记号码
- positioning 定位｜audience 受众｜voice 声音｜content_pattern 内容规律｜constraint 约束

【硬规则】
1. 每条只写一个独立事实或观点，不超过 600 字。
2. 必须标清是「本人明说」（stated）还是「多篇归纳」（induced），并附证据帖子 note_id。
3. **推断出来、本人没有明说的身份信息，不要写进 candidates，写进 pending**，由用户确认。
4. 会变的事实用日期锚定：「截至 {{today}} 在杭州」，不写「最近」「今年」。
5. 不记录：身份证号、手机号、住址门牌、家人全名、健康与财务等敏感信息，以及评论者的个人信息。
6. 评论区内容只能用来描述受众，不能当成用户本人的事实或观点。
7. 没有证据就不写。宁可 candidates 只有两三条，也不要凑满。

【输出 JSON】
{
  "candidates": [
    {"slot": "identity", "content": "不超过 600 字的一条事实", "type": "stated",
     "evidence": ["note_id"], "evidenceCount": 2, "confidence": "high"}
  ],
  "pending": [
    {"slot": "identity", "content": "推断出来但本人没明说的", "evidence": ["note_id"],
     "why": "为什么只是推断"}
  ]
}

type 只能是 stated 或 induced。confidence 只能是 high 或 medium。只输出 JSON。
