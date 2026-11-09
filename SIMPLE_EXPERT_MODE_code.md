# 8052 摆正器 · 小白/专家双模式 —— 逐块粘贴代码

目标文件：`C:\Users\ASUS TUF\hermes-projects\Web-STEP-Aligner\app.py`
一共 8 个改动块（B1–B8），全部是「整段替换 / 整段追加」，没有需要你手工拼的行。
改完重启：`python.exe app.py`（或双击 start_8052.bat），浏览器 Ctrl+F5。

默认是小白模式（勾选框不勾）。勾上右上角「专家模式」→ 全部硬核面板立刻回来。

--------------------------------------------------------------------------------
## B1 · 滑块行小工具（放在 `app = Dash(...)` 这一行之前，约第 191 行）

```python
def _slider_row(id_: str, label: str, lo: float, hi: float, step: float,
                val: float, unit: str = "") -> html.Div:
    """小白模式的一行滑块：标签 + range + 实时数值（数值由 clientside 回调回填）。"""
    return html.Div([
        html.Span(label, className="sl-k"),
        html.Input(id=id_, type="range", min=lo, max=hi, step=step, value=val, className="sl"),
        html.Span(f"{val}{unit}", id=f"{id_}_v", className="sl-v"),
    ], className="sl-row")
```

--------------------------------------------------------------------------------
## B2 · 顶栏右侧加「专家模式」开关

把 layout 里这一行：

```python
        html.Div(f"127.0.0.1:{PORT}", className="port"),
```

替换为：

```python
        html.Div([
            dcc.Checklist(id="expert", className="expert-switch",
                          options=[{"label": "专家模式", "value": "on"}], value=[]),
        ], className="expert-wrap"),
        html.Div(f"127.0.0.1:{PORT}", className="port"),
```

--------------------------------------------------------------------------------
## B3 · 根节点带上模式 class

把 `app.layout = html.Div([` 后面那行的收尾 `], className="root")` 改成：

```python
], id="root", className="root simple")
```

（即 `app.layout = html.Div([ ... ], id="root", className="root simple")`）

--------------------------------------------------------------------------------
## B4 · 视口右上角醒目提示

在 `.viewport` 里、`dcc.Graph(...)` 那一段的**后面**插入两行：

```python
            html.Div("只需点击「一键摆正」，或拖动滑块微调即可",
                     id="simple_hint", className="simple-hint"),
```

顺便把 `dcc.Store(id="sid")` 那一行下面加一个 store：

```python
    dcc.Store(id="sbase"),      # 小白模式的基准位姿（滑块的坐标系原点）
    dcc.Store(id="sdeb"),       # 滑块防抖后的值
```

--------------------------------------------------------------------------------
## B5 · 控制面板整段替换

把 `.panel` 里从 `html.Div("位姿控制", className="panel-title"),` 一直到最后
`html.Div("用法：上传 → 自动摆正 → 需要时微调 → 下载。…", className="footnote"),`
的整段，替换为下面这段（原有硬核面板原封不动搬进 `id="panel_expert"`）：

```python
            html.Div("位姿控制", className="panel-title"),

            # ==================== 小白模式（默认显示） ====================
            html.Div([
                html.Button("◈ 一键摆正（自动）", id="btn_auto_simple",
                            className="btn primary big"),
                html.Div("最长轴对 X、最薄轴对 Z、包围盒中心归零 —— 一步到位。",
                         className="hint"),

                html.Div("平移 · X / Y / Z", className="field-label"),
                _slider_row("px", "X", -100, 100, 1, 0, "%"),
                _slider_row("py", "Y", -100, 100, 1, 0, "%"),
                _slider_row("pz", "Z", -100, 100, 1, 0, "%"),
                html.Div("100 % = 半个包围盒尺寸", className="hint"),

                html.Div("旋转 · X / Y / Z", className="field-label"),
                _slider_row("srx", "Rx", -180, 180, 1, 0, "°"),
                _slider_row("sry", "Ry", -180, 180, 1, 0, "°"),
                _slider_row("srz", "Rz", -180, 180, 1, 0, "°"),

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
```

--------------------------------------------------------------------------------
## B6 · KPI 里「包围盒中心」标记成专家专属

`kpi_block()` 结尾这两行：

```python
    return [html.Div([html.Div(k, className="kpi-k"), html.Div(v, className="kpi-v")], className="kpi")
            for k, v in items]
```

替换为：

```python
    out = []
    for k, v in items:
        cls = "kpi kpi-center" if k == "包围盒中心" else "kpi"
        out.append(html.Div([html.Div(k, className="kpi-k"), html.Div(v, className="kpi-v")],
                            className=cls))
    return out
```

--------------------------------------------------------------------------------
## B7 · 模式开关 + 滑块三个回调（放在 `if __name__ == "__main__":` 之前）

```python
# ======================================================================================
# 小白 / 专家 双模式
# ======================================================================================
@app.callback(Output("root", "className"), Input("expert", "value"))
def mode_switch(expert):
    """勾上专家模式 → 根节点换 class，CSS 负责显隐全部硬核面板与 4x4 矩阵。"""
    return "root expert" if expert else "root simple"


# ---- 滑块数值实时显示（纯前端，不打服务器） --------------------------------------
app.clientside_callback(
    """
    function(px, py, pz, rx, ry, rz, sc){
        const f = (v, u) => (v === null || v === undefined) ? "–" : (String(v) + u);
        return [f(px, "%"), f(py, "%"), f(pz, "%"), f(rx, "°"), f(ry, "°"), f(rz, "°"), f(sc, "%")];
    }
    """,
    [Output("px_v", "children"), Output("py_v", "children"), Output("pz_v", "children"),
     Output("srx_v", "children"), Output("sry_v", "children"), Output("srz_v", "children"),
     Output("ssc_v", "children")],
    [Input("px", "value"), Input("py", "value"), Input("pz", "value"),
     Input("srx", "value"), Input("sry", "value"), Input("srz", "value"),
     Input("ssc", "value")],
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
    """把当前位姿记成滑块的基准，同时把 7 个滑块归零（0 / 0 / 0 / 0 / 0 / 0 / 100%）。"""
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
            al = alert("已一键摆正 · 现在只用滑块微调即可", "ok")
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
```

--------------------------------------------------------------------------------
## B8 · CSS（追加到 `app.index_string` 里 `</style>` 之前）

```css
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
    .sl{-webkit-appearance:none;appearance:none;width:100%;height:4px;border-radius:4px;
      background:linear-gradient(90deg,rgba(0,229,255,.55),rgba(157,0,255,.55));outline:none}
    .sl::-webkit-slider-thumb{-webkit-appearance:none;width:14px;height:14px;border-radius:50%;
      background:#eafcff;border:2px solid var(--cy);cursor:pointer;
      box-shadow:0 0 12px rgba(0,229,255,.9)}
    .sl::-moz-range-thumb{width:14px;height:14px;border-radius:50%;background:#eafcff;
      border:2px solid var(--cy);cursor:pointer}
```

--------------------------------------------------------------------------------
## 说明 / 注意

1. 三个原来输出 `view.figure` 等的回调现在都带 `allow_duplicate=True`，
   原 `dispatch` 不用改（Dash 允许「主回调 + 若干 allow_duplicate 副回调」）。
2. 滑块是「绝对式」的：每次都从 `sbase` 重算 → 来回拖不会累积误差，也不会漂。
3. 防抖用的是 clientside 回调返回 Promise（Dash 支持）。如果你的 Dash 版本太老、
   拖滑块没反应：删掉 B7 里 `Output("sdeb","data")` 那个 clientside 回调，
   再把 `apply_simple` 的 `Input("sdeb","data")` 改成 7 个滑块 Input、改读参数即可。
4. 专家模式里 `面→面贴合 / 单面→轴向 / 欧拉角 / 居中 / 高亮` 全部原样保留，
   一次都没删 —— 只是被 CSS 藏起来了。
