# 通用 OpenAI 格式反代注入服务 (自定义提示词、动态变量与正则处理)

本项目是一个使用 FastAPI 构建的通用 OpenAI 格式反代注入服务。它接收标准的 OpenAI Chat Completion API 格式请求，并将请求代理转发到任意的 OpenAI 兼容 API 端点，同时支持强大的提示词模板注入、动态变量处理和响应后处理功能。

## ✨ 核心特性

### 📡 通用反代注入
-   **动态目标提取**: 从请求 URL 中提取目标 OpenAI 兼容 API 端点
-   **URL 格式**: `/{http(s)://target.domain.com}/v1/chat/completions`
-   **多种认证方式**: 支持 Authorization 头或 URL 查询参数传递 API 密钥
-   **参数管理**: 忽略客户端参数，强制使用配置文件中的默认生成参数

### 🎯 提示词模板注入
-   **智能模板选择**: 根据用户输入内容自动选择合适的模板（有输入/无输入）
-   **历史消息注入**: 支持通过 `api_input_placeholder` 在模板中注入历史对话
-   **变量替换**: 模板中的 `{{user_input}}` 会被替换为用户的实际输入
-   **消息合并**: 自动合并相邻的同角色消息，优化对话结构

### 🎲 动态变量系统
-   **骰子投掷**: `{{roll XdY}}` - 模拟投掷 X 个 Y 面骰子并替换为总点数
-   **随机选择**: `{{random::选项1::选项2::选项3}}` - 从提供的选项中随机选择一个
-   **实时处理**: 每次请求时动态计算，确保结果的随机性

### 🔧 响应后处理
-   **正则表达式规则**: 对 API 响应内容应用自定义的查找替换规则
-   **JSON 载荷注入**: 支持向响应中注入结构化 JSON 数据
-   **规则级联**: 按定义顺序依次应用多个正则规则

### 🌊 流式与非流式支持
-   **真实流式**: 直接代理目标 API 的流式响应
-   **非流式**: 处理普通的完整响应
-   **错误处理**: 完善的超时和错误处理机制

## 📋 使用场景

-   **API 聚合**: 统一多个 OpenAI 兼容服务的访问接口
-   **提示词管理**: 集中管理和注入复杂的提示词模板
-   **响应定制**: 对 AI 响应进行格式化和后处理
-   **开发测试**: 为不同的 AI 服务提供统一的测试接口
-   **代理中转**: 在客户端和目标 API 之间提供增强的代理服务

## 🚀 快速开始

### 安装依赖

```bash
pip install -r requirements.txt
```

### 配置服务

1. 复制配置文件模板：
```bash
cp config/settings.yaml.example config/settings.yaml
```

2. 编辑 `config/settings.yaml` 配置文件，调整各项参数。

3. 准备提示词模板文件：
   - `templates/with_input.yaml` - 用户有输入时的模板
   - `templates/without_input.yaml` - 用户无输入时的模板

### 启动服务

```bash
python -m src.main
```

或使用 Uvicorn：
```bash
uvicorn src.main:app --host 0.0.0.0 --port 8000
```

## 📖 API 使用方法

### 基本请求格式

```
POST /{target_url}/v1/chat/completions
```

### URL 示例

```bash
# 代理到 OpenAI 官方 API
curl -X POST "http://localhost:8000/https://api.openai.com/v1/chat/completions" \
  -H "Authorization: Bearer your-openai-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4",
    "messages": [{"role": "user", "content": "Hello!"}],
    "stream": false
  }'

# 代理到其他兼容服务
curl -X POST "http://localhost:8000/https://api.anthropic.com/v1/chat/completions" \
  -H "Authorization: Bearer your-anthropic-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-3-sonnet",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'

# 使用 URL 参数传递 API 密钥
curl -X POST "http://localhost:8000/https://api.openai.com/v1/chat/completions?api_key=your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

### 支持的客户端参数

服务只接受以下客户端参数，其他参数会被忽略并使用配置文件中的默认值：

- `model` - 模型名称
- `messages` - 消息列表
- `stream` - 是否流式响应

## 📁 项目结构

```
hajimir/
├── src/
│   ├── main.py              # FastAPI 应用主入口
│   ├── openai_client.py     # OpenAI 客户端逻辑
│   ├── config.py            # 配置管理
│   ├── template_handler.py  # 模板处理器
│   ├── conversion_utils.py  # 响应后处理工具
│   └── __init__.py
├── config/
│   ├── settings.yaml        # 主配置文件
│   └── settings.yaml.example # 配置模板
├── templates/
│   ├── with_input.yaml      # 有用户输入时的模板
│   └── without_input.yaml   # 无用户输入时的模板
├── requirements.txt         # Python 依赖
└── README.md               # 项目文档
```

## ⚙️ 配置说明

### 主要配置项

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

在模板或用户输入中使用动态变量：

```yaml
- role: "user"
  content: "投掷一个六面骰子：{{roll 1d6}}，随机选择：{{random::选项A::选项B::选项C}}"
```

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
- type: "正则"
  查找: "```python\\n(.+?)\\n```"
  替换: '{"tool_code_interpreter_output": {"code": "$1", "language": "python"}}'
  action: "json_payload"
```

## 🛠️ 开发和部署

### 开发模式

```bash
# 设置调试模式
export DEBUG_MODE=true

# 启动开发服务器
python -m src.main
```

### 生产部署

```bash
# 使用 Gunicorn
gunicorn src.main:app -w 4 -k uvicorn.workers.UvicornWorker

# 使用 Docker
docker build -t hajimir .
docker run -p 8000:8000 hajimir
```

## 📝 注意事项

1. **API 密钥安全**: 确保 API 密钥的安全传输和存储
2. **超时设置**: 根据目标 API 的响应时间调整超时配置
3. **模板更新**: 模板文件支持热重载，修改后自动生效
4. **日志记录**: 详细的日志记录有助于调试和监控
5. **错误处理**: 服务会妥善处理各种错误情况并返回适当的 HTTP 状态码

## 🤝 贡献

欢迎提交 Issue 和 Pull Request 来改进项目！

## 📄 许可证

本项目采用 MIT 许可证。