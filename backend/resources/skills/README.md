# 内置求职技能（ASu-skills）

来源：https://github.com/Hisn00w/ASu-skills （MIT License）
内置方式：每个技能一个目录，SKILL.md（YAML frontmatter：name/description + 操作指南）+ references/。

加载模式为"渐进式加载"：技能目录清单（名称+一句话描述）进入 Agent 系统提示；
模型判断用户任务匹配某技能时，通过 `load_skill` 工具按需加载完整指南再执行。
