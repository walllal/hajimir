# OpenAI 反向代理 (自定义提示词注入与灵活流式处理)

本项目是一个使用 FastAPI 构建的 OpenAI API 反向代理服务。它允许在将请求转发到 OpenAI 之前，动态地从 YAML 模板注入自定义的提示词，并根据客户端请求和服务器配置灵活处理流式与非流式响应。

## 特性

-   **自定义提示词注入**: 从 YAML 文件加载提示词模板。
-   **变量替换**: 支持在模板中使用 `{{api_input}}` (历史消息) 和 `{{user_input}}` (最新用户消息)。
-   **参数透传**: 原始请求中的 `model`, `temperature` 等参数会透传给 OpenAI。
-   **灵活的流式处理**:
    -   **客户端请求非流式**: 代理向 OpenAI 发送非流式请求，返回完整 JSON。
    -   **客户端请求流式**:
        -   若服务器“假流式”配置启用: 代理向 OpenAI 发送非流式请求，向客户端模拟流式响应 (心跳 + 最终完整数据)。
        -   若服务器“假流式”配置禁用: 代理向 OpenAI 发送流式请求，并将 SSE 流实时转发给客户端。
-   **配置文件驱动**: 通过 `config/settings.yaml` 进行详细配置。
-   **并发支持**: 基于 FastAPI 和 `asyncio`，为高并发设计。
-   **日志记录**: 集成了基本的日志功能。

## 目录结构

```
.
├── src/                          # 源代码目录
│   ├── __init__.py
│   ├── main.py                   # FastAPI 应用主文件
│   ├── config.py                 # 配置加载和管理模块
│   ├── openai_proxy.py           # 核心代理逻辑模块
│   └── utils.py                  # (可选) 辅助函数
├── templates/                    # 提示词模板目录
│   └── default_prompt.yaml       # 默认提示词模板 (路径可在配置中修改)
├── config/                       # 配置文件目录
│   └── settings.yaml             # 应用配置文件
├── .gitignore
├── README.md
└── requirements.txt
```

## 安装与运行

1.  **克隆仓库**:
    ```bash
    git clone <your-repo-url>
    cd <your-repo-name>
    ```

2.  **创建并激活虚拟环境** (推荐):
    ```bash
    python -m venv venv
    # Linux/macOS:
    source venv/bin/activate
    # Windows (cmd.exe):
    # venv\Scripts\activate.bat
    # Windows (PowerShell):
    # venv\Scripts\Activate.ps1
    ```

3.  **安装依赖**:
    ```bash
    pip install -r requirements.txt
    ```

4.  **配置**:
    -   编辑 `config/settings.yaml` 以配置应用参数，例如：
        -   `app_name`: 应用名称。
        -   `log_level`: 日志级别 (如 INFO, DEBUG)。
        -   `proxy.prompt_template_path`: 提示词模板文件的路径。
        -   `proxy.fake_streaming.enabled`: 是否启用“假流式”功能。
        -   `proxy.fake_streaming.heartbeat_interval`: 假流式心跳间隔（秒）。
        -   `proxy.openai_request_timeout`: 请求 OpenAI API 的超时时间（秒）。
    -   编辑 `templates/default_prompt.yaml` (或您在配置中指定的其他模板文件) 来定义您的提示词结构。

5.  **运行服务** (开发模式):
    ```bash
    python -m uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload
    ```
    或者，如果 `src/main.py` 中 `if __name__ == "__main__":` 部分的 `uvicorn.run` 未被注释掉，可以直接运行：
    ```bash
    python src/main.py
    ```
    对于生产环境，建议使用 Gunicorn + Uvicorn workers:
    ```bash
    gunicorn -w 4 -k uvicorn.workers.UvicornWorker src.main:app -b 0.0.0.0:8000
    ```

## 使用方法

将您通常发送给 OpenAI API (例如 `https://api.openai.com/v1/chat/completions`) 的请求，改为发送到本代理服务的对应路径。路径参数应为完整的 OpenAI 目标 URL。

例如，如果代理服务运行在 `http://localhost:8000`：

**原始请求 URL**: `https://api.openai.com/v1/chat/completions`

**代理请求 URL**: `http://localhost:8000/https://api.openai.com/v1/chat/completions`
(注意 `https://` 前面的 `/`，因为 `:path` 会捕获它)

或者，如果您的 HTTP 客户端对 URL 中的 `//` 有问题，可以考虑对目标 URL 进行 URL编码后作为路径参数，但这会增加客户端的复杂性。当前实现依赖于路径参数能正确捕获包含协议的 URL。

**请求头**:
确保在请求头中包含您的 OpenAI API Key:
`Authorization: Bearer YOUR_OPENAI_API_KEY`

**请求体**:
与 OpenAI API 的格式一致，例如：
```json
{
  "model": "gpt-3.5-turbo",
  "messages": [
    {"role": "user", "content": "你好！"}
  ],
  "temperature": 0.7,
  "stream": false // 或 true，取决于您希望的响应方式
}
```

## 流式响应处理

本代理根据客户端请求以及服务器配置处理流式响应：

1.  **如果客户端请求非流式** (即请求体中 `stream` 为 `false` 或未提供):
    *   代理将向 OpenAI API 发送非流式请求。
    *   代理将向客户端返回一个完整的 JSON 响应。
    *   此行为不受服务器端“假流式”配置的影响。

2.  **如果客户端请求流式** (即请求体中 `stream` 为 `true`):
    *   **且服务器“假流式”配置已启用** (`config/settings.yaml` 中 `proxy.fake_streaming.enabled: true`):
        *   代理将向 OpenAI API 发送非流式请求。
        *   代理将向客户端模拟流式响应：立即建立连接，定期发送心跳消息（例如 `data: [HEARTBEAT]\n\n`），直到从 OpenAI 获取到完整响应后，将完整数据作为流的最后一部分发送，并以 `data: [DONE]\n\n` 结束。
    *   **且服务器“假流式”配置已禁用** (`config/settings.yaml` 中 `proxy.fake_streaming.enabled: false`):
        *   代理将向 OpenAI API 发送正常的流式请求。
        *   代理会将从 OpenAI API 收到的 Server-Sent Events (SSE) 流实时转发给客户端。

## 提示词模板 (`templates/default_prompt.yaml`)

提示词模板是一个 YAML 文件，定义了一个消息对象列表。

**特殊占位符**:
-   一个字典对象 `type: api_input_placeholder`: 标记历史消息 (`messages` 数组中除了最后一条用户消息之外的内容) 的插入位置。
-   字符串 `{{user_input}}`: 将被替换为原始请求中最后一条用户消息的 `content`。

**示例**:
```yaml
- role: system
  content: "这是来自模板的系统消息。"
- type: api_input_placeholder
- role: user
  content: "这是模板中的固定用户消息，位于 {{user_input}} 之前。"
- role: user
  content: "{{user_input}}"
- role: assistant
  content: "这是模板中的固定助手消息。"
```

根据这个模板和用户的输入，最终发送给 OpenAI 的 `messages` 列表会被动态构建。