# ① 监控面板设计（待实现）

## 总体方案

做一个 corp-assistant 专用监控面板，目标不是通用 OpenClaw 监控，而是把这条生产链路打通：

  企业微信消息
  -> corp-assistant session
  -> 模型路由
  -> sqlbot-workspace-dashboard skill
  -> SQLBot API
  -> SQL / record / artifacts
  -> assistant 回复
  -> 企业微信投递

  面板第一版建议采用“只读采集 + 后续轻量埋点”：

- P0：只读已有文件，快速上线可观测面板。

- P1：补充 SQLBot skill 结构化 trace，精确记录 SQLBot 执行阶段。

- P2：补充企微入口/出口埋点，精确记录消息接收和投递结果。

- P3：增加告警和运维操作，但默认保持只读。
  面板信息架构
1. 链路总览
   
   - 企微通道状态
   - corp-assistant agent 状态
   - SQLBot API 探活状态
   - 今日请求数、成功率、失败率、空结果率
   - 平均耗时、p95 耗时
   - 活跃用户数、活跃 session 数
   - 最近错误 Top N

2. 请求追踪
   
   - 每条用户请求一行：
     - 时间
     - 企业微信用户
     - sessionKey
     - sessionId
     - 问题摘要
     - skill 是否命中
     - SQLBot chat_id
     - SQLBot record_id
     - 状态：ok / empty / error / timeout / delivery_failed
     - 总耗时
   - 支持按用户、session、状态、record_id、时间范围筛选。

3. 单次请求详情
   
   - 展示 waterfall：
     message_received
     -> agent_started
     -> session_status
     -> skill_loaded
     -> sqlbot_start_chat
     -> sqlbot_question_stream
     -> sqlbot_record_data
     -> artifact_render
     -> state_save
     -> assistant_response
     -> message_sent
   
   - 展示 SQL 摘要、返回行数、字段、图表类型、错误原因。
   
   - 直接关联 artifacts：
     
     - raw-result.json
     - normalized.json
     - data.csv
     - chart.png

4. Session 视图
   
   - 每个企业微信用户一行：
     - 用户标识
     - sessionKey
     - sessionId
     - 当前 workspace
     - 当前 datasource
     - 当前 SQLBot chat_id
     - 最近 record_id
     - 最近问题
     - 最近更新时间
   - 标记异常：
     - datasource 缺失
     - 使用 default scope
     - session 频繁重建
     - SQLBot chat 为空但已有连续追问
     - artifacts 缺失

5. SQLBot 运行视图
   
   - SQLBot API 是否可达
   - 默认 workspace/datasource 是否可解析
   - 最近 SQLBot record 列表
   - query 成功/失败/空结果统计
   - chart 渲染成功率
   - data_csv / normalized_json 生成成功率
   
   数据来源
   当前无需改动即可读取：
- Agent session registry：
  /root/.openclaw/agents/corp-assistant/sessions/sessions.json

- Agent 对话和工具调用：
  /root/.openclaw/agents/corp-assistant/sessions/*.jsonl

- SQLBot session 状态：
  /root/.openclaw/workspace-corp-assistant-prod/skills/sqlbot-workspace-dashboard/.sqlbot-skill-state.json

- SQLBot artifacts：
  /root/.openclaw/workspace-corp-assistant-prod/skills/sqlbot-workspace-dashboard/artifacts/

- 配置健康：
  /root/.openclaw/logs/config-health.json

---

# ② Skill 更改建议（已完成）

## SQLBot Skill 更改建议
  建议重点改 sqlbot_skills.py，但保持兼容现有 CLI。
1. 增加 trace 参数
   
   - 新增：
     --trace-id <trace_id>
     --trace-file <path>
     --emit-trace
   
   - 如果没有传 --trace-id，skill 自动生成：
     sqlbot:<session_id>:<timestamp>:<pid>

2. 增加结构化事件日志
   
   - 默认写到：
     /root/.openclaw/monitoring/corp-assistant/sqlbot-events.jsonl
   
   - 每个事件一行 JSON：
     {
       "trace_id": "...",
       "ts": "...",
       "stage": "sqlbot.ask.start",
       "status": "ok",
       "session_key": "...",
       "session_id": "...",
       "workspace": "默认工作空间",
       "datasource": "水果通数据库",
       "chat_id": 147,
       "record_id": 372,
       "duration_ms": 1234,
       "error_kind": null,
       "error_message": null
     }

3. 拆分 SQLBot 执行阶段埋点
   
   - session_context.resolve
   - state.load
   - workspace.resolve
   - datasource.resolve
   - chat.start
   - question.stream
   - record.data.fetch
   - result.normalize
   - chart.plan
   - artifact.write_raw
   - artifact.write_csv
   - artifact.render_chart
   - state.save
   - ask.finish

4. 错误分类标准化
   
   - 当前错误多是文本摘要，建议增加稳定字段：
     - config_error
     - auth_error
     - network_error
     - sqlbot_api_error
     - sql_execution_error
     - timeout
     - empty_result
     - artifact_error
     - state_error
   - 面板不要靠字符串猜错误类型。

5. 给 ask 返回体增加 telemetry 字段
   
   - 保持现有返回结构不破坏，新增：
     {
       "telemetry": {
     
         "trace_id": "...",
         "started_at": "...",
         "finished_at": "...",
         "duration_ms": 65000,
         "stage_durations_ms": {
           "chat.start": 300,
           "question.stream": 61000,
           "artifact.render": 800
         }
     
       }
     }

6. 生成 artifact manifest
   
   - 每次 ask 在 artifact 目录写：
     manifest.json
   
   - 内容包括：
     
     - trace_id
     - session_id
     - session_key
     - question
     - workspace/datasource
     - chat_id/record_id
     - row_count
     - chart_kind
     - SQL 摘要
     - 各 artifact 文件路径
     - 生成状态

7. 增加健康检查命令
   
   - 新增：
     python3 sqlbot_skills.py health
   
   - 检查：
     
     - .env 是否存在
     - base_url 是否可访问
     - API key 是否有效
     - 默认 workspace 是否存在
     - 默认 datasource 是否存在
     - state 文件是否可读写
     - artifacts 目录是否可写

8. 增加只读 session list
   
   - 新增：
     python3 sqlbot_skills.py session list
   
   - 用于面板展示所有 SQLBot session 绑定，避免面板直接理解 state 文件内部结构。

9. 隐私与脱敏
   
   - trace 中不记录 API key、secret、完整 .env。
   - 用户问题可以保留，但面板应支持摘要展示。
   - SQL 默认展示 excerpt，完整 SQL 只在详情页展开。
   
   推荐第一阶段交付
   先做三件事：

10. 只读面板：读取现有 session、state、artifact，展示请求和 session。

11. 给 SQLBot skill 增加 --emit-trace、--trace-id、事件 JSONL。

12. 给每次 ask 生成 manifest.json，让面板不再反向解析复杂 artifact 目录。
