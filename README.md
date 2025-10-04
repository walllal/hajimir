# 通用 OpenAI 格式反代注入服务 (自定义提示词、动态变量与正则处理)

本项目是一个使用 FastAPI 构建的通用 OpenAI 格式反代注入服务。它接收标准的 OpenAI Chat Completion API 格式请求，并将请求代理转发到任意的 OpenAI 兼容 API 端点，同时支持强大的提示词模板注入、动态变量处理和响应后处理功能。

## ✨ 核心特性

### 📡 通用反代注入
- **动态目标提取**: 从请求 URL 中提取目标 OpenAI 兼容 API 端点
- **URL 格式**: `/{http(s)://target.domain.com}/v1/chat/completions`
- **多种认证方式**: 支持 Authorization 头或 URL 查询参数传递 API 密钥
- **参数管理**: 忽略客户端参数，强制使用配置文件中的默认生成参数

### 🎯 提示词模板注入
- **智能模板选择**: 根据用户输入内容自动选择合适的模板（有输入/无输入）
- **历史消息注入**: 支持通过 `api_input_placeholder` 在模板中注入历史对话
- **变量替换**: 模板中的 `{{user_input}}` 会被替换为用户的实际输入
- **消息合并**: 自动合并相邻的同角色消息，优化对话结构

### 🎲 动态变量系统
- **骰子投掷**: `{{roll XdY}}` - 模拟投掷 X 个 Y 面骰子并替换为总点数。
- **随机选择**: `{{random::选项1::选项2::选项3}}` - 从提供的选项中随机选择一个。
- **变量设置**: `{{setvar::变量名::值}}` - 设置一个变量，用于在对话中传递和存储状态。
- **变量获取**: `{{getvar::变量名}}` - 获取已设置的变量值。
- **实时处理**: 每次请求时动态计算，确保结果的随机性和状态的实时性。

### 🔧 响应后处理
- **正则表达式规则**: 对 API 响应内容应用自定义的查找替换规则。
- **JSON 载荷注入**: 支持向响应中注入结构化 JSON 数据。
- **规则级联**: 按定义顺序依次应用多个正则规则。

### 🌊 流式与非流式支持
- **真实流式**: 直接代理目标 API 的流式响应。
- **非流式**: 处理普通的完整响应。
- **错误处理**: 完善的超时和错误处理机制。

## 🚀 部署 (Deployment)

您可以选择使用 Docker (推荐) 或手动进行部署。

### 使用 Docker (推荐)

这是最简单、最推荐的部署方式，它将应用及其所有依赖项打包到一个隔离的容器中。

1.  **安装 Docker**: 确保您的系统已经安装了 Docker 和 Docker Compose。

2.  **配置服务**:
    -   复制配置文件模板：
        ```bash
        cp config/settings.yaml.example config/settings.yaml
        ```
    -   根据需要编辑 `config/settings.yaml` 和 `templates/` 目录下的模板文件。

3.  **启动服务**: 在项目根目录下，运行以下命令：
    ```bash
    docker compose up --build -d
    ```
    服务将在 `http://localhost:8001` 上运行。

    > **热更新说明**: `docker-compose.yml` 文件默认配置了目录挂载。这意味着您在本地修改 `config` 和 `templates` 目录下的文件时，容器内的应用会**自动热重载**，无需重启容器。同时，`src` 目录也被挂载，并开启了 `--reload` 模式，方便开发时修改代码后服务自动重启。

### 手动安装部署

1.  **安装依赖**:
    ```bash
    pip install -r requirements.txt
    ```

2.  **配置服务**:
    -   复制配置文件模板：
        ```bash
        cp config/settings.yaml.example config/settings.yaml
        ```
    -   编辑 `config/settings.yaml` 配置文件，调整各项参数。
    -   准备提示词模板文件：
        - `templates/with_input.yaml` - 用户有输入时的模板
        - `templates/without_input.yaml` - 用户无输入时的模板

3.  **启动服务**:
    ```bash
    # 默认端口为 8001
    uvicorn src.main:app --host 0.0.0.0 --port 8001
    ```

## 📖 API 使用方法

### 基本请求格式
```
POST /{target_url}/v1/chat/completions
```

### URL 示例
服务默认运行在 `8001` 端口。

```bash
# 代理到 OpenAI 官方 API
curl -X POST "http://localhost:8001/https://api.openai.com/v1/chat/completions" \
-H "Authorization: Bearer your-openai-api-key" \
-H "Content-Type: application/json" \
-d '{
  "model": "gpt-4",
  "messages": [{"role": "user", "content": "Hello!"}],
  "stream": false
}'

# 代理到其他兼容服务 (例如 Anthropic)
curl -X POST "http://localhost:8001/https://api.anthropic.com/v1/chat/completions" \
-H "x-api-key: your-anthropic-api-key" \
-H "Content-Type: application/json" \
-d '{
  "model": "claude-3-5-sonnet-20240620",
  "messages": [{"role": "user", "content": "Hello!"}]
}'

# 使用 URL 参数传递 API 密钥
curl -X POST "http://localhost:8001/https://api.openai.com/v1/chat/completions?api_key=your-api-key" \
-H "Content-Type: application/json" \
-d '{
  "model": "gpt-4",
  "messages": [{"role": "user", "content": "Hello!"}]
}'
```

### 支持的客户端参数
服务只接受以下客户端参数，其他参数会被忽略并使用配置文件中的默认值：
- `model`
- `messages`
- `stream`

## 📁 项目结构

```
hajimir/
├── src/
│   ├── main.py               # FastAPI 应用主入口
│   ├── openai_client.py      # OpenAI 客户端逻辑
│   ├── config.py             # 配置管理
│   ├── template_handler.py   # 模板处理器 (含动态变量)
│   ├── conversion_utils.py   # 响应后处理工具
│   └── __init__.py
├── config/
│   └── settings.yaml.example # 配置模板
├── templates/
│   ├── with_input.yaml       # 有用户输入时的模板
│   └── without_input.yaml    # 无用户输入时的模板
├── .gitignore
├── Dockerfile              # Docker 镜像构建文件
├── docker-compose.yml      # 编排文件
├── requirements.txt        # Python 依赖
└── README.md               # 项目文档
```

## ⚙️ 配置说明

### 主要配置项
`config/settings.yaml`
```yaml
app_name: "hajimir"
log_level: "INFO"
debug_mode: false

proxy:
  prompt_template_path_with_input: "templates/with_input.yaml"
  prompt_template_path_without_input: "templates/without_input.yaml"
  openai_request_timeout: 60

openai_generation:
  temperature: 1.0
  max_tokens: 4096
  top_p: 1.0
  frequency_penalty: 0.0
  presence_penalty: 0.0
```

### 模板文件格式
模板文件使用 YAML 格式，支持以下类型的项：
```yaml
# 普通消息模板
- role: "system"
  content: "你是一个有用的AI助手。用户输入：{{user_input}}"

# 历史消息占位符
- type: "api_input_placeholder"

# 正则处理规则
- type: "正则"
  查找: "\\[PLACEHOLDER\\]"
  替换: "实际内容"
  action: "replace"

# JSON 载荷注入
- type: "正则"
  查找: ".*"
  替换: '{"code": "print(\"Hello World\")", "language": "python"}'
  action: "json_payload"
```

## 🔍 高级功能

### 动态变量
在模板中使用动态变量，实现更复杂的逻辑和状态管理。

```yaml
# 示例：设置并使用变量
- role: "system"
  content: "记住用户名。{{setvar::username::Alex}}"

- role: "user"
  content: "我的名字是 {{getvar::username}}。投掷一个六面骰子：{{roll 1d6}}，随机选择：{{random::苹果::香蕉::橙子}}"
```
`{{setvar}}` 标签会在处理后被移除，而 `{{getvar}}`, `{{roll}}`, `{{random}}` 会被替换为计算后的值。

### 正则后处理
对 API 响应进行自动化处理：
```yaml
- type: "正则"
  查找: "\\b(错误|error)\\b"
  替换: "修正"
  action: "replace"
```

### JSON 载荷注入
向响应中注入结构化数据：
```yaml
# 此示例将代码块提取并注入到指定的 JSON 结构中
- type: "正则"
  查找: "```python\\n(.+?)\\n```"
  替换: '{"tool_code_interpreter_output": {"code": "$1", "language": "python"}}'
  action: "json_payload"
```

## 📝 注意事项
1.  **API 密钥安全**: 确保 API 密钥的安全传输和存储。
2.  **超时设置**: 根据目标 API 的响应时间调整 `openai_request_timeout` 配置。
3.  **模板更新**: 模板文件支持热重载，修改后自动生效。
4.  **日志记录**: 详细的日志记录有助于调试和监控，可通过 `log_level` 控制。
5.  **Docker 部署**: 使用 Docker 部署时，请确保 `docker-compose.yml` 中挂载的本地目录路径正确。

## 🤝 贡献
欢迎提交 Issue 和 Pull Request 来改进项目！

## 📄 许可证
本项目采用 MIT 许可证。
