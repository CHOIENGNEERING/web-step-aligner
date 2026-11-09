"""Generate a few small, hand-written AP214 STEP files (valid BREP boxes / L-bracket)
so the aligner can be tested without any CAD package.

Run:  python samples/make_sample.py
Writes: samples/box.stp, samples/lbracket.stp
"""
from __future__ import annotations

import itertools
import os

HERE = os.path.dirname(os.path.abspath(__file__))


class StepWriter:
    def __init__(self):
        self.lines: list[str] = [""]  # 1-based ids: lines[i] holds entity #i
        self._n = 0

    def add(self, txt: str) -> int:
        self._n += 1
        self.lines.append(txt)
        return self._n

    def ref(self, i: int) -> str:
        return f"#{i}"

    def text(self, name: str, schema: str = "AUTOMOTIVE_DESIGN { 1 0 10303 214 1 1 1 1 }") -> str:
        head = [
            "ISO-10303-21;",
            "HEADER;",
            "FILE_DESCRIPTION((''),'2;1');",
            f"FILE_NAME('{name}','2026-01-01T00:00:00',(''),(''),'web-step-aligner','web-step-aligner','');",
            f"FILE_SCHEMA(('{schema}'));",
            "ENDSEC;",
            "DATA;",
        ]
        body = [f"#{i} = {t};" for i, t in enumerate(self.lines) if i]
        tail = ["ENDSEC;", "END-ISO-10303-21;", ""]
        return "\n".join(head + body + tail)


def unit_entities(w: StepWriter):
    u_len = w.add("( LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.MILLI.,.METRE.) )")
    u_ang = w.add("( NAMED_UNIT(*) PLANE_ANGLE_UNIT() SI_UNIT($,.RADIAN.) )")
    u_sol = w.add("( NAMED_UNIT(*) SI_UNIT($,.STERADIAN.) SOLID_ANGLE_UNIT() )")
    unc = w.add(
        "UNCERTAINTY_MEASURE_WITH_UNIT(LENGTH_MEASURE(1.E-06),"
        f"{w.ref(u_len)},'distance_accuracy_value','confusion accuracy')"
    )
    ctx = w.add(
        "( GEOMETRIC_REPRESENTATION_CONTEXT(3) "
        f"GLOBAL_UNCERTAINTY_ASSIGNED_CONTEXT(({w.ref(unc)})) "
        f"GLOBAL_UNIT_ASSIGNED_CONTEXT(({w.ref(u_len)},{w.ref(u_ang)},{w.ref(u_sol)})) "
        "REPRESENTATION_CONTEXT('Context #1','3D Context with UNIT and UNCERTAINTY') )"
    )
    return ctx


def write_box(w: StepWriter, lo, hi, origin=(0.0, 0.0, 0.0), ctx: int | None = None) -> int:
    """Axis-aligned box [lo,hi] shifted by origin; returns the ADVANCED_BREP_SHAPE_REPRESENTATION id."""
    import math

    ctx = ctx or unit_entities(w)
    pts = {}
    for i, j, k in itertools.product((0, 1), repeat=3):
        x = (hi[0] if i else lo[0]) + origin[0]
        y = (hi[1] if j else lo[1]) + origin[1]
        z = (hi[2] if k else lo[2]) + origin[2]
        pts[(i, j, k)] = w.add(f"CARTESIAN_POINT('',({x:.6f},{y:.6f},{z:.6f}))")
    verts = {key: w.add(f"VERTEX_POINT('',{w.ref(p)})") for key, p in pts.items()}

    # 12 edges: corner pairs differing along exactly one index
    edges = {}
    for key in pts:
        for axis in range(3):
            if key[axis] == 0:
                other = list(key)
                other[axis] = 1
                other = tuple(other)
                a, b = key, other
                d = [0.0, 0.0, 0.0]
                d[axis] = 1.0
                dref = w.add(f"DIRECTION('',({d[0]:.1f},{d[1]:.1f},{d[2]:.1f}))")
                p0 = pts[a]
                vec = w.add(f"VECTOR('',{w.ref(dref)},1.0)")
                line = w.add(f"LINE('',{w.ref(p0)},{w.ref(vec)})")
                edge = w.add(f"EDGE_CURVE('',{w.ref(verts[a])},{w.ref(verts[b])},{w.ref(line)},.T.)")
                edges[(a, b)] = (edge, verts[a], verts[b])

    faces = []
    axes = [(0, 0), (0, 1), (1, 0), (1, 1), (2, 0), (2, 1)]
    for ax, side in axes:
        others = [a for a in range(3) if a != ax]
        corners = []
        for (p, q) in ((0, 0), (1, 0), (1, 1), (0, 1)):
            key = [0, 0, 0]
            key[ax] = side
            key[others[0]] = p
            key[others[1]] = q
            corners.append(tuple(key))
        # outward normal
        n = [0.0, 0.0, 0.0]
        n[ax] = 1.0 if side else -1.0
        # orientation: make the loop CCW seen from +normal -> reverse ordering if needed
        c0 = corners[0]
        c1 = corners[1]
        c2 = corners[2]
        p0 = [(hi[d] if c0[d] else lo[d]) + origin[d] for d in range(3)]
        p1 = [(hi[d] if c1[d] else lo[d]) + origin[d] for d in range(3)]
        p2 = [(hi[d] if c2[d] else lo[d]) + origin[d] for d in range(3)]
        e1 = [p1[d] - p0[d] for d in range(3)]
        e2 = [p2[d] - p1[d] for d in range(3)]
        cr = [e1[1] * e2[2] - e1[2] * e2[1], e1[2] * e2[0] - e1[0] * e2[2], e1[0] * e2[1] - e1[1] * e2[0]]
        if sum(cr[d] * n[d] for d in range(3)) < 0:
            corners = list(reversed(corners))
        oes = []
        for a_idx in range(4):
            a = corners[a_idx]
            b = corners[(a_idx + 1) % 4]
            if (a, b) in edges:
                edge, va, vb = edges[(a, b)]
                ori = ".T."
            else:
                edge, va, vb = edges[(b, a)]
                ori = ".F."
            oes.append(w.add(f"ORIENTED_EDGE('',*,*,{w.ref(edge)},{ori})"))
        loop = w.add("EDGE_LOOP('',(" + ",".join(w.ref(o) for o in oes) + "))")
        bound = w.add(f"FACE_OUTER_BOUND('',{w.ref(loop)},.T.)")
        centre = [0.0, 0.0, 0.0]
        for key in corners:
            for d in range(3):
                centre[d] += ((hi[d] if key[d] else lo[d]) + origin[d]) / 4.0
        cp = w.add(f"CARTESIAN_POINT('',({centre[0]:.6f},{centre[1]:.6f},{centre[2]:.6f}))")
        nd = w.add(f"DIRECTION('',({n[0]:.1f},{n[1]:.1f},{n[2]:.1f}))")
        # reference direction inside the plane
        ref = [0.0, 0.0, 0.0]
        ref[others[0]] = 1.0
        rd = w.add(f"DIRECTION('',({ref[0]:.1f},{ref[1]:.1f},{ref[2]:.1f}))")
        ax2 = w.add(f"AXIS2_PLACEMENT_3D('',{w.ref(cp)},{w.ref(nd)},{w.ref(rd)})")
        plane = w.add(f"PLANE('',{w.ref(ax2)})")
        faces.append(w.add(f"ADVANCED_FACE('',({w.ref(bound)}),{w.ref(plane)},.T.)"))

    shell = w.add("CLOSED_SHELL('',(" + ",".join(w.ref(f) for f in faces) + "))")
    brep = w.add(f"MANIFOLD_SOLID_BREP('box',{w.ref(shell)})")
    axis = w.add("AXIS2_PLACEMENT_3D('',#%d,#%d,#%d)" % (
        w.add("CARTESIAN_POINT('',(0.,0.,0.))"),
        w.add("DIRECTION('',(0.,0.,1.))"),
        w.add("DIRECTION('',(1.,0.,0.))"),
    ))
    rep = w.add(f"ADVANCED_BREP_SHAPE_REPRESENTATION('box',({w.ref(brep)},{w.ref(axis)}),{w.ref(ctx)})")
    w.add(f"SHAPE_DEFINITION_REPRESENTATION(#%d,%s)" % (
        w.add("PRODUCT_DEFINITION_SHAPE('',''," + w.ref(w.add("PRODUCT_DEFINITION('design','',#%d,#%d)" % (
            w.add("PRODUCT_DEFINITION_FORMATION('','',#%d)" % w.add("PRODUCT('box','box','',(#%d))" % w.add("PRODUCT_CONTEXT('',#%d,'mechanical')" % ctx))),
            w.add("PRODUCT_DEFINITION_CONTEXT('part design',#%d,'design')" % ctx),
        ))) + ")"),
        w.ref(rep),
    ))
    return rep


def main():
    # 1) plain box, slightly off-origin, in mm
    w = StepWriter()
    ctx = unit_entities(w)
    write_box(w, (0.0, 0.0, 0.0), (120.0, 60.0, 25.0), origin=(340.0, -120.0, 75.0), ctx=ctx)
    p = os.path.join(HERE, "box.stp")
    with open(p, "w", encoding="ascii") as f:
        f.write(w.text("box.stp"))
    print("wrote", p, os.path.getsize(p), "bytes")

    # 2) two boxes -> an L-ish bracket (still two solids in one file)
    w = StepWriter()
    ctx = unit_entities(w)
    write_box(w, (0.0, 0.0, 0.0), (100.0, 30.0, 12.0), origin=(0.0, 0.0, 0.0), ctx=ctx)
    write_box(w, (0.0, 0.0, 0.0), (30.0, 30.0, 80.0), origin=(0.0, 0.0, 12.0), ctx=ctx)
    p = os.path.join(HERE, "lbracket.stp")
    with open(p, "w", encoding="ascii") as f:
        f.write(w.text("lbracket.stp"))
    print("wrote", p, os.path.getsize(p), "bytes")


if __name__ == "__main__":
    main()
