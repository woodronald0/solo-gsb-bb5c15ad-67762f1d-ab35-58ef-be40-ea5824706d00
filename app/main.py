"""FastAPI 入口：提供静态页面与 /api/solve 联立求解、/api/redundancy 约束冗余核对、
/api/compare 两版比较、/api/repair 冲突单行修订建议、/api/anchor 绝对时间锚定、
/api/precedence 互斥说法下的先后查询接口。"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .solver import (
    check_redundancy,
    compare_texts,
    solve,
    solve_anchored,
    solve_precedence,
    suggest_repairs,
)

app = FastAPI(title="口述史事件时间线核对", version="1.5.0")


class SolveRequest(BaseModel):
    text: str = ""


class RedundancyRequest(BaseModel):
    text: str = ""
    line: int = 0


class CompareRequest(BaseModel):
    base: str = ""
    revised: str = ""


class AnchorRequest(BaseModel):
    text: str = ""
    anchors: str = ""


class PrecedenceRequest(BaseModel):
    text: str = ""
    groups: str = ""
    a: str = ""
    b: str = ""


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/api/solve")
def api_solve(req: SolveRequest) -> dict:
    return solve(req.text or "")


@app.post("/api/redundancy")
def api_redundancy(req: RedundancyRequest) -> dict:
    return check_redundancy(req.text or "", req.line)


@app.post("/api/compare")
def api_compare(req: CompareRequest) -> dict:
    # 两版各自独立校验与联立；任一版无效或矛盾时，分别带回该版错误/矛盾链，不生成差异结论
    return compare_texts(req.base or "", req.revised or "")


@app.post("/api/repair")
def api_repair(req: SolveRequest) -> dict:
    # 冲突单行修订建议：复用现有解析与求解；输入错误返回逐行错误，原文本可解时说明无需修订
    return suggest_repairs(req.text or "")


@app.post("/api/anchor")
def api_anchor(req: AnchorRequest) -> dict:
    # 绝对时间锚定：区间文本与锚点文本（事件名,YYYY-MM-DD HH:MM，无时区本地时间）联立；
    # 冲突时返回含区间行号与锚点行号的闭合矛盾链，可解时只定位与锚点连通的事件
    return solve_anchored(req.text or "", req.anchors or "")


@app.post("/api/precedence")
def api_precedence(req: PrecedenceRequest) -> dict:
    # 互斥说法下的先后查询：区间文本作共同约束，候选组每行“区间A | 区间B”二选一；
    # 枚举全部组合联立、排除冲突组合，跨组合合并 t(乙)-t(甲) 最紧界后判定先后
    return solve_precedence(req.text or "", req.groups or "", req.a or "", req.b or "")


@app.get("/")
def index() -> FileResponse:
    return FileResponse("static/index.html")


# 静态资源挂载放在最后（Starlette 中 Route 优先于 Mount 匹配，此处仅为可读性）
app.mount("/static", StaticFiles(directory="static"), name="static")
