"""
AI 策略代码生成器

用法：
  python strategy_generator.py "做一个 EMA 金叉策略，快线 5，慢线 20"
  python strategy_generator.py --list          # 列出所有生成策略
  python strategy_generator.py --delete MyEma  # 删除指定策略

生成策略接口规范（必须遵守）：
  class XxxStrategy(BaseStrategy):
      def compute(self, candles: List[Dict]) -> Tuple[int, float, float]:
          return signal_int, value1_float, value2_float

  signal:  1=BUY, -1=SELL, 0=HOLD
  candles: [{"open","high","low","close","volume","timestamp"}, ...]
"""

import os
import sys
import ast
import re
import logging
import argparse
from datetime import datetime
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# 确保可以导入项目模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

GENERATED_DIR = os.path.join(os.path.dirname(__file__), "generated_strategies")

SYSTEM_PROMPT = """你是一个量化交易策略专家。根据用户描述，生成一个符合以下接口规范的 Python 策略类。

接口规范（必须严格遵守）：
```python
from typing import List, Dict, Tuple

class {StrategyName}(BaseStrategy):
    def compute(self, candles: List[Dict]) -> Tuple[int, float, float]:
        closes = [c["close"] for c in candles]
        highs = [c["high"] for c in candles]
        lows = [c["low"] for c in candles]
        volumes = [c.get("volume", 0) for c in candles]
        
        # 你的策略逻辑
        # signal: 1=BUY, -1=SELL, 0=HOLD
        # value1, value2: 辅助指标值（用于日志/调试）
        
        return signal, value1, value2
```

规则：
1. 类名必须以 Strategy 结尾（如 EmaCrossStrategy）
2. 只返回纯 Python 代码，不要 markdown 代码块
3. 不做任何 import（BaseStrategy 已由框架提供）
4. 不做任何 API 调用
5. 使用 candles[-1] 获取最新数据
6. 添加类型注解

示例（EMA 金叉策略）：
```python
class EmaCrossStrategy(BaseStrategy):
    def compute(self, candles: List[Dict]) -> Tuple[int, float, float]:
        closes = [c["close"] for c in candles]
        if len(closes) < 21:
            return 0, 0.0, 0.0
        
        def ema(data, n):
            k = 2 / (n + 1)
            result = sum(data[:n]) / n
            for x in data[n:]:
                result = x * k + result * (1 - k)
            return result
        
        fast = ema(closes, 5)
        slow = ema(closes, 20)
        prev_fast = ema(closes[:-1], 5)
        prev_slow = ema(closes[:-1], 20)
        
        if prev_fast <= prev_slow and fast > slow:
            return 1, fast, slow
        elif prev_fast >= prev_slow and fast < slow:
            return -1, fast, slow
        return 0, fast, slow
```"""


def generate(prompt: str, model: str = None) -> Tuple[Optional[str], Optional[str]]:
    """
    根据自然语言描述生成策略代码

    Returns:
        (class_name, code) 或 (None, error_msg)
    """
    from llm_utils import get_llm

    llm = get_llm()

    user_prompt = f"请生成一个量化交易策略：{prompt}"
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    raw = llm.chat(messages, temperature=0.2, max_tokens=1500)
    if not raw:
        return None, "LLM 调用失败，请检查 API Key 配置"

    # 提取纯代码（去除可能的 markdown 包裹）
    code = raw.strip()
    if code.startswith("```"):
        lines = code.split("\n")
        lines = lines[1:] if len(lines) > 1 else lines
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        code = "\n".join(lines).strip()

    # 验证是有效 Python
    try:
        ast.parse(code)
    except SyntaxError as e:
        return None, f"生成的代码有语法错误: {e}"

    # 提取类名
    class_match = re.search(r"class\s+(\w+Strategy)\s*\(.*?BaseStrategy.*?\)", code)
    if not class_match:
        return None, "生成的代码中没有找到继承 BaseStrategy 的类"

    class_name = class_match.group(1)
    return class_name, code


def validate(code: str) -> Tuple[bool, str]:
    """验证生成的策略代码"""
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return False, f"语法错误: {e}"

    has_base = False
    has_compute = False
    class_name = None

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for base in node.bases:
                if hasattr(base, "id") and base.id == "BaseStrategy":
                    has_base = True
                    class_name = node.name
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == "compute":
                    has_compute = True

    if not has_base:
        return False, "没有找到继承 BaseStrategy 的类"
    if not has_compute:
        return False, "没有实现 compute 方法"

    return True, f"验证通过: {class_name}"


def save(class_name: str, code: str) -> str:
    """保存策略代码到 generated_strategies/ 目录"""
    os.makedirs(GENERATED_DIR, exist_ok=True)

    # 生成文件名
    safe_name = re.sub(r"(?<!^)(?=[A-Z])", "_", class_name).lower()
    if safe_name.endswith("_strategy"):
        safe_name = safe_name[:-9]

    # 完整代码（加上必要的导入）
    full_code = f'''"""
AI 生成策略: {class_name}
生成时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
"""

from typing import List, Dict, Tuple
from components.signal_engine import BaseStrategy, StrategyConfig

{code}
'''

    filepath = os.path.join(GENERATED_DIR, f"{safe_name}.py")
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(full_code)

    # 刷新注册中心
    try:
        from generated_strategies import discover
        discover()
    except Exception:
        pass

    return filepath


def delete(name: str) -> bool:
    """删除生成策略"""
    safe_name = name.lower().replace("gen_", "").replace("strategy", "")
    filepath = os.path.join(GENERATED_DIR, f"{safe_name}.py")
    if os.path.exists(filepath):
        os.remove(filepath)
        try:
            from generated_strategies import discover
            discover()
        except Exception:
            pass
        return True
    return False


def list_all() -> dict:
    """列出所有生成策略"""
    from generated_strategies import list_generated as _list
    return _list()


# ── CLI ─────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    parser = argparse.ArgumentParser(description="AI 策略代码生成器")
    parser.add_argument("prompt", nargs="?", help="自然语言描述的策略")
    parser.add_argument("--list", action="store_true", help="列出所有生成策略")
    parser.add_argument("--delete", metavar="NAME", help="删除指定策略")
    parser.add_argument("--dry-run", action="store_true", help="仅生成不保存")
    args = parser.parse_args()

    if args.list:
        strategies = list_all()
        if strategies:
            print(f"\n已生成 {len(strategies)} 个策略:")
            for name, path in strategies.items():
                print(f"  {name}  →  {path}")
        else:
            print("暂无生成策略")
        sys.exit(0)

    if args.delete:
        ok = delete(args.delete)
        print("已删除" if ok else f"未找到策略: {args.delete}")
        sys.exit(0)

    if not args.prompt:
        parser.print_help()
        sys.exit(1)

    print(f"\n正在生成策略: {args.prompt}\n")

    class_name, code = generate(args.prompt)
    if not class_name:
        print(f"生成失败: {code}")
        sys.exit(1)

    ok, msg = validate(code)
    if not ok:
        print(f"验证失败: {msg}")
        print(f"生成的代码:\n{code}")
        sys.exit(1)

    print(f"验证: {msg}")
    print(f"--- 生成的代码 ---")
    print(code)
    print("---")

    if args.dry_run:
        print("\n[dry-run] 未保存")
    else:
        path = save(class_name, code)
        print(f"\n已保存到: {path}")
        print(f"使用方式: AGENT_SYMBOLS=ETH/USDT:GEN_{class_name}:weex")
