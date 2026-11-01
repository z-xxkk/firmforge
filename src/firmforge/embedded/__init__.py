"""按硬件资料约束代码的嵌入式开发流程（DECISIONS 第 66–79 条）。

和旧流程（tasks/ 下与硬件无关的纯逻辑题，STM32 + 主机单元测试）分开放，旧流程保持不变：
- prompting：每个 Agent 的 system prompt = 固定开头 + 硬件档案 + 相关规则（第 67 条）
- design：设计分层 Agent，分出硬件层与逻辑层并拆模块（第 66 条第 ④ 步）
- roles：ESP-IDF 编码、写测试、诊断 Agent 的消息
"""
