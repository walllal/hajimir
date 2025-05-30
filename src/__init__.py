"""
此包包含了通用 OpenAI 格式反代注入应用的核心源代码。

主要模块包括：
- main.py: FastAPI 应用的主入口点，定义 API 端点和应用配置。
- openai_client.py: 与目标 OpenAI 格式 API 交互的客户端逻辑。
- conversion_utils.py: OpenAI 响应的后处理工具。
- config.py: 应用配置模型和加载逻辑。
- template_handler.py: 提示词模板处理、动态变量替换和消息准备逻辑。
- streaming_utils.py: 流式响应处理工具（如果需要）。
"""

__version__ = "1.0.0"

# 此文件使得 src 目录成为一个 Python 包。