"""
此模块负责与目标 OpenAI 格式 API 进行直接交互。

主要功能包括：
- 从 URL 中提取目标 OpenAI API 端点。
- 初始化 OpenAI 客户端。
- 处理流式和非流式的 OpenAI API 请求。
- 管理 API 密钥和授权。
- 构建和发送请求到目标 OpenAI 兼容服务。
- 处理和转换 OpenAI API 的响应。
"""
import asyncio
import json
import logging
import time
import uuid
import re
from typing import Dict, Any, Optional, List
from urllib.parse import urlparse, parse_qs

from fastapi import HTTPException, Request
import httpx

from .config import settings
from .template_handler import _prepare_openai_messages
from .conversion_utils import apply_regex_rules_to_response

# 获取基于应用配置的日志记录器
logger = logging.getLogger(settings.app_name)

def extract_target_url_and_auth(request: Request) -> tuple[str, str]:
    """
    从请求URL中提取目标OpenAI API端点和认证信息。
    
    URL格式: /http(s)://target.domain.com/v1/chat/completions
    或: /https://target.domain.com/v1/chat/completions?api_key=xxx
    
    Args:
        request: FastAPI Request对象
        
    Returns:
        tuple: (target_url, api_key)
        
    Raises:
        HTTPException: 如果URL格式不正确或缺少必要信息
    """
    path = request.url.path
    logger.debug(f"请求路径: {path}")
    
    # 匹配URL中的目标地址部分
    # 支持 /http://... 和 /https://... 格式
    if not (path.startswith('/http://') or path.startswith('/https://')):
        raise HTTPException(
            status_code=400, 
            detail="URL格式错误。请使用 /http(s)://target.domain.com/v1/chat/completions 格式"
        )
    
    # 移除开头的斜杠，获取完整的目标URL
    target_url = path[1:]  # 移除开头的 '/'
    
    logger.debug(f"提取的目标URL: {target_url}")
    
    # 验证URL格式
    try:
        parsed_url = urlparse(target_url)
        if not parsed_url.scheme or not parsed_url.netloc:
            raise ValueError("无效的URL格式")
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"目标URL格式无效: {e}"
        )
    
    # 从查询参数中提取API密钥（如果存在）
    api_key_from_url = None
    if parsed_url.query:
        query_params = parse_qs(parsed_url.query)
        if 'api_key' in query_params:
            api_key_from_url = query_params['api_key'][0]
    
    # 优先使用Authorization头中的API密钥，其次使用URL中的API密钥
    auth_header = request.headers.get("Authorization")
    api_key = None
    
    if auth_header and auth_header.startswith("Bearer "):
        api_key = auth_header.split("Bearer ")[1].strip()
        logger.debug("使用Authorization头中的API密钥")
    elif api_key_from_url:
        api_key = api_key_from_url
        logger.debug("使用URL查询参数中的API密钥")
    else:
        raise HTTPException(
            status_code=401,
            detail="缺少API密钥。请在Authorization头中提供 'Bearer <key>' 或在URL查询参数中提供 'api_key=<key>'"
        )
    
    # 清理目标URL，移除api_key查询参数（如果存在）
    if api_key_from_url:
        # 重新构建不含api_key的URL
        clean_query = '&'.join([f"{k}={v[0]}" for k, v in parse_qs(parsed_url.query).items() if k != 'api_key'])
        target_url = f"{parsed_url.scheme}://{parsed_url.netloc}{parsed_url.path}"
        if clean_query:
            target_url += f"?{clean_query}"
    
    logger.info(f"目标API端点: {target_url}")
    logger.debug(f"API密钥已提取（长度: {len(api_key) if api_key else 0}）")
    
    return target_url, api_key

async def execute_non_stream_openai_request(
    original_body: Dict[str, Any],
    target_url: str,
    api_key: str,
    prepared_data: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    处理一个完整的非流式 OpenAI API 请求。

    Args:
        original_body: 从客户端接收到的原始 OpenAI 格式的请求体
        target_url: 目标 OpenAI API 端点 URL
        api_key: 目标 API 的密钥
        prepared_data: 预先准备好的 OpenAI 消息体数据

    Returns:
        Dict[str, Any]: OpenAI API 的响应体

    Raises:
        HTTPException: 如果请求失败或响应无效
    """
    # 通过模板处理器准备 OpenAI 消息体
    if prepared_data is None:
        prepared_data = _prepare_openai_messages(original_body)
    
    prepared_messages: List[Dict[str, Any]] = prepared_data.get("messages", [])
    target_model_name: Optional[str] = prepared_data.get("model")
    selected_regex_rules: List[Dict[str, Any]] = prepared_data.get("selected_regex_rules", [])

    if not target_model_name:
        logger.error("在准备好的请求数据中未找到模型名称")
        raise HTTPException(status_code=400, detail="模型名称是必需的，但在处理请求后未找到")

    # 构建目标 OpenAI API 请求体
    target_request_body = {
        "model": target_model_name,
        "messages": prepared_messages,
        "stream": False  # 非流式请求
    }
    
    # 获取配置文件中的默认生成参数并添加到请求中
    default_config = settings.proxy.openai_generation
    target_request_body.update({
        "temperature": default_config.temperature,
        "max_tokens": default_config.max_tokens,
        "top_p": default_config.top_p,
        "frequency_penalty": default_config.frequency_penalty,
        "presence_penalty": default_config.presence_penalty
    })
    
    # 记录被忽略的客户端参数
    allowed_client_params = {"model", "messages", "stream"}
    ignored_params = []
    for key in original_body.keys():
        if key not in allowed_client_params:
            ignored_params.append(f"{key}={original_body[key]}")
    
    if ignored_params:
        logger.info(f"忽略客户端传递的以下参数（使用配置文件默认值）: {', '.join(ignored_params)}")

    # 构建请求头
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        "User-Agent": f"{settings.app_name}/1.0"
    }

    try:
        logger.debug(f"向 {target_url} 发送请求，模型: {target_model_name}")
        
        # 记录将要发送的非流式请求体
        logger.info(f"准备发送到目标API (非流式) 的请求体: {json.dumps(target_request_body, ensure_ascii=False, indent=2)}")
        
        async with httpx.AsyncClient(timeout=settings.proxy.openai_request_timeout) as client:
            response = await client.post(
                target_url,
                json=target_request_body,
                headers=headers
            )
            
            logger.debug(f"收到响应，状态码: {response.status_code}")
            
            if response.status_code != 200:
                error_text = response.text
                logger.error(f"目标API返回错误 {response.status_code}: {error_text}")
                raise HTTPException(
                    status_code=response.status_code,
                    detail=f"目标API错误: {error_text}"
                )
            
            response_data = response.json()
            logger.info(f"成功收到来自目标API的响应，模型: {target_model_name}")
            
            # 调试：记录原始响应内容
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(f"目标API原始响应内容: {json.dumps(response_data, ensure_ascii=False, indent=2)}")
            
            # 确保响应包含必要的 OpenAI API 标准字段
            if not response_data.get("id"):
                response_data["id"] = f"chatcmpl-proxy-{int(time.time())}-{uuid.uuid4().hex[:8]}"
                logger.debug("目标API响应缺少id字段，已自动生成")
            
            if not response_data.get("object"):
                response_data["object"] = "chat.completion"
                logger.debug("目标API响应缺少object字段，已自动设置")
            
            if not response_data.get("created"):
                response_data["created"] = int(time.time())
                logger.debug("目标API响应缺少created字段，已自动生成")
            
            # 应用正则规则到响应内容
            if selected_regex_rules and "choices" in response_data:
                response_data = apply_regex_rules_to_response(response_data, selected_regex_rules)
                
                # 调试：记录正则处理后的响应内容
                if logger.isEnabledFor(logging.DEBUG):
                    processed_response_for_debug = json.dumps(response_data, ensure_ascii=False, indent=2)
                    logger.debug(f"正则处理后的响应内容: {processed_response_for_debug}")
            
            return response_data
            
    except httpx.TimeoutException:
        logger.error(f"请求目标API超时: {target_url}")
        raise HTTPException(status_code=504, detail="请求目标API超时")
    except httpx.RequestError as e:
        logger.error(f"请求目标API时发生网络错误: {e}")
        raise HTTPException(status_code=502, detail=f"网络错误: {str(e)}")
    except Exception as e:
        logger.error(f"调用目标API时发生未知错误: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"调用目标API时出错: {str(e)}")

async def execute_stream_openai_request(
    original_body: Dict[str, Any],
    target_url: str,
    api_key: str,
    prepared_data: Optional[Dict[str, Any]] = None
):
    """
    处理流式 OpenAI API 请求。

    Args:
        original_body: 从客户端接收到的原始 OpenAI 格式的请求体
        target_url: 目标 OpenAI API 端点 URL
        api_key: 目标 API 的密钥
        prepared_data: 预先准备好的 OpenAI 消息体数据

    Yields:
        str: SSE 格式的响应数据

    Raises:
        HTTPException: 如果请求失败或响应无效
    """
    # 通过模板处理器准备 OpenAI 消息体
    if prepared_data is None:
        prepared_data = _prepare_openai_messages(original_body)
    
    prepared_messages: List[Dict[str, Any]] = prepared_data.get("messages", [])
    target_model_name: Optional[str] = prepared_data.get("model")
    selected_regex_rules: List[Dict[str, Any]] = prepared_data.get("selected_regex_rules", [])

    if not target_model_name:
        logger.error("在准备好的请求数据中未找到模型名称")
        raise HTTPException(status_code=400, detail="模型名称是必需的，但在处理请求后未找到")

    # 构建目标 OpenAI API 请求体
    target_request_body = {
        "model": target_model_name,
        "messages": prepared_messages,
        "stream": True  # 流式请求
    }
    
    # 获取配置文件中的默认生成参数并添加到请求中
    default_config = settings.proxy.openai_generation
    target_request_body.update({
        "temperature": default_config.temperature,
        "max_tokens": default_config.max_tokens,
        "top_p": default_config.top_p,
        "frequency_penalty": default_config.frequency_penalty,
        "presence_penalty": default_config.presence_penalty
    })

    # 构建请求头
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        "User-Agent": f"{settings.app_name}/1.0",
        "Accept": "text/event-stream",
        "Cache-Control": "no-cache"
    }

    try:
        logger.debug(f"向 {target_url} 发送流式请求，模型: {target_model_name}")
        
        # 记录将要发送的流式请求体
        logger.info(f"准备发送到目标API (流式) 的请求体: {json.dumps(target_request_body, ensure_ascii=False, indent=2)}")
        
        async with httpx.AsyncClient(timeout=settings.proxy.openai_request_timeout) as client:
            async with client.stream(
                'POST',
                target_url,
                json=target_request_body,
                headers=headers
            ) as response:
                
                if response.status_code != 200:
                    error_text = await response.aread()
                    logger.error(f"目标API返回错误 {response.status_code}: {error_text.decode()}")
                    raise HTTPException(
                        status_code=response.status_code,
                        detail=f"目标API错误: {error_text.decode()}"
                    )
                
                logger.info(f"开始接收来自目标API的流式响应，模型: {target_model_name}")
                
                # 收集完整响应用于正则处理
                full_content = ""
                chunks = []
                
                async for chunk in response.aiter_text():
                    if chunk.strip():
                        # 解析SSE数据
                        for line in chunk.split('\n'):
                            if line.startswith('data: '):
                                data_content = line[6:]  # 移除 'data: ' 前缀
                                if data_content.strip() == '[DONE]':
                                    # 应用正则规则到完整内容
                                    if selected_regex_rules and full_content:
                                        # 调试：记录正则处理前的完整内容
                                        if logger.isEnabledFor(logging.DEBUG):
                                            logger.debug(f"流式响应完整原始内容: {full_content}")
                                        
                                        processed_content = full_content
                                        for rule in selected_regex_rules:
                                            pattern = rule.get('查找', '')
                                            replacement = rule.get('替换', '')
                                            if pattern:
                                                try:
                                                    processed_content = re.sub(pattern, replacement, processed_content)
                                                except Exception as e:
                                                    logger.warning(f"正则规则应用失败: {e}")
                                        
                                        # 调试：记录正则处理后的内容
                                        if logger.isEnabledFor(logging.DEBUG):
                                            logger.debug(f"流式响应正则处理后内容: {processed_content}")
                                        
                                        # 如果内容被修改，需要重新构建流式响应
                                        if processed_content != full_content:
                                            # 发送修正后的完整响应
                                            corrected_chunk = {
                                                "choices": [
                                                    {
                                                        "delta": {"content": processed_content},
                                                        "index": 0,
                                                        "finish_reason": None
                                                    }
                                                ]
                                            }
                                            yield f"data: {json.dumps(corrected_chunk, ensure_ascii=False)}\n\n"
                                    
                                    yield f"data: [DONE]\n\n"
                                    return
                                else:
                                    try:
                                        chunk_data = json.loads(data_content)
                                        
                                        # 确保流式响应的 chunk 包含必要字段
                                        if not chunk_data.get("id"):
                                            chunk_data["id"] = f"chatcmpl-stream-{int(time.time())}-{uuid.uuid4().hex[:8]}"
                                        
                                        if not chunk_data.get("object"):
                                            chunk_data["object"] = "chat.completion.chunk"
                                            
                                        if not chunk_data.get("created"):
                                            chunk_data["created"] = int(time.time())
                                        
                                        # 提取内容用于正则处理
                                        if "choices" in chunk_data:
                                            for choice in chunk_data["choices"]:
                                                if "delta" in choice and "content" in choice["delta"]:
                                                    full_content += choice["delta"]["content"]
                                        
                                        chunks.append(chunk_data)
                                        yield f"data: {json.dumps(chunk_data, ensure_ascii=False)}\n\n"
                                    except json.JSONDecodeError:
                                        # 如果不是JSON，直接传递
                                        yield f"data: {data_content}\n\n"
                
    except httpx.TimeoutException:
        logger.error(f"流式请求目标API超时: {target_url}")
        raise HTTPException(status_code=504, detail="请求目标API超时")
    except httpx.RequestError as e:
        logger.error(f"流式请求目标API时发生网络错误: {e}")
        raise HTTPException(status_code=502, detail=f"网络错误: {str(e)}")
    except Exception as e:
        logger.error(f"流式调用目标API时发生未知错误: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"流式调用目标API时出错: {str(e)}") 