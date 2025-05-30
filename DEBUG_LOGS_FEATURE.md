# 调试日志功能说明

## 概述

为 hajimir 项目添加了详细的调试级别日志，用于记录 OpenAI API 响应的完整消息和正则处理后的消息。这有助于开发和调试过程中了解数据处理的关键步骤。

## 新增的调试日志

### 1. 非流式响应调试日志 (`src/openai_client.py`)

**位置**：`execute_non_stream_openai_request` 函数

**添加的日志**：
- `请求体`: 发送到目标API的完整请求体内容（换行JSON格式）
- `目标API原始响应内容`: 显示从目标 API 收到的完整原始响应
- `正则处理后的响应内容`: 应用正则规则后的最终响应内容

### 2. 流式响应调试日志 (`src/openai_client.py`)

**位置**：`execute_stream_openai_request` 函数

**添加的日志**：
- `流式请求体`: 发送到目标API的完整流式请求体内容（换行JSON格式）
- `流式响应完整原始内容`: 收集的完整流式响应原始内容
- `流式响应正则处理后内容`: 正则处理后的完整内容

### 3. 模拟流式响应调试日志 (`src/streaming_utils.py`)

**位置**：`fake_stream_generator_from_non_stream` 函数

**添加的日志**：
- `模拟流式响应正则处理后内容`: 正则处理后的助手消息内容

## 启用调试日志

### 配置要求

在 `config/settings.yaml` 中设置：
```yaml
log_level: "DEBUG"
```

### 日志条件检查

所有调试日志都使用条件检查以避免不必要的性能开销：
```python
if logger.isEnabledFor(logging.DEBUG):
    logger.debug(f"调试信息: {详细内容}")
```

## 调试日志示例

### 请求体示例
```
2024-01-20 10:30:44 - hajimir - 调试 - 请求体: {
  "model": "gpt-3.5-turbo",
  "messages": [
    {
      "role": "user",
      "content": "请简单介绍一下人工智能。"
    }
  ],
  "stream": false,
  "temperature": 0.7,
  "max_tokens": 2048
}
```

### 非流式响应示例
```
2024-01-20 10:30:45 - hajimir - 调试 - 目标API原始响应内容: {
  "id": "chatcmpl-123",
  "object": "chat.completion",
  "created": 1705708245,
  "choices": [
    {
      "message": {
        "role": "assistant",
        "content": "人工智能是计算机科学的一个分支..."
      },
      "finish_reason": "stop"
    }
  ]
}
```

### 正则处理后响应示例
```
2024-01-20 10:30:45 - hajimir - 调试 - 正则处理后的响应内容: {
  "id": "chatcmpl-123",
  "object": "chat.completion", 
  "choices": [
    {
      "message": {
        "role": "assistant",
        "content": "AI是计算机科学的一个分支..."
      },
      "finish_reason": "stop"
    }
  ]
}
```

### 流式响应示例
```
2024-01-20 10:30:45 - hajimir - 调试 - 流式响应完整原始内容: 人工智能是计算机科学的一个分支，旨在创建能够执行通常需要人类智能的任务的系统...
2024-01-20 10:30:45 - hajimir - 调试 - 流式响应正则处理后内容: AI是计算机科学的一个分支，旨在创建能够执行通常需要人类智能的任务的系统...
```

## 测试脚本

使用 `test_debug_logs.py` 脚本可以测试调试日志功能：

```bash
python test_debug_logs.py
```

该脚本会：
1. 发送非流式请求并检查相关调试日志
2. 发送流式请求并检查相关调试日志
3. 提供详细的测试反馈

## 性能考虑

- 调试日志仅在日志级别为 DEBUG 时生效
- 使用 `logger.isEnabledFor(logging.DEBUG)` 进行条件检查
- JSON 格式化仅在需要时执行，避免不必要的性能开销
- 流式响应的内容收集不会影响实时流式传输

## 用途

1. **开发调试**：了解 API 响应的原始结构
2. **正则规则验证**：验证正则替换规则是否按预期工作
3. **问题排查**：当客户端接收到异常响应时追踪数据处理过程
4. **性能监控**：了解响应数据的大小和结构
5. **合规审计**：记录数据处理的完整过程

## 注意事项

- 调试日志可能包含敏感信息，生产环境中请谨慎使用
- 大量的调试日志可能影响性能，建议仅在开发和测试环境中启用
- 确保日志存储有足够的空间来处理详细的调试信息