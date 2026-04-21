# mini-agent-web

Mini-Agent HTTP API Service + Web Chat UI，配套网站集成方案。

## 架构

```
网站(你的域名/chat) 
    ↓ HTTPS
Nginx (/mini-agent/ 代理)
    ↓
FastAPI 服务 (localhost:<YOUR_MINI_AGENT_PORT>)
    ↓ 内部调用
Mini-Agent (Agent 类 + LLMClient)
    ↓
MiniMax API
```

## 目录结构

```
mini-agent-web/
├── server/                 # Mini-Agent HTTP API 服务
│   ├── server.py          # FastAPI 包装层（核心）
│   ├── config-example.yaml # 配置文件示例
│   └── system_prompt.md    # 系统提示词
├── nginx/
│   └── your-site.com       # Nginx 站点配置（将 your-domain.com 替换为你的域名）
├── website/
│   ├── ai-section.html     # 网站新增的「AI 工具」分区 HTML
│   ├── ai-chat-tool.html   # AI Chat 工具卡片 + Modal HTML/CSS/JS
│   └── injections.txt      # 需要注入到 index.html 的 JS 代码片段
└── README.md
```

## 部署步骤

### 1. 配置 Mini-Agent

```bash
cd server/
cp config-example.yaml config.yaml
# 编辑 config.yaml，填入你的 MiniMax API Key
```

### 2. 安装依赖 & 启动服务

```bash
cd server/
pip install mini-agent fastapi uvicorn
python server.py
# 或用 systemd 管理（见 server/ 目录）
```

### 3. Nginx 配置

```bash
# 1. 将 nginx/your-site.com 复制到 Nginx 配置目录
# 2. 替换文件中的 your-domain.com 为你的真实域名
# 3. 替换 <YOUR_FEEDBACK_API_PORT> 和 <YOUR_MINI_AGENT_PORT> 为实际端口
sudo cp nginx/your-site.com /etc/nginx/sites-available/your-domain.com
sudo ln -s /etc/nginx/sites-available/your-domain.com /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

### 4. 网站集成

将 `website/` 下的三个文件内容合并到你的 `index.html`：

```html
<!-- 1. 在工具 grid 区域添加「AI 工具」分区（参考 ai-section.html） -->

<!-- 2. 在 </body> 前添加 AI Chat Modal（参考 ai-chat-tool.html） -->

<!-- 3. 在 TOOL_INIT_MAP 中添加 chat:true -->
const TOOL_INIT_MAP = {
  ...
  chat:true
};
```

## API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | 服务健康检查 |
| GET | `/health` | 返回 `{"status":"ok"}` |
| POST | `/chat` | 对话接口 |

### POST /chat

**请求体：**
```json
{
  "message": "你好",
  "session_id": "可选，用于保持会话"
}
```

**响应：**
```json
{
  "session_id": "session-1",
  "response": "你好！有什么可以帮你的吗？",
  "thinking": "...",
  "tool_calls": []
}
```

## 环境变量

| 变量 | 说明 |
|------|------|
| `MINIMAX_API_KEY` | MiniMax API Key（从 config.yaml 或环境变量读取） |

## License

MIT
