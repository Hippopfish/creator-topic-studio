---
id: comment_insight
title: 评论洞察
output: json
source: 照搬 Beav xhs-comment-insight（输入边界、六类信号、单选题输出）
---

你只做一件事：从一条笔记的评论区里，找出一个值得写的选题。

【原笔记（只作背景，不做主输入）】
标题：{{noteTitle}}
正文：{{noteBody}}

【评论（共 {{commentCount}} 条，按赞数排序）】
{{commentsText}}

【第一步：给评论分型】
逐条判断属于哪一类（一条只归一类）：
- 追问：在问具体怎么做、在哪买、为什么
- 反驳：不同意作者，或者指出作者说错
- 补充：提供自己的经验、案例、反例
- 情绪：只是表达共鸣、好笑、破防
- 行动意图：想要链接、想买、想收藏、想试
- 作者互动：作者本人在回复

【第二步：统计信号】
看这几件事：哪些评论赞最高、哪些下面回复最多、追问集中在什么问题上、被反驳的是哪一句、补充里有没有作者没提到的信息、有没有具体的行动诉求。

【第三步：提取 2-4 个真实需求或矛盾】
不要总结"评论区都在问什么"这种大话。要具体到：谁、在什么情境下、卡在哪。

【第四步：选最小切口，只输出一个选题】
选那个"评论区已经有人明确表达，但正文没回答"的点。一个就够，不要给多个。

【第五步：原笔记退到背景位】
选题的说服力来自评论区的信号，不是原笔记本来讲得好不好。

【标题规则】
≤20 字，含标点。禁止「不是A，而是B」「你以为A，其实B」「与其A，不如B」这类转折句式。禁止空钩子「你知道吗」「竟然」。禁止 AI 腔「底层逻辑」「真正的」。

【质量要求】
- content_direction 必须点名是哪个信号触发的（比如"3 条高赞追问都在问怎么拒绝"）
- 标题必须和 content_direction 讲同一件事
- 不许把评论区的情绪直接当成选题（"大家都说很真实"不是选题）
- 不许编造评论里没有的内容

【输出格式：只输出 JSON，不要 markdown 代码块外的任何文字】
{
  "content_direction": "一句话说清这篇要讲什么，点名触发信号",
  "thinking_process": ["看了什么信号", "得出什么判断", "所以选这个切口"],
  "topic": {
    "title": "20字以内",
    "connections": [1, 2]
  },
  "direction_frame": {
    "target_reader": "谁在什么时刻",
    "core_tension": "核心矛盾是什么",
    "angle": "七选一：direct_value/curiosity_gap/counterintuitive/scenario_resonance/decision_help/case_story/timely_interpretation",
    "material_entry": "从什么材料切入"
  },
  "signal_summary": {
    "ask": 0, "disagree": 0, "add": 0, "emotion": 0, "action": 0, "author": 0
  }
}
