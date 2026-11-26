---
id: page_plan
title: 页序方案
output: json
source: 照搬 Beav type-playbooks（通用约定 + 页面角色映射 + 11 种形式骨架）
---

给一篇图文笔记排页序。你排的是"读者任务"，不是图上要写的字。

【选题】{{topicTitle}}
【内容形式】{{contentForm}}
【可用的真实素材】{{material}}
【这条绝不能被改成】{{intentMustNotDrift}}
【作者动机】{{intentGoal}}

排页序之前先看一眼上面那条禁止项：页序决定了读者先看到什么，最容易把一条"观察"排成"教程"。
如果按当前形式排下去会自然落进那句禁止里，就在 note 里说明并把页序改到不越界为止。

【通用约定】
- 页数由内容任务、素材和阅读节奏决定。常见区间：简单内容 2–3 张，一般 4–7 张，信息多的 7–10 张。配文越长、要点越多，页数越多。单图短文同样成立。
- 页序骨架：封面 → 问题或背景 → 核心内容逐页展开 → 收束。收束页可以是最后一个有内容的页、总结页或总表，不默认加引导关注或互动页。
- 结尾默认不加 CTA。
- 不要把页码或角色名渲染到图里。

【每个页面角色对应的 imageJob】
| 页面角色 | imageJob |
| --- | --- |
| 封面 | attention_hook |
| 问题场景、痛点 | narrative_scene；只营造气氛时用 mood_or_world |
| 总览、目录、合集总表、速查清单 | information_card |
| 误区与正解、前后对比、选项对照 | comparison |
| 步骤、操作过程 | demonstration；读者要在图上跟着做时用 information_card |
| 细节、截图、资质、现场 | evidence |
| 情绪收尾、余韵 | mood_or_world |

【图上文字档位，四选一】
- none：画面自己说话，不加字
- anchor：加一个词或一个名字就够
- annotation：标细节、标位置、标数量
- operational：读者要在图上跟着做，需要完整步骤文案

【11 种形式的页序要点】
- collection_list 合集清单：封面 → 总表/目录（information_card）→ 逐项展开（每项一个页面，comparison 或 evidence）→ 收束。张数 5–9。
- operational_guidance 教程攻略：封面 → 适用前提 → 步骤逐页（demonstration 或 information_card）→ 易错点 → 收束。张数 5–10。
- evidence_review 实测种草：封面 → 结论前置（information_card）→ 证据逐条（evidence）→ 边界与不适合谁 → 收束。张数 4–8。
- decision_comparison 测评对比：封面 → 对比维度总表（information_card）→ 两两对照（comparison）→ 怎么选（decision）→ 收束。张数 4–7。
- sensory_experience 探店体验：封面 → 场景 → 细节体验 → 一句判断。张数 3–6。
- life_record 生活记录：封面 → 场景序列（narrative_scene）→ 收束（mood_or_world）。张数 4–9，允许无文字。
- narrative_sequence 个人故事：封面 → 起点 → 转折 → 结果 → 一句回看。张数 4–8。
- opinion_short 观点：封面 → 论点（一句话）→ 三条支撑（comparison 或 information_card）→ 让步或边界 → 收束。张数 3–6。
- emotional_resonance 情绪共鸣：封面 → 场景命名 → 三个具体片段 → 一句收束。张数 3–5，字少图强。
- knowledge_explainer 知识科普：封面 → 结论 → 框架（information_card）→ 分点展开 → 例子 → 边界 → 收束。张数 6–10。
- proof_led_commercial 商业转化：封面 → 具体结果（evidence）→ 怎么做到 → 适合谁 → 行动入口。张数 4–7。

【判断原则】
内容有知识点，不等于每页都该做信息卡。画面能直说的，就不要用文字卡片替代。

【输出格式：只输出 JSON】
{
  "contentForm": "确认后的形式，如果原形式不合适可以改并说明",
  "countRange": [最小张数, 最大张数],
  "pages": [
    {
      "index": 1,
      "role": "cover | overview | problem | point | evidence | step | boundary | summary | mood",
      "imageJob": "attention_hook | mood_or_world | narrative_scene | evidence | demonstration | comparison | information_card",
      "textLevel": "none | anchor | annotation | operational",
      "text": "图上要写的字（可为空）",
      "readerTask": "这一页让读者完成什么",
      "uniqueGain": "这一页给读者什么别处没有的信息"
    }
  ],
  "note": "一句话说明为什么这样排"
}
