"""
Skill Router - NOFXi 风格的 Skill-First 路由层

核心原则：80% skill + 20% 动态规划
- 高频任务走预定义 skill 路径
- AI 只处理复杂、跨 skill、未知问题
"""

import json
import re
from pathlib import Path
from typing import Optional, Callable
from dataclasses import dataclass, field
from enum import Enum
import logging

logger = logging.getLogger(__name__)

SKILL_REGISTRY_PATH = Path(__file__).parent / "skills" / "skill_registry.json"


class IntentMatchMode(Enum):
    EXACT = "exact"       # 精确关键词匹配
    FUZZY = "fuzzy"       # 模糊匹配
    LLM = "llm"           # LLM 判断


@dataclass
class Skill:
    id: str
    name: str
    name_en: str
    description: str
    examples: list[str]
    intents: list[str]
    action_fields: list[str]
    high_risk_actions: list[str]
    requires_confirmation: list[str]
    handler: Optional[Callable] = None
    
    def match_intent(self, user_message: str) -> tuple[bool, float]:
        """判断用户消息是否匹配此 skill
        
        Returns:
            (is_match, confidence_score)
        """
        msg_lower = user_message.lower()
        
        # 1. 检查 examples 是否包含用户消息的关键词
        for example in self.examples:
            if any(word in msg_lower for word in example.lower().split()):
                return True, 0.7
        
        # 2. 检查 intents 是否匹配
        for intent in self.intents:
            if intent.lower() in msg_lower:
                return True, 0.8
        
        # 3. 检查 description 关键词
        desc_words = self.description.lower().split()
        match_count = sum(1 for word in desc_words if word in msg_lower)
        if match_count >= 2:
            return True, 0.5 + match_count * 0.1
        
        return False, 0.0


@dataclass
class SkillSession:
    """当前活跃的 skill 会话状态"""
    skill_id: str
    action: Optional[str] = None
    params: dict = field(default_factory=dict)
    state: str = "initialized"  # initialized -> collecting -> confirmed -> executing -> completed
    missing_fields: list[str] = field(default_factory=list)
    last_update: float = 0.0


class SkillRouter:
    """
    Skill 路由核心类
    
    处理流程：
    1. 接收用户消息
    2. 判断是否继续当前 skill 会话
    3. 如果没有活跃会话，匹配最佳 skill
    4. 调用 skill handler 处理请求
    5. 管理会话状态
    """
    
    def __init__(self, llm_client: Optional[Callable] = None):
        self.skills: dict[str, Skill] = {}
        self.active_session: Optional[SkillSession] = None
        self.llm_client = llm_client
        self._load_skills()
        self._register_handlers()
    
    def _load_skills(self):
        """从 JSON 文件加载 skill 注册表"""
        if not SKILL_REGISTRY_PATH.exists():
            logger.warning(f"Skill registry not found at {SKILL_REGISTRY_PATH}")
            return
        
        with open(SKILL_REGISTRY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        for skill_data in data.get("skills", []):
            skill = Skill(**skill_data)
            self.skills[skill.id] = skill
        
        logger.info(f"Loaded {len(self.skills)} skills")
    
    def _register_handlers(self):
        """注册各 skill 的处理函数"""
        from agent import skill_handlers
        for skill_id, skill in self.skills.items():
            handler_name = f"handle_{skill_id}"
            if hasattr(skill_handlers, handler_name):
                skill.handler = getattr(skill_handlers, handler_name)
    
    def route(self, user_message: str) -> dict:
        """
        路由用户消息
        
        Returns:
            {
                "skill_id": str,
                "action": str,
                "params": dict,
                "response": str,
                "needs_confirmation": bool,
                "continue_skill": bool
            }
        """
        # 模式1: 继续当前活跃会话
        if self.active_session:
            skill = self.skills.get(self.active_session.skill_id)
            if skill and self._should_continue_session(user_message):
                return self._continue_session(user_message, skill)
        
        # 模式2: 匹配新 skill
        matched_skill, confidence = self._match_skill(user_message)
        
        if matched_skill and confidence >= 0.5:
            return self._start_new_session(user_message, matched_skill)
        
        # 模式3: 降级到动态规划（LLM 兜底）
        return self._fallback_to_llm(user_message)
    
    def _match_skill(self, user_message: str) -> tuple[Optional[Skill], float]:
        """匹配最合适的 skill"""
        best_skill = None
        best_score = 0.0
        
        for skill in self.skills.values():
            is_match, score = skill.match_intent(user_message)
            if is_match and score > best_score:
                best_skill = skill
                best_score = score
        
        return best_skill, best_score
    
    def _should_continue_session(self, user_message: str) -> bool:
        """判断是否应该继续当前 skill 会话"""
        # 如果用户明确切换目标（如"不聊这个话题了"），则中断
        interrupt_words = ["算了", "不要了", "换个话题", "不聊这个", "算了"]
        if any(word in user_message for word in interrupt_words):
            self.active_session = None
            return False
        
        # 如果用户明确提到另一个 skill 关键词，则切换
        for skill_id, skill in self.skills.items():
            if skill_id != self.active_session.skill_id:
                is_match, _ = skill.match_intent(user_message)
                if is_match:
                    self.active_session = None
                    return False
        
        return True
    
    def _start_new_session(self, user_message: str, skill: Skill) -> dict:
        """启动新的 skill 会话"""
        action, params = self._extract_action_and_params(user_message, skill)
        
        self.active_session = SkillSession(
            skill_id=skill.id,
            action=action,
            params=params,
            state="collecting" if params else "initialized"
        )
        
        return {
            "skill_id": skill.id,
            "action": action,
            "params": params,
            "response": f"好的，我帮你{skill.name}。",
            "needs_confirmation": action in skill.requires_confirmation if action else False,
            "continue_skill": True
        }
    
    def _continue_session(self, user_message: str, skill: Skill) -> dict:
        """继续当前 skill 会话"""
        session = self.active_session
        
        # 如果缺少参数，尝试从用户消息中提取
        if session.state == "collecting" and session.missing_fields:
            for field in session.missing_fields:
                extracted = self._extract_field_value(user_message, field)
                if extracted:
                    session.params[field] = extracted
                    session.missing_fields.remove(field)
        
        # 调用 handler
        if skill.handler:
            try:
                result = skill.handler(
                    action=session.action,
                    params=session.params,
                    user_message=user_message
                )
                return result
            except Exception as e:
                logger.error(f"Skill handler error: {e}")
                return {
                    "skill_id": skill.id,
                    "response": f"处理失败: {str(e)}",
                    "continue_skill": True
                }
        
        return {
            "skill_id": skill.id,
            "response": "Skill handler not implemented yet.",
            "continue_skill": True
        }
    
    def _extract_action_and_params(self, user_message: str, skill: Skill) -> tuple[Optional[str], dict]:
        """从用户消息中提取 action 和参数"""
        params = {}
        action = None
        
        # 提取 action（基于 intents 匹配）
        for intent in skill.intents:
            if intent.lower() in user_message.lower():
                action = intent
                break
        
        # 提取参数（基于关键词模式）
        param_patterns = {
            "trader_name": [r"(\w+)\s*交易员", r"交易员\s*(\w+)"],
            "exchange": [r"(币安|binance|okx|bybit|hyperliquid)", r"(\w+)\s*交易所"],
            "symbol": [r"([A-Z]{2,10})/USDT", r"([A-Z]{2,10})/U")]
        }
        
        for field, patterns in param_patterns.items():
            if field in skill.action_fields:
                for pattern in patterns:
                    match = re.search(pattern, user_message, re.IGNORECASE)
                    if match:
                        params[field] = match.group(1).upper()
        
        return action, params
    
    def _extract_field_value(self, user_message: str, field: str) -> Optional[str]:
        """从用户消息中提取特定字段值"""
        patterns = {
            "trader_name": [r"(\w+)\s*交易员"],
            "exchange": [r"(币安|binance|okx|bybit)"],
            "symbol": [r"([A-Z]{2,10})/USDT"]
        }
        
        field_patterns = patterns.get(field, [])
        for pattern in field_patterns:
            match = re.search(pattern, user_message, re.IGNORECASE)
            if match:
                return match.group(1).upper()
        
        return None
    
    def _fallback_to_llm(self, user_message: str) -> dict:
        """降级到 LLM 动态规划"""
        if self.llm_client:
            response = self.llm_client(user_message)
            return {
                "skill_id": None,
                "action": "llm_fallback",
                "params": {},
                "response": response,
                "needs_confirmation": False,
                "continue_skill": False
            }
        
        return {
            "skill_id": None,
            "action": "llm_fallback",
            "params": {},
            "response": "抱歉，无法理解你的请求。请尝试：创建交易员、查看持仓、配置交易所等。",
            "needs_confirmation": False,
            "continue_skill": False
        }
    
    def clear_session(self):
        """清除当前会话"""
        self.active_session = None