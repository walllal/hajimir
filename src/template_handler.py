# src/template_handler.py
"""
此模块负责处理提示词模板的加载、解析、动态变量替换以及最终消息的准备。

主要功能包括：
- 从 YAML 文件加载和缓存提示词模板及正则表达式规则。
- 支持模板的热重载，当模板文件更新时自动重新加载。
- 处理模板内容中的动态变量，例如：
    - 骰子投掷 (`{{roll XdY}}`): 模拟投掷X个Y面骰子，并替换为总点数。
    - 随机选择 (`{{random::opt1::opt2...}}`): 从提供的选项中随机选择一个。
    - 变量设置 (`{{setvar::变量名::值}}`): 设置一个变量的值。
    - 变量获取 (`{{getvar::变量名}}`): 获取变量的值，如果不存在则返回空字符串。
- 应用用户定义的正则表达式规则对生成内容或模板内容进行后处理。
- 根据加载的模板、用户输入历史和最新的用户输入，构建最终用于提交给大语言模型的
  OpenAI 格式消息列表。这包括模板注入、消息合并等逻辑。
"""
import yaml
import copy
import logging
import re
import random
import os
import json # <--- 新增导入 json 模块
from typing import List, Dict, Any

from .config import settings

logger = logging.getLogger(settings.app_name) # 获取logger实例，用于记录模块相关信息

# --- 模块级全局变量 ---
_CACHED_PROMPT_BLUEPRINTS: Dict[str, List[Dict[str, Any]]] = {}  # 按模板路径缓存的提示词蓝图
_CACHED_REGEX_RULES: Dict[str, List[Dict[str, Any]]] = {}        # 按模板路径缓存的正则规则
_LAST_TEMPLATE_MTIME: Dict[str, float] = {}                      # 按模板路径缓存的最后修改时间

# --- 新增：全局变量存储 ---
_GLOBAL_VARIABLES: Dict[str, str] = {}                          # 全局变量存储字典

def _get_template_path_for_user_input(user_input_content: str) -> str:
    """
    根据用户输入内容选择合适的模板文件路径。
    
    Args:
        user_input_content (str): 用户输入的内容
        
    Returns:
        str: 选择的模板文件路径
    """
    if user_input_content and user_input_content.strip():
        # 用户输入有内容，使用 with_input 模板
        return settings.proxy.prompt_template_path_with_input
    else:
        # 用户输入无内容，使用 without_input 模板
        return settings.proxy.prompt_template_path_without_input

def _load_templates(template_path: str, force_reload: bool = False) -> None:
    """
    加载或热加载在 YAML 文件中定义的提示词模板和正则表达式规则。

    此函数从指定的 YAML 文件中读取模板内容，并将其分类为两种类型：
    - 正则表达式规则：用于在消息内容中查找和替换特定模式的字典项。
      此时应包含 `查找` (find_pattern) 和 `替换` (replace_pattern) 两个键。
    - 其他所有字典项被视为提示词蓝图，通常包含 `role` 和 `content` 键。

    此函数会按模板路径缓存加载的内容。只有在模板文件被修改或 `force_reload` 参数为 True 时，
    才会执行重新加载操作。

    Args:
        template_path (str): 模板文件的路径。
        force_reload (bool, optional): 如果为 True，则强制重新加载模板文件，
                                       忽略文件修改时间的比较。默认为 False。

    Returns:
        None: 此函数不返回任何值，但会更新模块级的全局缓存变量：
              `_CACHED_PROMPT_BLUEPRINTS` (按路径缓存的提示词蓝图字典),
              `_CACHED_REGEX_RULES` (按路径缓存的正则规则字典),
              和 `_LAST_TEMPLATE_MTIME` (按路径缓存的最后加载时文件的时间戳)。
    """
    global _CACHED_PROMPT_BLUEPRINTS, _CACHED_REGEX_RULES, _LAST_TEMPLATE_MTIME
    
    try:
        current_mtime = os.path.getmtime(template_path) # 获取模板文件当前的最后修改时间
    except FileNotFoundError:
        # 文件未找到时的处理逻辑
        if not hasattr(_load_templates, '_logged_not_found_paths'):
            # 使用函数属性（静态变量类似物）来跟踪已记录的未找到路径集合，避免日志刷屏
            _load_templates._logged_not_found_paths = set()
        if template_path not in _load_templates._logged_not_found_paths:
            logger.error(f"提示词模板文件 '{template_path}' 未找到。将使用空模板和规则。")
            _load_templates._logged_not_found_paths.add(template_path) # 将路径加入已记录集合
        # 清空该路径的缓存，确保后续逻辑使用空模板/规则
        _CACHED_PROMPT_BLUEPRINTS[template_path] = []
        _CACHED_REGEX_RULES[template_path] = []
        _LAST_TEMPLATE_MTIME[template_path] = 0.0 # 重置时间戳
        return

    # 条件性跳过加载：
    # 1. 非强制重载
    # 2. 当前文件修改时间与上次加载时相同
    # 3. 上次加载时间戳不为0（表示至少成功加载过一次）
    if not force_reload and current_mtime == _LAST_TEMPLATE_MTIME.get(template_path, 0.0) and _LAST_TEMPLATE_MTIME.get(template_path, 0.0) != 0.0:
        logger.debug(f"模板文件 '{template_path}' 未更改，跳过加载。")
        return

    logger.info(f"尝试加载/热加载模板文件: '{template_path}' (上次修改时间: {_LAST_TEMPLATE_MTIME.get(template_path, 0.0)}, 当前文件修改时间: {current_mtime})")
    try:
        with open(template_path, "r", encoding="utf-8") as f:
            loaded_yaml_content = yaml.safe_load(f) # 从 YAML 文件安全加载内容
        
        # 如果之前记录过此路径未找到，现在找到了就从集合中移除，以便下次找不到时能再次记录
        if hasattr(_load_templates, '_logged_not_found_paths') and template_path in _load_templates._logged_not_found_paths:
            _load_templates._logged_not_found_paths.remove(template_path)

        if isinstance(loaded_yaml_content, list):
            # YAML 内容成功加载且为列表类型
            new_blueprints = [] # 用于存储本次加载的提示词蓝图
            new_regex_rules = []  # 用于存储本次加载的正则规则
            for item_idx, item in enumerate(loaded_yaml_content): # 遍历YAML中的每个顶层列表项
                if isinstance(item, dict):
                    item_type = item.get("type") # 获取项的类型，用于区分正则规则和普通提示
                    if item_type == "正则":
                        # 处理正则表达式规则
                        find_pattern = item.get("查找")
                        replace_pattern = item.get("替换")
                        rule_action = item.get("action", "replace") # 默认为替换操作
                        
                        if find_pattern is not None and replace_pattern is not None:
                            rule_entry = {
                                "查找": str(find_pattern),    # 确保查找模式是字符串
                                "替换": str(replace_pattern),  # 确保替换内容是字符串
                                "action": str(rule_action).lower() # 确保action是小写字符串
                            }
                            # 如果 action 是 json_payload，可以添加额外的验证或处理
                            if rule_entry["action"] == "json_payload":
                                try:
                                    # 尝试解析替换模式（此时应为JSON字符串）以验证其有效性
                                    json.loads(rule_entry["替换"]) 
                                    logger.debug(f"加载 JSON 载荷正则规则 #{len(new_regex_rules)+1}: 查找='{find_pattern}', 动作='json_payload'")
                                except json.JSONDecodeError:
                                    logger.warning(f"模板文件 '{template_path}' 中的 '正则' 类型块 (索引 {item_idx}) action 为 'json_payload' 但 '替换' 字段不是有效的JSON字符串: '{replace_pattern}'。此规则可能无法按预期工作。")
                                    # 仍然添加规则，但记录警告
                            
                            new_regex_rules.append(rule_entry)
                            if rule_entry["action"] != "json_payload": # 避免重复记录已记录的json_payload
                                logger.debug(f"加载正则规则 #{len(new_regex_rules)}: 查找='{find_pattern}', 替换='{replace_pattern}', 动作='{rule_action}'")
                        else:
                            logger.warning(f"模板文件 '{template_path}' 中的 '正则' 类型块 (索引 {item_idx}) 缺少 '查找' 或 '替换' 字段，或其值为 None，已忽略: {item}")
                    else:
                        # 非 "正则" 类型的项视为提示词蓝图
                        new_blueprints.append(item)
                else:
                    logger.warning(f"模板文件 '{template_path}' 中包含非字典类型的顶层列表项 (索引 {item_idx})，已忽略: {item}")
            
            # 成功解析完所有项后，更新该路径的缓存
            _CACHED_PROMPT_BLUEPRINTS[template_path] = new_blueprints
            _CACHED_REGEX_RULES[template_path] = new_regex_rules
            _LAST_TEMPLATE_MTIME[template_path] = current_mtime # 更新最后修改时间戳
            logger.info(f"提示词模板 '{template_path}' 已成功加载/热加载。提示词块数: {len(new_blueprints)}, 正则规则数: {len(new_regex_rules)}")
        else:
            # YAML 文件内容不是列表，加载失败
            logger.warning(f"加载/热加载模板 '{template_path}' 失败：文件内容不是一个列表 (实际类型: {type(loaded_yaml_content).__name__})。将保留上一个有效版本（如果有）。")
            if _LAST_TEMPLATE_MTIME.get(template_path, 0.0) == 0.0: # 如果是首次加载就失败，则确保缓存为空
                 _CACHED_PROMPT_BLUEPRINTS[template_path] = []
                 _CACHED_REGEX_RULES[template_path] = []
    except yaml.YAMLError as e:
        # YAML 解析错误
        logger.error(f"解析模板文件 '{template_path}' 失败: {e}。将保留上一个有效版本（如果有）。")
        if _LAST_TEMPLATE_MTIME.get(template_path, 0.0) == 0.0: # 首次加载失败
            _CACHED_PROMPT_BLUEPRINTS[template_path] = []
            _CACHED_REGEX_RULES[template_path] = []
    except Exception as e:
        # 其他未知错误
        logger.error(f"加载模板文件 '{template_path}' 时发生未知错误: {e}。将保留上一个有效版本（如果有）。", exc_info=settings.debug_mode)
        if _LAST_TEMPLATE_MTIME.get(template_path, 0.0) == 0.0: # 首次加载失败
            _CACHED_PROMPT_BLUEPRINTS[template_path] = []
            _CACHED_REGEX_RULES[template_path] = []

def _process_variables(text_content: str) -> str:
    """
    处理文本内容中的变量设置和获取操作。
    
    支持的变量操作：
    - {{setvar::变量名::值}}: 设置一个全局变量的值，替换为空字符串
    - {{getvar::变量名}}: 获取全局变量的值，如果不存在则返回空字符串
    
    变量设置会先于变量获取执行，这样可以在同一条消息中设置和获取变量。
    
    Args:
        text_content (str): 可能包含变量操作的原始文本内容。
        
    Returns:
        str: 处理过变量操作后的文本内容。如果输入不是字符串，则原样返回。
    """
    global _GLOBAL_VARIABLES
    
    if not isinstance(text_content, str):
        logger.debug("输入到 _process_variables 的内容非字符串，跳过处理。")
        return text_content
    
    current_content = text_content
    
    # 第一步：处理变量设置 {{setvar::变量名::值}}
    def replace_setvar(match: re.Match) -> str:
        """内部辅助函数，用于处理变量设置。"""
        try:
            var_name = match.group(1).strip() # 变量名
            var_value = match.group(2) if match.group(2) is not None else "" # 变量值，允许为空
            
            if not var_name:
                logger.warning("变量设置操作中变量名为空，操作被跳过。")
                return f"{{setvar::{var_name}::{var_value} - 变量名为空}}"
            
            # 设置全局变量
            _GLOBAL_VARIABLES[var_name] = var_value
            logger.debug(f"设置全局变量: '{var_name}' = '{var_value}'")
            
            # 返回空字符串，即移除setvar标签
            return ""
            
        except Exception as e:
            logger.error(f"处理变量设置 {{setvar::{match.group(1)}::{match.group(2)}}} 时出错: {e}", exc_info=settings.debug_mode)
            return f"{{setvar::{match.group(1)}::{match.group(2)} - 处理错误}}"
    
    # 使用正则表达式处理所有的setvar操作
    # 模式解释：匹配 {{setvar::变量名::值}}，其中值可能包含::，所以用非贪婪匹配到最后的}}
    current_content = re.sub(r"\{\{setvar::([^:]+)::(.*?)\}\}", replace_setvar, current_content)
    
    # 第二步：处理变量获取 {{getvar::变量名}}
    def replace_getvar(match: re.Match) -> str:
        """内部辅助函数，用于处理变量获取。"""
        try:
            var_name = match.group(1).strip() # 变量名
            
            if not var_name:
                logger.warning("变量获取操作中变量名为空，返回空字符串。")
                return ""
            
            # 获取全局变量值，如果不存在则返回空字符串
            var_value = _GLOBAL_VARIABLES.get(var_name, "")
            logger.debug(f"获取全局变量: '{var_name}' = '{var_value}'")
            
            return var_value
            
        except Exception as e:
            logger.error(f"处理变量获取 {{getvar::{match.group(1)}}} 时出错: {e}", exc_info=settings.debug_mode)
            return f"{{getvar::{match.group(1)} - 处理错误}}"
    
    # 使用正则表达式处理所有的getvar操作
    current_content = re.sub(r"\{\{getvar::([^}]+)\}\}", replace_getvar, current_content)
    
    return current_content

def get_global_variables() -> Dict[str, str]:
    """
    获取当前所有全局变量的副本。
    
    Returns:
        Dict[str, str]: 包含所有全局变量的字典副本。
    """
    return _GLOBAL_VARIABLES.copy()

def set_global_variable(var_name: str, var_value: str) -> None:
    """
    手动设置一个全局变量。
    
    Args:
        var_name (str): 变量名
        var_value (str): 变量值
    """
    global _GLOBAL_VARIABLES
    if not isinstance(var_name, str) or not var_name.strip():
        logger.warning("试图设置的变量名无效，操作被跳过。")
        return
    
    _GLOBAL_VARIABLES[var_name.strip()] = str(var_value) if var_value is not None else ""
    logger.debug(f"手动设置全局变量: '{var_name}' = '{var_value}'")

def clear_global_variables() -> None:
    """
    清除所有全局变量。
    """
    global _GLOBAL_VARIABLES
    cleared_count = len(_GLOBAL_VARIABLES)
    _GLOBAL_VARIABLES.clear()
    logger.info(f"已清除 {cleared_count} 个全局变量。")

def _process_dice_rolls(text_content: str) -> str:
    """
    处理文本内容中的骰子投掷变量 `{{roll XdY}}`。

    将匹配到的 `{{roll XdY}}` 替换为 X 个 Y 面骰子的投掷结果总和。
    例如 `{{roll 2d6}}` 会被替换为一个表示两次六面骰子投掷总和的数字字符串。

    Args:
        text_content (str): 可能包含骰子变量的原始文本内容。

    Returns:
        str: 处理过骰子变量后的文本内容。如果输入不是字符串，则原样返回。
             如果骰子参数无效或处理出错，会将错误信息嵌入替换后的字符串中。
    """
    if not isinstance(text_content, str):
        logger.debug("输入到 _process_dice_rolls 的内容非字符串，跳过处理。")
        return text_content # 如果输入不是字符串，直接返回

    def replace_dice_roll(match: re.Match) -> str:
        """内部辅助函数，用于替换单个骰子匹配项。"""
        try:
            num_dice = int(match.group(1))    # 骰子数量 X
            num_sides = int(match.group(2))   # 骰子面数 Y

            if num_dice <= 0 or num_sides <= 0:
                logger.warning(f"无效的骰子参数: {{roll {num_dice}d{num_sides}}}，数量和面数必须为正。")
                return f"{{roll {num_dice}d{num_sides} - 无效的骰子参数}}" # 参数错误提示
            
            # 模拟投掷
            total_roll = sum(random.randint(1, num_sides) for _ in range(num_dice))
            logger.debug(f"处理骰子变量: {{roll {num_dice}d{num_sides}}} -> {total_roll}")
            return str(total_roll)
        except ValueError:
            # 参数无法转换为整数
            logger.warning(f"骰子参数无法转换为整数: {{roll {match.group(1)}d{match.group(2)}}}")
            return f"{{roll {match.group(1)}d{match.group(2)} - 参数非整数}}"
        except Exception as e:
            # 其他处理错误
            logger.error(f"处理骰子变量 {{roll {match.group(1)}d{match.group(2)}}} 时出错: {e}", exc_info=settings.debug_mode)
            return f"{{roll {match.group(1)}d{match.group(2)} - 处理错误}}"

    # 使用正则表达式查找所有 {{roll XdY}} 格式的变量，并用其投掷结果替换
    # \s* 允许数字前后有空格，例如 {{roll 2 d 6}}
    return re.sub(r"\{\{roll\s*(\d+)\s*d\s*(\d+)\s*\}\}", replace_dice_roll, text_content)

def _process_random_choices(text_content: str) -> str:
    """
    处理文本内容中的随机选择变量 `{{random::opt1::opt2...}}`。

    将匹配到的 `{{random::opt1::opt2...}}` 替换为从 `opt1`, `opt2` 等选项中
    随机选择的一个。选项之间用 `::` 分隔。

    Args:
        text_content (str): 可能包含随机选择变量的原始文本内容。

    Returns:
        str: 处理过随机选择变量后的文本内容。如果输入不是字符串，则原样返回。
             如果无选项或处理出错，会将错误信息嵌入替换后的字符串中。
    """
    if not isinstance(text_content, str):
        logger.debug("输入到 _process_random_choices 的内容非字符串，跳过处理。")
        return text_content # 如果输入不是字符串，直接返回

    def replace_random_choice(match: re.Match) -> str:
        """内部辅助函数，用于替换单个随机选择匹配项。"""
        try:
            options_str = match.group(1) # 获取 `::` 分隔的选项字符串
            if not options_str: # 例如 {{random::}}
                logger.warning("随机选择变量 {{random::}} 无任何选项。")
                return "{{random:: - 无选项}}"
            
            options = options_str.split('::') # 按 '::' 分割选项
            
            # 检查并处理空选项，例如 {{random::a::::b}} 会产生空字符串
            if not all(options): 
                 logger.warning(f"随机选择变量 {{random::{options_str}}} 包含空选项。将过滤空选项。")
                 options = [opt for opt in options if opt] # 过滤掉空字符串选项
                 if not options: # 如果过滤后没有有效选项了
                     logger.warning(f"随机选择变量 {{random::{options_str}}} 过滤空选项后无有效选项。")
                     return "{{random:: - 过滤后无有效选项}}"

            chosen = random.choice(options) # 从有效选项中随机选择一个
            logger.debug(f"处理随机选择变量: {{random::{options_str}}} -> {chosen}")
            return chosen
        except Exception as e:
            # 处理错误
            logger.error(f"处理随机选择变量 {{random::{match.group(1)}}} 时出错: {e}", exc_info=settings.debug_mode)
            return f"{{random::{match.group(1)}}} - 处理错误}}"
            
    # 使用正则表达式查找所有 {{random::...}} 格式的变量
    # (.*?) 是非贪婪匹配，匹配两个 "random::" 和 "}}" 之间的任何字符
    return re.sub(r"\{\{random::(.*?)\}\}", replace_random_choice, text_content)

def _apply_regex_rules_to_content(text_content: str, regex_rules: List[Dict[str, Any]]) -> str:
    """
    按顺序将指定的正则表达式规则应用于给定的文本内容。

    这些规则从模板文件中加载（类型为 "正则" 的项）。
    每个规则包含 "查找" (正则表达式模式) 和 "替换" (替换字符串) 以及可选的 "action"。
    支持的 action:
        - "replace" (默认): 执行标准的查找和替换。
        - "json_payload": 将 `text_content` 视为 JSON 字符串，将 `rule['替换']` 也视为 JSON 字符串。
                          尝试将 `rule['替换']` 中的 "payload" 键的值，更新或添加到 `text_content` JSON 对象
                          的 "tool_code_interpreter_output" 键下（如果已存在则更新，不存在则创建）。
                          `rule['查找']` 在此 action 下通常不直接用于 re.sub，而是可能用于条件判断（当前未实现）。

    Args:
        text_content (str): 需要应用正则规则的原始文本内容。
        regex_rules (List[Dict[str, Any]]): 要应用的正则规则列表。

    Returns:
        str: 应用所有正则规则处理后的文本内容。
             如果无规则或输入不是字符串，则原样返回。
    """
    if not regex_rules: # 如果没有正则规则
        logger.debug("无正则规则，跳过 _apply_regex_rules_to_content 处理。")
        return text_content
    if not isinstance(text_content, str): # 如果输入不是字符串
        logger.debug("输入到 _apply_regex_rules_to_content 的内容非字符串，跳过处理。")
        return text_content

    current_content = text_content
    logger.debug(f"开始对内容应用 {len(regex_rules)} 条正则规则。")
    for rule_idx, rule in enumerate(regex_rules): # 遍历所有规则
        try:
            find_pattern = rule.get("查找", "")
            replace_pattern = rule.get("替换", "")
            action = rule.get("action", "replace") # 默认为 "replace"

            if action == "json_payload":
                logger.debug(f"处理 JSON 载荷规则 #{rule_idx + 1}: 查找='{find_pattern}'")
                try:
                    # 替换模式此时应为包含 "payload" 键的 JSON 字符串
                    payload_obj_from_rule = json.loads(replace_pattern)
                    payload_to_inject = payload_obj_from_rule.get("payload")

                    if payload_to_inject is None:
                        logger.warning(f"JSON 载荷规则 #{rule_idx + 1} 的 '替换' JSON 中缺少 'payload' 键或其值为 null，规则跳过。替换内容: {replace_pattern}")
                        continue

                    # 原始内容也应为 JSON 字符串，或者至少是可解析为 JSON 的
                    try:
                        current_content_obj = json.loads(current_content)
                        if not isinstance(current_content_obj, dict):
                             logger.warning(f"JSON 载荷规则 #{rule_idx + 1}: 当前内容解析为 JSON 但不是字典类型 (类型: {type(current_content_obj).__name__})，无法注入 payload。规则跳过。")
                             continue
                    except json.JSONDecodeError:
                         # 如果当前内容不是有效的 JSON，则创建一个新的字典结构
                         logger.warning(f"JSON 载荷规则 #{rule_idx + 1}: 当前内容不是有效的 JSON，将创建新的 JSON 结构以注入 payload。原始内容: '{current_content[:100]}...'")
                         current_content_obj = {} # 或者可以决定如何处理这种情况，例如跳过

                    # 将 payload 注入或更新到 current_content_obj 的 "tool_code_interpreter_output" 键
                    # 注意：这里简单地覆盖或设置。如果需要更复杂的合并逻辑，需要相应修改。
                    current_content_obj["tool_code_interpreter_output"] = payload_to_inject
                    
                    # 将修改后的对象转换回 JSON 字符串
                    processed_content = json.dumps(current_content_obj, ensure_ascii=False, indent=2)
                    logger.info(f"JSON 载荷规则 #{rule_idx + 1} 应用成功。'tool_code_interpreter_output' 已更新/设置。")

                except json.JSONDecodeError as jde:
                    logger.error(f"JSON 载荷规则 #{rule_idx + 1} 处理时发生 JSON 解析错误: {jde}. '替换'内容: '{replace_pattern}', 当前内容: '{current_content[:100]}...'. 规则跳过。")
                    continue # 跳过此规则
                except Exception as e_json_op:
                    logger.error(f"JSON 载荷规则 #{rule_idx + 1} 处理时发生未知错误: {e_json_op}. 规则跳过。", exc_info=settings.debug_mode)
                    continue # 跳过此规则
            
            elif action == "replace":
                # 使用 re.sub 应用标准正则表达式替换
                processed_content = re.sub(find_pattern, replace_pattern, current_content)
                if processed_content != current_content:
                    logger.debug(f"应用替换正则规则 #{rule_idx + 1}: 查找='{find_pattern}', 替换='{replace_pattern}'. 内容已更改。")
            else:
                logger.warning(f"未知的正则规则 action: '{action}' (规则 #{rule_idx + 1})。规则跳过。")
                processed_content = current_content # 保持不变

            current_content = processed_content
        except re.error as e_re:
            # 正则表达式本身有错误 (主要针对 action="replace")
            logger.error(f"应用正则规则 #{rule_idx + 1} (查找='{find_pattern}') 时发生正则表达式错误: {e_re}. 该规则被跳过。")
        except Exception as e_outer_rule:
            # 其他未知错误
            logger.error(f"应用正则规则 #{rule_idx + 1} (查找='{find_pattern}', 替换='{replace_pattern}') 时发生未知错误: {e_outer_rule}. 该规则被跳过。", exc_info=settings.debug_mode)
    
    logger.debug("所有正则规则应用完毕。")
    return current_content

def _prepare_openai_messages(original_body: Dict[str, Any]) -> Dict[str, Any]:
    """
    根据原始请求体中的消息、加载的提示词模板和动态变量处理，准备最终的 OpenAI 格式消息列表。

    处理流程包括：
    1. 提取原始消息中的历史记录和最后一个用户输入。
    2. 根据用户输入内容选择合适的模板文件（有输入/无输入）。
    3. 确保选定的模板已加载（调用 `_load_templates`）。
    4. 根据模板注入历史消息和用户输入：
        - 如果模板中有 `type: api_input_placeholder`，则在该位置插入历史消息。
        - 将模板内容中的 `{{user_input}}` 替换为最后一个用户输入。
    5. 对所有生成的消息内容应用全局动态变量处理 ({{roll}}, {{random}}, {{setvar}}, {{getvar}})
    6. 移除内容为空或 None 的消息。
    7. 合并相邻的、角色相同的消息。

    Args:
        original_body (Dict[str, Any]): 原始的 OpenAI 格式请求体，
                                       期望包含 "messages" (消息列表) 和 "model" (模型名称) 键。

    Returns:
        Dict[str, Any]: 一个包含三个键的字典：
                        - "model": 从原始请求中获取的模型名称。
                        - "messages": 处理和合并后的最终 OpenAI 格式消息列表。
                        - "selected_regex_rules": 选定模板的正则规则列表，用于响应处理阶段。
    """
    original_messages: List[Dict[str, Any]] = original_body.get("messages", [])
    if not isinstance(original_messages, list):
        logger.warning(f"请求体中的 'messages' 不是一个列表 (实际类型: {type(original_messages).__name__})，将视为空消息列表。")
        original_messages = [] # 如果 messages 无效，视为空列表

    # 1. 提取历史消息和最后一个用户输入
    processed_messages: List[Dict[str, Any]] = [] # 用于构建处理后的消息列表
    last_user_input_content_for_processing: Any = "" # 用户的完整最后输入 (str 或 list)
    last_user_text_for_templating: str = ""          # 从用户最后输入中提取的文本，用于模板选择和替换
    historic_messages: List[Dict[str, Any]] = []     # 存储除最后一个用户输入外的历史消息

    if original_messages:
        if original_messages[-1].get("role") == "user":
            # 如果最后一条消息是用户消息，则将其视为当前用户输入，其余为历史
            last_user_input_content_for_processing = original_messages[-1].get("content", "")
            historic_messages = original_messages[:-1]

            if isinstance(last_user_input_content_for_processing, list):
                # 从列表中提取第一个文本部分用于模板
                for item in last_user_input_content_for_processing:
                    if isinstance(item, dict) and item.get("type") == "text":
                        last_user_text_for_templating = item.get("text", "")
                        break
                # 如果列表中没有文本，last_user_text_for_templating 保持 ""
            elif isinstance(last_user_input_content_for_processing, str):
                last_user_text_for_templating = last_user_input_content_for_processing
            # else (e.g., content is None or other type), last_user_text_for_templating 保持 ""
        else:
            # 如果最后一条不是用户消息，则所有消息都视为历史
            # last_user_input_content_for_processing 和 last_user_text_for_templating 保持 ""
            historic_messages = original_messages
    
    # 2. 根据用户输入内容选择合适的模板文件
    selected_template_path = _get_template_path_for_user_input(last_user_text_for_templating) # 使用提取的文本进行判断
    logger.debug(f"根据用户输入选择模板文件: '{selected_template_path}' (用于模板的文本内容长度: {len(last_user_text_for_templating.strip()) if last_user_text_for_templating else 0})")
    
    # 3. 确保选定的模板已加载
    _load_templates(template_path=selected_template_path)
    current_blueprints = _CACHED_PROMPT_BLUEPRINTS.get(selected_template_path, []) # 获取选定模板的缓存
    current_regex_rules = _CACHED_REGEX_RULES.get(selected_template_path, [])     # 获取选定模板的正则规则
    
    # 4. 根据模板注入消息和用户输入
    if not current_blueprints: # 如果没有加载任何模板蓝图
        logger.debug("未加载任何提示词模板，直接使用原始消息（如有）。")
        processed_messages.extend(copy.deepcopy(historic_messages)) # 直接使用历史消息
        if last_user_input_content_for_processing: # 如果有最后的用户输入 (str 或 list)，也添加进去
             processed_messages.append({"role": "user", "content": copy.deepcopy(last_user_input_content_for_processing)})
        # 特殊情况：如果原始消息只有一条用户消息，且无历史，确保它被加入
        elif not historic_messages and original_messages and original_messages[-1].get("role") == "user":
            # 确保使用深拷贝，并且是原始的 content (可能是列表)
            user_msg_copy = copy.deepcopy(original_messages[-1])
            # content 已经是原始的，不需要从 last_user_input_content_for_processing 再次获取
            processed_messages.append(user_msg_copy)
        
        # 如果上述处理后 processed_messages 仍为空，但 original_messages 不为空
        # (例如，original_messages 只包含 assistant 消息且无模板)，则直接使用原始消息。
        # 这种情况通常不应该发生，因为期望至少有用户输入或模板。
        if not processed_messages and original_messages:
            logger.debug("无模板且无明确用户输入分离，直接深拷贝原始消息作为基础。")
            processed_messages = copy.deepcopy(original_messages)
    else: # 有模板蓝图的情况
        logger.debug(f"使用 {len(current_blueprints)} 条模板蓝图处理消息。")
        for blueprint_msg_template in current_blueprints:
            blueprint_msg = copy.deepcopy(blueprint_msg_template) # 深拷贝模板项以防修改缓存
            if blueprint_msg.get("type") == "api_input_placeholder":
                # 如果模板项是历史消息占位符，则在此处插入历史消息
                logger.debug(f"在模板中遇到 'api_input_placeholder'，插入 {len(historic_messages)} 条历史消息。")
                processed_messages.extend(copy.deepcopy(historic_messages))
            else:
                # 普通模板项
                content_template = blueprint_msg.get("content")
                role = blueprint_msg.get("role")

                if role == "user" and isinstance(content_template, str) and "{{user_input}}" in content_template:
                    if isinstance(last_user_input_content_for_processing, list):
                        # 用户输入是多模态列表，需要将模板文本和用户输入列表合并
                        parts = content_template.split("{{user_input}}", 1) # 只分割一次，得到占位符前后的部分
                        new_content_list = []
                        if parts[0]: # 如果 {{user_input}} 前面有文本
                            new_content_list.append({"type": "text", "text": parts[0]})
                        
                        new_content_list.extend(copy.deepcopy(last_user_input_content_for_processing)) # 添加用户输入的多模态内容
                        
                        if len(parts) > 1 and parts[1]: # 如果 {{user_input}} 后面有文本
                            new_content_list.append({"type": "text", "text": parts[1]})
                        
                        blueprint_msg["content"] = new_content_list
                        logger.debug(f"用户角色蓝图的多模态 content 构建完成: {blueprint_msg['content']}")
                    elif isinstance(last_user_input_content_for_processing, str):
                        # 用户输入是纯文本字符串，直接替换
                        blueprint_msg["content"] = content_template.replace("{{user_input}}", last_user_input_content_for_processing)
                        logger.debug(f"用户角色蓝图的文本 content ({{user_input}} 存在) 被用户文本输入替换: {blueprint_msg['content']}")
                    else:
                        # 用户输入为空或非预期类型，用空字符串替换 {{user_input}}
                        blueprint_msg["content"] = content_template.replace("{{user_input}}", "")
                        logger.debug(f"用户角色蓝图的 {{user_input}} 被空字符串替换，因用户输入为空或非列表/字符串。")
                elif isinstance(content_template, str):
                    # 对于其他角色，或者用户角色但其模板 content 不包含 "{{user_input}}" 的情况
                    # 如果模板内容中仍有 {{user_input}} (理论上不应进入此分支如果上面逻辑正确覆盖了 user role 的情况)
                    # 或者对于非 user role 的模板，我们仍然用 last_user_text_for_templating 替换
                    blueprint_msg["content"] = content_template.replace("{{user_input}}", last_user_text_for_templating)
                    # logger.debug(f"角色 '{role}' 蓝图的 content ({{user_input}} 可能存在) 被 'last_user_text_for_templating' 处理。")

                # 如果 content_template 本身不是字符串 (例如，模板中直接定义了列表内容)，则不进行替换，保留原样
                
                processed_messages.append(blueprint_msg)
        
        # 检查模板中是否实际包含了代表当前用户输入的 'user' role 蓝图
        # (更准确的检查是看 last_user_input_content_for_processing 是否已经被某个 user role 的蓝图使用了)
        # 一个简化的检查：如果 last_user_input_content_for_processing 存在，但 processed_messages 中最后一条不是 user role，
        # 或者最后一条是 user role 但其 content 与 last_user_input_content_for_processing 不同源（这比较难判断）
        # 我们假设：如果模板中没有显式的 user role 蓝图来承接 last_user_input_content_for_processing，
        # 并且 last_user_input_content_for_processing 确实存在，那么它应该被追加。

        # 查找是否有 user role 的蓝图处理了 {{user_input}}
        user_input_explicitly_handled = False
        for bp_msg in current_blueprints: # 检查原始蓝图
            if bp_msg.get("role") == "user" and isinstance(bp_msg.get("content"), str) and "{{user_input}}" in bp_msg.get("content"):
                user_input_explicitly_handled = True
                break
        
        if not user_input_explicitly_handled and last_user_input_content_for_processing:
            # 如果没有任何 user role 的蓝图包含 {{user_input}} 来处理当前用户输入，
            # 并且确实存在当前用户输入，则将其作为新的 user消息追加。
            # 这覆盖了 "模板中无 'api_input_placeholder'，但存在最后用户输入" 的情况，
            # 以及 "原始消息仅一条用户消息，且模板未处理它" 的情况。
            logger.debug("当前用户输入未被任何包含 '{{user_input}}' 的用户角色蓝图处理，将其作为新消息追加。")
            processed_messages.append({"role": "user", "content": copy.deepcopy(last_user_input_content_for_processing)})

    # 5. 对所有消息内容应用全局动态变量处理 ({{roll}}, {{random}}, {{setvar}}, {{getvar}})
    final_messages_step1: List[Dict[str, Any]] = []
    logger.debug(f"开始对 {len(processed_messages)} 条消息应用动态变量处理。")
    for msg in processed_messages:
        new_msg = copy.deepcopy(msg) # 使用深拷贝以防修改原始列表中的字典
        content_val = new_msg.get("content")
        if isinstance(content_val, str):
            # 对字符串内容应用动态变量处理
            # 按顺序应用：变量操作 -> 骰子投掷 -> 随机选择
            content_after_vars = _process_variables(content_val)
            content_after_dice = _process_dice_rolls(content_after_vars)
            content_after_random = _process_random_choices(content_after_dice)
            new_msg["content"] = content_after_random
        elif isinstance(content_val, list):
            # 对列表内容中的文本部分应用动态变量处理
            processed_list_content = []
            for item in content_val:
                if isinstance(item, dict) and item.get("type") == "text":
                    text_part = item.get("text", "")
                    # 按顺序应用：变量操作 -> 骰子投掷 -> 随机选择
                    text_after_vars = _process_variables(text_part)
                    text_after_dice = _process_dice_rolls(text_after_vars)
                    text_after_random = _process_random_choices(text_after_dice)
                    # 创建新字典或更新副本以避免修改原始 item
                    processed_item = copy.deepcopy(item)
                    processed_item["text"] = text_after_random
                    processed_list_content.append(processed_item)
                else:
                    processed_list_content.append(copy.deepcopy(item)) # 其他部分原样保留
            new_msg["content"] = processed_list_content
        final_messages_step1.append(new_msg)

    # 6. 移除 content 为空字符串或 None 的消息
    final_messages_step2: List[Dict[str, Any]] = []
    if final_messages_step1:
        original_count = len(final_messages_step1)
        for msg in final_messages_step1:
            if isinstance(msg, dict) and msg.get("content") is not None:
                content_val = msg.get("content")
                if isinstance(content_val, str) and content_val == "": # 空字符串内容
                    pass # 不添加
                elif isinstance(content_val, list) and not content_val: # 空列表内容
                    pass # 不添加
                else:
                    final_messages_step2.append(msg) # 其他情况（非空字符串，非空列表）
            # 如果 msg 不是字典或 content 为 None，则不添加
        if len(final_messages_step2) < original_count:
            logger.debug(f"移除了 {original_count - len(final_messages_step2)} 条 content 为空/None 或无效的消息。")
        if not final_messages_step2 and original_count > 0: # 如果所有消息都被移除了
            logger.warning("所有消息因 content 为空/None 或无效被移除。最终消息列表将为空。")
    
    # 7. 合并相邻的、角色相同的消息
    if not final_messages_step2:
        merged_messages: List[Dict[str, Any]] = []
        logger.debug("消息列表为空，无需合并。")
    else:
        merged_messages = []
        # 深拷贝第一个消息作为合并起点
        current_message = copy.deepcopy(final_messages_step2[0]) 
        
        for i in range(1, len(final_messages_step2)):
            next_msg = final_messages_step2[i]
            # 检查角色是否相同，且内容是否都是字符串（才能合并）
            if next_msg.get("role") == current_message.get("role") and \
               isinstance(next_msg.get("content"), str) and \
               isinstance(current_message.get("content"), str):
                # 合并内容，用换行符分隔
                current_message["content"] += "\n" + next_msg["content"]
            else:
                # 角色不同或内容类型不适合合并，将当前已合并的消息加入列表
                merged_messages.append(current_message)
                current_message = copy.deepcopy(next_msg) # 开始新的合并段
        merged_messages.append(current_message) # 添加最后一个处理中的消息段
        logger.debug(f"消息合并后，消息数量从 {len(final_messages_step2)} 变为 {len(merged_messages)}。")

    # 准备最终返回结果
    result = {
        "model": original_body.get("model"), # 保留原始请求中的模型名称
        "messages": merged_messages,
        "selected_regex_rules": current_regex_rules
    }
    
    if result.get("model") is None:
        logger.warning("请求中 model 参数为 None 或未提供。")
    if not merged_messages:
        logger.info("预处理后最终的 messages 列表为空。") # 使用 info 级别，因为这可能是重要情况
        
    return result

# 应用启动时执行一次模板加载，确保初始状态正确
_load_templates(template_path=settings.proxy.prompt_template_path_with_input, force_reload=True)
_load_templates(template_path=settings.proxy.prompt_template_path_without_input, force_reload=True)

# 导出主要函数供外部使用
def prepare_messages(original_body: Dict[str, Any]) -> Dict[str, Any]:
    """
    公开的消息准备函数，供外部模块调用。
    
    Args:
        original_body (Dict[str, Any]): 原始的 OpenAI 格式请求体
        
    Returns:
        Dict[str, Any]: 处理后的消息数据，包含 model、messages 和 selected_regex_rules
    """
    return _prepare_openai_messages(original_body)

def apply_regex_rules(content: str, regex_rules: List[Dict[str, Any]]) -> str:
    """
    公开的正则规则应用函数，供外部模块调用。
    
    Args:
        content (str): 需要处理的内容
        regex_rules (List[Dict[str, Any]]): 正则规则列表
        
    Returns:
        str: 处理后的内容
    """
    return _apply_regex_rules_to_content(content, regex_rules)

def reload_templates(force: bool = False) -> None:
    """
    手动重载所有模板文件。
    
    Args:
        force (bool): 是否强制重载，忽略文件修改时间检查
    """
    _load_templates(template_path=settings.proxy.prompt_template_path_with_input, force_reload=force)
    _load_templates(template_path=settings.proxy.prompt_template_path_without_input, force_reload=force)
    logger.info("模板重载完成。")

def get_template_status() -> Dict[str, Any]:
    """
    获取当前模板加载状态的信息。
    
    Returns:
        Dict[str, Any]: 包含模板状态信息的字典
    """
    return {
        "with_input_template": {
            "path": settings.proxy.prompt_template_path_with_input,
            "blueprints_count": len(_CACHED_PROMPT_BLUEPRINTS.get(settings.proxy.prompt_template_path_with_input, [])),
            "regex_rules_count": len(_CACHED_REGEX_RULES.get(settings.proxy.prompt_template_path_with_input, [])),
            "last_modified": _LAST_TEMPLATE_MTIME.get(settings.proxy.prompt_template_path_with_input, 0.0)
        },
        "without_input_template": {
            "path": settings.proxy.prompt_template_path_without_input,
            "blueprints_count": len(_CACHED_PROMPT_BLUEPRINTS.get(settings.proxy.prompt_template_path_without_input, [])),
            "regex_rules_count": len(_CACHED_REGEX_RULES.get(settings.proxy.prompt_template_path_without_input, [])),
            "last_modified": _LAST_TEMPLATE_MTIME.get(settings.proxy.prompt_template_path_without_input, 0.0)
        },
        "global_variables_count": len(_GLOBAL_VARIABLES)
    }
