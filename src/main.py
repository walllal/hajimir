# src/main.py
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
import asyncio
import logging # 添加日志记录

from .config import settings
from .openai_proxy import (
    get_openai_non_stream_response,
    stream_openai_response_to_client,
    fake_stream_generator_from_non_stream,
    _apply_regex_rules_to_content # 导入新的辅助函数
)

# 配置日志
logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(settings.app_name)


app = FastAPI(title=settings.app_name)

@app.on_event("startup")
async def startup_event():
    logger.info(f"应用 '{settings.app_name}' 已启动。")
    logger.info(f"日志级别: {settings.log_level.upper()}")
    logger.info(f"提示词模板路径: {settings.proxy.prompt_template_path}")
    logger.info(f"假流式启用配置: {settings.proxy.fake_streaming.enabled}")
    if settings.proxy.fake_streaming.enabled:
        logger.info(f"假流式心跳间隔: {settings.proxy.fake_streaming.heartbeat_interval}s")
    logger.info(f"OpenAI 请求超时: {settings.proxy.openai_request_timeout}s")


@app.post("/{openai_target_url:path}")
async def proxy_openai_endpoint(openai_target_url: str, request: Request):
    try:
        original_body = await request.json()
        logger.debug(f"收到请求: {request.method} {request.url}, 目标: {openai_target_url}")
        logger.debug(f"请求体: {original_body}")
    except Exception as e:
        logger.error(f"解析请求体失败: {e}")
        raise HTTPException(status_code=400, detail=f"无效的 JSON 请求体: {e}")

    auth_header = request.headers.get("Authorization")
    if not auth_header:
        logger.warning("请求未提供 Authorization header")
        raise HTTPException(status_code=401, detail="未提供 Authorization header。")

    # 简单的 URL 格式校验，确保它是以 http:// 或 https:// 开头
    # 注意：openai_target_url 捕获的是 path 部分，需要从 request.url 中获取 scheme 和 netloc
    # 或者，我们假设用户会输入完整的 URL 作为路径参数的一部分，例如 /https://api.openai.com/...
    # 当前的 :path 会捕获 //，所以 /https://... 是可以的
    # 但更稳妥的是，让用户只输入域名后的路径，然后我们拼接
    # 根据之前的计划，openai_target_url 是完整的 URL
    if not openai_target_url.startswith(("http://", "https://")):
        # 如果不是以 http(s):// 开头，尝试从原始请求的 URL 构建
        # 这是一个备选方案，如果用户只提供了路径
        # 但我们坚持之前的约定：openai_target_url 是完整的
        logger.error(f"目标 OpenAI URL 格式不正确: {openai_target_url}")
        raise HTTPException(status_code=400, detail=f"目标 OpenAI URL '{openai_target_url}' 格式不正确，应以 http:// 或 https:// 开头。")


    client_requests_stream = original_body.get("stream", False)
    logger.info(f"客户端请求流式: {client_requests_stream}, 假流式配置: {settings.proxy.fake_streaming.enabled}")

    try:
        if not client_requests_stream:
            logger.info("处理非流式请求")
            response_data = await get_openai_non_stream_response(
                original_body, openai_target_url, auth_header
            )
            logger.debug(f"从目标 API 收到的原始非流式响应数据: {response_data}")

            # 对助手消息应用正则规则 (如果存在)
            if isinstance(response_data, dict) and "choices" in response_data and response_data["choices"]:
                if response_data["choices"][0].get("message", {}).get("role") == "assistant":
                    original_content = response_data["choices"][0].get("message", {}).get("content", "")
                    if isinstance(original_content, str): # 确保 content 是字符串
                        processed_content = _apply_regex_rules_to_content(original_content)
                        if processed_content != original_content:
                            logger.info("非流式响应：助手消息内容已通过正则规则处理。")
                            response_data["choices"][0]["message"]["content"] = processed_content
                        else:
                            logger.debug("非流式响应：正则规则未改变助手消息内容。")
            
            logger.debug(f"准备返回给客户端的最终非流式响应数据: {response_data}")
            return JSONResponse(content=response_data)
        else:
            # 客户端请求流式
            if settings.proxy.fake_streaming.enabled:
                logger.info("处理假流式请求")
                non_stream_task = asyncio.create_task(
                    get_openai_non_stream_response(original_body, openai_target_url, auth_header)
                )
                return StreamingResponse(
                    fake_stream_generator_from_non_stream(non_stream_task, original_body), # 传递 original_body
                    media_type="text/event-stream"
                )
            else:
                logger.info("处理真实流式请求")
                return StreamingResponse(
                    stream_openai_response_to_client(original_body, openai_target_url, auth_header),
                    media_type="text/event-stream"
                )
    except HTTPException as e: # 重新抛出已知的HTTP异常
        logger.error(f"HTTPException: {e.status_code} - {e.detail}")
        raise e
    except Exception as e: # 捕获其他所有意外错误
        logger.error(f"处理请求时发生未知错误: {e}", exc_info=True) # exc_info=True 会记录堆栈跟踪
        raise HTTPException(status_code=500, detail=f"服务器内部错误: {str(e)}")


if __name__ == "__main__":
    import uvicorn
    # 这个仅用于本地开发测试，生产环境应使用 Gunicorn + Uvicorn workers
    # uvicorn.run("src.main:app", host="0.0.0.0", port=8000, reload=True, workers=1)
    # 为了能直接运行，可以将 reload=True 改为 reload_dirs=["src"] 如果 uvicorn 支持
    # 或者直接 uvicorn.run(app, ...)
    uvicorn.run(app, host="0.0.0.0", port=8000) # 简化运行命令