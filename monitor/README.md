# Corp Assistant Monitor

Corp Assistant 监控面板 —— 基于 FastAPI + Alpine.js 的单页运维看板，运行于 192.168.4.15，通过 nginx（192.168.4.11）对外暴露。

---

## 功能

- **总览**：今日请求总数、成功率、平均/P95 耗时、错误数、活跃 Session 数、最近错误列表（10 秒自动刷新）
- **请求追踪**：可按状态/日期/Session 过滤的 trace 列表，点击行查看各执行阶段的瀑布图
- **Session 视图**：当前所有 scope 的 workspace/datasource/chat 绑定状态，异常行高亮提示
- **SQLBot 健康**：主动探活 SQLBot API，显示可达状态、HTTP 状态码和错误原因

---

## 目录结构

```text
monitor/
├── app.py                          # FastAPI 主程序（路由、API、认证中间件）
├── auth.py                         # bcrypt 密码存储 + itsdangerous cookie 会话
├── config.toml                     # 运行时配置（路径、端口、超时等）
├── requirements.txt                # Python 依赖
├── readers/
│   ├── trace.py                    # 读 sqlbot-events.jsonl，计算 overview / trace 列表 / 瀑布
│   ├── state.py                    # 读 .sqlbot-skill-state.json，检测 session 异常
│   ├── artifacts.py                # 扫描 artifacts/ 目录，读取 manifest.json
│   └── sqlbot_health.py            # 从 skill .env 解析 SQLBOT_BASE_URL，GET /api/v1/datasource/list
├── templates/
│   ├── login.html                  # 登录页（Jinja2 + Bootstrap 5）
│   └── change_password.html        # 首次登录强制改密页
├── static/
│   └── index.html                  # 单页前端（Alpine.js 3 + Chart.js 4 + Bootstrap 5，全 CDN）
├── corp-assistant-monitor.service  # systemd unit 文件
├── nginx.conf.example              # 192.168.4.11 上的 nginx 反代配置示例
└── setup.sh                        # 一键部署脚本（在 192.168.4.15 上运行）
```

---

## 网络拓扑

```
用户浏览器
    │
    ▼
192.168.4.11:80  (nginx)
    │  proxy_pass
    ▼
192.168.4.15:8765  (corp-assistant-monitor, 本服务)
    │
    ├── 读取（只读）
    │   ├── sqlbot-events.jsonl
    │   ├── .sqlbot-skill-state.json
    │   ├── artifacts/
    │   └── .env（仅读取 SQLBOT_BASE_URL）
    │
    └── HTTP 探活
        └── 192.168.4.11  SQLBot /api/v1/datasource/list
```

ufw 规则仅允许来自 192.168.4.11 的入站访问 8765 端口，直接访问 `192.168.4.15:8765` 会被防火墙拦截。

---

## 数据来源（均只读，位于 192.168.4.15）

| 数据 | 路径 |
|---|---|
| Trace 事件 | `.../skills/sqlbot-workspace-dashboard/monitoring/sqlbot-events.jsonl` |
| Session 状态 | `.../skills/sqlbot-workspace-dashboard/.sqlbot-skill-state.json` |
| 查询产物 | `.../skills/sqlbot-workspace-dashboard/artifacts/` |
| skill 环境变量 | `.../skills/sqlbot-workspace-dashboard/.env` |

完整路径在 `config.toml` 中配置。

---

## 部署

### 前置条件

- Ubuntu 24.04，Python 3.12.3，systemd，ufw
- `corp-assistant` 和 `sqlbot-workspace-dashboard` skill 已正常运行
- nginx 已安装在 192.168.4.11

### 一键部署（192.168.4.15）

```bash
# 将 monitor/ 目录传到服务器
scp -r monitor/ root@192.168.4.15:/opt/corp-assistant-monitor

# 执行部署脚本
ssh root@192.168.4.15 "bash /opt/corp-assistant-monitor/setup.sh"
```

`setup.sh` 会完成：创建 venv、安装依赖、配置 ufw 规则、注册并启动 systemd 服务。

### 配置 nginx（192.168.4.11）

```bash
cp monitor/nginx.conf.example /etc/nginx/sites-available/corp-monitor
ln -s /etc/nginx/sites-available/corp-monitor /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx
```

---

## 配置

编辑 `config.toml`（部署后位于 `/opt/corp-assistant-monitor/config.toml`）：

```toml
[server]
host = "0.0.0.0"
port = 8765
secret_key = ""          # 留空则首次启动自动生成并写回

[data]
trace_file    = "/root/.openclaw/workspace-corp-assistant-prod/skills/sqlbot-workspace-dashboard/monitoring/sqlbot-events.jsonl"
state_file    = "/root/.openclaw/workspace-corp-assistant-prod/skills/sqlbot-workspace-dashboard/.sqlbot-skill-state.json"
artifacts_dir = "/root/.openclaw/workspace-corp-assistant-prod/skills/sqlbot-workspace-dashboard/artifacts/"
sessions_dir  = "/root/.openclaw/agents/corp-assistant/sessions/"
skill_env     = "/root/.openclaw/workspace-corp-assistant-prod/skills/sqlbot-workspace-dashboard/.env"
tail_lines    = 5000     # 最多读取 trace 文件的最后 N 行

[sqlbot]
health_timeout = 5       # 探活超时（秒）
```

---

## 认证

- 用户信息存储于 `users.json`（bcrypt 哈希，不存明文密码）
- 默认账号 `admin` / `admin`，首次登录强制修改密码（最少 8 位）
- 会话 Cookie 名称：`cam_session`，有效期 24 小时
- 所有 `/api/*` 端点未认证时返回 HTTP 401，前端自动跳转登录页

---

## 运维命令

```bash
# 查看服务状态
systemctl status corp-assistant-monitor

# 查看日志（最近 100 行）
journalctl -u corp-assistant-monitor -n 100

# 重启服务
systemctl restart corp-assistant-monitor

# 停止服务
systemctl stop corp-assistant-monitor
```

---

## API 端点

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/overview` | 今日统计汇总 |
| GET | `/api/traces` | trace 列表（可选 `?status=&date=&session=`） |
| GET | `/api/traces/{trace_id}` | 单条 trace 详情（含各阶段耗时） |
| GET | `/api/sessions` | 所有 scope 的 session 状态 |
| GET | `/api/sqlbot/health` | SQLBot 探活结果 |
| GET | `/api/artifacts` | 产物文件列表（可选 `?trace_id=`） |
