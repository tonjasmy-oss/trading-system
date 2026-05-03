"""
通达信公式编译器 — TdxCompiler
=============================

将通达信公式（如 "RSI.VWR"、"KDJ"、"MACD" 等）编译为可执行的 Python 函数，
在历史 OHLCV 数据上逐 K 线计算指标值，生成 BUY/SELL 信号。

支持：
  - 赋值语句 := 和 :
  - 中间变量（RSV, K, D, J...）
  - 条件表达式 IF(condition, true_val, false_val)
  - 穿越判断 CROSS(A, B)
  - 所有常用通达信函数：LLV/HHV/SMA/EMA/MA/REF/SUM/ABS/MAX/MIN/STD/IF/CROSS...
  - 运算：+ - * / > < >= <= = != AND OR NOT

信号生成规则：
  - OUTPUT 变量（: 定义）如名称含 BUY/买 且值从 0→非0 → BUY 信号
  - OUTPUT 变量如名称含 SELL/卖/平 且值从 0→非0 → SELL 信号
  - 支持 RETURN 语法：RETURN X; 直接输出最终信号序列

使用示例：
  compiler = TdxCompiler()
  indicator_fn, signal_fn = compiler.compile(
      "RSV:=(CLOSE-LLV(LOW,9))/(HHV(HIGH,9)-LLV(LOW,9))*100;"
      "K:SMA(RSV,3,1);"
      "D:SMA(K,3,1);"
      "J:3*K-2*D;"
      "DRAWICON(CROSS(K,D) AND K<20, LOW, 1);"
      "DRAWICON(CROSS(D,K) AND K>80, HIGH, 2);"
  )

  indicators = indicator_fn(candles)   # dict[str, list[float]]
  signals    = signal_fn(candles)       # list[int]  1=BUY, -1=SELL, 0=HOLD

  # 或者用策略类：
  strategy = FormulaStrategy(formula=formula_str, symbol="ETH/USDT")
  signals = strategy.populate_entry_trend(candles)
"""

import re
import math
import logging
from typing import Callable, Dict, List, Optional, Set, Tuple, Any

logger = logging.getLogger(__name__)

# ============================================================
# Token 定义
# ============================================================

TOKEN_TYPES = [
    ("NUMBER",    r"\d+\.?\d*"),
    ("STRING",    r"\'[^\']*\'"),
    ("IDENT",     r"[A-Za-z_\u4e00-\u9fff][A-Za-z0-9_\u4e00-\u9fff]*"),
    ("OP",        r":=|:|-|\+|\*|/|>=|<=|!=|>|<|=|AND|OR|NOT"),
    ("LPAREN",    r"\("),
    ("RPAREN",    r"\)"),
    ("COMMA",     r","),
    ("SEMI",      r";"),
    ("DRAWICON",  r"DRAWICON"),
    ("RETURN",    r"RETURN"),
    ("FILTER",    r"FILTER"),
    ("BARSLAST",  r"BARSLAST"),
    ("EOL",       r"$"),
]

_TOKEN_RE = re.compile("|".join(f"(?P<{n}>{p})" for n, p in TOKEN_TYPES))


class Token:
    __slots__ = ("type", "value", "pos")
    def __init__(self, type_: str, value: str, pos: int = 0):
        self.type = type_
        self.value = value
        self.pos = pos
    def __repr__(self):
        return f"Token({self.type!r}, {self.value!r})"


# ============================================================
# 词法分析
# ============================================================

class Lexer:
    def __init__(self, text: str):
        self.text = text
        self.pos = 0

    def tokenize(self) -> List[Token]:
        tokens = []
        while self.pos < len(self.text):
            # 跳过空白
            while self.pos < len(self.text) and self.text[self.pos] in " \t\n\r":
                self.pos += 1
            if self.pos >= len(self.text):
                break
            for typename, pattern in TOKEN_TYPES:
                m = re.match(pattern, self.text[self.pos:])
                if m:
                    val = m.group()
                    # 忽略空白 token
                    if typename != "EOL" and val.strip():
                        tokens.append(Token(typename, val, self.pos))
                    self.pos += len(val)
                    break
            else:
                raise SyntaxError(f"无法识别的字符 at pos {self.pos}: {self.text[self.pos:]!r}")
        return tokens


# ============================================================
# 节点定义（AST）
# ============================================================

class ASTNode:
    pass

class NumNode(ASTNode):
    __slots__ = ("value",)
    def __init__(self, value: float): self.value = value

class VarNode(ASTNode):
    __slots__ = ("name",)
    def __init__(self, name: str): self.name = name

class RefNode(ASTNode):
    """REF(X, N) — 取 N 周期前的值"""
    __slots__ = ("name", "n")
    def __init__(self, name: str, n: int): self.name = name; self.n = n

class BinOpNode(ASTNode):
    __slots__ = ("op", "left", "right")
    def __init__(self, op: str, left: ASTNode, right: ASTNode):
        self.op = op; self.left = left; self.right = right

class UnaryOpNode(ASTNode):
    __slots__ = ("op", "operand")
    def __init__(self, op: str, operand: ASTNode):
        self.op = op; self.operand = operand

class CallNode(ASTNode):
    __slots__ = ("func", "args")
    def __init__(self, func: str, args: List[ASTNode]):
        self.func = func.upper(); self.args = args

class ConditionalNode(ASTNode):
    """IF(condition, true_val, false_val)"""
    __slots__ = ("cond", "true_val", "false_val")
    def __init__(self, cond: ASTNode, true_val: ASTNode, false_val: ASTNode):
        self.cond = cond; self.true_val = true_val; self.false_val = false_val

class AssignmentNode(ASTNode):
    """VAR := expr 或 VAR : expr（输出变量）"""
    __slots__ = ("name", "expr", "is_output")
    def __init__(self, name: str, expr: ASTNode, is_output: bool):
        self.name = name; self.expr = expr; self.is_output = is_output

class DrawIconNode(ASTNode):
    """DRAWICON(COND, PRICE, N)"""
    __slots__ = ("cond", "price", "icon")
    def __init__(self, cond: ASTNode, price: ASTNode, icon: int):
        self.cond = cond; self.price = price; self.icon = icon

class ReturnNode(ASTNode):
    """RETURN expr"""
    __slots__ = ("expr",)
    def __init__(self, expr: ASTNode): self.expr = expr


# ============================================================
# 语法分析（递归下降）
# ============================================================

class Parser:
    def __init__(self, tokens: List[Token]):
        self.tokens = tokens
        self.pos = 0

    def peek(self) -> Token:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else Token("EOF", "")

    def consume(self, expected_type: str = None) -> Token:
        tok = self.peek()
        if expected_type and tok.type != expected_type:
            raise SyntaxError(f"期望 {expected_type}，实际 {tok}")
        self.pos += 1
        return tok

    def parse(self) -> List[ASTNode]:
        nodes = []
        while self.pos < len(self.tokens):
            nodes.append(self._parse_statement())
        return nodes

    def _parse_statement(self) -> ASTNode:
        tok = self.peek()

        # 预判：VAR : EXPR（赋值+输出）
        if tok.type == "IDENT":
            name = tok.value.upper()
            self.pos += 1
            next_tok = self.peek()
            if next_tok.type == "OP" and next_tok.value == ":":
                # 是赋值语句（output 或 assign）
                self.pos += 1  # 消费单个 :
                expr = self._parse_expr()
                self._maybe_semi()
                return AssignmentNode(name, expr, is_output=True)
            elif next_tok.type == "OP" and next_tok.value == ":=":
                self.pos += 1
                expr = self._parse_expr()
                self._maybe_semi()
                return AssignmentNode(name, expr, is_output=False)
            else:
                # 回退，重新作为表达式解析
                self.pos -= 1

        if tok.type == "RETURN":
            self.pos += 1
            expr = self._parse_expr()
            self._maybe_semi()
            return ReturnNode(expr)
        elif tok.type == "DRAWICON":
            return self._parse_drawicon()
        else:
            expr = self._parse_expr()
            self._maybe_semi()
            return expr

    def _parse_drawicon(self) -> DrawIconNode:
        self.consume("DRAWICON")
        self.consume("LPAREN")
        cond = self._parse_expr()
        self.consume("COMMA")
        price = self._parse_expr()
        self.consume("COMMA")
        icon_tok = self.consume("NUMBER")
        self.consume("RPAREN")
        self._maybe_semi()
        return DrawIconNode(cond, price, int(float(icon_tok.value)))

    def _maybe_semi(self):
        if self.pos < len(self.tokens) and self.peek().type == "SEMI":
            self.pos += 1

    def _parse_expr(self) -> ASTNode:
        return self._parse_or()

    def _parse_or(self) -> ASTNode:
        left = self._parse_and()
        while self.peek().type == "OP" and self.peek().value.upper() == "OR":
            self.pos += 1
            right = self._parse_and()
            left = BinOpNode("OR", left, right)
        return left

    def _parse_and(self) -> ASTNode:
        left = self._parse_cmp()
        while self.peek().type == "OP" and self.peek().value.upper() == "AND":
            self.pos += 1
            right = self._parse_cmp()
            left = BinOpNode("AND", left, right)
        return left

    def _parse_cmp(self) -> ASTNode:
        left = self._parse_addsub()
        while self.peek().type == "OP" and self.peek().value in (">", "<", ">=", "<=", "=", "!=", "=="):
            op = self.consume().value
            if op == "==":
                op = "="
            right = self._parse_addsub()
            left = BinOpNode(op, left, right)
        return left

    def _parse_addsub(self) -> ASTNode:
        left = self._parse_muldiv()
        while self.peek().type == "OP" and self.peek().value in ("+", "-"):
            op = self.consume().value
            right = self._parse_muldiv()
            left = BinOpNode(op, left, right)
        return left

    def _parse_muldiv(self) -> ASTNode:
        left = self._parse_unary()
        while self.peek().type == "OP" and self.peek().value in ("*", "/"):
            op = self.consume().value
            right = self._parse_unary()
            left = BinOpNode(op, left, right)
        return left

    def _parse_unary(self) -> ASTNode:
        tok = self.peek()
        if tok.type == "OP" and tok.value == "-":
            self.pos += 1
            return UnaryOpNode("-", self._parse_unary())
        elif tok.type == "OP" and tok.value.upper() == "NOT":
            self.pos += 1
            return UnaryOpNode("NOT", self._parse_unary())
        return self._parse_primary()

    def _parse_primary(self) -> ASTNode:
        tok = self.peek()
        if tok.type == "NUMBER":
            self.pos += 1
            return NumNode(float(tok.value))
        if tok.type == "IDENT":
            name = tok.value.upper()
            self.pos += 1
            # 函数调用？
            if self.peek().type == "LPAREN":
                return self._parse_call(name)
            # REF(X, N) 特殊形式？
            if self.peek().type == "OP" and self.peek().value == ",":
                # 这是 REF(X, N)，下面会走函数
                return self._parse_call(name)
            return VarNode(name)
        if tok.type == "LPAREN":
            self.pos += 1
            expr = self._parse_expr()
            self.consume("RPAREN")
            return expr
        if tok.type == "IDENT" and tok.value.upper() == "IF":
            return self._parse_if()
        if tok.type == "IDENT" and tok.value.upper() == "REF":
            return self._parse_call("REF")
        if tok.type == "IDENT" and tok.value.upper() == "CROSS":
            return self._parse_call("CROSS")
        if tok.type == "IDENT" and tok.value.upper() == "DRAWICON":
            return self._parse_drawicon()
        raise SyntaxError(f"无法解析 token: {tok}")

    def _parse_call(self, func: str) -> ASTNode:
        self.consume("LPAREN")
        args = []
        while self.peek().type != "RPAREN":
            args.append(self._parse_expr())
            if self.peek().type == "COMMA":
                self.pos += 1
            else:
                break
        self.consume("RPAREN")
        return CallNode(func, args)

    def _parse_if(self) -> ConditionalNode:
        self.consume("IDENT")  # IF
        self.consume("LPAREN")
        cond = self._parse_expr()
        self.consume("COMMA")
        true_val = self._parse_expr()
        self.consume("COMMA")
        false_val = self._parse_expr()
        self.consume("RPAREN")
        return ConditionalNode(cond, true_val, false_val)


# ============================================================
# 公式编译器 — 将 AST 编译为可执行函数
# ============================================================

# 通达信函数注册表
TDX_FUNCTIONS: Dict[str, int] = {
    # 周期函数（返回序列）
    "LLV": 2, "HHV": 2, "SUM": 2, "MA": 2, "EMA": 2, "SMA": 3,
    "STD": 2, "STDDEV": 2, "AVEDEV": 2,
    "REF": 2, "ABS": 1, "MAX": 2, "MIN": 2,
    "POW": 2, "SQRT": 1, "LOG": 1, "EXP": 1,
    "SIN": 1, "COS": 1, "TAN": 1, "ATAN": 1,
    "INTPART": 1, "MOD": 2, "REVERSE": 1,
    "IF": 3, "IFF": 3, "IFN": 3,
    "CROSS": 2, "FILTER": 2, "BARSLAST": 1, "BARSCOUNT": 1,
    "BACKSET": 2, "BETWEEN": 3,
    "CAPITAL": 0, "TOTALCAPITAL": 0, "VOL": 0, "VOLINSTICK": 0,
    "DYNAINFO": 1,
    "CURRBARSCOUNT": 0,
    "TOTALBARSCOUNT": 0,
    # 附图指标特殊输出（不常用，简单处理）
    "MACD": 0, "KDJ": 0, "RSI": 0, "WR": 0, "BIAS": 0,
    "CCI": 0, "PSY": 0, "ROC": 0, "MTM": 0, "RSR": 0,
}


class TdxCompiler:
    """
    通达信公式编译器

    使用方式：
        compiler = TdxCompiler()
        indicator_fn, signal_fn = compiler.compile(formula_string)
        indicators = indicator_fn(candles)   # dict of list[float]
        signals    = signal_fn(candles)        # list[int]  1/0/-1
    """

    def __init__(self):
        self._ns: Dict[str, Any] = {}   # 全局命名空间（编译时填充）
        self._all_vars: Set[str] = set()
        self._output_vars: Set[str] = set()

    def compile(self, formula: str) -> Tuple[Callable, Callable]:
        """
        编译公式，返回 (indicator_fn, signal_fn)

        indicator_fn(candles: List[Dict]) -> Dict[str, List[float]]
            计算所有 OUTPUT 变量的指标序列

        signal_fn(candles) -> List[int]
            生成交易信号序列，1=BUY, -1=SELL, 0=HOLD
        """
        # 词法分析
        lexer = Lexer(formula)
        tokens = lexer.tokenize()

        # 语法分析
        parser = Parser(tokens)
        ast_nodes = parser.parse()

        # 分析：收集所有变量、输出变量、RETURN 语句
        self._all_vars = set()
        self._output_vars = set()
        self._return_var: Optional[str] = None
        self._drawicon_conditions: List[ASTNode] = []
        self._drawicon_prices: List[ASTNode] = []

        for node in ast_nodes:
            self._collect_vars(node)

        # 注册通达信函数到命名空间
        self._ns = {}
        self._ns.update(self._TDX_FUNCTIONS)
        self._ns["CLOSE"] = _ohlc_loader("close")
        self._ns["OPEN"]  = _ohlc_loader("open")
        self._ns["HIGH"]  = _ohlc_loader("high")
        self._ns["LOW"]   = _ohlc_loader("low")
        self._ns["VOL"]   = _ohlc_loader("volume")
        self._ns["AMOUNT"]= _ohlc_loader("quote_volume")
        self._ns["OPENINT"] = _ohlc_loader("open_interest")

        # 编译所有语句
        compiled_statements = []
        for node in ast_nodes:
            compiled_statements.append(self._compile_node(node))

        # 构建指标函数
        def indicator_fn(candles: List[Dict]) -> Dict[str, List[float]]:
            n = len(candles)
            vars_: Dict[str, List[float]] = {}

            # 预填充所有变量为空列表
            for v in self._all_vars:
                vars_[v] = [0.0] * n

            # 预填充内置 OHLCV
            for key in ("CLOSE", "OPEN", "HIGH", "LOW", "VOL", "AMOUNT"):
                if key in self._ns and callable(self._ns[key]):
                    vals = self._ns[key](candles)
                    if len(vals) == n:
                        vars_[key] = vals
                    else:
                        vars_[key] = [0.0] * n

            # 逐 K 线执行
            for i in range(n):
                ctx = _EvalCtx(i, n, candles, vars_, self._ns)
                for stmt in compiled_statements:
                    stmt(ctx)

            # 返回所有输出变量
            if self._return_var:
                return {self._return_var: vars_.get(self._return_var, [0.0]*n)}
            return {v: vars_[v] for v in self._output_vars if v in vars_}

        # 构建信号函数
        def signal_fn(candles: List[Dict]) -> List[int]:
            indicators = indicator_fn(candles)
            n = len(candles)
            signals = [0] * n

            # 用输出变量生成信号
            for var_name, values in indicators.items():
                if _is_buy_signal_var(var_name):
                    for i in range(1, n):
                        if values[i] != 0 and values[i-1] == 0:
                            signals[i] = 1
                elif _is_sell_signal_var(var_name):
                    for i in range(1, n):
                        if values[i] != 0 and values[i-1] == 0:
                            signals[i] = -1

            # RETURN 变量直接作为信号（1=买/-1=卖/0=持有）
            if self._return_var and self._return_var in indicators:
                vals = indicators[self._return_var]
                for i in range(n):
                    v = vals[i]
                    if v > 0:
                        signals[i] = 1
                    elif v < 0:
                        signals[i] = -1
                    else:
                        signals[i] = 0

            return signals

        return indicator_fn, signal_fn

    def _collect_vars(self, node: ASTNode):
        """遍历 AST 收集变量名"""
        if isinstance(node, AssignmentNode):
            self._all_vars.add(node.name)
            if node.is_output:
                self._output_vars.add(node.name)
            self._collect_vars(node.expr)
        elif isinstance(node, ReturnNode):
            if isinstance(node.expr, VarNode):
                self._return_var = node.expr.name.upper()
                self._all_vars.add(self._return_var)
            self._collect_vars(node.expr)
        elif isinstance(node, VarNode):
            self._all_vars.add(node.name.upper())
        elif isinstance(node, NumNode):
            pass
        elif isinstance(node, BinOpNode):
            self._collect_vars(node.left)
            self._collect_vars(node.right)
        elif isinstance(node, UnaryOpNode):
            self._collect_vars(node.operand)
        elif isinstance(node, CallNode):
            for a in node.args:
                self._collect_vars(a)
        elif isinstance(node, ConditionalNode):
            self._collect_vars(node.cond)
            self._collect_vars(node.true_val)
            self._collect_vars(node.false_val)
        elif isinstance(node, DrawIconNode):
            self._collect_vars(node.cond)
            self._collect_vars(node.price)
            self._drawicon_conditions.append(node.cond)
            self._drawicon_prices.append(node.price)
        elif isinstance(node, RefNode):
            self._all_vars.add(node.name.upper())

    def _compile_node(self, node: ASTNode):
        """将 AST 节点编译为可执行的闭包（每个语句一个）"""
        if isinstance(node, AssignmentNode):
            compiled_expr = self._compile_expr(node.expr)
            def eval_assign(ctx: _EvalCtx):
                vals = compiled_expr(ctx)
                ctx.vars[node.name.upper()] = vals
                ctx.vars[node.name.upper()][ctx.bar] = vals[ctx.bar]
            return eval_assign

        elif isinstance(node, ReturnNode):
            compiled_expr = self._compile_expr(node.expr)
            def eval_return(ctx: _EvalCtx):
                vals = compiled_expr(ctx)
                ctx.vars[self._return_var or "RETURN"] = vals
                ctx.vars[self._return_var or "RETURN"][ctx.bar] = vals[ctx.bar]
            return eval_return

        elif isinstance(node, DrawIconNode):
            compiled_cond = self._compile_expr(node.cond)
            compiled_price = self._compile_expr(node.price)
            icon = node.icon
            def eval_drawicon(ctx: _EvalCtx):
                cond_vals = compiled_cond(ctx)
                price_vals = compiled_price(ctx)
                ctx.vars["_DI_COND"] = cond_vals
                ctx.vars["_DI_PRICE"] = price_vals
            return eval_drawicon

        else:
            compiled_expr = self._compile_expr(node)
            def eval_expr(ctx: _EvalCtx):
                compiled_expr(ctx)
            return eval_expr

    def _compile_expr(self, node: ASTNode):
        """将表达式节点编译为 (ctx -> list[float]) 闭包"""
        if isinstance(node, NumNode):
            val = node.value
            def const(_ctx): return [val] * _ctx.n
            return const

        if isinstance(node, VarNode):
            name = node.name.upper()
            def get_var(ctx: _EvalCtx):
                if name in ctx.vars:
                    return ctx.vars[name]
                if name in ctx.ohlcv:
                    return ctx.ohlcv[name]
                return [0.0] * ctx.n
            return get_var

        if isinstance(node, BinOpNode):
            left_fn = self._compile_expr(node.left)
            right_fn = self._compile_expr(node.right)
            op = node.op
            if op in ("+", "-", "*", "/", ">", "<", ">=", "<=", "=", "!=", "AND", "OR"):
                def bin_op(ctx: _EvalCtx):
                    L = left_fn(ctx)
                    R = right_fn(ctx)
                    return [_bin_scalar(op, L[i], R[i]) for i in range(ctx.n)]
            elif op == "=":
                def bin_op(ctx): L=left_fn(ctx); R=right_fn(ctx); return [1.0 if L[i]==R[i] else 0.0 for i in range(ctx.n)]
            return bin_op

        if isinstance(node, UnaryOpNode):
            inner = self._compile_expr(node.operand)
            if node.op == "-":
                def neg(ctx): return [-v for v in inner(ctx)]
            elif node.op == "NOT":
                def not_fn(ctx): return [0.0 if v else 1.0 for v in inner(ctx)]
            return neg if node.op == "-" else not_fn

        if isinstance(node, CallNode):
            return self._compile_call(node.func, node.args)

        if isinstance(node, ConditionalNode):
            cond_fn = self._compile_expr(node.cond)
            true_fn = self._compile_expr(node.true_val)
            false_fn = self._compile_expr(node.false_val)
            def if_expr(ctx):
                C = cond_fn(ctx)
                T = true_fn(ctx)
                F = false_fn(ctx)
                return [T[i] if C[i] != 0 else F[i] for i in range(ctx.n)]
            return if_expr

        if isinstance(node, RefNode):
            n = node.n
            name = node.name.upper()
            def ref_fn(ctx: _EvalCtx):
                src = ctx.vars.get(name, [0.0]*ctx.n)
                return [src[i-n] if i >= n else 0.0 for i in range(ctx.n)]
            return ref_fn

        raise SyntaxError(f"无法编译节点类型: {type(node)}")

    def _compile_call(self, func: str, args: List[ASTNode]):
        func = func.upper()
        n_args = len(args)
        arg_fns = [self._compile_expr(a) for a in args]

        # ---- 周期函数（滚动窗口） ----
        if func in ("LLV", "HHV", "SUM", "MA", "AVEDEV"):
            period_fn = arg_fns[1]  # 返回标量或序列
            def make_window_fn(op_name):
                def window_fn(ctx: _EvalCtx):
                    X = arg_fns[0](ctx)
                    # 获取周期
                    if n_args >= 2:
                        P = arg_fns[1](ctx)
                        period = int(P[0]) if all(p == P[0] for p in P) else max(1, int(P[0]))
                    else:
                        period = 1
                    n = ctx.n
                    result = [0.0] * n
                    for i in range(n):
                        start = max(0, i - period + 1)
                        chunk = X[start:i+1]
                        if op_name == "LLV":
                            result[i] = min(chunk) if chunk else 0.0
                        elif op_name == "HHV":
                            result[i] = max(chunk) if chunk else 0.0
                        elif op_name == "SUM":
                            result[i] = sum(chunk)
                        elif op_name == "MA":
                            result[i] = sum(chunk) / len(chunk) if chunk else 0.0
                        elif op_name == "AVEDEV":
                            m = sum(chunk) / len(chunk) if chunk else 0.0
                            result[i] = sum(abs(v - m) for v in chunk) / len(chunk) if chunk else 0.0
                    return result
                return window_fn

            if func == "LLV": return make_window_fn("LLV")
            if func == "HHV": return make_window_fn("HHV")
            if func == "SUM": return make_window_fn("SUM")
            if func == "MA":  return make_window_fn("MA")
            if func == "AVEDEV": return make_window_fn("AVEDEV")

        if func == "EMA":
            # EMA(X, N): 指数移动平均
            period_fn = arg_fns[1]
            def ema_fn(ctx: _EvalCtx):
                X = arg_fns[0](ctx)
                P = period_fn(ctx)
                period = int(P[0]) if all(p == P[0] for p in P) else max(1, int(P[0]))
                n = ctx.n
                result = [0.0] * n
                mult = 2.0 / (period + 1)
                # 前 period-1 个为 0
                if n >= period:
                    result[period-1] = sum(X[:period]) / period
                    for i in range(period, n):
                        result[i] = (X[i] - result[i-1]) * mult + result[i-1]
                return result
            return ema_fn

        if func == "SMA":
            # SMA(X, N, M): X的N日移动平均，M为权重因子
            # 通达信 SMA = (X * M + Y' * (N-M)) / N
            # 其中 Y' 是前一日的 SMA 值
            def sma_fn(ctx: _EvalCtx):
                X = arg_fns[0](ctx)
                N = int(arg_fns[1](ctx)[0])
                M = float(arg_fns[2](ctx)[0])
                n = ctx.n
                result = [0.0] * n
                mult = M / N
                for i in range(min(N-1, n)):
                    result[i] = sum(X[:i+1]) / (i+1) if i > 0 else X[0]
                for i in range(N-1, n):
                    result[i] = X[i] * mult + result[i-1] * (1 - mult)
                return result
            return sma_fn

        if func == "STD" or func == "STDDEV":
            def std_fn(ctx: _EvalCtx):
                X = arg_fns[0](ctx)
                P = int(arg_fns[1](ctx)[0])
                n = ctx.n
                result = [0.0] * n
                for i in range(n):
                    start = max(0, i - P + 1)
                    chunk = X[start:i+1]
                    if len(chunk) >= 2:
                        mean = sum(chunk) / len(chunk)
                        variance = sum((v - mean) ** 2 for v in chunk) / len(chunk)
                        result[i] = variance ** 0.5
                return result
            return std_fn

        if func == "REF":
            n_fn = arg_fns[1]
            def ref_fn(ctx: _EvalCtx):
                X = arg_fns[0](ctx)
                N = int(n_fn(ctx)[0])
                return [X[i-N] if i >= N else 0.0 for i in range(ctx.n)]
            return ref_fn

        if func == "ABS":
            def abs_fn(ctx): return [abs(v) for v in arg_fns[0](ctx)]
            return abs_fn

        if func == "MAX":
            def max_fn(ctx: _EvalCtx):
                A = arg_fns[0](ctx); B = arg_fns[1](ctx)
                return [max(A[i], B[i]) for i in range(ctx.n)]
            return max_fn

        if func == "MIN":
            def min_fn(ctx: _EvalCtx):
                A = arg_fns[0](ctx); B = arg_fns[1](ctx)
                return [min(A[i], B[i]) for i in range(ctx.n)]
            return min_fn

        if func == "POW":
            def pow_fn(ctx: _EvalCtx):
                A = arg_fns[0](ctx); B = arg_fns[1](ctx)
                return [A[i] ** B[i] for i in range(ctx.n)]
            return pow_fn

        if func == "SQRT":
            def sqrt_fn(ctx): return [math.sqrt(max(0, v)) for v in arg_fns[0](ctx)]
            return sqrt_fn

        if func == "LOG":
            def log_fn(ctx): return [math.log(max(1e-10, v)) for v in arg_fns[0](ctx)]
            return log_fn

        if func == "EXP":
            def exp_fn(ctx): return [math.exp(v) for v in arg_fns[0](ctx)]
            return exp_fn

        if func in ("SIN", "COS", "TAN", "ATAN", "INTPART", "MOD"):
            import math as _math
            def math_fn(ctx, op=func):
                V = arg_fns[0](ctx)
                if op == "SIN":   return [math.sin(v) for v in V]
                if op == "COS":   return [math.cos(v) for v in V]
                if op == "TAN":   return [math.tan(v) for v in V]
                if op == "ATAN": return [math.atan(v) for v in V]
                if op == "INTPART": return [float(int(v)) for v in V]
                if op == "MOD":  return [v % arg_fns[1](ctx)[i] for i, v in enumerate(V)]
            return math_fn

        if func == "IF" or func == "IFF":
            cond_fn = arg_fns[0]; true_fn = arg_fns[1]; false_fn = arg_fns[2]
            def if_fn(ctx):
                C = cond_fn(ctx); T = true_fn(ctx); F = false_fn(ctx)
                return [T[i] if C[i] != 0 else F[i] for i in range(ctx.n)]
            return if_fn

        if func == "IFN":
            # IFN(COND, A, B) = IF(COND, B, A)
            cond_fn = arg_fns[0]; a_fn = arg_fns[1]; b_fn = arg_fns[2]
            def ifn_fn(ctx):
                C = cond_fn(ctx); A = a_fn(ctx); B = b_fn(ctx)
                return [B[i] if C[i] != 0 else A[i] for i in range(ctx.n)]
            return ifn_fn

        if func == "CROSS":
            a_fn = arg_fns[0]; b_fn = arg_fns[1]
            def cross_fn(ctx: _EvalCtx):
                A = a_fn(ctx); B = b_fn(ctx)
                return [1.0 if i > 0 and A[i] > B[i] and A[i-1] <= B[i-1] else 0.0 for i in range(ctx.n)]
            return cross_fn

        if func == "BETWEEN":
            def between_fn(ctx: _EvalCtx):
                X = arg_fns[0](ctx); A = arg_fns[1](ctx); B = arg_fns[2](ctx)
                return [1.0 if A[i] <= X[i] <= B[i] else 0.0 for i in range(ctx.n)]
            return between_fn

        if func == "FILTER":
            # FILTER(X, N): X 满足条件后，将其设置为 1，并持续 N 周期
            sig_fn = arg_fns[0]; n_fn = arg_fns[1]
            def filter_fn(ctx: _EvalCtx):
                X = sig_fn(ctx)
                N = int(n_fn(ctx)[0])
                result = [0.0] * ctx.n
                i = 0
                while i < ctx.n:
                    if X[i] != 0:
                        for j in range(i, min(i+N, ctx.n)):
                            result[j] = X[j] if X[j] != 0 else result[j-1] if j > 0 else 1.0
                        i += N
                    else:
                        i += 1
                return result
            return filter_fn

        if func == "BARSLAST":
            # BARSLAST(X): 上一次 X 不为 0 到当前的周期数
            def barslast_fn(ctx: _EvalCtx):
                X = arg_fns[0](ctx)
                result = [0] * ctx.n
                cnt = 0
                found = False
                for i in range(ctx.n):
                    if X[i] != 0:
                        cnt = 0
                        found = True
                    else:
                        if found:
                            cnt += 1
                    result[i] = float(cnt)
                return result
            return barslast_fn

        if func == "BARSCOUNT":
            def barscount_fn(ctx: _EvalCtx):
                return [float(i+1) for i in range(ctx.n)]
            return barscount_fn

        if func == "BACKSET":
            # BACKSET(X, N): 若 X 非零，则将当前及前 N-1 周期设为 1
            sig_fn = arg_fns[0]; n_fn = arg_fns[1]
            def backset_fn(ctx: _EvalCtx):
                X = sig_fn(ctx)
                N = int(n_fn(ctx)[0])
                result = [0.0] * ctx.n
                for i in range(ctx.n):
                    if X[i] != 0:
                        for j in range(max(0, i-N+1), i+1):
                            result[j] = 1.0
                return result
            return backset_fn

        if func == "VOL" or func == "VOLINSTICK":
            def vol_fn(ctx: _EvalCtx):
                return ctx.ohlcv.get("VOL", [0.0]*ctx.n)
            return vol_fn

        if func in ("CLOSE", "OPEN", "HIGH", "LOW"):
            key = func
            def ohlcv_fn(ctx, k=key):
                return ctx.ohlcv.get(k, [0.0]*ctx.n)
            return ohlcv_fn

        # 未知函数：当常量序列返回
        def unknown_fn(ctx: _EvalCtx):
            return [0.0] * ctx.n
        return unknown_fn

    # 通达信函数映射
    @property
    def _TDX_FUNCTIONS(self) -> Dict[str, int]:
        return TDX_FUNCTIONS


# ============================================================
# 运行时上下文
# ============================================================

class _EvalCtx:
    """每个 bar 的求值上下文"""
    __slots__ = ("bar", "n", "candles", "vars", "ns")
    def __init__(self, bar: int, n: int, candles: List[Dict], vars_: Dict, ns: Dict):
        self.bar = bar
        self.n = n
        self.candles = candles
        self.vars = vars_
        self.ns = ns

    @property
    def ohlcv(self) -> Dict[str, List[float]]:
        return {
            "CLOSE":  [self.candles[i].get("close", 0) for i in range(self.n)],
            "OPEN":   [self.candles[i].get("open", 0) for i in range(self.n)],
            "HIGH":   [self.candles[i].get("high", 0) for i in range(self.n)],
            "LOW":    [self.candles[i].get("low", 0) for i in range(self.n)],
            "VOL":    [self.candles[i].get("volume", 0) for i in range(self.n)],
            "AMOUNT": [self.candles[i].get("quote_volume", 0) for i in range(self.n)],
        }


# ============================================================
# 辅助函数
# ============================================================

def _ohlcv_key(key: str):
    """生成 OHLCV 访问器"""
    def getter(candles: List[Dict]) -> List[float]:
        return [c.get(key, 0) for c in candles]
    return getter

_ohlc_loader = _ohlcv_key  # 别名


def _bin_scalar(op: str, a: float, b: float) -> float:
    """标量二元运算"""
    if op == "+":   return a + b
    if op == "-":   return a - b
    if op == "*":   return a * b
    if op == "/":   return a / b if b != 0 else 0.0
    if op == ">":   return 1.0 if a > b else 0.0
    if op == "<":   return 1.0 if a < b else 0.0
    if op == ">=":  return 1.0 if a >= b else 0.0
    if op == "<=":  return 1.0 if a <= b else 0.0
    if op in ("=", "=="): return 1.0 if a == b else 0.0
    if op == "!=":  return 1.0 if a != b else 0.0
    if op == "AND": return 1.0 if (a != 0) and (b != 0) else 0.0
    if op == "OR":  return 1.0 if (a != 0) or (b != 0) else 0.0
    return 0.0


def _is_buy_signal_var(name: str) -> bool:
    n = name.upper()
    buy_keywords = ("BUY", "买入", "买", "LONG", "多", "买入信号", "买信号",
                    "XG", "JGX", "崔买", "出击", "最佳买入")
    return any(kw in n for kw in buy_keywords)


def _is_sell_signal_var(name: str) -> bool:
    n = name.upper()
    sell_keywords = ("SELL", "卖出", "卖", "SHORT", "空", "卖出信号", "卖信号",
                     "止盈", "止损", "平仓", "获利", "清仓", "撤退")
    return any(kw in n for kw in sell_keywords)


# ============================================================
# FormulaStrategy — 集成到回测/实盘的策略类
# ============================================================

class FormulaStrategy:
    """
    通达信公式策略

    用法：
        strategy = FormulaStrategy(
            formula="RSV:=(CLOSE-LLV(LOW,9))/(HHV(HIGH,9)-LLV(LOW,9))*100;"
                    "K:SMA(RSV,3,1);"
                    "D:SMA(K,3,1);"
                    "DRAWICON(CROSS(K,D) AND K<20, LOW, 1);"
                    "DRAWICON(CROSS(D,K) AND K>80, HIGH, 2);",
            symbol="ETH/USDT",
            timeframe="4h",
        )
        signals = strategy.populate_entry_trend(candles)
    """

    def __init__(
        self,
        formula: str,
        symbol: str = "BTC/USDT",
        timeframe: str = "4h",
        stop_loss: float = 0.05,
        take_profit: float = 0.10,
    ):
        self.formula = formula
        self.symbol = symbol
        self.timeframe = timeframe
        self.stop_loss = stop_loss
        self.take_profit = take_profit
        self._compiler = TdxCompiler()
        self._indicator_fn, self._signal_fn = self._compiler.compile(formula)
        self._indicators_cache: Dict[str, List[float]] = {}

    def populate_indicators(self, candles: List[Dict]) -> Dict[str, List[float]]:
        self._indicators_cache = self._indicator_fn(candles)
        return self._indicators_cache

    def populate_entry_trend(self, candles: List[Dict]) -> List[int]:
        """买入信号：Buy_var 从 0 变非0"""
        if not self._indicators_cache:
            self.populate_indicators(candles)
        signals = self._signal_fn(candles)
        # 已经在 signal_fn 里处理了，这里透传
        return signals

    def populate_exit_trend(self, candles: List[Dict]) -> List[int]:
        """卖出信号：Sell_var 从 0 变非0"""
        if not self._indicators_cache:
            self.populate_indicators(candles)
        signals = self._signal_fn(candles)
        # 注意：exit 返回 -1=SELL, 0=HOLD
        return [0 if s >= 0 else -1 for s in signals]

    def get_config(self):
        from strategies import StrategyConfig
        return StrategyConfig(
            symbol=self.symbol,
            timeframe=self.timeframe,
            stop_loss=self.stop_loss,
            take_profit=self.take_profit,
        )


# ============================================================
# 内置常用公式模板
# ============================================================

BUILTIN_FORMULAS = {
    # RSI（通达信经典）
    "RSI": """
RSV:SMA(MAX(CLOSE-REF(CLOSE,1),0),6,1)/SMA(ABS(CLOSE-REF(CLOSE,1)),6,1)*100;
RSI:RSV;
买:IF(RSV<20,RSV,0);
卖:IF(RSV>80,RSV,0);
""",
    # KDJ
    "KDJ": """
RSV:=(CLOSE-LLV(LOW,9))/(HHV(HIGH,9)-LLV(LOW,9))*100;
K:SMA(RSV,3,1);
D:SMA(K,3,1);
J:3*K-2*D;
买:CROSS(K,D) AND K<20;
卖:CROSS(D,K) AND K>80;
""",
    # MACD（标准 12/26/9）
    "MACD": """
DIF:EMA(CLOSE,12)-EMA(CLOSE,26);
DEA:EMA(DIF,9);
MACD:(DIF-DEA)*2;
买:CROSS(DIF,DEA) AND DIF<0;
卖:CROSS(DEA,DIF) AND DIF>0;
""",
    # CCI 超卖超买
    "CCI": """
TYP:=(HIGH+LOW+CLOSE)/3;
CCI:(TYP-MA(TYP,14))/(0.015*AVEDEV(TYP,14));
买:REF(CCI,1)<-100 AND CCI>-100;
卖:REF(CCI,1)>100 AND CCI<100;
""",
    # 布林带
    "BOLL": """
BOLL:MA(CLOSE,20);
UB:BOLL+2*STD(CLOSE,20);
LB:BOLL-2*STD(CLOSE,20);
买:CROSS(CLOSE,LB);
卖:CROSS(UB,CLOSE);
""",
    # 威廉指标
    "WR": """
WR1:100*(HHV(HIGH,14)-CLOSE)/(HHV(HIGH,14)-LLV(LOW,14));
WR2:100*(HHV(HIGH,28)-CLOSE)/(HHV(HIGH,28)-LLV(LOW,28));
买:WR1<WRS2 AND WR1>WR2;
卖:WR1>WRS1 AND WR1<WR2;
""",
    # 均线金叉死叉
    "MA_CROSS": """
MA5:MA(CLOSE,5);
MA10:MA(CLOSE,10);
MA20:MA(CLOSE,20);
买:CROSS(MA5,MA10);
卖:CROSS(MA10,MA5);
""",
}


# ============================================================
# CLI 测试
# ============================================================

if __name__ == "__main__":
    import json

    # 模拟 K 线数据
    import random
    random.seed(42)
    BASE = 2000.0
    candles = []
    for i in range(200):
        o = BASE + random.uniform(-50, 50)
        h = o + random.uniform(0, 30)
        l = o - random.uniform(0, 30)
        c = (o + h + l) / 3 + random.uniform(-10, 10)
        vol = random.uniform(100, 1000)
        candles.append({
            "timestamp": 1600000000000 + i * 3600000,
            "open": round(o, 2), "high": round(h, 2),
            "low": round(l, 2), "close": round(c, 2),
            "volume": round(vol, 2),
        })
        BASE = c

    print("=" * 60)
    print("通达信公式编译器 — 测试")
    print("=" * 60)

    test_cases = [
        ("KDJ", BUILTIN_FORMULAS["KDJ"]),
        ("MACD", BUILTIN_FORMULAS["MACD"]),
        ("RSI", BUILTIN_FORMULAS["RSI"]),
    ]

    for name, formula in test_cases:
        print(f"\n{'='*60}")
        print(f"  测试公式: {name}")
        print(f"{'='*60}")
        try:
            compiler = TdxCompiler()
            ind_fn, sig_fn = compiler.compile(formula)
            indicators = ind_fn(candles)
            signals = sig_fn(candles)

            print(f"  输出变量: {list(indicators.keys())}")
            for var, vals in indicators.items():
                if len(vals) > 5:
                    print(f"  {var}: {[round(v, 2) for v in vals[-5:]]}")

            buy_count = sum(1 for s in signals if s == 1)
            sell_count = sum(1 for s in signals if s == -1)
            print(f"  信号: 买={buy_count}, 卖={sell_count}, 持有={len(signals)-buy_count-sell_count}")
        except Exception as e:
            print(f"  ❌ 错误: {e}")
            import traceback; traceback.print_exc()

    print(f"\n{'='*60}")
    print("  自定义公式测试")
    print(f"{'='*60}")

    custom = "MA5:MA(CLOSE,5);MA10:MA(CLOSE,10);买:CROSS(MA5,MA10);卖:CROSS(MA10,MA5);"
    try:
        compiler = TdxCompiler()
        ind_fn, sig_fn = compiler.compile(custom)
        indicators = ind_fn(candles)
        signals = sig_fn(candles)
        print(f"  输出变量: {list(indicators.keys())}")
        print(f"  MA5 最后5根: {[round(v, 2) for v in indicators.get('MA5', [])[-5:]]}")
        print(f"  MA10 最后5根: {[round(v, 2) for v in indicators.get('MA10', [])[-5:]]}")
        buy_sig = [i for i, s in enumerate(signals) if s == 1]
        sell_sig = [i for i, s in enumerate(signals) if s == -1]
        print(f"  买入信号位置: {buy_sig}")
        print(f"  卖出信号位置: {sell_sig}")
        print("  ✅ 自定义公式测试通过")
    except Exception as e:
        print(f"  ❌ 错误: {e}")
        import traceback; traceback.print_exc()
