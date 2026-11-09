"""Web-STEP-Aligner · app.py

One page. Drop a STEP file, pose it, download it.

    python app.py            ->  http://127.0.0.1:8052

The geometry work lives in aligner_core.py; this file is UI + HTTP routes.
"""
from __future__ import annotations

import base64
import io
import json
import os
import sys
import time
import traceback
import urllib.parse

import numpy as np
import plotly.graph_objects as go
from dash import Dash, Input, Output, State, callback_context, dcc, html, no_update
from flask import jsonify, request, send_file

import aligner_core as ac

APP_VERSION = "0.2.0"
HERE = os.path.dirname(os.path.abspath(__file__))
WORKDIR = os.environ.get("ALIGNER_WORKDIR", os.path.join(HERE, "jobs"))
MAX_MB = float(os.environ.get("ALIGNER_MAX_MB", "200"))
PORT = int(os.environ.get("ALIGNER_PORT", "8052"))
os.makedirs(WORKDIR, exist_ok=True)

CYAN = "#00e5ff"
MAGENTA = "#ff2d95"
VIOLET = "#9d00ff"
INK = "#05060a"
PANEL = "rgba(14,17,28,0.72)"
TEXT = "#cfd8e3"
DIM = "#6c7a8a"
GREEN = "#39ff88"

KERNEL_LABEL = "cascadio / OpenCASCADE" if ac.HAVE_CASCADIO else "MISSING"


def _occ_version() -> str:
    return KERNEL_LABEL


def _badge_text() -> str:
    if not ac.HAVE_CASCADIO:
        return "预览内核缺失 · pip install cascadio"
    return "OpenCASCADE · AP203/214/242 · 纯本地"


# ======================================================================================
# Figure construction
# ======================================================================================
def _mesh3d(mesh: ac.Mesh, color: str, opacity: float = 1.0, highlight_faces: set[int] | None = None,
            name: str = "solid") -> go.Mesh3d:
    V, T = mesh.V, mesh.T
    if highlight_faces:
        mask = np.isin(mesh.tri_face, list(highlight_faces))
        if mask.any():
            T = T[mask]
            V = V[np.unique(T)]
            remap = {int(old): i for i, old in enumerate(np.unique(T.reshape(-1)))}
            T = np.vectorize(remap.get)(T)
    return go.Mesh3d(
        x=V[:, 0].tolist(), y=V[:, 1].tolist(), z=V[:, 2].tolist(),
        i=T[:, 0].tolist(), j=T[:, 1].tolist(), k=T[:, 2].tolist(),
        color=color, opacity=opacity, flatshading=True, name=name, showlegend=False,
        lighting=dict(ambient=0.42, diffuse=0.85, specular=0.28, roughness=0.55, fresnel=0.14),
        lightposition=dict(x=120, y=-180, z=220),
        hoverinfo="none",
    )


def _lines(pts: np.ndarray, color: str, width: int = 3, name: str = "", dash: str | None = None,
           opacity: float = 1.0) -> go.Scatter3d:
    xs, ys, zs = [], [], []
    for seg in pts:
        xs += [float(seg[0][0]), float(seg[1][0]), None]
        ys += [float(seg[0][1]), float(seg[1][1]), None]
        zs += [float(seg[0][2]), float(seg[1][2]), None]
    return go.Scatter3d(x=xs, y=ys, z=zs, mode="lines", name=name, showlegend=False,
                        line=dict(color=color, width=width, dash=dash or "solid"),
                        opacity=opacity, hoverinfo="none")


def _bbox_wire(bb: dict, color: str = "rgba(0,229,255,0.35)") -> go.Scatter3d:
    lo, hi = bb["min"], bb["max"]
    corners = [[(lo[0], lo[1], lo[2]), (hi[0], lo[1], lo[2])],
               [(hi[0], lo[1], lo[2]), (hi[0], hi[1], lo[2])],
               [(hi[0], hi[1], lo[2]), (lo[0], hi[1], lo[2])],
               [(lo[0], hi[1], lo[2]), (lo[0], lo[1], lo[2])],
               [(lo[0], lo[1], hi[2]), (hi[0], lo[1], hi[2])],
               [(hi[0], lo[1], hi[2]), (hi[0], hi[1], hi[2])],
               [(hi[0], hi[1], hi[2]), (lo[0], hi[1], hi[2])],
               [(lo[0], hi[1], hi[2]), (lo[0], lo[1], hi[2])],
               [(lo[0], lo[1], lo[2]), (lo[0], lo[1], hi[2])],
               [(hi[0], lo[1], lo[2]), (hi[0], lo[1], hi[2])],
               [(hi[0], hi[1], lo[2]), (hi[0], hi[1], hi[2])],
               [(lo[0], hi[1], lo[2]), (lo[0], hi[1], hi[2])]]
    return _lines(np.array(corners, dtype=float), color, width=2, dash="dot")


def build_figure(mesh: ac.Mesh, highlight: set[int] | None = None,
                 from_face: int | None = None, to_face: int | None = None) -> go.Figure:
    bb = mesh.bbox()
    fig = go.Figure()
    fig.add_trace(_mesh3d(mesh, "#12525f", 1.0))
    if mesh.edges is not None and len(mesh.edges):
        fig.add_trace(_lines(mesh.edges, CYAN, width=2))
    if from_face is not None:
        fig.add_trace(_mesh3d(mesh, MAGENTA, 0.92, {from_face}, name="from"))
    if to_face is not None:
        fig.add_trace(_mesh3d(mesh, VIOLET, 0.92, {to_face}, name="to"))
    if highlight:
        fig.add_trace(_mesh3d(mesh, "#ffd400", 0.85, set(highlight), name="hl"))
    fig.add_trace(_bbox_wire(bb))

    cen = np.array(bb["center"])
    size = np.maximum(np.array(bb["size"]), 1e-6)
    # v2.1（Bug 1 · 模型视觉拉伸）：旧代码三轴都用 half = 0.78·最长边 → 三轴跨度相等，
    # 再配合 aspectmode="data"，等于把 131×57×44 的勺子画成 1:1:1 的方块（就是「被拉长/压扁」）。
    # 现在：每轴跨度 = 自身尺寸 + 6% 余量（最薄轴至少留 2% 最长边，避免薄片压成一条线），
    # aspectratio 直接由跨度归一化算出 —— 屏幕上的长宽比恒等于几何长宽比。
    pad = float(size.max()) * 0.06
    floor = float(size.max()) * 0.02
    ranges, spans = [], []
    for i in range(3):
        h = max(float(size[i]) / 2.0 + pad, floor)
        ranges.append([float(cen[i] - h), float(cen[i] + h)])
        spans.append(2.0 * h)
    smax = max(spans) or 1.0
    aspect = {k: max(v / smax, 1e-3) for k, v in zip("xyz", spans)}
    fig.update_layout(
        margin=dict(l=0, r=0, t=0, b=0),
        paper_bgcolor=INK, plot_bgcolor=INK,
        showlegend=False, uirevision="keep",
        font=dict(family="Consolas, 'JetBrains Mono', monospace", color=TEXT, size=12),
        scene=dict(
            bgcolor=INK,
            aspectmode="manual",
            aspectratio=aspect,
            xaxis=dict(range=ranges[0], backgroundcolor=INK, gridcolor="#12232e", zerolinecolor="#1d3b4a",
                       color=DIM, title=dict(text=f"X  [{mesh.unit_label}]", font=dict(size=11, color=CYAN))),
            yaxis=dict(range=ranges[1], backgroundcolor=INK, gridcolor="#12232e", zerolinecolor="#1d3b4a",
                       color=DIM, title=dict(text=f"Y  [{mesh.unit_label}]", font=dict(size=11, color=CYAN))),
            zaxis=dict(range=ranges[2], backgroundcolor=INK, gridcolor="#12232e", zerolinecolor="#1d3b4a",
                       color=DIM, title=dict(text=f"Z  [{mesh.unit_label}]", font=dict(size=11, color=CYAN))),
            camera=dict(eye=dict(x=1.45, y=1.45, z=1.05)),
        ),
    )
    return fig


# ======================================================================================
# KPI / matrix rendering
# ======================================================================================
def kpi_block(mesh: ac.Mesh, project: ac.Project | None = None) -> list:
    bb = mesh.bbox()
    size = bb["size"]
    vol = mesh.volume()
    cen = bb["center"]
    u = mesh.unit_label
    face_count = len({f.get("face_index") for f in mesh.faces}) or len(mesh.faces)
    items = [
        ("包围盒 X×Y×Z", f"{size[0]:.3f} × {size[1]:.3f} × {size[2]:.3f} {u}"),
        ("体积", f"{vol:,.1f} {u}³"),
        ("包围盒中心", f"({cen[0]:+.3f}, {cen[1]:+.3f}, {cen[2]:+.3f})"),
        ("BREP 面 / 三角片", f"{face_count} / {len(mesh.T):,}"),
    ]
    if project is not None:
        items.append(("文件", project.name))
    out = []
    for k, v in items:
        cls = "kpi kpi-center" if k == "包围盒中心" else "kpi"
        out.append(html.Div([html.Div(k, className="kpi-k"), html.Div(v, className="kpi-v")],
                            className=cls))
    return out


def matrix_block(tf: ac.Transform, unit: str) -> list:
    m = tf.matrix4()
    rows = []
    for r in m:
        rows.append(html.Div("  ".join(f"{v:+11.5f}" for v in r), className="mx-row"))
    return [
        html.Div("变换矩阵  p' = M · p", className="mx-title"),
        html.Div(rows, className="mx"),
        html.Div(f"scale = {tf.s:.6g}   |   |t| = {float(np.linalg.norm(tf.t)):.4f} {unit}",
                 className="mx-foot"),
    ]


def alert(msg: str, kind: str = "info") -> html.Div:
    return html.Div(msg, className=f"alert alert-{kind}")


def _slider_row(id_: str, label: str, lo: float, hi: float, step: float,
                val: float, unit: str = "") -> html.Div:
    """小白模式的一行滑块：标签 + range + 实时数值（数值由 clientside 回调回填）。"""
    # Dash 4 已经没有 html.Input（4.x 移除了整套 html 表单标签）→ 用 dcc.Slider（Radix）。
    # updatemode="mouseup"：松手才写 value，配合下面的 clientside 防抖 = 拖动不卡。
    return html.Div([
        html.Span(label, className="sl-k"),
        dcc.Slider(id=id_, min=lo, max=hi, step=step, value=val, marks=None,
                   allow_direct_input=True, updatemode="mouseup",
                   tooltip={"always_visible": False, "placement": "top"},
                   className="sl"),
        html.Span(f"{val}{unit}", id=f"{id_}_v", className="sl-v"),
    ], className="sl-row")


def _rot_row(id_: str, label: str) -> html.Div:
    """旋转一行：标签 + 滑块 + 精确数字框（三者由 clientside 回调双向联动）。"""
    return html.Div([
        html.Span(label, className="sl-k"),
        dcc.Slider(id=id_, min=-180, max=180, step=1, value=0, marks=None,
                   allow_direct_input=True, updatemode="mouseup",
                   tooltip={"always_visible": False, "placement": "top"},
                   className="sl"),
        dcc.Input(id=f"{id_}_n", type="number", step=1, value=0, debounce=True,
                  className="sl-num"),
    ], className="sl-row rot")


# ======================================================================================
# App
# ======================================================================================
app = Dash(__name__, title="Web-STEP-Aligner", update_title=None, suppress_callback_exceptions=True)
server = app.server
app.layout = html.Div([
    dcc.Store(id="sid"),
    dcc.Store(id="sbase"),      # 小白模式的基准位姿（滑块的坐标系原点）
    dcc.Store(id="sdeb"),       # 滑块防抖后的值
    dcc.Store(id="rot_mirror"), # 旋转滑块 → 数字框 的镜像通道（不用 Output 直接写，避免回调成环）
    dcc.Download(id="dl"),
    html.Div([
        html.Div([
            html.Span("WEB·STEP·ALIGNER", className="brand"),
            html.Span(f"v{APP_VERSION}", className="brand-ver"),
        ], className="brand-wrap"),
        html.Div([
            html.Span("●", className="dot"),
            html.Span(_badge_text(), className="badge-text"),
        ], className="badge"),
        html.Div([
            dcc.Checklist(id="expert", className="expert-switch", value=[],
                          options=[{"label": "专家模式", "value": "on"}]),
        ], className="expert-wrap"),
        html.Div(f"127.0.0.1:{PORT}", className="port"),
    ], className="topbar"),

    html.Div([
        # ---------------- viewport ----------------
        html.Div([
            dcc.Graph(id="view", figure=go.Figure(layout=dict(paper_bgcolor=INK, plot_bgcolor=INK,
                                                             margin=dict(l=0, r=0, t=0, b=0))),
                      config={"displaylogo": False, "scrollZoom": True,
                              "modeBarButtonsToRemove": ["toImage", "resetCameraLastSave3d"]},
                      className="graph"),
            html.Div("只需点击「一键摆正」，或拖动滑块微调即可",
                     id="simple_hint", className="simple-hint"),
            html.Div(id="kpis", className="kpis"),
            html.Div(id="matrix", className="matrix"),
            dcc.Upload(id="upload", className="dropzone",
                       children=html.Div([
                           html.Div("⬇  拖入 STEP / STP 文件  （或点击选择）", className="dz-main"),
                           html.Div(f"AP203 / AP214 / AP242 · 上限 {MAX_MB:.0f} MB · 文件不离开本机",
                                    className="dz-sub"),
                       ]),
                       multiple=False),
            html.Div([
                html.Button("载入示例 · 方块", id="btn_sample_box", className="btn ghost small"),
                html.Button("载入示例 · L 支架", id="btn_sample_l", className="btn ghost small"),
            ], className="sample-row"),
        ], className="viewport"),

        # ---------------- control panel ----------------
        html.Div([
            html.Div("位姿控制", className="panel-title"),

            # ==================== 小白模式（默认显示） ====================
            html.Div([
                # 悬停提示：黑底白字，用 CSS ::after 画，不需要 JS
                html.Div(html.Button("◈ 一键摆正（自动）", id="btn_auto_simple",
                                     className="btn primary big"),
                         className="tip",
                         **{"data-tip": "将自动计算零件的主惯性轴，把零件平稳地放置在坐标系正中心。"}),
                # 灰色小字：把 PCA 的原理翻译成人话
                html.Div("⚙ 摆正原理：最长边对齐 X 轴，最薄面平放于 XY 平面（Z 轴朝上），中心点归零。",
                         className="hint"),

                html.Div("平移 · X / Y / Z", className="field-label"),
                _slider_row("px", "X", -100, 100, 1, 0, "%"),
                _slider_row("py", "Y", -100, 100, 1, 0, "%"),
                _slider_row("pz", "Z", -100, 100, 1, 0, "%"),
                html.Div("100 % = 半个包围盒尺寸", className="hint"),

                html.Div("旋转 · X / Y / Z  (° · 可输入任意角度)", className="field-label"),
                _rot_row("srx", "Rx"),
                _rot_row("sry", "Ry"),
                _rot_row("srz", "Rz"),

                html.Div("增量旋转 90°", className="hint", style={"margin": "9px 0 5px"}),
                html.Div([
                    html.Button("X +90°", id="q_x90", className="btn mini", n_clicks=0),
                    html.Button("Y +90°", id="q_y90", className="btn mini", n_clicks=0),
                    html.Button("Z +90°", id="q_z90", className="btn mini", n_clicks=0),
                ], className="qgrid"),
                html.Div("增量翻转 180°", className="hint", style={"margin": "0 0 5px"}),
                html.Div([
                    html.Button("X +180°", id="q_x180", className="btn mini", n_clicks=0),
                    html.Button("Y +180°", id="q_y180", className="btn mini", n_clicks=0),
                    html.Button("Z +180°", id="q_z180", className="btn mini", n_clicks=0),
                ], className="qgrid"),
                html.Button("⟲ 归零  (Reset Rotation)", id="q_reset",
                            className="btn ghost wide", n_clicks=0),
                html.Div("超出 ±180° 会按 360° 求余归一化（450° → 90°，200° → -160°），"
                         "保证底层变换矩阵永远在安全范围。", className="hint"),

                html.Div("等比缩放", className="field-label"),
                _slider_row("ssc", "S", 20, 400, 1, 100, "%"),
                html.Div("绕包围盒中心旋转/缩放，滑块松手才生效（防抖 180 ms）。",
                         className="hint"),
            ], id="panel_simple"),

            # ==================== 专家模式（勾选后显示） ====================
            html.Div([
            html.Div("对齐模式", className="field-label"),
            dcc.RadioItems(id="mode", className="modes",
                           options=[{"label": "主惯性 / 最小包围盒", "value": "principal"},
                                    {"label": "面 → 面 贴合", "value": "face"},
                                    {"label": "单面 → 轴向", "value": "axis"},
                                    {"label": "欧拉角 / 平移 / 缩放", "value": "euler"},
                                    {"label": "仅居中", "value": "center"}],
                           value="principal", labelClassName="mode-item",
                           inputClassName="mode-radio"),

            html.Div([
                html.Button("▸ 自动摆正（一键）", id="btn_auto", className="btn primary big"),
                html.Div("PCA + 最小包围盒搜索 → 最长轴对 X，最薄轴对 Z，包围盒中心归零。",
                         className="hint"),
            ], id="box_principal", className="mode-box"),

            html.Div([
                html.Div("贴合面（源 → 目标，法向相对）", className="field-label"),
                dcc.Dropdown(id="dd_from", className="dd", placeholder="源面 …"),
                dcc.Dropdown(id="dd_to", className="dd", placeholder="目标面 …"),
                html.Button("▸ 面贴合", id="btn_snap", className="btn primary"),
                html.Div("把源面的法向转到与目标面法向相对，再把两个平面贴到一起。", className="hint"),
            ], id="box_face", className="mode-box", style={"display": "none"}),

            html.Div([
                html.Div("把面摆到坐标轴", className="field-label"),
                dcc.Dropdown(id="dd_face_axis", className="dd", placeholder="选一个平面 …"),
                dcc.Dropdown(id="dd_axis", className="dd", clearable=False,
                             options=[{"label": f"法向落到 {a}-Z 面", "value": a} for a in "XYZ"],
                             value="Z"),
                html.Button("▸ 贴到基准面", id="btn_axis", className="btn primary"),
                html.Div("仿 3-2-1 基准：选定面的法向对齐到 -轴向，再落到该轴向的 0 平面。",
                         className="hint"),
            ], id="box_axis", className="mode-box", style={"display": "none"}),

            html.Div([
                html.Div("欧拉角 (°)", className="field-label"),
                html.Div([
                    dcc.Input(id="rx", type="number", value=0, step="any", className="num", placeholder="rx"),
                    dcc.Input(id="ry", type="number", value=0, step="any", className="num", placeholder="ry"),
                    dcc.Input(id="rz", type="number", value=0, step="any", className="num", placeholder="rz"),
                ], className="num-row"),
                dcc.Dropdown(id="order", className="dd", clearable=False,
                             options=[{"label": "顺序 XYZ", "value": "XYZ"},
                                      {"label": "顺序 ZYX", "value": "ZYX"}], value="XYZ"),
                html.Div("平移", className="field-label"),
                html.Div([
                    dcc.Input(id="tx", type="number", value=0, step="any", className="num", placeholder="tx"),
                    dcc.Input(id="ty", type="number", value=0, step="any", className="num", placeholder="ty"),
                    dcc.Input(id="tz", type="number", value=0, step="any", className="num", placeholder="tz"),
                ], className="num-row"),
                html.Div("等比缩放", className="field-label"),
                dcc.Input(id="scale", type="number", value=1.0, step="any", className="num wide"),
                html.Button("▸ 应用绝对位姿", id="btn_euler", className="btn primary"),
                html.Div("1.0 保持；25.4 = in→mm；0.001 = mm→m。等比缩放会同步缩放半径。",
                         className="hint"),
            ], id="box_euler", className="mode-box", style={"display": "none"}),

            html.Div([
                html.Div("居中方式", className="field-label"),
                dcc.RadioItems(id="center_mode", className="modes",
                               options=[{"label": "包围盒中心", "value": "bbox"},
                                        {"label": "质心（面积加权）", "value": "mass"}],
                               value="bbox", labelClassName="mode-item", inputClassName="mode-radio"),
                html.Button("▸ 归零", id="btn_center", className="btn primary"),
            ], id="box_center", className="mode-box", style={"display": "none"}),

            html.Div(className="divider"),
            html.Div("高亮 / 拾取", className="field-label"),
            dcc.Dropdown(id="dd_hl", className="dd", placeholder="高亮某个平面 …"),
            ], id="panel_expert"),

            html.Div(className="divider"),
            html.Div([
                html.Button("⤓ 下载 STEP", id="btn_dl_step", className="btn download"),
                html.Button("⤓ 下载回执 JSON", id="btn_dl_rcpt", className="btn ghost"),
            ], className="dl-row"),
            html.Button("↺ 撤销全部变换（复位）", id="btn_reset", className="btn ghost wide"),
            html.Div(id="alert", className="alert-zone"),
            html.Div("用法：上传 → 自动摆正 → 需要时微调 → 下载。导出的 STEP 是把变换烘进几何实体的，"
                     "下游 CAM / CAE 不会忽略。", className="footnote"),
        ], className="panel"),
    ], className="main"),
], id="root", className="root simple")


# ======================================================================================
# callback
# ======================================================================================
PARSE_FAIL = "❌ 模型解析失败：几何体过于复杂或包含破面"


def _parse_error_text(exc: BaseException) -> str:
    """把所有「解析不出来」的异常压成一句能让用户看懂、能截图发给别人的红字。"""
    if isinstance(exc, getattr(ac, "TessellationError", ())):
        return f"{PARSE_FAIL} —— {exc}"
    return f"{PARSE_FAIL} —— {type(exc).__name__}: {exc}"


def _load_project(sid: str) -> ac.Project:
    return ac.Project.open(WORKDIR, sid)


def _sample_payload(path: str) -> tuple[str, bytes]:
    with open(path, "rb") as f:
        return os.path.basename(path), f.read()


@app.callback(
    Output("sid", "data"), Output("view", "figure"), Output("kpis", "children"),
    Output("matrix", "children"), Output("alert", "children"),
    Output("dd_from", "options"), Output("dd_to", "options"), Output("dd_hl", "options"),
    Output("dd_face_axis", "options"), Output("dl", "data"),
    Input("upload", "contents"), Input("btn_sample_box", "n_clicks"), Input("btn_sample_l", "n_clicks"),
    Input("mode", "value"), Input("btn_auto", "n_clicks"),
    Input("btn_snap", "n_clicks"), Input("btn_axis", "n_clicks"),
    Input("btn_euler", "n_clicks"), Input("btn_center", "n_clicks"), Input("btn_reset", "n_clicks"),
    Input("btn_dl_step", "n_clicks"), Input("btn_dl_rcpt", "n_clicks"),
    Input("dd_hl", "value"), Input("dd_from", "value"), Input("dd_to", "value"),
    Input("dd_face_axis", "value"),
    State("upload", "filename"), State("sid", "data"),
    State("rx", "value"), State("ry", "value"), State("rz", "value"), State("order", "value"),
    State("tx", "value"), State("ty", "value"), State("tz", "value"), State("scale", "value"),
    State("center_mode", "value"),
    prevent_initial_call=True,
)
def dispatch(upload_contents, n_box, n_l, mode, n_auto, n_snap, n_axis, n_euler, n_center, n_reset,
             n_dl_step, n_dl_rcpt, hl_value, from_value, to_value, face_axis_value,
             upload_name, sid, rx, ry, rz, order, tx, ty, tz, scale, center_mode):
    trig = callback_context.triggered_id

    def out(fig=no_update, kpis=no_update, mx=no_update, al=no_update, st=no_update,
            opts=None, dl=no_update):
        o = [st, fig, kpis, mx, al]
        if opts is None:
            o += [no_update, no_update, no_update, no_update]
        else:
            o += [opts["from"], opts["to"], opts["hl"], opts["axis"]]
        o.append(dl)
        return o

    # ---- upload / samples -------------------------------------------------------
    if trig in ("upload", "btn_sample_box", "btn_sample_l"):
        try:
            if trig == "upload":
                if not upload_contents:
                    return out(al=alert("没有读到文件内容", "err"))
                _, b64 = upload_contents.split(",", 1)
                payload = base64.b64decode(b64)
                name = upload_name or "model.step"
            else:
                name, payload = _sample_payload(
                    os.path.join(HERE, "samples", "box.stp" if trig == "btn_sample_box" else "lbracket.stp"))
            if len(payload) > MAX_MB * 1024 * 1024:
                return out(al=alert(f"文件超过 {MAX_MB:.0f} MB 上限", "err"))
            t0 = time.time()
            p = ac.Project.create(WORKDIR, name, payload)
            dt = time.time() - t0
            opts = _face_options(p.mesh)
            return out(fig=build_figure(p.mesh), kpis=kpi_block(p.mesh, p),
                       mx=matrix_block(p.transform, p.mesh.unit_label),
                       al=alert(f"{name} 已载入 · {len(p.mesh.T):,} 三角片 · 解析 {dt:.2f}s · sid {p.sid}", "ok"),
                       st=p.sid, opts=opts)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            return out(al=alert(_parse_error_text(exc), "err"))

    if not sid:
        return out(al=alert("先上传一个 STEP 文件", "warn"))
    try:
        p = _load_project(sid)
    except Exception as exc:  # noqa: BLE001
        return out(al=alert(f"会话已失效（{exc}），请重新上传", "err"), st=None)

    u = p.mesh.unit_label
    msg, kind = no_update, "info"
    world = p.mesh.transformed(p.transform)   # faces for picking live in the CURRENT pose

    # ---- transforms -------------------------------------------------------------
    try:
        if trig == "btn_auto":
            p.set_transform(ac.align_principal(p.mesh), "principal")
            msg, kind = f"✅ 已对齐至最佳视角（最长边 → X，最薄面 → Z）· 包围盒 {_size(p, 'aligned')}", "ok"
        elif trig == "btn_snap":
            if from_value is None or to_value is None:
                msg, kind = "请先选择源面和目标面", "warn"
            else:
                p.apply(ac.snap_faces(world, int(from_value), int(to_value)), "snap")
                msg, kind = "已完成面→面贴合", "ok"
        elif trig == "btn_axis":
            if face_axis_value is None:
                msg, kind = "请先选择一个平面", "warn"
            else:
                p.apply(ac.align_to_axis(world, int(face_axis_value), "Z"), "axis")
                msg, kind = "已把该面贴到基准面", "ok"
        elif trig == "btn_euler":
            def num(v, d=0.0):
                return d if v is None else float(v)
            s = num(scale, 1.0)
            if abs(s) < 1e-9:
                s = 1.0
            R = ac.euler_matrix(num(rx), num(ry), num(rz), order or "XYZ")
            p.set_transform(ac.Transform(R=R, t=np.array([num(tx), num(ty), num(tz)]),
                                         s=s), "euler")
            msg, kind = f"已应用绝对位姿 · R{euler_order(order)} · s={s:g}", "ok"
        elif trig == "btn_center":
            p.apply(ac.align_center(world, center_mode or "bbox"), "center")
            msg, kind = "已居中", "ok"
        elif trig == "btn_reset":
            p.reset()
            msg, kind = "已复位到原始位姿", "info"
        elif trig in ("btn_dl_step", "btn_dl_rcpt"):
            fname = os.path.splitext(p.name)[0] or "model"
            if trig == "btn_dl_step":
                data = p.export_bytes()
                base = os.path.splitext(p.name)[0] or "model"
                return out(dl=dcc.send_bytes(data, f"{base}_aligned.step"))
            else:
                rc = p.receipt()
                return out(dl=dcc.send_string(json.dumps(rc, indent=2, ensure_ascii=False),
                                              f"{fname}_aligned.json"))
        elif trig == "mode":
            msg, kind = "切换对齐模式", "info"
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        msg, kind = f"操作失败：{exc}", "err"

    # ---- render ------------------------------------------------------------------
    try:
        shown = p.mesh.transformed(p.transform)
        hl = {int(hl_value)} if hl_value is not None else None
        ff = int(from_value) if (mode == "face" and from_value is not None) else None
        tf_ = int(to_value) if (mode == "face" and to_value is not None) else None
        fig = build_figure(shown, highlight=hl, from_face=ff, to_face=tf_)
        return out(fig=fig, kpis=kpi_block(shown, p), mx=matrix_block(p.transform, u),
                   al=alert(msg, kind) if isinstance(msg, str) else no_update,
                   st=sid, opts=_face_options(shown))
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        return out(al=alert(f"渲染失败：{exc}", "err"))


def euler_order(o: str | None) -> str:
    return (o or "XYZ").upper()


def _size(p: ac.Project, which: str = "aligned") -> str:
    bb = p.mesh.transformed(p.transform).bbox()["size"]
    return f"{bb[0]:.3f} × {bb[1]:.3f} × {bb[2]:.3f} {p.mesh.unit_label}"


def _face_options(mesh: ac.Mesh) -> dict[str, list[dict]]:
    inv = ac.face_inventory(mesh)
    opts = []
    for f in inv:
        label = f"#{f['id']:>3}  n={f['normal_str']}  {f['size_str']} {mesh.unit_label}   A={f['area']:.1f}"
        opts.append({"label": label, "value": f["id"]})
    return {"from": opts, "to": opts, "hl": opts, "axis": opts}


@app.callback(
    Output("box_principal", "style"), Output("box_face", "style"),
    Output("box_axis", "style"), Output("box_euler", "style"), Output("box_center", "style"),
    Input("mode", "value"),
)
def show_mode(mode):
    def st(m):
        return {"display": "block"} if m == mode else {"display": "none"}
    return st("principal"), st("face"), st("axis"), st("euler"), st("center")


# ======================================================================================
# HTTP API  (same operations the UI performs)
# ======================================================================================
@server.route("/api/health")
def api_health():
    return jsonify(ok=True, version=APP_VERSION, kernel=ac.HAVE_CASCADIO,
                   workdir=WORKDIR, units_per_metre=None)


@server.post("/api/upload")
def api_upload():
    try:
        f = request.files.get("file")
        if f is None:
            return jsonify(ok=False, error="缺少 multipart 字段 file"), 400
        payload = f.read()
        if len(payload) > MAX_MB * 1024 * 1024:
            return jsonify(ok=False, error="文件过大"), 413
        p = ac.Project.create(WORKDIR, f.filename or "model.step", payload)
        return jsonify(ok=True, sid=p.sid, name=p.name, units=p.mesh.unit_label,
                       bbox=p.mesh.bbox(), volume=p.mesh.volume(),
                       faces=len(p.mesh.faces), triangles=int(len(p.mesh.T)))
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        return jsonify(ok=False, error=_parse_error_text(exc)), 500


@server.post("/api/align")
def api_align():
    try:
        body = request.get_json(force=True, silent=True) or {}
        p = _load_project(body.get("sid", ""))
        mode = body.get("mode", "principal")
        params = body.get("params") or {}
        if mode == "principal":
            tf = ac.align_principal(p.mesh)
        elif mode == "center":
            tf = ac.align_center(p.mesh, params.get("center", "bbox"))
        elif mode == "face":
            tf = ac.snap_faces(p.mesh, int(params["from_face"]), int(params["to_face"]))
        elif mode == "axis":
            tf = ac.align_to_axis(p.mesh, int(params["face"]), params.get("axis", "Z"))
        else:
            return jsonify(ok=False, error=f"未知模式 {mode}"), 400
        p.apply(tf, f"api:{mode}")
        m = p.mesh.transformed(p.transform)
        return jsonify(ok=True, bbox=m.bbox(), volume=m.volume(), matrix=p.transform.matrix4())
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        return jsonify(ok=False, error=str(exc)), 500


@server.post("/api/transform")
def api_transform():
    try:
        body = request.get_json(force=True, silent=True) or {}
        p = _load_project(body.get("sid", ""))
        rot = body.get("rotate") or [0, 0, 0]
        tra = body.get("translate") or [0, 0, 0]
        sc = float(body.get("scale", 1.0) or 1.0)
        R = ac.euler_matrix(float(rot[0]), float(rot[1]), float(rot[2]), body.get("order", "XYZ"))
        p.set_transform(ac.Transform(R=R, t=np.array([float(x) for x in tra]), s=sc), "api:transform")
        m = p.mesh.transformed(p.transform)
        return jsonify(ok=True, bbox=m.bbox(), volume=m.volume(), matrix=p.transform.matrix4())
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        return jsonify(ok=False, error=str(exc)), 500


@server.get("/api/mesh")
def api_mesh():
    try:
        p = _load_project(request.args.get("sid", ""))
        m = p.mesh.transformed(p.transform)
        return jsonify(ok=True, units=m.unit_label,
                       x=m.V[:, 0].tolist(), y=m.V[:, 1].tolist(), z=m.V[:, 2].tolist(),
                       i=m.T[:, 0].tolist(), j=m.T[:, 1].tolist(), k=m.T[:, 2].tolist(),
                       edges=m.edges.reshape(-1, 3).tolist() if len(m.edges) else [],
                       bbox=m.bbox(), faces=ac.face_inventory(m))
    except Exception as exc:  # noqa: BLE001
        return jsonify(ok=False, error=str(exc)), 500


@server.get("/api/export")
def api_export():
    try:
        p = _load_project(request.args.get("sid", ""))
        data = p.export_bytes()
        base = os.path.splitext(p.name)[0] or "model"
        return send_file(io.BytesIO(data), mimetype="application/step", as_attachment=True,
                         download_name=f"{base}_aligned.step")
    except Exception as exc:  # noqa: BLE001
        return jsonify(ok=False, error=str(exc)), 500


@server.get("/api/receipt")
def api_receipt():
    try:
        p = _load_project(request.args.get("sid", ""))
        return jsonify(p.receipt())
    except Exception as exc:  # noqa: BLE001
        return jsonify(ok=False, error=str(exc)), 500


@server.get("/api/sessions")
def api_sessions():
    out = []
    for name in sorted(os.listdir(WORKDIR), reverse=True):
        meta = os.path.join(WORKDIR, name, "meta.json")
        if os.path.exists(meta):
            try:
                d = json.load(open(meta, encoding="utf-8"))
                out.append({"sid": name, "name": d.get("name"),
                            "created": d.get("created"), "identity": not d.get("transform", {}).get("translation")})
            except Exception:  # noqa: BLE001
                continue
    return jsonify(ok=True, sessions=out[:100])


# ======================================================================================
# cyberpunk styling
# ======================================================================================
app.index_string = """<!DOCTYPE html>
<html>
<head>
  {%metas%}
  <title>{%title%}</title>
  {%favicon%}
  {%css%}
  <style>
    @import url('https://fonts.googleapis.com/css2?family=Chakra+Petch:wght@400;600;700&family=JetBrains+Mono:wght@400;600&display=swap');
    :root{
      --ink:#05060a; --panel:#0b0e17; --line:#182438; --cy:#00e5ff; --mg:#ff2d95;
      --vi:#9d00ff; --txt:#cfd8e3; --dim:#6c7a8a; --gr:#39ff88;
    }
    *{box-sizing:border-box}
    html,body{margin:0;padding:0;height:100%;overflow:hidden;background:
      radial-gradient(1200px 700px at 15% -10%, #08202b 0%, transparent 60%),
      radial-gradient(900px 600px at 100% 0%, #1b0730 0%, transparent 55%),
      var(--ink);
      color:var(--txt);font-family:'Chakra Petch','Segoe UI',sans-serif;}
    .root{height:100vh;display:flex;flex-direction:column;}
    .topbar{height:60px;display:flex;align-items:center;gap:18px;padding:0 22px;
      border-bottom:1px solid var(--line);
      background:linear-gradient(180deg, rgba(10,16,26,.95), rgba(5,6,10,.65));
      backdrop-filter:blur(6px);}
    .brand-wrap{display:flex;align-items:baseline;gap:10px}
    .brand{font-weight:700;letter-spacing:.22em;font-size:15px;
      background:linear-gradient(90deg,var(--cy),#9df 60%,var(--mg));
      -webkit-background-clip:text;background-clip:text;color:transparent;
      text-shadow:0 0 22px rgba(0,229,255,.28);}
    .brand-ver{font-family:'JetBrains Mono',monospace;font-size:11px;color:var(--dim)}
    .badge{display:flex;align-items:center;gap:8px;padding:5px 12px;border:1px solid rgba(0,229,255,.25);
      border-radius:999px;background:rgba(0,229,255,.06);font-size:11.5px;color:#9fe9f5;
      font-family:'JetBrains Mono',monospace}
    .dot{color:var(--gr);filter:drop-shadow(0 0 6px rgba(57,255,136,.9))}
    .port{margin-left:auto;font-family:'JetBrains Mono',monospace;font-size:11.5px;color:var(--dim)}
    .main{flex:1;display:flex;min-height:0;gap:16px;padding:16px;}
    .viewport{flex:1;min-width:0;display:flex;flex-direction:column;gap:12px;
      border:1px solid var(--line);border-radius:14px;overflow:hidden;
      background:linear-gradient(160deg, rgba(9,14,24,.9), rgba(4,5,9,.9));
      box-shadow:0 0 0 1px rgba(0,229,255,.05), 0 24px 60px -40px rgba(0,229,255,.6) inset;}
    .graph{flex:1;min-height:0}
    .kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(176px,1fr));gap:10px;padding:0 14px}
    .kpi{border:1px solid var(--line);border-radius:10px;padding:9px 11px;
      background:linear-gradient(180deg,rgba(11,20,30,.9),rgba(7,10,16,.9))}
    .kpi-k{font-size:10.5px;letter-spacing:.14em;text-transform:uppercase;color:var(--dim);margin-bottom:4px}
    .kpi-v{font-family:'JetBrains Mono',monospace;font-size:11.5px;color:#e8f6ff;
      white-space:normal;word-break:break-word;line-height:1.45}
    .matrix{margin:0 14px;border:1px dashed rgba(0,229,255,.18);border-radius:10px;padding:9px 12px;
      display:flex;gap:18px;align-items:center;background:rgba(0,229,255,.03)}
    .mx-title{font-size:10.5px;letter-spacing:.16em;color:var(--dim);text-transform:uppercase}
    .mx{font-family:'JetBrains Mono',monospace;font-size:11.5px;color:#b9ecf7;line-height:1.5}
    .mx-row{white-space:pre}
    .mx-foot{margin-left:auto;font-family:'JetBrains Mono',monospace;font-size:11px;color:var(--dim);white-space:nowrap}
    .dropzone{margin:0 14px;border:1px dashed rgba(157,0,255,.45);border-radius:12px;padding:14px;
      text-align:center;cursor:pointer;transition:.18s;
      background:linear-gradient(180deg,rgba(157,0,255,.07),rgba(0,229,255,.04));}
    .dropzone:hover{border-color:var(--cy);box-shadow:0 0 26px -8px rgba(0,229,255,.55)}
    .dz-main{font-size:13px;letter-spacing:.06em;color:#dff6ff}
    .dz-sub{font-size:10.5px;color:var(--dim);margin-top:5px;font-family:'JetBrains Mono',monospace}
    .sample-row{display:flex;gap:8px;padding:0 14px 14px}
    .panel{width:410px;flex:0 0 410px;border:1px solid var(--line);border-radius:14px;
      padding:16px;overflow-y:auto;
      background:linear-gradient(180deg, rgba(16,10,28,.85), rgba(6,8,14,.92));
      box-shadow:0 0 80px -60px var(--vi) inset;}
    .panel::-webkit-scrollbar{width:8px}
    .panel::-webkit-scrollbar-thumb{background:#1d2b3f;border-radius:4px}
    .panel-title{font-size:12px;letter-spacing:.28em;text-transform:uppercase;color:var(--cy);
      margin-bottom:14px;padding-bottom:8px;border-bottom:1px solid var(--line)}
    .field-label{font-size:10.5px;letter-spacing:.16em;text-transform:uppercase;color:var(--dim);
      margin:14px 0 6px}
    .hint{font-size:10.5px;line-height:1.5;color:#5f7186;margin-top:7px}
    .modes{display:flex;flex-direction:column;gap:6px}
    .modes .dash-options-list-option{display:flex;align-items:center;gap:9px;font-size:12.5px;
      padding:8px 10px;border:1px solid var(--line);border-radius:9px;cursor:pointer;
      background:rgba(255,255,255,.015);transition:.15s}
    .modes .dash-options-list-option:hover{border-color:rgba(0,229,255,.45);background:rgba(0,229,255,.05)}
    .modes .dash-options-list-option.selected{border-color:rgba(0,229,255,.75);
      background:linear-gradient(180deg,rgba(0,229,255,.18),rgba(0,229,255,.04));
      box-shadow:0 0 20px -8px rgba(0,229,255,.95)}
    .mode-item{color:#cfe9f5}
    .mode-radio{accent-color:#00e5ff;margin:0;width:13px;height:13px}
    .mode-box{margin-top:12px;padding-top:4px}
    /* ---- dcc.Dropdown (Dash 4 / radix) ---- */
    .dash-dropdown,button.dash-dropdown{width:100%;margin-bottom:8px;text-align:left;
      background:#0a1420 !important;border:1px solid var(--line) !important;border-radius:9px !important;
      color:var(--txt) !important;font-family:'JetBrains Mono',monospace;font-size:11.5px;
      padding:7px 9px;min-height:34px;display:flex;align-items:center;justify-content:space-between;
      cursor:pointer;box-shadow:none !important}
    .dash-dropdown:hover,.dash-dropdown[data-state="open"]{border-color:rgba(0,229,255,.55) !important}
    .dash-dropdown-placeholder{color:#5c6d80 !important}
    .dash-dropdown-value{color:#dcefff !important;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
    .dash-dropdown-content{background:#0a1420 !important;border:1px solid rgba(0,229,255,.35) !important;
      border-radius:10px !important;box-shadow:0 20px 50px -20px rgba(0,229,255,.5) !important;
      z-index:9999;padding:5px !important}
    .dash-dropdown-options .dash-options-list-option{display:flex;align-items:center;gap:6px;
      font-family:'JetBrains Mono',monospace;font-size:11.5px;color:#cfe0ee;padding:6px 8px;
      border-radius:7px;cursor:pointer}
    .dash-dropdown-options .dash-options-list-option:hover,
    .dash-dropdown-options .dash-options-list-option.selected{background:rgba(0,229,255,.15);color:#eafaff}
    /* ---- dcc.Input (Dash 4) ---- */
    .panel .dash-input-element,.num-row .dash-input-element{width:100%;padding:7px 9px;border-radius:9px;
      border:1px solid var(--line) !important;background:#0a1420 !important;color:var(--txt) !important;
      font-family:'JetBrains Mono',monospace;font-size:12px;outline:none;height:34px}
    .panel .dash-input-element:focus{border-color:var(--cy) !important;
      box-shadow:0 0 0 2px rgba(0,229,255,.14) !important}
    .num-row{display:grid;grid-template-columns:repeat(3,1fr);gap:7px;margin-bottom:8px}
    .btn{font-family:'Chakra Petch',sans-serif;font-size:12.5px;letter-spacing:.06em;cursor:pointer;
      border-radius:9px;padding:9px 14px;border:1px solid rgba(0,229,255,.4);
      background:linear-gradient(180deg,rgba(0,229,255,.16),rgba(0,229,255,.05));
      color:#d9f8ff;transition:.15s;width:100%}
    .btn:hover{background:linear-gradient(180deg,rgba(0,229,255,.28),rgba(0,229,255,.1));
      box-shadow:0 0 22px -6px rgba(0,229,255,.7)}
    .btn.primary{border-color:rgba(0,229,255,.55);margin-top:6px}
    .btn.big{font-size:13.5px;padding:12px}
    .btn.ghost{border-color:var(--line);background:rgba(255,255,255,.02);color:#a8bccd}
    .btn.ghost:hover{border-color:rgba(255,45,149,.5);color:#ffd6e8}
    .btn.small{width:auto;padding:6px 11px;font-size:11px}
    .btn.wide{margin-top:8px}
    .btn.download{border-color:rgba(57,255,136,.5);color:#c8ffe2;
      background:linear-gradient(180deg,rgba(57,255,136,.18),rgba(57,255,136,.04))}
    .btn.download:hover{box-shadow:0 0 24px -6px rgba(57,255,136,.75)}
    .dl-row{display:grid;grid-template-columns:1.2fr 1fr;gap:8px}
    .divider{height:1px;background:linear-gradient(90deg,transparent,var(--line),transparent);margin:16px 0}
    .alert-zone{min-height:36px;margin-top:10px}
    .alert{font-size:11.5px;line-height:1.5;padding:8px 11px;border-radius:8px;
      font-family:'JetBrains Mono',monospace;border:1px solid var(--line)}
    .alert-ok{border-color:rgba(57,255,136,.35);background:rgba(57,255,136,.07);color:#a9ffd0}
    .alert-err{border-color:rgba(255,45,149,.45);background:rgba(255,45,149,.08);color:#ffc4dd}
    .alert-warn{border-color:rgba(255,212,0,.4);background:rgba(255,212,0,.07);color:#ffe9a3}
    .alert-info{border-color:rgba(0,229,255,.3);background:rgba(0,229,255,.06);color:#b6ecff}
    .footnote{margin-top:14px;font-size:10.5px;line-height:1.6;color:#4f5f70}
    .js-plotly-plot .plotly .modebar{background:transparent !important}
    /* ================= 小白 / 专家 模式 ================= */
    .expert-wrap{display:flex;align-items:center;margin-left:8px}
    .expert-switch .dash-options-list{display:flex;gap:6px}
    .expert-switch .dash-options-list-option{display:flex;align-items:center;gap:7px;
      padding:5px 11px;border:1px solid var(--line);border-radius:999px;cursor:pointer;
      font-size:11.5px;color:#8fa6ba;background:rgba(255,255,255,.02);transition:.15s}
    .expert-switch .dash-options-list-option:hover{border-color:rgba(0,229,255,.45);color:#cfefff}
    .expert-switch .dash-options-list-option.selected{border-color:rgba(255,45,149,.65);
      color:#ffd6e8;background:linear-gradient(180deg,rgba(255,45,149,.18),rgba(255,45,149,.05))}
    .expert-switch input{accent-color:#ff2d95}

    /* 模式显隐：钩子放在根节点上，一个输出搞定全部面板 */
    .root.simple #panel_expert{display:none}
    .root.expert #panel_simple{display:none}
    .root.simple #matrix{display:none}          /* 4x4 矩阵面板 */
    .root.simple .kpi-center{display:none}      /* 包围盒中心 KPI */

    /* 右上角醒目提示 */
    .viewport{position:relative}
    .simple-hint{display:none}
    .root.simple .simple-hint{display:flex;align-items:center;gap:8px;
      position:absolute;top:14px;right:18px;z-index:5;max-width:52%;
      padding:9px 14px;border-radius:10px;font-size:12px;color:#d8f6ff;
      border:1px solid rgba(0,229,255,.55);background:rgba(0,229,255,.1);
      box-shadow:0 0 30px -10px rgba(0,229,255,.9);backdrop-filter:blur(4px)}
    .root.simple .simple-hint::before{content:"➤";color:var(--cy)}

    /* 滑块 */
    .sl-row{display:grid;grid-template-columns:26px 1fr 46px;align-items:center;gap:9px;
      margin-bottom:7px}
    .sl-k{font-family:'JetBrains Mono',monospace;font-size:11px;color:#7f95a9}
    .sl-v{font-family:'JetBrains Mono',monospace;font-size:11px;color:#b9ecf7;text-align:right}
    /* Dash 4 的 Slider 是 Radix 结构（.dash-slider-*），旧的 input[type=range] 样式全部失效 */
    .sl .dash-range-slider-input{display:none}         /* 数字输入框藏起来，读数看右侧 sl-v */
    .sl .dash-slider-wrapper{display:flex;align-items:center;width:100%}
    .sl .dash-slider-root{position:relative;display:flex;align-items:center;width:100%;
      height:16px;touch-action:none;user-select:none}
    .sl .dash-slider-track{position:relative;flex-grow:1;height:4px;border-radius:4px;
      background:linear-gradient(90deg,rgba(0,229,255,.55),rgba(157,0,255,.55))}
    .sl .dash-slider-range{position:absolute;height:100%;border-radius:4px;
      background:rgba(0,229,255,.22)}
    .sl .dash-slider-thumb{display:block;width:14px;height:14px;border-radius:50%;
      background:#eafcff;border:2px solid var(--cy);cursor:pointer;outline:none;
      box-shadow:0 0 12px rgba(0,229,255,.9)}
    .sl .dash-slider-thumb:focus{box-shadow:0 0 0 4px rgba(0,229,255,.25)}
    .sl .dash-slider-tooltip{background:#08131d;border:1px solid rgba(0,229,255,.45);
      border-radius:6px;padding:2px 6px;font-family:'JetBrains Mono',monospace;
      font-size:10.5px;color:#d8f6ff;white-space:nowrap}
    /* ---- 旋转模块：滑块 + 精确数字框 + 快捷角度 ---- */
    .sl-row.rot{grid-template-columns:26px 1fr 64px}
    .panel .sl-num.dash-input-element{width:100%;height:26px;padding:1px 6px;text-align:right;
      font-size:11px;border-radius:7px;border-color:rgba(0,229,255,.28) !important}
    .panel .sl-num.dash-input-element:focus{border-color:var(--cy) !important}
    .qgrid{display:grid;grid-template-columns:repeat(3,1fr);gap:6px;margin:0 0 7px}
    .btn.mini{width:100%;padding:6px 2px;font-size:10.5px;letter-spacing:.02em;border-radius:8px;
      border-color:rgba(157,0,255,.45);color:#e6d9ff;
      background:linear-gradient(180deg,rgba(157,0,255,.18),rgba(157,0,255,.04))}
    .btn.mini:hover{box-shadow:0 0 18px -6px rgba(157,0,255,.85);
      background:linear-gradient(180deg,rgba(157,0,255,.3),rgba(157,0,255,.08))}
    /* ---- 黑底白字悬浮提示（一键摆正） ---- */
    .tip{position:relative;display:block}
    .tip::after{content:attr(data-tip);position:absolute;left:50%;top:calc(100% + 8px);
      transform:translateX(-50%);width:252px;background:#000;color:#fff;font-size:11px;
      line-height:1.55;padding:8px 11px;border-radius:7px;text-align:center;
      border:1px solid rgba(255,255,255,.2);box-shadow:0 10px 28px rgba(0,0,0,.75);
      opacity:0;pointer-events:none;transition:opacity .15s;z-index:99}
    .tip:hover::after{opacity:1}
  </style>
</head>
<body>
  {%app_entry%}
  <footer>
    {%config%}
    {%scripts%}
    {%renderer%}
  </footer>
</body>
</html>"""

# ======================================================================================
# 小白 / 专家 双模式
# ======================================================================================
@app.callback(Output("root", "className"), Input("expert", "value"))
def mode_switch(expert):
    """勾上专家模式 → 根节点换 class，CSS 负责显隐全部硬核面板与 4x4 矩阵。"""
    return "root expert" if expert else "root simple"


# ---- 滑块数值实时显示（纯前端，不打服务器） --------------------------------------
# 旋转三轴的读数已经变成可编辑的 dcc.Input，改由下面的 rot_* 回调负责，这里只管平移/缩放。
app.clientside_callback(
    """
    function(px, py, pz, sc){
        const f = (v, u) => (v === null || v === undefined) ? "–" : (String(v) + u);
        return [f(px, "%"), f(py, "%"), f(pz, "%"), f(sc, "%")];
    }
    """,
    [Output("px_v", "children"), Output("py_v", "children"),
     Output("pz_v", "children"), Output("ssc_v", "children")],
    [Input("px", "value"), Input("py", "value"),
     Input("pz", "value"), Input("ssc", "value")],
)


# ---- 旋转：滑块 → 数字框（镜像） --------------------------------------------------
# 若把「滑块→数字框」和「数字框→滑块」各写成一个回调，Output 与 Input 会互相指，
# Dash 会报 Dependency Cycle。这里用 dash_clientside.set_props 直接推 props，
# 不产生依赖边，环就断开了。回调的 Output 只是个 Store，用来当触发器。
app.clientside_callback(
    """
    function(rx, ry, rz){
        const S = dash_clientside.set_props;
        if (S){
            S("srx_n", {value: rx});
            S("sry_n", {value: ry});
            S("srz_n", {value: rz});
        }
        return Date.now();
    }
    """,
    Output("rot_mirror", "data"),
    [Input("srx", "value"), Input("sry", "value"), Input("srz", "value")],
)


# ---- 旋转：数字框 / 快捷按钮 → 滑块（归一化 + 联动） -------------------------------
app.clientside_callback(
    """
    function(nx, ny, nz, c_x90, c_y90, c_z90, c_x180, c_y180, c_z180, c_rst, sx, sy, sz){
        const ctx = dash_clientside.callback_context;
        let tid = ctx.triggered_id;
        if (!tid && ctx.triggered && ctx.triggered.length){
            tid = ctx.triggered[0].prop_id.split(".")[0];
        }
        const NU = window.dash_clientside.no_update;

        // 归一化到 (-180, 180]：450 -> 90，200 -> -160，-450 -> -90，NaN/空 -> 0
        const N = function(a){
            let v = Number(a);
            if (!isFinite(v)) v = 0;
            v = ((v % 360) + 360) % 360;
            if (v > 180) v -= 360;
            return v;
        };
        const cur = {srx: N(sx), sry: N(sy), srz: N(sz)};

        // 1) 归零
        if (tid === "q_reset") return [0, 0, 0];

        // 2) 增量按钮：在当前角度上加减（会自动求余）
        const step = {"q_x90": ["srx", 90],  "q_y90": ["sry", 90],  "q_z90": ["srz", 90],
                      "q_x180": ["srx", 180], "q_y180": ["sry", 180], "q_z180": ["srz", 180]};
        if (step[tid]){
            const key = step[tid][0], d = step[tid][1];
            cur[key] = N(cur[key] + d);
            return [cur.srx, cur.sry, cur.srz];
        }

        // 3) 数字框：绝对值，先归一化再推给滑块；归一化改动了数字时把它写回输入框
        const v = {srx: N(nx), sry: N(ny), srz: N(nz)};
        const raw = {srx: nx, sry: ny, srz: nz};
        const S = dash_clientside.set_props;
        ["srx", "sry", "srz"].forEach(function(k){
            const r = raw[k];
            if (S && r !== null && r !== undefined && r !== "" && Number(r) !== v[k]){
                S(k + "_n", {value: v[k]});     // 450 -> 90 的视觉反馈
            }
        });
        if (Math.abs(v.srx - cur.srx) < 1e-9 &&
            Math.abs(v.sry - cur.sry) < 1e-9 &&
            Math.abs(v.srz - cur.srz) < 1e-9) return [NU, NU, NU];
        return [v.srx, v.sry, v.srz];
    }
    """,
    Output("srx", "value", allow_duplicate=True),
    Output("sry", "value", allow_duplicate=True),
    Output("srz", "value", allow_duplicate=True),
    [Input("srx_n", "value"), Input("sry_n", "value"), Input("srz_n", "value"),
     Input("q_x90", "n_clicks"), Input("q_y90", "n_clicks"), Input("q_z90", "n_clicks"),
     Input("q_x180", "n_clicks"), Input("q_y180", "n_clicks"), Input("q_z180", "n_clicks"),
     Input("q_reset", "n_clicks")],
    [State("srx", "value"), State("sry", "value"), State("srz", "value")],
    prevent_initial_call=True,
)


# ---- 滑块防抖：拖动时不打断，松手 180 ms 后才打服务器 ------------------------------
app.clientside_callback(
    """
    function(px, py, pz, rx, ry, rz, sc){
        window.__aln = window.__aln || {};
        clearTimeout(window.__aln.t);
        return new Promise(function(resolve){
            window.__aln.t = setTimeout(function(){
                resolve({px: px, py: py, pz: pz, rx: rx, ry: ry, rz: rz, sc: sc});
            }, 180);
        });
    }
    """,
    Output("sdeb", "data"),
    [Input("px", "value"), Input("py", "value"), Input("pz", "value"),
     Input("srx", "value"), Input("sry", "value"), Input("srz", "value"),
     Input("ssc", "value")],
)


def _pct(v, d: float = 0.0) -> float:
    return d if v is None else float(v)


# ---- 基准位姿：上传 / 一键摆正 / 复位 / 切回小白模式时刷新 --------------------------
@app.callback(
    Output("sbase", "data"),
    Output("px", "value"), Output("py", "value"), Output("pz", "value"),
    Output("srx", "value"), Output("sry", "value"), Output("srz", "value"),
    Output("ssc", "value"),
    Output("view", "figure", allow_duplicate=True),
    Output("kpis", "children", allow_duplicate=True),
    Output("matrix", "children", allow_duplicate=True),
    Output("alert", "children", allow_duplicate=True),
    Input("sid", "data"),
    Input("btn_auto_simple", "n_clicks"),
    Input("btn_reset", "n_clicks"),
    Input("expert", "value"),
    prevent_initial_call=True,
)
def simple_base(sid, n_auto, n_reset, expert):
    """把当前位姿记成滑块的基准，同时把 7 个滑块归零（0/0/0/0/0/0/100%）。"""
    zero = (0, 0, 0, 0, 0, 0, 100)
    if not sid:
        return (None, *zero, no_update, no_update, no_update, no_update)
    try:
        p = _load_project(sid)
        fig = kpis = mx = al = no_update
        if callback_context.triggered_id == "btn_auto_simple":
            p.set_transform(ac.align_principal(p.mesh), "principal")
            shown = p.mesh.transformed(p.transform)
            fig, kpis = build_figure(shown), kpi_block(shown, p)
            mx = matrix_block(p.transform, p.mesh.unit_label)
            al = alert("✅ 已对齐至最佳视角（最长边 → X，最薄面 → Z）· 现在只用滑块微调即可", "ok")
        return ({"sid": sid, "tf": p.transform.to_dict()}, *zero, fig, kpis, mx, al)
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        return (None, *zero, no_update, no_update, no_update, alert(f"操作失败：{exc}", "err"))


# ---- 滑块 → 底层 4x4 变换（绕包围盒中心，可逆、无累计漂移） ------------------------
@app.callback(
    Output("view", "figure", allow_duplicate=True),
    Output("kpis", "children", allow_duplicate=True),
    Output("matrix", "children", allow_duplicate=True),
    Output("alert", "children", allow_duplicate=True),
    Input("sdeb", "data"),
    State("sid", "data"), State("sbase", "data"),
    prevent_initial_call=True,
)
def apply_simple(deb, sid, sbase):
    """
    数学：设基准位姿 base: x -> s_b·R_b·x + t_b，包围盒中心 C，半尺寸 h。
      d  = (px,py,pz)/100 · h                 ← 平移滑块（相对包围盒）
      A  = (sc/100) · R(rx,ry,rz)             ← 缩放 + 旋转
      y  = C + A·(base(x) − C) + d
    展开成 Transform：
      s' = (sc/100)·s_b
      R' = R·R_b
      t' = C + A·(t_b − C) + d
    每次都从 base 重算，所以滑块来回拖动不会累积误差。
    """
    if not deb or not sid:
        return no_update, no_update, no_update, no_update
    # 全零 / 100% = 正好停在基准位姿上（上传、一键摆正、复位、切换模式都会把滑块归零）。
    # 这种情况不写历史、不刷提示，否则「已载入 xxx」会被一条「滑块微调 ±0.00」的废话顶掉。
    _v = [_pct(deb.get(k)) for k in ("px", "py", "pz", "rx", "ry", "rz")]
    if all(abs(v) < 1e-9 for v in _v) and abs(_pct(deb.get("sc"), 100.0) - 100.0) < 1e-9:
        return no_update, no_update, no_update, no_update
    try:
        p = _load_project(sid)
    except Exception as exc:  # noqa: BLE001
        return no_update, no_update, no_update, alert(f"会话已失效（{exc}），请重新上传", "err")

    base = ac.Transform.from_dict(sbase["tf"]) if (sbase and sbase.get("sid") == sid) else p.transform.copy()
    bb = p.mesh.transformed(base).bbox()
    C = np.array(bb["center"], dtype=float)
    half = float(max(bb["size"])) / 2.0

    d = np.array([_pct(deb.get("px")), _pct(deb.get("py")), _pct(deb.get("pz"))]) / 100.0 * half
    sc = _pct(deb.get("sc"), 100.0) / 100.0
    if abs(sc) < 1e-6:
        sc = 1.0
    R = ac.euler_matrix(_pct(deb.get("rx")), _pct(deb.get("ry")), _pct(deb.get("rz")), "XYZ")
    A = sc * R

    tf = ac.Transform(R=A @ base.R,
                      t=C + A @ (np.asarray(base.t, dtype=float) - C) + d,
                      s=sc * float(base.s))
    p.set_transform(tf, "simple")

    shown = p.mesh.transformed(tf)
    msg = (f"滑块微调 · 平移 ({d[0]:+.2f}, {d[1]:+.2f}, {d[2]:+.2f}) {p.mesh.unit_label} · "
           f"旋转 ({_pct(deb.get('rx')):.0f}, {_pct(deb.get('ry')):.0f}, "
           f"{_pct(deb.get('rz')):.0f})° · 缩放 {sc:.3f}")
    return (build_figure(shown), kpi_block(shown, p),
            matrix_block(tf, p.mesh.unit_label), alert(msg, "info"))


if __name__ == "__main__":
    print(f"  ▸ Web-STEP-Aligner  v{APP_VERSION}")
    print(f"  ▸ kernel   {'cascadio / OpenCASCADE' if ac.HAVE_CASCADIO else 'MISSING (pip install cascadio)'}")
    print(f"  ▸ workdir  {WORKDIR}")
    print(f"  ▸ ready    http://127.0.0.1:{PORT}")
    app.run(host="127.0.0.1", port=PORT, debug=bool(os.environ.get("ALIGNER_DEBUG")))
