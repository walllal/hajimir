# src/openai_proxy.py
import httpx
import yaml
import copy
import asyncio
import json
import logging # 添加日志模块
import time # 添加 time 模块
import re # 新增：正则表达式模块
import random # 新增：随机模块
from fastapi import HTTPException
from typing import List, Dict, Any, AsyncGenerator

from .config import settings
import os # 用于检查文件修改时间

logger = logging.getLogger(settings.app_name) # 获取logger实例

# 全局变量用于缓存模板和跟踪修改时间
_CACHED_PROMPT_BLUEPRINTS: List[Dict[str, Any]] = [] # 用于构建请求的提示
_CACHED_REGEX_RULES: List[Dict[str, str]] = []    # 用于处理响应的正则规则
_LAST_TEMPLATE_MTIME: float = 0.0
_TEMPLATE_PATH: str = settings.proxy.prompt_template_path # 从配置获取路径

def _load_templates(force_reload: bool = False) -> None:
    """
    加载或热加载提示词模板，并分离正则规则。
    如果文件未更改且非强制重载，则不执行操作。
    """
    global _CACHED_PROMPT_BLUEPRINTS, _CACHED_REGEX_RULES, _LAST_TEMPLATE_MTIME, _TEMPLATE_PATH
    
    try:
        current_mtime = os.path.getmtime(_TEMPLATE_PATH)
    except FileNotFoundError:
        if not hasattr(_load_templates, '_logged_not_found_paths'):
            _load_templates._logged_not_found_paths = set()
        if _TEMPLATE_PATH not in _load_templates._logged_not_found_paths:
            logger.error(f"提示词模板文件 '{_TEMPLATE_PATH}' 未找到。")
            _load_templates._logged_not_found_paths.add(_TEMPLATE_PATH)
        _CACHED_PROMPT_BLUEPRINTS = []
        _CACHED_REGEX_RULES = []
        _LAST_TEMPLATE_MTIME = 0.0
        return

    if not force_reload and current_mtime == _LAST_TEMPLATE_MTIME and _LAST_TEMPLATE_MTIME != 0.0:
        return

    logger.info(f"尝试加载/热加载模板文件: '{_TEMPLATE_PATH}' (上次修改时间: {_LAST_TEMPLATE_MTIME}, 当前文件修改时间: {current_mtime})")
    try:
        with open(_TEMPLATE_PATH, "r", encoding="utf-8") as f:
            loaded_yaml_content = yaml.safe_load(f)
        
        if hasattr(_load_templates, '_logged_not_found_paths') and _TEMPLATE_PATH in _load_templates._logged_not_found_paths:
            _load_templates._logged_not_found_paths.remove(_TEMPLATE_PATH)

        if isinstance(loaded_yaml_content, list):
            new_blueprints = []
            new_regex_rules = []
            for item in loaded_yaml_content:
                if isinstance(item, dict):
                    item_type = item.get("type")
                    if item_type == "正则":
                        find_pattern = item.get("查找")
                        replace_pattern = item.get("替换")
                        if find_pattern is not None and replace_pattern is not None:
                            new_regex_rules.append({
                                "查找": str(find_pattern),
                                "替换": str(replace_pattern)
                            })
                        else:
                            logger.warning(f"模板中的 '正则' 类型块缺少 '查找' 或 '替换' 字段，或其值为 None，已忽略: {item}")
                    else:
                        new_blueprints.append(item)
                else:
                    logger.warning(f"模板文件 '{_TEMPLATE_PATH}' 中包含非字典类型的顶层列表项，已忽略: {item}")
            
            _CACHED_PROMPT_BLUEPRINTS = new_blueprints
            _CACHED_REGEX_RULES = new_regex_rules
            _LAST_TEMPLATE_MTIME = current_mtime
            logger.info(f"提示词模板 '{_TEMPLATE_PATH}' 已成功加载/热加载。提示词块数: {len(_CACHED_PROMPT_BLUEPRINTS)}, 正则规则数: {len(_CACHED_REGEX_RULES)}")
        else:
            logger.warning(f"加载/热加载模板 '{_TEMPLATE_PATH}' 失败：文件内容不是一个列表。将保留上一个有效版本（如果有）。")
            if _LAST_TEMPLATE_MTIME == 0.0:
                 _CACHED_PROMPT_BLUEPRINTS = []
                 _CACHED_REGEX_RULES = []
    except yaml.YAMLError as e:
        logger.error(f"解析模板文件 '{_TEMPLATE_PATH}' 失败: {e}。将保留上一个有效版本（如果有）。")
        if _LAST_TEMPLATE_MTIME == 0.0:
            _CACHED_PROMPT_BLUEPRINTS = []
            _CACHED_REGEX_RULES = []
    except Exception as e:
        logger.error(f"加载模板文件 '{_TEMPLATE_PATH}' 时发生未知错误: {e}。将保留上一个有效版本（如果有）。")
        if _LAST_TEMPLATE_MTIME == 0.0:
            _CACHED_PROMPT_BLUEPRINTS = []
            _CACHED_REGEX_RULES = []

# 应用启动时首次加载模板
_load_templates(force_reload=True)


# --- 新增：动态变量处理函数 ---
def _process_dice_rolls(text_content: str) -> str:
    """处理文本中的 {{roll XdY}} 骰子变量"""
    if not isinstance(text_content, str):
        return text_content

    def replace_dice_roll(match):
        try:
            num_dice = int(match.group(1))
            num_sides = int(match.group(2))
            if num_dice <= 0 or num_sides <= 0:
                return f"{{roll {num_dice}d{num_sides} - 无效的骰子参数}}"
            
            total_roll = sum(random.randint(1, num_sides) for _ in range(num_dice))
            logger.debug(f"处理骰子变量: {{roll {num_dice}d{num_sides}}} -> {total_roll}")
            return str(total_roll)
        except ValueError:
            return f"{{roll {match.group(1)}d{match.group(2)} - 参数非整数}}"
        except Exception as e:
            logger.error(f"处理骰子变量 {{roll {match.group(1)}d{match.group(2)}}} 时出错: {e}")
            return f"{{roll {match.group(1)}d{match.group(2)} - 处理错误}}"

    # 正则表达式查找 {{roll XdY}}，允许数字前后有空格
    # 使用非贪婪匹配，以防一个变量处理函数干扰另一个（如果它们有相似的定界符）
    # 但这里 {{...}} 是明确的，所以贪婪/非贪婪影响不大
    return re.sub(r"\{\{roll\s*(\d+)\s*d\s*(\d+)\s*\}\}", replace_dice_roll, text_content)

def _process_random_choices(text_content: str) -> str:
    """处理文本中的 {{random::opt1::opt2...}} 随机选择变量"""
    if not isinstance(text_content, str):
        return text_content

    def replace_random_choice(match):
        try:
            options_str = match.group(1)
            if not options_str: # 处理 {{random::}} 这种情况
                return "{{random:: - 无选项}}"
            options = options_str.split('::')
            if not all(options): # 如果分割后有空字符串选项，例如 {{random::a::::b}}
                 logger.warning(f"随机选择变量 {{random::{options_str}}} 包含空选项。")
                 # 可以选择过滤掉空选项，或者将其视为有效选项
                 options = [opt for opt in options if opt] # 过滤空选项
                 if not options:
                     return "{{random:: - 过滤后无有效选项}}"

            chosen = random.choice(options)
            logger.debug(f"处理随机选择变量: {{random::{options_str}}} -> {chosen}")
            return chosen
        except Exception as e:
            logger.error(f"处理随机选择变量 {{random::{match.group(1)}}} 时出错: {e}")
            return f"{{random::{match.group(1)}}} - 处理错误}}"
            
    # 正则表达式查找 {{random::...}}
    # (.*?)是非贪婪匹配，匹配两个::之间的任何字符，直到第一个}}
    return re.sub(r"\{\{random::(.*?)\}\}", replace_random_choice, text_content)

def _apply_regex_rules_to_content(text_content: str) -> str:
    """
    按顺序将缓存的正则规则应用于给定的文本内容。
    """
    if not _CACHED_REGEX_RULES or not isinstance(text_content, str):
        return text_content

    current_content = text_content
    for rule_idx, rule in enumerate(_CACHED_REGEX_RULES):
        try:
            find_pattern = rule.get("查找", "")
            replace_pattern = rule.get("替换", "")
            # re.sub 支持在 replace_pattern 中使用 \1, \2, \g<0> 等
            processed_content = re.sub(find_pattern, replace_pattern, current_content)
            if processed_content != current_content:
                logger.debug(f"应用正则规则 #{rule_idx + 1}: 查找='{find_pattern}', 替换='{replace_pattern}'. 内容已更改。")
            else:
                logger.debug(f"应用正则规则 #{rule_idx + 1}: 查找='{find_pattern}'. 内容未更改。")
            current_content = processed_content
        except re.error as e:
            logger.error(f"应用正则规则 #{rule_idx + 1} (查找='{find_pattern}') 时发生正则表达式错误: {e}. 该规则被跳过。")
        except Exception as e:
            logger.error(f"应用正则规则 #{rule_idx + 1} 时发生未知错误: {e}. 该规则被跳过。")
    return current_content
# --- 结束：动态变量处理函数 ---


def _prepare_openai_request_body(original_body: Dict[str, Any], stream_to_openai: bool) -> Dict[str, Any]:
    # 在每次准备请求体时，尝试热加载模板（如果文件有变动）
    _load_templates()
    current_blueprints = _CACHED_PROMPT_BLUEPRINTS # 使用已加载/缓存的提示词蓝图
    """
    准备发送给OpenAI的请求体，包括注入提示词和设置stream参数。
    附加参数暂时只传递 temperature。
    """
    original_messages: List[Dict[str, Any]] = original_body.get("messages", [])
    # stream_to_openai 参数决定了我们要发送给目标API的stream标志

    historic_messages: List[Dict[str, Any]] = []
    last_user_input_content: str = ""

    if original_messages and isinstance(original_messages, list):
        if original_messages and original_messages[-1].get("role") == "user":
            last_user_input_content = original_messages[-1].get("content", "")
            historic_messages = original_messages[:-1]
        else:
            historic_messages = original_messages
    elif original_messages:
        raise HTTPException(status_code=400, detail="请求体中的 'messages' 必须是一个有效的消息列表。")

    final_messages: List[Dict[str, Any]] = []
    if not current_blueprints:
        temp_final_messages = historic_messages
        if last_user_input_content or (not historic_messages and any(m.get("role") == "user" for m in original_messages)):
            if not historic_messages and original_messages and original_messages[-1].get("role") == "user":
                 temp_final_messages = [{"role": "user", "content": original_messages[-1].get("content","")}]
            elif last_user_input_content:
                 temp_final_messages = historic_messages + [{"role": "user", "content": last_user_input_content}]
        
        if not temp_final_messages and original_messages:
            final_messages = original_messages
        else:
            final_messages = temp_final_messages
    else:
        for blueprint_msg_template in current_blueprints:
            blueprint_msg = copy.deepcopy(blueprint_msg_template)
            if blueprint_msg.get("type") == "api_input_placeholder":
                final_messages.extend(historic_messages)
            else:
                content_template = blueprint_msg.get("content")
                if isinstance(content_template, str):
                    processed_content = content_template.replace("{{user_input}}", last_user_input_content)
                    processed_content = _process_dice_rolls(processed_content)
                    processed_content = _process_random_choices(processed_content)
                    blueprint_msg["content"] = processed_content
                final_messages.append(blueprint_msg)
    
    # 再次遍历 final_messages 以处理可能来自 historic_messages 的动态变量 (如果需要)
    # 当前的二次处理逻辑是 pass，所以这部分实际上没有改变行为。
    # 如果确实需要处理 historic_messages 中的动态变量，这里的 pass 需要替换成实际处理代码。
    processed_final_messages = []
    for msg in final_messages:
        new_msg = msg.copy()
        content = new_msg.get("content")
        if isinstance(content, str):
            # 假设 {{user_input}} 已在上面处理。
            # 如果 historic_messages 也需要处理新动态变量，则取消下面两行的注释：
            # content = _process_dice_rolls(content)
            # content = _process_random_choices(content)
            # new_msg["content"] = content
            pass
        processed_final_messages.append(new_msg)
    final_messages = processed_final_messages

    # 新增：合并相邻的 system 消息
    if final_messages:
        merged_system_messages = []
        for msg in final_messages:
            # 确保 msg 是字典并且有 'role' 和 'content'
            if not isinstance(msg, dict) or "role" not in msg or "content" not in msg:
                merged_system_messages.append(msg) # 如果格式不对，直接添加并继续
                logger.warning(f"发现格式不正确的消息项，已跳过合并逻辑: {msg}")
                continue

            if msg.get("role") == "system" and \
               merged_system_messages and \
               isinstance(merged_system_messages[-1], dict) and \
               merged_system_messages[-1].get("role") == "system":
                # 合并内容
                # 确保前一个消息的 content 是字符串
                if isinstance(merged_system_messages[-1].get("content"), str):
                    merged_system_messages[-1]["content"] += f"\n\n{msg['content']}"
                else: # 如果前一个 system 消息的 content 不是字符串，则不合并，直接追加当前消息
                    logger.warning(f"前一个 system 消息的 content 不是字符串，无法合并: {merged_system_messages[-1]['content']}")
                    merged_system_messages.append(msg)
            else:
                merged_system_messages.append(msg)
        final_messages = merged_system_messages
        logger.debug(f"合并 system 消息后的 final_messages: {json.dumps(final_messages, ensure_ascii=False)}")

    # 新增：移除 content 为空或 None 的消息
    if final_messages:
        original_message_count = len(final_messages)
        final_messages_content_checked = [
            msg for msg in final_messages
            if isinstance(msg, dict) and msg.get("content") is not None and msg.get("content") != ""
        ]
        if len(final_messages_content_checked) < original_message_count:
            logger.debug(f"移除了 {original_message_count - len(final_messages_content_checked)} 条 content 为空或 None 的消息。")
        final_messages = final_messages_content_checked
        if not final_messages: # 如果过滤后列表为空
            logger.debug("所有消息因 content 为空或 None 被移除，final_messages 现为空列表。")


    # 构建新的请求体，只包含必要的和明确允许的参数
    new_request_body: Dict[str, Any] = { # 明确类型
        "model": original_body.get("model"),
        "messages": final_messages,
        "stream": stream_to_openai
    }

    if "temperature" in original_body:
        new_request_body["temperature"] = original_body["temperature"]
    
    if new_request_body.get("model") is None:
        logger.warning("请求中 model 参数为 None 或未提供，可能导致目标 API 错误。")

    if not original_body.get("messages") and not final_messages:
        if "messages" in new_request_body:
            logger.debug("原始请求无messages且处理后messages为空，从发送体中移除messages键。")
            del new_request_body["messages"]

    logger.debug(f"最终构造的发送给目标 API 的请求体（参数已筛选）: {json.dumps(new_request_body, ensure_ascii=False)}")
    return new_request_body


async def get_openai_non_stream_response(
    original_body: Dict[str, Any],
    openai_target_url: str,
    auth_header: str
) -> Dict[str, Any]:
    """获取OpenAI的非流式响应。"""
    new_request_body = _prepare_openai_request_body(original_body, stream_to_openai=False)
    # 构造请求头，部分记录 Authorization
    auth_header_to_log = f"Bearer {auth_header[7:12]}..." if auth_header and auth_header.startswith("Bearer ") and len(auth_header) > 12 else "Not a Bearer token or too short"
    if not auth_header:
        auth_header_to_log = "None"
    headers_for_openai = {"Authorization": auth_header, "Content-Type": "application/json"}
    
    logger.debug(f"准备发送给目标 API (非流式) 的请求体: {json.dumps(new_request_body, ensure_ascii=False)}")
    logger.debug(f"准备发送给目标 API (非流式) 的请求头: {{'Authorization': '{auth_header_to_log}', 'Content-Type': 'application/json'}}")

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                openai_target_url,
                json=new_request_body,
                headers=headers_for_openai,
                timeout=settings.proxy.openai_request_timeout
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            error_detail = f"OpenAI API 非流式请求失败: {exc.response.status_code}"
            try: error_detail += f" - {exc.response.json()}"
            except Exception: error_detail += f" - {exc.response.text}"
            raise HTTPException(status_code=exc.response.status_code, detail=error_detail)
        except httpx.RequestError as exc:
            raise HTTPException(status_code=502, detail=f"请求 OpenAI API 网络错误 (非流式): {exc}")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"处理对OpenAI的非流式请求时发生内部错误: {str(e)}")


async def stream_openai_response_to_client(
    original_body: Dict[str, Any],
    openai_target_url: str,
    auth_header: str
) -> AsyncGenerator[str, None]:
    """从OpenAI获取流式响应并直接转发给客户端。"""
    new_request_body = _prepare_openai_request_body(original_body, stream_to_openai=True)
    # 构造请求头，部分记录 Authorization
    auth_header_to_log = f"Bearer {auth_header[7:12]}..." if auth_header and auth_header.startswith("Bearer ") and len(auth_header) > 12 else "Not a Bearer token or too short"
    if not auth_header:
        auth_header_to_log = "None"
    headers_for_openai = {"Authorization": auth_header, "Content-Type": "application/json", "Accept": "text/event-stream"}

    logger.debug(f"准备发送给目标 API (流式) 的请求体: {json.dumps(new_request_body, ensure_ascii=False)}")
    logger.debug(f"准备发送给目标 API (流式) 的请求头: {{'Authorization': '{auth_header_to_log}', 'Content-Type': 'application/json', 'Accept': 'text/event-stream'}}")

    async with httpx.AsyncClient() as client:
        try:
            async with client.stream(
                "POST",
                openai_target_url,
                json=new_request_body,
                headers=headers_for_openai,
                timeout=settings.proxy.openai_request_timeout # httpx stream 也支持 timeout
            ) as response:
                if response.status_code != 200: # 初始状态检查
                    error_content_bytes = await response.aread()
                    error_detail = f"OpenAI API 流式请求初始错误: {response.status_code}"
                    try:
                        error_json = json.loads(error_content_bytes.decode('utf-8', errors='ignore'))
                        error_detail += f" - {error_json}"
                    except Exception:
                        error_detail += f" - {error_content_bytes.decode('utf-8', errors='ignore')}"
                    yield f"data: {json.dumps({'error': {'message': error_detail, 'code': response.status_code, 'type':'openai_stream_error'}})}\n\n"
                    yield f"data: [DONE]\n\n"
                    return

                async for chunk in response.aiter_bytes(): # OpenAI 通常返回 bytes
                    yield chunk.decode('utf-8', errors='ignore') # 解码为字符串并发送
        except httpx.HTTPStatusError as exc: # 理论上 client.stream 不会直接抛这个，除非在连接建立前
            error_detail = f"OpenAI API 流式请求状态错误: {exc.response.status_code}"
            try: error_detail += f" - {exc.response.json()}"
            except Exception: error_detail += f" - {exc.response.text}"
            yield f"data: {json.dumps({'error': {'message': error_detail, 'code': exc.response.status_code, 'type':'http_status_error'}})}\n\n"
            yield f"data: [DONE]\n\n"
        except httpx.RequestError as exc: # 网络层面错误
            error_payload = {"error": {"message": f"请求 OpenAI API 网络错误 (流式): {str(exc)}", "type": "network_error"}}
            yield f"data: {json.dumps(error_payload)}\n\n"
            yield f"data: [DONE]\n\n"
        except Exception as e: # 其他未知错误
            error_payload = {"error": {"message": f"处理对OpenAI的流式请求时发生内部错误: {str(e)}", "type": "internal_stream_error"}}
            yield f"data: {json.dumps(error_payload)}\n\n"
            yield f"data: [DONE]\n\n"


async def fake_stream_generator_from_non_stream(
    non_stream_task: asyncio.Task, # Task that calls get_openai_non_stream_response
    original_body: Dict[str, Any] # 新增 original_body 参数
) -> AsyncGenerator[str, None]:
    """假流式生成器。定期发送空的、结构符合OpenAI流式块的JSON，直到OpenAI非流式响应完成，然后发送完整响应。"""
    heartbeat_interval = settings.proxy.fake_streaming.heartbeat_interval
    done = False
    while not done:
        try:
            await asyncio.wait_for(asyncio.shield(non_stream_task), timeout=heartbeat_interval)
            done = True # 如果 await 完成，说明 task 结束了
        except asyncio.TimeoutError:
            current_ts = int(time.time())
            # 尝试从原始请求获取模型，如果没有则使用默认值
            model_name = original_body.get("model", "gpt-3.5-turbo-proxy")
            
            empty_chunk = {
                "id": f"chatcmpl-fake-{current_ts}",
                "object": "chat.completion.chunk",
                "created": current_ts,
                "model": model_name,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": ""}, # 内容为空的 delta
                        "finish_reason": None
                    }
                ]
            }
            empty_chunk_message = f"data: {json.dumps(empty_chunk)}\n\n"
            logger.debug(f"发送假流式空数据块: {empty_chunk_message.strip()}")
            yield empty_chunk_message
        except Exception: # non_stream_task 内部可能抛出异常 (例如 HTTPException)
            done = True
            # 异常将在下面的 await non_stream_task 时被捕获并处理
            break # 退出循环，让下面的代码处理任务结果/异常

    try:
        full_response_data = await non_stream_task
        logger.debug(f"从非流式任务获取到的原始完整响应: {json.dumps(full_response_data, ensure_ascii=False)}")

        # 对助手消息应用正则规则 (如果存在)
        if isinstance(full_response_data, dict) and "choices" in full_response_data and full_response_data["choices"]:
            if full_response_data["choices"][0].get("message", {}).get("role") == "assistant":
                original_content = full_response_data["choices"][0].get("message", {}).get("content", "")
                if isinstance(original_content, str): # 确保 content 是字符串
                    processed_content = _apply_regex_rules_to_content(original_content)
                    if processed_content != original_content:
                        logger.info("助手消息内容已通过正则规则处理。")
                        full_response_data["choices"][0]["message"]["content"] = processed_content
                    else:
                        logger.debug("正则规则未改变助手消息内容。")

        # 将可能已处理的非流式响应模拟成流式 delta 块发送
        if isinstance(full_response_data, dict) and "choices" in full_response_data and full_response_data["choices"]:
            choice = full_response_data["choices"][0]
            message = choice.get("message", {}) # message 现在可能包含处理后的 content
            
            resp_id = full_response_data.get("id", f"chatcmpl-simulated-{int(time.time())}")
            resp_model = full_response_data.get("model", original_body.get("model", "gpt-3.5-turbo-proxy"))
            resp_created = full_response_data.get("created", int(time.time()))
            
            # 1. 发送 role (如果存在)
            role = message.get("role")
            if role:
                role_chunk = {
                    "id": resp_id, "object": "chat.completion.chunk", "created": resp_created, "model": resp_model,
                    "choices": [{"index": 0, "delta": {"role": role}, "finish_reason": None}]
                }
                role_chunk_message = f"data: {json.dumps(role_chunk)}\n\n"
                logger.debug(f"发送假流式 role delta: {role_chunk_message.strip()}")
                yield role_chunk_message

            # 2. 发送 content (如果存在)
            content = message.get("content")
            if content: # 即使是空字符串也发送，以符合 delta 结构
                content_chunk = {
                    "id": resp_id, "object": "chat.completion.chunk", "created": resp_created, "model": resp_model,
                    "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": None}]
                }
                content_chunk_message = f"data: {json.dumps(content_chunk)}\n\n"
                logger.debug(f"发送假流式 content delta: {content_chunk_message.strip()}")
                yield content_chunk_message
            
            # 3. 发送 finish_reason (如果存在)
            finish_reason = choice.get("finish_reason")
            if finish_reason:
                finish_chunk = {
                    "id": resp_id, "object": "chat.completion.chunk", "created": resp_created, "model": resp_model,
                    "choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}]
                }
                finish_chunk_message = f"data: {json.dumps(finish_chunk)}\n\n"
                logger.debug(f"发送假流式 finish_reason delta: {finish_chunk_message.strip()}")
                yield finish_chunk_message
        else:
            # 如果响应格式不符合预期，仍然尝试发送原始数据，但记录警告
            logger.warning(f"接收到的 full_response_data 格式不符合预期的 chat.completion 结构，将尝试直接发送: {full_response_data}")
            data_message = f"data: {json.dumps(full_response_data)}\n\n"
            logger.debug(f"发送假流式原始数据 (格式不符警告): {data_message.strip()}")
            yield data_message
            
    except HTTPException as e: # 由 get_openai_non_stream_response 抛出的已知API错误
        error_payload = {"error": {"message": str(e.detail), "code": e.status_code, "type": "api_error"}}
        error_message = f"data: {json.dumps(error_payload)}\n\n"
        logger.debug(f"发送假流式错误数据 (HTTPException): {error_message.strip()}")
        yield error_message
    except Exception as e: # 其他意外错误
        error_payload = {"error": {"message": f"获取OpenAI响应时发生未知错误: {str(e)}", "type": "internal_fake_stream_error"}}
        error_message = f"data: {json.dumps(error_payload)}\n\n"
        logger.debug(f"发送假流式错误数据 (Exception): {error_message.strip()}")
        yield error_message
    finally:
        done_message = "data: [DONE]\n\n"
        logger.debug(f"发送假流式结束标记: {done_message.strip()}")
        yield done_message