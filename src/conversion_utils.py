# src/conversion_utils.py
"""
此模块负责对 OpenAI API 响应进行后处理。

主要功能包括：
- 应用正则表达式规则对响应内容进行后处理。
"""
import json
import re
import logging
from typing import Dict, Any, List, Optional

from .config import settings # 导入应用配置

logger = logging.getLogger(settings.app_name) # 获取logger实例

def apply_regex_rules_to_response(
    response_data: Dict[str, Any], 
    regex_rules: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """
    将正则表达式规则应用于 OpenAI API 响应内容。

    Args:
        response_data: OpenAI API 的响应数据
        regex_rules: 正则规则列表，每个规则包含 '查找' 和 '替换' 字段

    Returns:
        Dict[str, Any]: 处理后的响应数据
    """
    if not regex_rules or "choices" not in response_data:
        return response_data
    
    # 创建响应数据的副本，避免修改原始数据
    processed_response = json.loads(json.dumps(response_data))
    
    try:
        for choice in processed_response["choices"]:
            if "message" in choice and "content" in choice["message"]:
                # 非流式响应的内容处理
                content = choice["message"]["content"]
                if content:
                    processed_content = _apply_regex_rules_to_content(content, regex_rules)
                    choice["message"]["content"] = processed_content
            elif "delta" in choice and "content" in choice["delta"]:
                # 流式响应的内容处理
                content = choice["delta"]["content"]
                if content:
                    processed_content = _apply_regex_rules_to_content(content, regex_rules)
                    choice["delta"]["content"] = processed_content
    except Exception as e:
        logger.error(f"应用正则规则时发生错误: {e}", exc_info=True)
        # 如果处理失败，返回原始响应
        return response_data
    
    return processed_response

def _apply_regex_rules_to_content(
    content: str, 
    regex_rules: List[Dict[str, Any]]
) -> str:
    """
    将正则表达式规则列表应用于指定的文本内容。

    Args:
        content: 要处理的文本内容
        regex_rules: 正则规则列表

    Returns:
        str: 处理后的文本内容
    """
    if not content or not regex_rules:
        return content
    
    processed_content = content
    
    for rule in regex_rules:
        try:
            pattern = rule.get('查找', '')
            replacement = rule.get('替换', '')
            
            if pattern:
                # 使用 re.sub 应用正则替换
                processed_content = re.sub(pattern, replacement, processed_content)
                logger.debug(f"应用正则规则: '{pattern}' -> '{replacement}'")
        except Exception as e:
            logger.warning(f"正则规则应用失败: 模式='{pattern}', 替换='{replacement}', 错误={e}")
            continue
    
    return processed_content