# Round 7 Real Course Test

## Environment

* Windows
* RTX 5070 12GB
* 32GB RAM
* Ollama
* `qwen3-vl:8b`
* `qwen38-27b-main:latest`

## Course Source

新东方云课堂：专升本高等数学课程。

No account, password, or personal information recorded.

## Runtime

开始时间：2026-09-23 10:53:54  
停止请求：2026-09-23 11:07:15  
最终阶段结束：2026-09-23 11:11:44  
课程持续时间：约 13 分钟实时阶段，停止和最终阶段约 4.5 分钟

Session directory:

`sessions/2026-09-23_10-53-54`

## Audio

实际表现：

* Hearsay 成功捕获系统音频。
* Whisper 使用 `turbo` / `zh` / CUDA。
* 首次 transcript 约在课程开始后 5 分钟出现，之后持续产生。
* `transcript.md` 最终 11420 bytes。
* `events.jsonl` 共 423 条事件。
* 中文转写整体可用，但存在明显重复和口语误转，例如连续“对对对对对对对对对对”。
* 数学课程中的术语有一定错听/断句，但核心内容仍可理解。

## Vision

PPT：

* 成功识别课程标题页、专升本高等数学、课程信息。
* PPT 静止时没有大量重复 VisualEvent；最终仅 8 条 visual events，8 条描述均不同。

板书：

* 能发现右侧板书新增，例如 `4° 听懂 √`、`做练习`。
* 手写内容识别不稳定，出现 `4+日+田+⑧` 这类不可确认文本。

公式：

* 本段实际课程主要处于学习方法/课程介绍阶段，未观察到完整公式推导。
* 模型没有编造明确公式，但也没有可验证的公式识别结果。

图表：

* 后期画面中出现手绘几何图形，视觉事件提到角度标记 `30°` 和 `10°`。
* 该识别无法由当前测试独立确认，置信度有限。

## Fusion

实时课程状态：

* `course_state.jsonl` 共 5 次更新。
* 主题识别为“学习方法”。
* 能融合 transcript 和 visual events，形成“听懂不等于学会”“必须自己从头做题”“基础扎实对综合题重要”“每天至少三小时学习”等知识点和强调。

实时笔记：

* `live_notes.md` 最终 10619 bytes。
* 实时笔记有明显学习价值，不只是“老师说了/屏幕出现了”。
* 能提取老师解释、强调、易错点和对应板书。
* 存在少量过度概括和待确认项，例如“基础梳理的具体方法需要确认”。

## Tail Data

课程最后 30–60 秒：

* 最后阶段有新的 transcript 内容，例如“第一章系统梳理基础知识”“每天坚持学习”等。
* 最后阶段视觉事件包含 `11:07` 的新板书变化。
* 这些内容已进入 `live_notes.md` 和 `course_state.jsonl`。

是否进入 Final Fusion：

* 进入 Final Fusion 路径，但 Final Fusion 失败。
* `partial_notes.md` 原因为：

```text
Final Fusion failure — full final_summary.md skipped
```

是否出现在 final_summary.md：

* 否。`final_summary.md` 未生成。

## Final Summary

是否成功：否

结果：

* `final_summary.md` 不存在。
* `partial_notes.md` 已生成，40898 bytes。
* `partial_notes.md` 包含 transcript、visual events、live_notes 和 course_state 的汇总，可作为部分笔记使用。

质量观察：

* 实时阶段的融合结果可用。
* 最终总结阶段失败，不能作为完整复习材料交付。

## VRAM Lifecycle

实时阶段：

* `/api/ps` 只看到 `qwen3-vl:8b`。
* 未看到 `qwen38-27b-main:latest` 同时驻留。

最终阶段：

* 实时模型释放后 `/api/ps` 为空。
* 随后加载 `qwen38-27b-main:latest` 进入 Final Fusion/Final Summary。
* Final Fusion 失败后，27B 最终被释放。

最终 `/api/ps`：

```json
{"models":[]}
```

## Problems

P1 — Final Fusion failure prevented final summary.

* 实际影响：课程结束后未生成 `final_summary.md`，只生成 `partial_notes.md`。
* 复现方式：运行真实课程约 13 分钟后，使用 `stop_course.ps1` 正常停止。
* 证据：session 中 `final_summary.md` 不存在，`partial_notes.md` 原因写明 `Final Fusion failure — full final_summary.md skipped`。
* 说明：实时听、看、融合可用，但最终交付物缺失，因此当前版本不能视为完整可用。

P2 — Vision 对手写板书识别不稳定。

* 实际影响：`4° 听懂`、`做练习` 等可识别，但手写符号出现 `4+日+田+⑧` 等不可确认内容。
* 复现方式：观察 `visual_events.jsonl` 中板书变化事件。
* 说明：不阻塞基本听课，但会影响板书复习质量。

P2 — Whisper 中文转写有重复和断句问题。

* 实际影响：部分口语内容重复，如连续“对对对对对对对对对对”；术语和长句可能被切碎。
* 复现方式：检查 `transcript.md` / `events.jsonl`。
* 说明：核心意思仍可抓住，但转写质量还不是最终复习级别。

P3 — 本段课程未覆盖完整公式/例题场景。

* 实际影响：无法评价公式推导、复杂例题和代码/图表场景。
* 复现方式：本次课程实际内容以学习方法、课程介绍和基础板书为主。

## Recommendation

当前版本是否已经具备“可以实际听课”的基本可用性：**部分具备，但不能正式交付完整课后总结。**

实时听、看、融合已经能形成有价值的 live notes；12GB 分时生命周期也成立。阻塞项是 Final Fusion 在真实课程数据上失败，导致没有 `final_summary.md`。在修复这一点之前，只能作为实时笔记工具使用，不能作为完整的课后复习总结工具。
