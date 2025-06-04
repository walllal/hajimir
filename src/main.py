# src/main.py
"""
此模块是 FastAPI 应用的主入口点。
它负责初始化应用、配置日志、定义 API 端点等。
"""
import asyncio
import logging
import logging.config # 用于字典配置日志
from contextlib import asynccontextmanager
from typing import Dict, Any # 用于类型注解，新增导入 Any

import colorlog # 用于彩色日志输出
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse

from .config import settings # 导入应用配置
from .openai_client import execute_non_stream_openai_request, execute_stream_openai_request, extract_target_url_and_auth
from .template_handler import _prepare_openai_messages

# ---- 自定义彩色日志格式化器 (含中文级别) ----
class ChineseColoredFormatter(colorlog.ColoredFormatter):
    """
    自定义日志格式化器，支持颜色和中文日志级别名称。
    """
    level_name_map: Dict[str, str] = {
        "DEBUG": "调试",
        "INFO": "信息",
        "WARNING": "警告",
        "ERROR": "错误",
        "CRITICAL": "严重",
    }
    
    # 消息翻译映射
    message_translation_map: Dict[str, str] = {
        "Started server process": "已启动服务器进程",
        "Waiting for application startup.": "等待应用程序启动。",
        "Application startup complete.": "应用程序启动完成。",
        "Uvicorn running on": "Uvicorn 运行在",
        "(Press CTRL+C to quit)": "（按 CTRL+C 退出）",
        "Shutting down": "正在关闭",
        "Waiting for application shutdown.": "等待应用程序关闭。",
        "Application shutdown complete.": "应用程序关闭完成。",
        "Finished server process": "已完成服务器进程",
    }

    def format(self, record: logging.LogRecord) -> str:
        # 修改 record.levelname 为中文，以便在格式字符串中使用 %(levelname_chinese)s
        original_levelname = record.levelname
        record.levelname_chinese = self.level_name_map.get(original_levelname, original_levelname)
        
        # 翻译消息内容
        message = record.getMessage()
        for english_text, chinese_text in self.message_translation_map.items():
            if english_text in message:
                message = message.replace(english_text, chinese_text)
        
        # 临时修改 record 的消息
        original_msg = record.msg
        original_args = record.args
        record.msg = message
        record.args = ()
        
        try:
            result = super().format(record)
        finally:
            # 恢复原始消息
            record.msg = original_msg
            record.args = original_args
        
        return result

# ---- 日志配置字典 ----
# 日志级别将从 settings.log_level.upper() 获取
# 应用名称将从 settings.app_name 获取
LOGGING_CONFIG: Dict[str, Any] = {
    "version": 1,
    "disable_existing_loggers": False, # 通常设为 False
    "formatters": {
        "default_color": {
            "()": "__main__.ChineseColoredFormatter", # 指向上面定义的类
            # 格式: 时间 - 应用名称 - 级别(中文) - 消息
            # 颜色通过 colorlog 的 log_colors 和 secondary_log_colors 控制
            "format": f"%(log_color)s%(asctime)s - %(blue)s{settings.app_name}%(reset)s - %(log_color)s%(levelname_chinese)s%(reset)s - %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S",
            "force_color": settings.log_colors_enabled, # 根据配置决定是否强制颜色
            "log_colors": { # 日志级别名称的颜色
                "DEBUG": "cyan",
                "INFO": "green",
                "WARNING": "yellow",
                "ERROR": "red",
                "CRITICAL": "red,bg_white", # 例如：红字白底
            },
            "secondary_log_colors": { # 其他日志记录字段的颜色
                "message": { # 可以为不同级别的消息设置不同颜色，如果需要
                    # "ERROR": "red",
                    # "CRITICAL": "red"
                },
                "asctime": {"DEBUG": "white", "INFO": "white", "WARNING": "white", "ERROR": "white", "CRITICAL": "white"}, # 时间戳统一白色
                # 应用名称的颜色已在主格式字符串中用 %(blue)s 指定
            },
            "reset": True, # 每条日志后重置颜色
            "style": "%",
        },
        "access_color": {
            "()": "colorlog.ColoredFormatter", # Uvicorn 访问日志直接用 colorlog
            # 格式: 时间 - 应用名称 - "访问" - 消息内容
            # "访问" 硬编码为中文，并指定颜色
            "format": f"%(log_color)s%(asctime)s - %(blue)s{settings.app_name}%(reset)s - %(green)s访问%(reset)s - %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S",
            "force_color": settings.log_colors_enabled, # 根据配置决定是否强制颜色
            "log_colors": { # 访问日志通常是 INFO 级别
                "INFO": "green", # 用于 "访问" 二字的颜色 (通过 %(green)s 实现)
            },
            "secondary_log_colors": {
                 "asctime": {"INFO": "white"},
                 # 应用名称的颜色已在主格式字符串中用 %(blue)s 指定
            },
            "reset": True,
            "style": "%",
        },
    },
    "handlers": {
        "default": { # 应用日志和 uvicorn.error 的处理器
            "formatter": "default_color",
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stderr", # 日志通常输出到 stderr
        },
        "access": { # uvicorn.access 的处理器
            "formatter": "access_color",
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stdout", # 访问日志可以输出到 stdout
        },
    },
    "loggers": {
        settings.app_name: { # 我们应用的主 logger
            "handlers": ["default"],
            "level": settings.log_level.upper(), # 从配置读取
            "propagate": False, # 不向上传播给 root logger
        },
        "uvicorn.error": { # Uvicorn 的错误和常规信息日志，重定向到应用名称格式
            "handlers": ["default"], # 使用我们定义的 default handler 和 formatter
            "level": "INFO", # Uvicorn 自身的日志级别
            "propagate": False,
        },
        "uvicorn.access": { # Uvicorn 的访问日志，现在会显示应用名称
            "handlers": ["access"], # 使用我们定义的 access handler 和 formatter
            "level": "INFO",
            "propagate": False,
        },
    },
    # 可以选择配置 root logger，如果希望所有未明确配置的 logger 也采用某种默认行为
    # "root": {
    #     "level": "WARNING", # 例如，默认只显示警告及以上
    #     "handlers": ["default"],
    # },
}

# 在模块加载时应用日志配置 (如果 uvicorn.run 的 log_config 不完全覆盖)
# logging.config.dictConfig(LOGGING_CONFIG) # 通常 uvicorn 的 log_config 会处理，这里可能不需要

# 获取以应用名称命名的日志记录器实例
# 注意：getLogger 必须在 dictConfig 应用之后（或由 uvicorn 的 log_config 间接应用后）
# 才能获取到已配置的 logger。但由于 settings.app_name 用于 LOGGING_CONFIG 内部，
# 这里的 getLogger 主要是为了在 main.py 的其他地方使用。
# Uvicorn 的 log_config 会在 uvicorn.run 时应用。
logger = logging.getLogger(settings.app_name)

# ---- Lifespan 事件处理器 ----
@asynccontextmanager
async def lifespan(app_instance: FastAPI):
    # 应用启动时执行的逻辑
    # 日志现在应该会自动使用 LOGGING_CONFIG 中为 settings.app_name logger 配置的格式
    logger.info(f"应用 '{settings.app_name}' (通过 lifespan) 启动中...") # logger.info 会使用新配置
    # 以下日志由 uvicorn.error logger 处理，也会使用新配置
    logger.info(f"服务器监听地址: {settings.server_host}:{settings.server_port}")
    logger.info(f"调试模式: {settings.debug_mode}")
    logger.info(f"日志级别: {settings.log_level.upper()}")
    logger.info(f"提示词模板路径（有输入）: {settings.proxy.prompt_template_path_with_input}")
    logger.info(f"提示词模板路径（无输入）: {settings.proxy.prompt_template_path_without_input}")
    logger.info(f"模拟流式响应启用状态: {settings.proxy.fake_streaming.enabled}")
    if settings.proxy.fake_streaming.enabled:
        logger.info(f"模拟流式响应心跳间隔: {settings.proxy.fake_streaming.heartbeat_interval} 秒")
    logger.info(f"代理请求超时时间: {settings.proxy.openai_request_timeout} 秒")
    
    # Lifespan 函数的核心
    yield
    
    # 应用关闭时执行的逻辑
    logger.info(f"应用 '{settings.app_name}' (通过 lifespan) 关闭中...")

# ---- FastAPI 应用实例 ----
app = FastAPI(
    title=settings.app_name, 
    version="1.0.0", 
    lifespan=lifespan,
    description="通用 OpenAI 格式反代注入服务"
)

@app.post("/{target_url:path}/v1/chat/completions")
async def chat_completions_proxy_endpoint(request: Request, target_url: str):
    """
    处理通用 OpenAI 格式反代注入请求的 API 端点。
    
    URL格式: /{http(s)://target.domain.com}/v1/chat/completions
    或: /{https://target.domain.com}/v1/chat/completions?api_key=xxx
    
    只接受客户端的模型名称、消息内容、流式设置和 API 密钥，其他所有参数都将被忽略。
    """
    try:
        original_body = await request.json()
    except Exception as e:
        logger.error(f"解析请求体失败: {e}", exc_info=settings.debug_mode)
        raise HTTPException(status_code=400, detail=f"无效的 JSON 请求体: {e}")

    logger.info(f"接收到的原始请求体 (parsed json): {original_body}") # <-- 新增日志：记录原始请求体

    # 从URL中提取目标API端点和认证信息
    try:
        actual_target_url, api_key = extract_target_url_and_auth(request)
    except HTTPException as e:
        logger.error(f"提取目标URL和认证信息失败: {e.detail}")
        raise e

    # 记录并过滤客户端传递的不支持参数
    allowed_client_params = {"model", "messages", "stream"}
    ignored_params = []
    for key in original_body.keys():
        if key not in allowed_client_params:
            ignored_params.append(f"{key}={original_body[key]}")
    
    if ignored_params:
        logger.info(f"忽略客户端传递的以下参数（将使用配置文件默认值）: {', '.join(ignored_params)}")

    client_requests_stream: bool = original_body.get("stream", False)
    logger.info(f"客户端请求流式响应: {client_requests_stream}")

    try:
        if not client_requests_stream:
            logger.info(f"正在处理非流式请求到目标: {actual_target_url}")
            response_data = await execute_non_stream_openai_request(
                original_body, actual_target_url, api_key
            )
            return JSONResponse(content=response_data)
        else:
            # 检查是否启用了模拟流式响应
            if settings.proxy.fake_streaming.enabled:
                logger.info(f"模拟流式响应已启用，将非流式响应转换为流式格式")
                logger.info(f"模拟流式心跳间隔: {settings.proxy.fake_streaming.heartbeat_interval} 秒")
                
                prepared_data = _prepare_openai_messages(original_body)
                logger.info(f"模拟流式 - _prepare_openai_messages 处理后的数据: {prepared_data}") # <-- 新增日志：记录处理后数据
                
                # 导入模拟流式功能
                from .streaming_utils import fake_stream_generator_from_non_stream
                
                # 先准备消息以获取正则规则 (已在上一步完成)
                # prepared_data = _prepare_openai_messages(original_body)
                selected_regex_rules = prepared_data.get("selected_regex_rules", [])
                
                # 创建非流式请求任务
                non_stream_task = asyncio.create_task(
                    execute_non_stream_openai_request(original_body, actual_target_url, api_key, prepared_data)
                )
                
                # 使用模拟流式生成器
                return StreamingResponse(
                    fake_stream_generator_from_non_stream(non_stream_task, original_body, selected_regex_rules),
                    media_type="text/event-stream"
                )
            else:
                logger.info(f"正在处理真实流式请求到目标: {actual_target_url}")
                
                # 先准备消息以获取正则规则
                prepared_data = _prepare_openai_messages(original_body)
                logger.info(f"真实流式 - _prepare_openai_messages 处理后的数据: {prepared_data}") # <-- 新增日志：记录处理后数据
                
                return StreamingResponse(
                    execute_stream_openai_request(original_body, actual_target_url, api_key, prepared_data),
                    media_type="text/event-stream"
                )
    except HTTPException as e:
        logger.error(f"HTTPException 捕获于端点: {e.status_code} - {e.detail}", exc_info=settings.debug_mode)
        raise e
    except Exception as e:
        logger.error(f"处理请求时发生未知错误: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"服务器内部错误: {str(e)}")

@app.get("/")
async def root():
    """
    根路径端点，提供API使用说明。
    """
    return {
        "message": "欢迎使用通用 OpenAI 格式反代注入服务",
        "usage": "使用格式: /{http(s)://target.domain.com}/v1/chat/completions",
        "example": "/https://api.openai.com/v1/chat/completions",
        "version": "1.0.0"
    }

@app.get("/health")
async def health():
    """
    健康检查端点。
    """
    return {"status": "healthy", "service": settings.app_name}

# ---- 本地开发服务器启动 ----
if __name__ == "__main__":
    import uvicorn
    
    # logger.info(f"正在以本地开发模式启动 Uvicorn 服务器，监听地址: {settings.server_host}:{settings.server_port}")
    # 上面这行日志现在会由 uvicorn.error logger 使用新格式打印，所以这里可以注释掉或移除
    
    uvicorn.run(
        app, # 或者 "src.main:app" 如果是从外部调用 uvicorn CLI
        host=settings.server_host,
        port=settings.server_port,
        log_config=LOGGING_CONFIG # 应用新的日志配置
    )