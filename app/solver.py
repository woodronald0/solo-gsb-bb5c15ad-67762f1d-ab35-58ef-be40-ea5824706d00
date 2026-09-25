"""差分约束求解：联立事件时间差区间，检测矛盾并给出可行时间线。

每条输入行：事件甲, 事件乙, 最少分钟 lo, 最多分钟 hi
含义：lo <= t(乙) - t(甲) <= hi

改写为标准差分约束（最短路）形式：
    t(乙) - t(甲) <= hi        -> 边 甲 -> 乙，权 hi（上界）
    t(甲) - t(乙) <= -lo       -> 边 乙 -> 甲，权 -lo（下界）

引入指向所有事件、权为 0 的虚拟源（实现上等价于把所有距离初始化为 0），
用 Bellman-Ford 判定：第 N 轮仍可松弛 => 存在负环，即区间互相矛盾。

约束冗余核对（check_redundancy）：删去所选行对应的上、下界两条边后，
在其余约束构成的图上分别求 甲->乙 与 乙->甲 的单源最短路：
    甲->乙 最短路 = 其余约束能推出的 t(乙)-t(甲) 最紧上界（不可达则无界）
    乙->甲 最短路取负 = 其余约束能推出的 t(乙)-t(甲) 最紧下界（不可达则无界）
仅当推导区间 [下界, 上界] 完全落在所选行区间 [lo, hi] 内时判为“可删”。

两版比较（compare_texts）：基准文本与修订文本各自独立 parse_text + solve_rows，
任一版校验失败或存在负环时，分别原样带上该版错误/闭合矛盾链，不生成差异结论；
两版均可解时，以“最早事件第 0 分钟”的规范化时刻按事件名对齐，列出共同事件的
两版时刻与变化分钟数，并用 _derive_bound 复用冗余核对同款最短路推导，
把推出修订时刻的修订行（原始行号、所用界值）作为变化证据；同时给出新增、删除事件。

冲突单行修订建议（suggest_repairs）：文本语法有效但联立矛盾时，逐行暂时移除该行的
上、下界约束；其余行仍矛盾的行排除，其余可解时用冗余核对同款最短路求这两个事件
时间差的最紧可行区间，只放宽该行一个端点至首次相交的整数分钟（区间含整数端点，
最小改动即“移到边界”），给出最小改动量、替换后的整行，以及迫使该端点移动的
原始行号与界值推导链；建议按改动分钟数、原始行号排序，无单行修复时明确提示。

绝对时间锚定（solve_anchored）：每条锚点行“事件名,YYYY-MM-DD HH:MM”按同一无时区
本地时间换算为相对统一零点（公历 0001-01-01 00:00）的分钟数 M，引入零点节点 ZERO
与一对等式边（ZERO->事件 权 M、事件->ZERO 权 -M，即“事件相对零点的等式”），与区间行
复用同一套差分约束 Bellman-Ford 联立；同名锚点重复也作为两条等式参与判定（时刻不同
即构成负环）。冲突时闭合矛盾链同时标注区间行号、锚点行号及所用界值；可解时不取任何
任意平移的可行解充当真实日期，而是对每个事件用冗余核对同款单源最短路求其相对零点的
最紧下界/上界：仅与锚点连通（两端都有界）的事件给出绝对日期时刻（精确点或范围），
其余事件标为“未定位”。

互斥说法下的先后查询（solve_precedence）：共同约束沿用事件区间文本；候选组每行
“区间A | 区间B”表示两条只能选一条的互斥说法（最多 6 行，两侧复用区间行格式与同款
字段校验）。枚举全部 2^N 个“每组选一侧”的组合，分别与共同约束联立（同一套差分约束
Bellman-Ford），存在负环的组合判为冲突并排除；对每个可行组合复用冗余核对同款
_derive_bound 求 t(乙) − t(甲) 的最紧下界/上界（推不出的端点为无界），再跨组合合并：
下界取各组合最小值、上界取各组合最大值，任一组合对应端无界则合并端无界。
合并下界 ≥ 1 分钟判“甲始终先于乙”，合并上界 ≤ −1 分钟判“甲始终后于乙”，否则判
“不确定”；无可行组合时不作先后判断。查询的甲、乙必须出现在共同约束中。

最少撤回（find_minimum_withdrawals）：复用 parse_text 与同一套差分约束 Bellman-Ford，
输入为现有区间格式文本（非空行最多 MAX_WITHDRAWAL_LINES=10 条，超限明确报错）。
空约束集（空文本）视为可行，直接返回零撤回方案与空时间线；原文已可行时同样返回
零撤回方案。否则按撤回行数 k=1,2,… 递增枚举 C(n,k) 个原始行号组合，第一个存在可行
剩余子集的 k 即最少撤回数，返回该层全部可行组合（每个方案附剩余约束的一条规范化
可行时间线，撤回全部行时时间线为空）；语法错误逐行返回。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import combinations, product

_INT_RE = re.compile(r"^[+-]?\d+$")

# 绝对时间锚定：统一零点（无时区本地时间）与图中零点节点的内部名。
# 内部名以 \x00 开头，用户事件名不可能与之撞名；对外展示统一映射为 ZERO_DISPLAY。
ZERO_DT = datetime(1, 1, 1, 0, 0)
ZERO_NODE = "\x00zero"
ZERO_DISPLAY = "统一零点"

# 锚点日期时间格式：YYYY-MM-DD HH:MM（允许任意空白分隔日期与时刻）
_ANCHOR_DT_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})\s+(\d{2}):(\d{2})$")


def _display_name(name: str) -> str:
    """图中内部节点名转对外显示名（零点节点显示为“统一零点”）。"""
    return ZERO_DISPLAY if name == ZERO_NODE else name


@dataclass(frozen=True)
class Row:
    line: int
    a: str
    b: str
    low: int
    high: int


@dataclass(frozen=True)
class Edge:
    u: int
    v: int
    w: int
    line: int
    kind: str  # "upper"（上界边）、"lower"（下界边）或 "anchor"（锚点等式边）
    bound: int  # 原始行中的界值（上界为 high，下界为 low；锚点边为相对零点的分钟数）
    source: str = "interval"  # "interval"（区间行）或 "anchor"（锚点行）
    label: str = ""  # 锚点边展示用的日期时刻文本（YYYY-MM-DD HH:MM）


@dataclass(frozen=True)
class Anchor:
    line: int
    event: str
    minute: int  # 相对统一零点 ZERO_DT 的分钟数
    text: str  # 规范化后的日期时刻文本（YYYY-MM-DD HH:MM）


@dataclass(frozen=True)
class Claim:
    line: int  # 候选组文本的原始行号
    side: str  # "A" 或 "B"：该组互斥说法的左/右侧
    a: str
    b: str
    low: int
    high: int


# 互斥说法候选组最多行数：每行二选一，组合数为 2 的幂，限制行数保证枚举耗时可控
MAX_GROUP_LINES = 6


def _validate_interval_fields(a: str, b: str, low_s: str, high_s: str) -> tuple[int | None, int | None, list[str]]:
    """校验一条区间的四个字段：事件名非空、上下界为整数分钟、下界不大于上界。

    返回 (low, high, 错误片段列表)；有错误时 low/high 可能为 None，调用方应忽略。
    共同约束行与互斥说法候选组两侧共用同一套校验与措辞。
    """
    errors: list[str] = []
    if not a:
        errors.append("事件甲名称为空")
    if not b:
        errors.append("事件乙名称为空")

    low: int | None = None
    high: int | None = None
    if _INT_RE.match(low_s):
        low = int(low_s)
    else:
        errors.append(f"最少分钟必须是整数分钟，得到 “{low_s}”")
    if _INT_RE.match(high_s):
        high = int(high_s)
    else:
        errors.append(f"最多分钟必须是整数分钟，得到 “{high_s}”")

    if low is not None and high is not None and low > high:
        errors.append(f"下界 {low} 大于上界 {high}，时间差区间为空")
    return low, high, errors


def parse_text(text: str) -> tuple[list[Row], list[dict]]:
    """按行解析与校验。返回 (有效行, 错误列表)。"""
    errors: list[dict] = []
    rows: list[Row] = []

    if not text or not text.strip():
        return rows, [{"line": None, "message": "输入为空：请粘贴“事件甲,事件乙,最少分钟,最多分钟”格式的多行文本。"}]

    for line_no, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue  # 空行不计入校验，但行号保留为原始行号

        # 兼容中文逗号
        parts = [p.strip() for p in line.replace("，", ",").split(",")]
        if len(parts) != 4:
            errors.append({
                "line": line_no,
                "message": f"第{line_no}行：应为 4 个字段（事件甲,事件乙,最少分钟,最多分钟），实际得到 {len(parts)} 个。",
            })
            continue

        a, b, low_s, high_s = parts
        low, high, row_errors = _validate_interval_fields(a, b, low_s, high_s)
        if row_errors:
            errors.append({"line": line_no, "message": f"第{line_no}行：" + "；".join(row_errors) + "。"})
            continue

        rows.append(Row(line_no, a, b, low, high))  # type: ignore[arg-type]

    return rows, errors


def _edge_step(names: list[str], e: Edge) -> dict:
    """把一条约束边还原为可读步骤：来源（区间行/锚点行）、原始行号、方向、所用界与界值。"""
    if e.kind == "anchor":
        # 锚点等式边：ZERO->事件 权 M（不晚于锚定时刻）或 事件->ZERO 权 -M（不早于锚定时刻）
        if names[e.u] == ZERO_NODE:
            relation = f"{names[e.v]} 不晚于 {e.label}（锚定时刻）"
        else:
            relation = f"{names[e.u]} 不早于 {e.label}（锚定时刻）"
        return {
            "from": _display_name(names[e.u]),
            "to": _display_name(names[e.v]),
            "weight": e.w,
            "line": e.line,
            "kind": e.kind,
            "kind_label": "锚点",
            "bound": e.bound,
            "source": "anchor",
            "anchor_time": e.label,
            "relation": relation,
        }
    if e.kind == "upper":
        relation = f"{names[e.v]}−{names[e.u]} ≤ {e.bound}"
        kind_label = "上界（最多）"
    else:
        # 边 v -> u、权 -low 对应 names[v]−names[u] >= low，即 u 事件比 v 事件早至少 low
        relation = f"{names[e.u]}−{names[e.v]} ≥ {e.bound}"
        kind_label = "下界（最少）"
    return {
        "from": _display_name(names[e.u]),
        "to": _display_name(names[e.v]),
        "weight": e.w,
        "line": e.line,
        "kind": e.kind,
        "kind_label": kind_label,
        "bound": e.bound,
        "source": "interval",
        "relation": relation,
    }


def _build_graph(rows: list[Row]) -> tuple[list[str], dict[str, int], list[Edge]]:
    """事件按首次出现顺序编号，并把每行转换为上界、下界两条边。"""
    names: list[str] = []
    index: dict[str, int] = {}
    for r in rows:
        for name in (r.a, r.b):
            if name not in index:
                index[name] = len(names)
                names.append(name)

    edges: list[Edge] = []
    for r in rows:
        ia, ib = index[r.a], index[r.b]
        edges.append(Edge(ia, ib, r.high, r.line, "upper", r.high))
        edges.append(Edge(ib, ia, -r.low, r.line, "lower", r.low))
    return names, index, edges


def _relax_all(names: list[str], edges: list[Edge]) -> tuple[list[int], list[tuple[int, Edge] | None], int | None]:
    """全源 Bellman-Ford：所有距离初始化为 0（等价于虚拟源向每个顶点连权 0 的边）。

    返回 (dist, pred, last_relaxed)；last_relaxed 非 None 表示第 N 轮仍可松弛，即存在负环。
    """
    n = len(names)
    dist = [0] * n
    pred: list[tuple[int, Edge] | None] = [None] * n
    last_relaxed: int | None = None

    # Bellman-Ford：共 N 轮，第 N 轮仍可松弛 => 存在负环
    for iteration in range(n):
        relaxed = False
        for e in edges:
            cand = dist[e.u] + e.w
            if cand < dist[e.v]:
                dist[e.v] = cand
                pred[e.v] = (e.u, e)
                relaxed = True
                if iteration == n - 1:
                    last_relaxed = e.v
        if not relaxed:
            break
    return dist, pred, last_relaxed


def solve_rows(rows: list[Row]) -> dict:
    """对已通过校验的行联立 Bellman-Ford，返回内部求解结构（供 solve / 冗余核对 / 两版比较复用）。

    成功时含 rows/names/index/edges/dist/minutes（minutes 已平移到最早事件为 0）；
    存在负环时直接返回 _conflict_result 的公开矛盾链结构。
    没有任何行（空约束集）时无任何边，天然可行，返回空图与空时间线。
    """
    names, index, edges = _build_graph(rows)
    if not names:
        # 空约束集：无事件无边，任意时间线都可行（最少撤回时撤回全部行会走到这里）
        return {
            "status": "ok",
            "rows": rows,
            "names": names,
            "index": index,
            "edges": edges,
            "dist": [],
            "minutes": [],
        }
    dist, pred, last_relaxed = _relax_all(names, edges)

    if last_relaxed is not None:
        return _conflict_result(names, pred, last_relaxed, len(names))

    # 平移使最早事件落在第 0 分钟（差值不变，约束全部保留）
    base = min(dist)
    minutes = [dist[i] - base for i in range(len(names))]
    return {
        "status": "ok",
        "rows": rows,
        "names": names,
        "index": index,
        "edges": edges,
        "dist": dist,
        "minutes": minutes,
    }


def _format_ok(system: dict) -> dict:
    """把 solve_rows 的内部成功结构转为 /api/solve 的公开返回格式。"""
    names = system["names"]
    minutes = system["minutes"]
    rows = system["rows"]
    index = system["index"]
    dist = system["dist"]

    timeline = [
        {"event": names[i], "minute": minutes[i]}
        for i in range(len(names))
    ]
    timeline.sort(key=lambda x: (x["minute"], x["event"]))

    differences = [
        {
            "line": r.line,
            "a": r.a,
            "b": r.b,
            "low": r.low,
            "high": r.high,
            "actual": dist[index[r.b]] - dist[index[r.a]],
        }
        for r in rows
    ]

    return {
        "status": "ok",
        "timeline": timeline,
        "differences": differences,
        "event_count": len(names),
        "line_count": len(rows),
    }


def _parse_and_solve(text: str) -> tuple[list[Row], dict]:
    """解析校验一版文本并联立。成功返回 solve_rows 内部结构，否则返回公开错误结构。"""
    rows, errors = parse_text(text)
    if errors:
        return rows, {"status": "error", "errors": errors}
    if not rows:
        return rows, {
            "status": "error",
            "errors": [{"line": None, "message": "没有可联立的有效约束行。"}],
        }
    return rows, solve_rows(rows)


def solve(text: str) -> dict:
    _, result = _parse_and_solve(text)
    return _format_ok(result) if result["status"] == "ok" else result


def _conflict_result(names: list[str], pred, start: int, n: int) -> dict:
    """从第 N 轮仍被松弛的顶点出发，沿前驱找回负环并还原为原始行构成的矛盾链。"""
    x = start
    for _ in range(n):
        step = pred[x]
        if step is None:
            break
        x = step[0]

    cycle_edges: list[tuple[int, Edge]] = []
    seen: set[int] = set()
    cur = x
    while cur not in seen:
        seen.add(cur)
        step = pred[cur]
        if step is None:
            break
        pu, e = step
        cycle_edges.append((pu, e))
        cur = pu

    # 沿前驱收集是逆序的，翻转回边的正向（u -> v）
    cycle_edges.reverse()

    steps = [_edge_step(names, e) for _, e in cycle_edges]

    total = sum(s["weight"] for s in steps)
    return {
        "status": "conflict",
        "conflict": {
            "steps": steps,
            "weight_sum": total,
            "closes_to": steps[0]["from"] if steps else None,
        },
    }


def _bellman_ford(names: list[str], edges: list[Edge], source: int) -> tuple[list[int | None], list[tuple[int, Edge] | None]]:
    """单源最短路（调用前已确认无负环）。不可达顶点的距离为 None。"""
    n = len(names)
    dist: list[int | None] = [None] * n
    dist[source] = 0
    pred: list[tuple[int, Edge] | None] = [None] * n

    for _ in range(n - 1):
        relaxed = False
        for e in edges:
            du = dist[e.u]
            if du is None:
                continue
            cand = du + e.w
            if dist[e.v] is None or cand < dist[e.v]:
                dist[e.v] = cand
                pred[e.v] = (e.u, e)
                relaxed = True
        if not relaxed:
            break
    return dist, pred


def _derive_bound(names: list[str], edges: list[Edge], source: int, target: int) -> tuple[int | None, list[dict]]:
    """t(target) − t(source) 的最紧上界及其推导路径（source -> target 最短路）。

    返回 (上界, 路径步骤)；不可达时上界为 None、路径为空（该端点标为无界）。
    source == target 时上界为 0、路径为空（同一事件时间差恒为 0）。
    """
    dist, pred = _bellman_ford(names, edges, source)
    if dist[target] is None:
        return None, []

    path_edges: list[Edge] = []
    cur = target
    while cur != source:
        step = pred[cur]
        if step is None:  # 不可达时不会走到这里，防御性退出
            break
        pu, e = step
        path_edges.append(e)
        cur = pu
    path_edges.reverse()
    return dist[target], [_edge_step(names, e) for e in path_edges]


def check_redundancy(text: str, line: int) -> dict:
    """约束冗余核对：删去第 line 行后，求其余约束对该行两事件时间差的最紧区间。

    输入无效或原约束互相冲突时，直接返回 solve() 的现有求解结果。
    """
    rows, result = _parse_and_solve(text)
    if result["status"] != "ok":
        return result

    target = next((r for r in rows if r.line == line), None)
    if target is None:
        return {
            "status": "error",
            "errors": [{"line": line, "message": f"第{line}行不是一条有效约束行（空行或不存在的行号），无法核对。"}],
        }

    names = result["names"]
    index = result["index"]

    # 移除所选行对应的上、下界两条约束，其余行照常建边
    edges = [e for e in result["edges"] if e.line != target.line]

    ia, ib = index[target.a], index[target.b]
    # 上界：t(乙) − t(甲) 的最紧上界 = 甲 -> 乙 最短路
    high_val, upper_path = _derive_bound(names, edges, ia, ib)
    # 下界：t(甲) − t(乙) 的最紧上界 = 乙 -> 甲 最短路，取负即 t(乙) − t(甲) 的最紧下界
    low_val, lower_path = _derive_bound(names, edges, ib, ia)
    derived_low = -low_val if low_val is not None else None
    derived_high = high_val

    # 推导区间完全落在所选行区间内 <=> 可删；任一端点无界或超出 <=> 必要
    removable = (
        derived_low is not None
        and derived_high is not None
        and derived_low >= target.low
        and derived_high <= target.high
    )

    return {
        "status": "ok",
        "line": target.line,
        "a": target.a,
        "b": target.b,
        "low": target.low,
        "high": target.high,
        "derived_low": derived_low,
        "derived_high": derived_high,
        "lower_bounded": derived_low is not None,
        "upper_bounded": derived_high is not None,
        "lower_path": lower_path,
        "upper_path": upper_path,
        "verdict": "removable" if removable else "necessary",
        "event_count": len(names),
        "line_count": len(rows),
    }


# ---------- 两版比较 ----------

def _evidence_for(system: dict, anchor: str, target: str, minute: int, annotate) -> dict | None:
    """修订版中确定 target 相对第 0 分钟事件 anchor 的规范化时刻的推导路径证据。

    复用冗余核对同款 _derive_bound：anchor -> target 最短路给出最紧上界，
    target -> anchor 最短路取负给出最紧下界；规范化可行解恰好顶在其中一个紧界上，
    选中与 minute 相等的那条路径，路径上的修订行原始行号即“导致变化的修订行号”。
    target 与 anchor 不连通（属于另一个独立事件组件）时返回 None：两版各按自己的
    最早事件归零，该事件的“分钟变化”只是规范化假象，不构成可比差异。
    """
    names = system["names"]
    edges = system["edges"]
    index = system["index"]
    ia, it = index[anchor], index[target]

    if ia == it:
        return {"is_anchor": True, "direction": None, "bound": 0, "lines": [], "path": []}

    upper_val, upper_path = _derive_bound(names, edges, ia, it)
    back_val, back_path = _derive_bound(names, edges, it, ia)
    lower_val = -back_val if back_val is not None else None
    if upper_val is None and lower_val is None:
        return None  # target 与锚点分属互不连通的事件组件，无法相对比较

    if lower_val is not None and lower_val == minute:
        path, direction, bound = back_path, "lower", lower_val
    elif upper_val is not None and upper_val == minute:
        path, direction, bound = upper_path, "upper", upper_val
    elif lower_val is not None:  # 防御性回退：正常情况下必有一个紧界等于 minute
        path, direction, bound = back_path, "lower", lower_val
    else:
        path, direction, bound = upper_path, "upper", upper_val

    path = annotate(path)
    lines: list[int] = []
    for step in path:  # 同一行的上、下界极端情况下可能都在路径上，行号去重保序
        if step["line"] not in lines:
            lines.append(step["line"])
    return {"is_anchor": False, "direction": direction, "bound": bound, "lines": lines, "path": path}


def compare_texts(base_text: str, revised_text: str) -> dict:
    """两版独立校验、独立联立后按事件名对齐比较。

    任一版无效（error）或互相矛盾（conflict）时，不生成差异结论，
    分别在 base / revised 字段中带回该版的完整公开求解结果（错误列表或闭合矛盾链）。
    """
    base_rows, base_res = _parse_and_solve(base_text)
    revised_rows, revised_res = _parse_and_solve(revised_text)

    if base_res["status"] != "ok" or revised_res["status"] != "ok":
        return {
            "status": "invalid",
            "base": _format_ok(base_res) if base_res["status"] == "ok" else base_res,
            "revised": _format_ok(revised_res) if revised_res["status"] == "ok" else revised_res,
        }

    b_names = base_res["names"]
    r_names = revised_res["names"]
    b_minute = dict(zip(b_names, base_res["minutes"]))
    r_minute = dict(zip(r_names, revised_res["minutes"]))
    b_set, r_set = set(b_names), set(r_names)

    # 两版各自“第 0 分钟”的事件；优先选用两版共有的第 0 分钟事件作锚点，
    # 使共同事件的变化分钟数不夹带整体平移；没有共有锚点时各取本版最早事件。
    b_zeros = [name for name in b_names if b_minute[name] == 0]
    r_zeros = [name for name in r_names if r_minute[name] == 0]
    common_zero = next((name for name in b_zeros if name in r_zeros), None)
    base_anchor = common_zero or b_zeros[0]
    revised_anchor = common_zero or r_zeros[0]

    # 基准行按书写事件对索引，用于标注修订行相对基准是“新增/修改/未变”
    base_pairs: dict[tuple[str, str], list[tuple[int, int]]] = {}
    for r in base_rows:
        base_pairs.setdefault((r.a, r.b), []).append((r.low, r.high))
    revised_by_line = {r.line: r for r in revised_rows}

    def annotate(path: list[dict]) -> list[dict]:
        for step in path:
            row = revised_by_line.get(step["line"])
            label = "unchanged"
            if row is not None:
                base_bounds = base_pairs.get((row.a, row.b))
                if base_bounds is None:
                    label = "added"
                elif (row.low, row.high) not in base_bounds:
                    label = "changed"
            step["change"] = label
        return path

    alignment: list[dict] = []
    changed_count = 0

    # 共同事件：按基准版首次出现顺序对齐
    incomparable_count = 0
    for name in b_names:
        if name not in r_set:
            continue
        bm, rm = b_minute[name], r_minute[name]
        delta = rm - bm
        entry: dict = {
            "kind": "common",
            "event": name,
            "base_minute": bm,
            "revised_minute": rm,
            "delta": delta,
            "evidence": None,
        }
        if delta != 0:
            evidence = _evidence_for(revised_res, revised_anchor, name, rm, annotate)
            if evidence is None:
                # 与两版公共锚点不连通：分钟差只是各组件独立归零的假象，不判为变化
                entry["kind"] = "incomparable"
                incomparable_count += 1
            else:
                changed_count += 1
                entry["evidence"] = evidence
        alignment.append(entry)

    # 删除事件：仅基准版存在（保持基准版首次出现顺序）
    for name in b_names:
        if name not in r_set:
            alignment.append({
                "kind": "removed",
                "event": name,
                "base_minute": b_minute[name],
                "revised_minute": None,
                "delta": None,
                "evidence": None,
            })

    # 新增事件：仅修订版存在（保持修订版首次出现顺序）
    for name in r_names:
        if name not in b_set:
            alignment.append({
                "kind": "added",
                "event": name,
                "base_minute": None,
                "revised_minute": r_minute[name],
                "delta": None,
                "evidence": None,
            })

    return {
        "status": "ok",
        "base": {
            "event_count": len(b_names),
            "line_count": len(base_rows),
            "anchor": base_anchor,
        },
        "revised": {
            "event_count": len(r_names),
            "line_count": len(revised_rows),
            "anchor": revised_anchor,
        },
        "anchor_shifted": base_anchor != revised_anchor,
        "common_count": len(b_set & r_set),
        "changed_count": changed_count,
        "incomparable_count": incomparable_count,
        "added_count": len(r_set - b_set),
        "removed_count": len(b_set - r_set),
        "alignment": alignment,
    }


# ---------- 冲突单行修订建议 ----------

def _row_text(row: Row, low: int, high: int) -> str:
    """把一条约束还原为输入格式的整行文本（事件名原样，界值替换为给定值）。"""
    return f"{row.a},{row.b},{low},{high}"


def suggest_repairs(text: str) -> dict:
    """冲突单行修订建议：找出“只放宽某一行的某一个端点即可消除矛盾”的最小改动。

    流程：复用 parse_text 逐行校验（输入错误直接返回逐行错误）；原文本可解时
    明确说明无需修订；联立矛盾时遍历每条有效约束行，暂时移除该行的上、下界
    两条边后对其余行运行 Bellman-Ford——仍矛盾的行排除（只改该行一个端点救不回
    其余行自身的矛盾）；其余可解时复用冗余核对同款 _derive_bound 求这两个事件
    时间差的最紧可行区间 [dl, dh]：

    - 上界冲突（dh < low）：只放宽下界至 dh（low' = dh，上界不动），改动 low − dh 分钟，
      证据为推出该最紧上界的原始行号与界值推导链；
    - 下界冲突（dl > high）：只放宽上界至 dl（high' = dl，下界不动），改动 dl − high 分钟，
      证据为推出该最紧下界的原始行号与界值推导链。

    区间含整数端点，移到边界即首次相交的整数分钟，改动量最小。建议按改动分钟数、
    原始行号升序排序；没有任何单行修复时明确提示。
    """
    rows, errors = parse_text(text)
    if errors:
        return {"status": "error", "errors": errors}
    if not rows:
        return {
            "status": "error",
            "errors": [{"line": None, "message": "没有可联立的有效约束行。"}],
        }

    result = solve_rows(rows)
    if result["status"] == "ok":
        return {
            "status": "ok",
            "message": "原文本区间无冲突，无需修订。",
            "event_count": len(result["names"]),
            "line_count": len(rows),
        }

    # 冲突分支不含内部结构，重建图（与 solve_rows 同一套建边逻辑）
    names, index, all_edges = _build_graph(rows)

    suggestions: list[dict] = []
    for row in rows:
        # 暂时移除该行的上、下界两条约束，其余行照常联立
        edges = [e for e in all_edges if e.line != row.line]
        rest_rows = [r for r in rows if r.line != row.line]
        if rest_rows and solve_rows(rest_rows)["status"] != "ok":
            continue  # 其余行自身仍矛盾：只改该行一个端点无法修复，排除
        # 其余行为空时无任何约束，必然可解

        ia, ib = index[row.a], index[row.b]
        # 其余约束推出的最紧可行区间 [derived_low, derived_high]（两端必有界，否则其余行无解）
        high_val, upper_path = _derive_bound(names, edges, ia, ib)
        low_val, lower_path = _derive_bound(names, edges, ib, ia)
        derived_high = high_val
        derived_low = -low_val if low_val is not None else None
        if derived_low is None or derived_high is None:
            continue  # 防御：其余行可解时两事件互相可达，两端必有界

        if derived_high < row.low:
            # 上界冲突：只放宽下界至首次相交的整数分钟 derived_high
            suggestions.append({
                "line": row.line,
                "a": row.a,
                "b": row.b,
                "low": row.low,
                "high": row.high,
                "endpoint": "low",
                "endpoint_label": "下界（最少分钟）",
                "old_value": row.low,
                "new_value": derived_high,
                "change_minutes": row.low - derived_high,
                "new_line": _row_text(row, derived_high, row.high),
                "derived_low": derived_low,
                "derived_high": derived_high,
                "forcing_path": upper_path,
                "forcing_lines": [s["line"] for s in upper_path],
            })
        elif derived_low > row.high:
            # 下界冲突：只放宽上界至首次相交的整数分钟 derived_low
            suggestions.append({
                "line": row.line,
                "a": row.a,
                "b": row.b,
                "low": row.low,
                "high": row.high,
                "endpoint": "high",
                "endpoint_label": "上界（最多分钟）",
                "old_value": row.high,
                "new_value": derived_low,
                "change_minutes": derived_low - row.high,
                "new_line": _row_text(row, row.low, derived_low),
                "derived_low": derived_low,
                "derived_high": derived_high,
                "forcing_path": lower_path,
                "forcing_lines": [s["line"] for s in lower_path],
            })
        # 其余情形（该行区间已含推导区间）说明其余行不可能可解，不会走到

    suggestions.sort(key=lambda s: (s["change_minutes"], s["line"]))

    return {
        "status": "conflict",
        "conflict": result["conflict"],
        "suggestions": suggestions,
        "suggestion_count": len(suggestions),
        "checked_lines": len(rows),
        "event_count": len(names),
        "line_count": len(rows),
    }


# ---------- 绝对时间锚定 ----------

def _dt_to_minute(dt: datetime) -> int:
    """无时区本地日期时刻转相对统一零点 ZERO_DT 的分钟数（整数运算，无浮点误差）。"""
    delta = dt - ZERO_DT
    return delta.days * 1440 + delta.seconds // 60


def _minute_to_text(minute: int) -> str | None:
    """相对零点的分钟数转回 “YYYY-MM-DD HH:MM”；超出 datetime 可表示范围时返回 None。"""
    try:
        dt = ZERO_DT + timedelta(minutes=minute)
    except OverflowError:
        return None
    return f"{dt.year:04d}-{dt.month:02d}-{dt.day:02d} {dt.hour:02d}:{dt.minute:02d}"


def parse_anchors(text: str) -> tuple[list[Anchor], list[dict]]:
    """按行解析锚点：事件名,YYYY-MM-DD HH:MM（同一无时区本地时间，精确到分钟）。

    返回 (有效锚点, 错误列表)；空行跳过但行号保留为锚点文本的原始行号。
    """
    errors: list[dict] = []
    anchors: list[Anchor] = []

    if not text or not text.strip():
        return anchors, [{"line": None, "message": "锚点输入为空：请粘贴“事件名,YYYY-MM-DD HH:MM”格式的多行文本。"}]

    for line_no, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue  # 空行不计入校验，但行号保留为锚点文本的原始行号

        # 兼容中文逗号
        parts = [p.strip() for p in line.replace("，", ",").split(",")]
        if len(parts) != 2:
            errors.append({
                "line": line_no,
                "message": f"锚点第{line_no}行：应为 2 个字段（事件名,YYYY-MM-DD HH:MM），实际得到 {len(parts)} 个。",
            })
            continue

        name, dt_s = parts
        if not name:
            errors.append({"line": line_no, "message": f"锚点第{line_no}行：事件名为空。"})
            continue

        m = _ANCHOR_DT_RE.match(dt_s)
        if not m:
            errors.append({
                "line": line_no,
                "message": f"锚点第{line_no}行：日期时刻应为 “YYYY-MM-DD HH:MM”（24 小时制，精确到分钟），得到 “{dt_s}”。",
            })
            continue

        year, month, day, hour, minute = (int(g) for g in m.groups())
        try:
            dt = datetime(year, month, day, hour, minute)
        except ValueError:
            errors.append({
                "line": line_no,
                "message": f"锚点第{line_no}行：日期时刻 “{dt_s}” 不存在（请检查月份、日期与时分是否越界）。",
            })
            continue

        anchors.append(Anchor(line_no, name, _dt_to_minute(dt), _minute_to_text(_dt_to_minute(dt)) or dt_s))

    return anchors, errors


def solve_anchored(text: str, anchors_text: str) -> dict:
    """绝对时间锚定：区间约束与锚点等式联立。

    每个锚点换算为相对统一零点（0001-01-01 00:00，无时区本地时间）的分钟数 M，
    引入零点节点 ZERO 与一对等式边（ZERO->事件 权 M、事件->ZERO 权 -M），与区间行
    复用同一套差分约束 Bellman-Ford 联立；同名锚点重复也作为两条等式参与判定，
    时刻不同即构成负环。负环还原的闭合矛盾链同时含区间行号、锚点行号与所用界值。

    可解时不取任何任意平移的可行解充当真实日期：对每个事件用冗余核对同款单源
    最短路求其相对零点的最紧下界/上界，仅当两端都有界（与锚点连通）时给出绝对
    日期时刻（下界==上界为精确点，否则为范围），其余事件标为“未定位”。
    """
    errors: list[dict] = []
    rows: list[Row] = []
    if text and text.strip():
        rows, errors = parse_text(text)  # 复用现有区间校验；区间文本留空即纯锚点模式
    anchors, anchor_errors = parse_anchors(anchors_text)
    errors = errors + anchor_errors
    if errors:
        return {"status": "error", "errors": errors}
    if not rows and not anchors:
        # parse_anchors 对空文本已报错，正常不会走到；防御性返回
        return {"status": "error", "errors": [{"line": None, "message": "没有可联立的区间约束或锚点。"}]}

    # 区间行复用现有建边逻辑，再补入零点节点与每个锚点的等式边
    names, index, edges = _build_graph(rows)
    index[ZERO_NODE] = len(names)
    names.append(ZERO_NODE)
    iz = index[ZERO_NODE]
    for a in anchors:
        ie = index.get(a.event)
        if ie is None:
            ie = len(names)
            index[a.event] = ie
            names.append(a.event)
        edges.append(Edge(iz, ie, a.minute, a.line, "anchor", a.minute, "anchor", a.text))
        edges.append(Edge(ie, iz, -a.minute, a.line, "anchor", a.minute, "anchor", a.text))

    dist, pred, last_relaxed = _relax_all(names, edges)
    if last_relaxed is not None:
        return _conflict_result(names, pred, last_relaxed, len(names))

    # 每个事件相对零点的最紧界：上界 = ZERO->事件 最短路；下界 = -(事件->ZERO 最短路)。
    # 反向图上从 ZERO 跑一次最短路即得全部“事件->ZERO”距离。
    upper_dist, _ = _bellman_ford(names, edges, iz)
    rev_edges = [Edge(e.v, e.u, e.w, e.line, e.kind, e.bound, e.source, e.label) for e in edges]
    lower_dist, _ = _bellman_ford(names, rev_edges, iz)

    events: list[dict] = []
    for i, name in enumerate(names):
        if name == ZERO_NODE:
            continue
        ub, lb = upper_dist[i], lower_dist[i]
        if ub is None or lb is None:
            # 与零点不连通（独立事件组件）：只有相对区间，不编造任何绝对日期
            events.append({
                "event": name,
                "located": False,
                "minute_low": None,
                "minute_high": None,
                "abs_low": None,
                "abs_high": None,
                "exact": False,
            })
            continue
        lo, hi = -lb, ub
        events.append({
            "event": name,
            "located": True,
            "minute_low": lo,
            "minute_high": hi,
            "abs_low": _minute_to_text(lo),
            "abs_high": _minute_to_text(hi),
            "exact": lo == hi,
        })

    # 已定位事件按最早时刻排序在前，未定位事件按名称排序在后
    events.sort(key=lambda e: (not e["located"], e["minute_low"] if e["minute_low"] is not None else 0, e["event"]))
    located_count = sum(1 for e in events if e["located"])

    return {
        "status": "ok",
        "anchors": [
            {"line": a.line, "event": a.event, "time": a.text, "minute": a.minute}
            for a in anchors
        ],
        "events": events,
        "event_count": len(events),
        "located_count": located_count,
        "anchor_count": len(anchors),
        "line_count": len(rows),
    }


# ---------- 互斥说法下的先后查询 ----------

# 先后结论的对外标签
_VERDICT_LABELS = {
    "before": "甲始终先于乙",
    "after": "甲始终后于乙",
    "undetermined": "不确定",
    "no_feasible": "无可行组合",
}


def parse_groups(text: str) -> tuple[list[list[Claim]], list[dict]]:
    """按行解析互斥说法候选组：每行 “区间A | 区间B”，两侧均复用区间行格式与同款校验。

    返回 (候选组列表, 错误列表)；每个候选组为 [A侧说法, B侧说法]。
    空行跳过但行号保留为候选组文本的原始行号；最多 MAX_GROUP_LINES 行。
    """
    errors: list[dict] = []
    groups: list[list[Claim]] = []

    if not text or not text.strip():
        return groups, [{"line": None, "message": "候选组输入为空：请粘贴每行 “区间A | 区间B” 格式的互斥说法（最多 6 行）。"}]

    non_empty = 0
    for line_no, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue  # 空行不计入校验，但行号保留为候选组文本的原始行号
        non_empty += 1

        # 兼容中文竖线
        sides = [s.strip() for s in line.replace("｜", "|").split("|")]
        if len(sides) != 2:
            errors.append({
                "line": line_no,
                "message": f"候选组第{line_no}行：应为 “区间A | 区间B” 两段互斥说法（以一个 | 分隔），实际得到 {len(sides)} 段。",
            })
            continue

        claims: list[Claim] = []
        for side_label, side_text in zip(("A", "B"), sides):
            # 兼容中文逗号
            parts = [p.strip() for p in side_text.replace("，", ",").split(",")]
            if len(parts) != 4:
                errors.append({
                    "line": line_no,
                    "message": f"候选组第{line_no}行{side_label}侧：应为 4 个字段（事件甲,事件乙,最少分钟,最多分钟），实际得到 {len(parts)} 个。",
                })
                break
            a, b, low_s, high_s = parts
            low, high, field_errors = _validate_interval_fields(a, b, low_s, high_s)
            if field_errors:
                errors.append({
                    "line": line_no,
                    "message": f"候选组第{line_no}行{side_label}侧：" + "；".join(field_errors) + "。",
                })
                break
            claims.append(Claim(line_no, side_label, a, b, low, high))  # type: ignore[arg-type]
        else:
            groups.append(claims)  # 两侧均有效才成组（for 正常结束、未 break）

    if non_empty > MAX_GROUP_LINES:
        errors.append({
            "line": None,
            "message": f"候选组最多 {MAX_GROUP_LINES} 行，实际得到 {non_empty} 行（每行二选一，组合数为 2 的幂，限制行数以保证枚举耗时可控）。",
        })

    return groups, errors


def solve_precedence(text: str, groups_text: str, qa: str, qb: str) -> dict:
    """互斥说法下的先后查询：共同约束 + 每组二选一的全部组合枚举联立。

    共同约束沿用事件区间文本；候选组每行 “区间A | 区间B” 表示两条只能选一条的说法。
    枚举全部 2^N 个组合（N ≤ MAX_GROUP_LINES），每个组合 = 共同约束 + 各组所选一侧，
    复用同一套差分约束 Bellman-Ford 联立：存在负环的组合判为冲突并排除；对其余可行
    组合复用冗余核对同款 _derive_bound 求 t(乙) − t(甲) 的最紧下界/上界（推不出的
    端点为无界）。跨组合合并：下界取各组合最小值、上界取各组合最大值，任一组合对应
    端无界则合并端无界。

    判定：无可行组合 ⇒ 不作先后判断（no_feasible）；合并下界 ≥ 1 分钟 ⇒ 甲始终先于乙
    （before）；合并上界 ≤ −1 分钟 ⇒ 甲始终后于乙（after）；其余 ⇒ 不确定（undetermined）。
    查询的甲、乙必须出现在共同约束中。
    """
    qa, qb = (qa or "").strip(), (qb or "").strip()

    rows, errors = parse_text(text)  # 共同约束复用现有区间校验（含空文本报错）
    groups, group_errors = parse_groups(groups_text)
    errors = errors + group_errors

    # 查询的甲、乙须出现在共同约束中
    if not qa:
        errors.append({"line": None, "message": "查询的事件甲为空：请填写要查询先后关系的事件甲。"})
    if not qb:
        errors.append({"line": None, "message": "查询的事件乙为空：请填写要查询先后关系的事件乙。"})
    if qa and qb and qa == qb:
        errors.append({"line": None, "message": f"查询的事件甲、乙相同（“{qa}”）：请查询两个不同事件的先后关系。"})
    if rows:
        common_names = {r.a for r in rows} | {r.b for r in rows}
        for label, name in (("甲", qa), ("乙", qb)):
            if name and name not in common_names:
                errors.append({
                    "line": None,
                    "message": f"查询的事件{label}“{name}”未出现在共同约束中：先后查询的甲、乙须出现在共同约束中。",
                })
    if errors:
        return {"status": "error", "errors": errors}

    combinations: list[dict] = []
    feasible_count = 0
    merged_low: int | None = None   # 跨组合最小下界（只统计有界端点）
    merged_high: int | None = None  # 跨组合最大上界（只统计有界端点）
    lower_unbounded = False  # 任一可行组合下界无界 ⇒ 合并下界无界
    upper_unbounded = False  # 任一可行组合上界无界 ⇒ 合并上界无界

    for combo_index, picks in enumerate(product(*groups), start=1):
        # 该组合 = 共同约束 + 各组所选一侧，联立（同一套建边与 Bellman-Ford）
        combo_rows = rows + [Row(c.line, c.a, c.b, c.low, c.high) for c in picks]
        names, index, edges = _build_graph(combo_rows)
        _, _, last_relaxed = _relax_all(names, edges)
        pick_desc = [
            {"line": c.line, "side": c.side, "a": c.a, "b": c.b, "low": c.low, "high": c.high}
            for c in picks
        ]
        if last_relaxed is not None:
            combinations.append({
                "index": combo_index,
                "picks": pick_desc,
                "feasible": False,
                "low": None,
                "high": None,
                "lower_bounded": False,
                "upper_bounded": False,
            })
            continue

        ia, ib = index[qa], index[qb]
        # 上界：t(乙) − t(甲) 的最紧上界 = 甲 -> 乙 最短路；下界 = 乙 -> 甲 最短路取负
        high_val, _ = _derive_bound(names, edges, ia, ib)
        low_val, _ = _derive_bound(names, edges, ib, ia)
        low = -low_val if low_val is not None else None
        high = high_val

        feasible_count += 1
        if low is None:
            lower_unbounded = True
        else:
            merged_low = low if merged_low is None else min(merged_low, low)
        if high is None:
            upper_unbounded = True
        else:
            merged_high = high if merged_high is None else max(merged_high, high)
        combinations.append({
            "index": combo_index,
            "picks": pick_desc,
            "feasible": True,
            "low": low,
            "high": high,
            "lower_bounded": low is not None,
            "upper_bounded": high is not None,
        })

    conflict_count = len(combinations) - feasible_count
    if feasible_count == 0:
        # 无可行组合：不作先后判断，合并区间无意义
        verdict = "no_feasible"
        lower_bounded = upper_bounded = False
        merged_low = merged_high = None
    else:
        lower_bounded = not lower_unbounded
        upper_bounded = not upper_unbounded
        if lower_bounded and merged_low is not None and merged_low >= 1:
            verdict = "before"
        elif upper_bounded and merged_high is not None and merged_high <= -1:
            verdict = "after"
        else:
            verdict = "undetermined"

    return {
        "status": "ok",
        "a": qa,
        "b": qb,
        "groups": [
            {
                "line": g[0].line,
                "sides": [
                    {"side": c.side, "a": c.a, "b": c.b, "low": c.low, "high": c.high}
                    for c in g
                ],
            }
            for g in groups
        ],
        "group_count": len(groups),
        "combination_count": len(combinations),
        "feasible_count": feasible_count,
        "conflict_count": conflict_count,
        "lower_bounded": lower_bounded,
        "upper_bounded": upper_bounded,
        "merged_low": merged_low if lower_bounded else None,
        "merged_high": merged_high if upper_bounded else None,
        "verdict": verdict,
        "verdict_label": _VERDICT_LABELS[verdict],
        "combinations": combinations,
        "event_count": len({r.a for r in rows} | {r.b for r in rows}),
        "line_count": len(rows),
    }


# ---------- 最少撤回 ----------

# 撤回搜索允许的最多非空行：枚举 C(n,k) 个子集做 Bellman-Ford，限 10 条保证耗时可控
MAX_WITHDRAWAL_LINES = 10


def find_minimum_withdrawals(text: str) -> dict:
    """最少撤回：按撤回行数递增搜索使剩余约束可行的最少原始行号组合。

    复用 parse_text 的逐行校验（语法错误逐行返回）与同一套差分约束 Bellman-Ford
    （solve_rows/_format_ok）。非空行最多 MAX_WITHDRAWAL_LINES 条，超限明确报错
    （以实际非空行数提示，行号按原始文本计算）。

    空约束集（空文本/只有空行）视为可行，直接返回零撤回方案与空时间线；原文本身
    可行时也返回零撤回方案。否则按 k=1,2,… 枚举 C(n,k) 个撤回组合，找到第一个存在
    可行剩余子集的 k 即停止（撤回数最少），返回该层全部可行组合；每个方案附剩余
    约束的一条规范化可行时间线（最早事件为第 0 分钟），撤回全部行时时间线为空。
    """
    # 空文本/只有空行：空约束集视为可行（parse_text 对空文本会报错，这里单独处理）
    if not text or not text.strip():
        return {
            "status": "ok",
            "minimum": 0,
            "total_lines": 0,
            "checked_combinations": 0,
            "message": "没有任何非空约束：空约束集视为可行，无需撤回任何行（空时间线）。",
            "original_feasible": True,
            "plans": [
                {
                    "index": 1,
                    "withdraw_count": 0,
                    "withdrawn_lines": [],
                    "remaining_count": 0,
                    "timeline": [],
                }
            ],
        }

    # 超限检查以非空行为准（与候选组限制同款口径：空行不计入、行号保留）
    non_empty = sum(1 for raw in text.splitlines() if raw.strip())
    rows, errors = parse_text(text)
    if non_empty > MAX_WITHDRAWAL_LINES:
        errors = errors + [{
            "line": None,
            "message": (
                f"最少撤回最多处理 {MAX_WITHDRAWAL_LINES} 条非空约束，"
                f"实际得到 {non_empty} 条（搜索按撤回行数枚举全部行号组合，"
                "组合数随行数快速增长，限 10 条以保证耗时可控）。"
            ),
        }]
    if errors:
        return {"status": "error", "errors": errors}

    n = len(rows)
    lines = [r.line for r in rows]

    # k=0：先试原文是否已可行（此时方案为零撤回，时间线即原文规范化可行解）
    original = solve_rows(rows)
    checked = 1
    if original["status"] == "ok":
        return {
            "status": "ok",
            "minimum": 0,
            "total_lines": n,
            "checked_combinations": checked,
            "message": "原文区间无冲突，无需撤回任何行；以下为原文的一条可行时间线。",
            "original_feasible": True,
            "plans": [{
                "index": 1,
                "withdraw_count": 0,
                "withdrawn_lines": [],
                "remaining_count": n,
                "timeline": _format_ok(original)["timeline"],
            }],
        }

    # 原文矛盾：按撤回行数 k 递增枚举 C(n,k) 个原始行号组合，第一个有可行方案的 k 最少
    original_conflict = original["conflict"]
    for k in range(1, n + 1):
        plans: list[dict] = []
        for combo in combinations(range(n), k):
            checked += 1
            withdrawn = {lines[i] for i in combo}
            remaining = [r for r in rows if r.line not in withdrawn]
            system = solve_rows(remaining)
            if system["status"] != "ok":
                continue  # 剩余约束仍矛盾：该撤回组合不能消除冲突
            plans.append({
                "withdrawn_lines": sorted(withdrawn),
                "remaining_count": len(remaining),
                "timeline": _format_ok(system)["timeline"],
            })
        if plans:
            # 组合按原始行号升序枚举（combinations 保序），方案直接编号
            for i, p in enumerate(plans, start=1):
                p["index"] = i
                p["withdraw_count"] = k
            if k == n:
                message = (
                    f"原 {n} 条约束互相冲突，最少需要撤回全部 {n} 行；撤回后为空约束集，"
                    "视为可行（空时间线）。"
                )
            else:
                message = (
                    f"原 {n} 条约束互相冲突，最少撤回 {k} 行即可使剩余约束可行；"
                    f"共找到 {len(plans)} 个最少撤回方案，每个方案附剩余约束的一条可行时间线。"
                )
            return {
                "status": "ok",
                "minimum": k,
                "total_lines": n,
                "checked_combinations": checked,
                "message": message,
                "original_feasible": False,
                "conflict": original_conflict,
                "plans": plans,
            }

    # 理论上不可达：撤回全部行后空约束集必可行
    return {"status": "error", "errors": [{"line": None, "message": "搜索失败：未找到任何可行的撤回方案。"}]}
