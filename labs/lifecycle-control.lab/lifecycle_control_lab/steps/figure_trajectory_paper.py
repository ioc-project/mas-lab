#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Pipeline figure step (migrated from lab script)."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from mas.lab.benchmark.pipeline import PipelineStep, StepOutput, register_step_type

from ._helpers import execute_trace_figure

if TYPE_CHECKING:
    from mas.lab.benchmark.pipeline.executor import ExecutionContext

def render(output_path: Path, trace_baseline_path: Path, trace_guardrail_path: Path) -> None:
    import pathlib, xml.sax.saxutils as X

    C_LLM, C_TOOL, C_BLOCKED = "#ea580c", "#16a34a", "#dc2626"
    C_BG, C_LANE_A, C_LANE_B  = "#ffffff", "#f1f5f9", "#ffffff"
    C_IDENTICAL, C_SEP, C_LIFELINE = "#f0fdf4", "#e2e8f0", "#94a3b8"
    C_LABEL, C_TITLE, C_ACCENT = "#334155", "#0f172a", "#0e7490"

    def rgba(c, a=0.12):
        r,g,b = int(c[1:3],16), int(c[3:5],16), int(c[5:7],16)
        return f"rgba({r},{g},{b},{a})"

    LABEL_W=92; TITLE_H=24; LANE_H=56; LANE_MID=28; TRANS_H=24
    STATE_R=8; LEGEND_H=22; N_SLOTS=5; SLOT_W=72
    TIMELINE_W=N_SLOTS*SLOT_W; PANEL_W=LABEL_W+TIMELINE_W; GAP_W=24
    TOTAL_W=2*PANEL_W+GAP_W; TOTAL_H=TITLE_H+4*LANE_H+LEGEND_H+8

    AGENTS=["Orchestrator","Concierge","Schedule","Itinerary"]
    BASELINE={
        "Orchestrator":[(0,"LLM","LLM"),(4,"LLM","LLM")],
        "Concierge":[(0,"LLM","LLM"),(1,"TOOL","T"),(2,"TOOL","T"),(3,"TOOL","T"),(4,"LLM","LLM")],
        "Schedule":[(0,"LLM","LLM"),(2,"TOOL","T"),(4,"LLM","LLM")],
        "Itinerary":[(0,"LLM","LLM"),(2,"TOOL","T"),(4,"LLM","LLM")],
    }
    GUARDRAIL={
        "Orchestrator":[(0,"LLM","LLM"),(4,"LLM","LLM")],
        "Concierge":[(0,"LLM","LLM"),(1,"TOOL","T"),(2,"TOOL","T"),(3,"TOOL","T"),(4,"LLM","LLM")],
        "Schedule":[(0,"LLM","LLM"),(2,"TOOL","T"),(4,"LLM","LLM")],
        "Itinerary":[(0,"LLM","LLM"),(2,"BLOCKED","denied")],
    }
    IDENTICAL_ROWS={0,1,2}

    def scx(px,s): return px+LABEL_W+(s+0.5)*SLOT_W
    def rtop(i): return float(TITLE_H+i*LANE_H)
    def rcy(i): return rtop(i)+LANE_MID

    def call_box(cx,cy,ct,lbl,bw):
        bx=cx-bw/2; by=cy-TRANS_H/2
        c={"LLM":C_LLM,"TOOL":C_TOOL,"BLOCKED":C_BLOCKED}[ct]
        p=[]
        if ct=="BLOCKED":
            p.append(f"<rect x=\"{bx:.1f}\" y=\"{by:.1f}\" width=\"{bw:.1f}\" height=\"{TRANS_H}\" rx=\"4\" fill=\"{rgba(c,.08)}\" stroke=\"{c}\" stroke-width=\"1.5\" stroke-dasharray=\"4,2\"/>")
            r=6.0
            p.append(f"<line x1=\"{cx-r:.1f}\" y1=\"{cy-r:.1f}\" x2=\"{cx+r:.1f}\" y2=\"{cy+r:.1f}\" stroke=\"{c}\" stroke-width=\"2\" stroke-linecap=\"round\"/>")
            p.append(f"<line x1=\"{cx+r:.1f}\" y1=\"{cy-r:.1f}\" x2=\"{cx-r:.1f}\" y2=\"{cy+r:.1f}\" stroke=\"{c}\" stroke-width=\"2\" stroke-linecap=\"round\"/>")
            p.append(f"<text x=\"{cx:.1f}\" y=\"{by+TRANS_H+10:.1f}\" text-anchor=\"middle\" font-size=\"8\" font-style=\"italic\" fill=\"{c}\" font-family=\"ui-monospace,SFMono-Regular,Consolas,monospace\">denied</text>")
        else:
            mc=max(2,int(bw/7)); t=lbl[:mc]
            p.append(f"<rect x=\"{bx:.1f}\" y=\"{by:.1f}\" width=\"{bw:.1f}\" height=\"{TRANS_H}\" rx=\"4\" fill=\"{rgba(c)}\" stroke=\"{c}\" stroke-width=\"1.5\"/>")
            p.append(f"<text x=\"{cx:.1f}\" y=\"{cy+3.5:.1f}\" text-anchor=\"middle\" font-size=\"9\" font-weight=\"bold\" fill=\"{c}\" font-family=\"ui-monospace,SFMono-Regular,Consolas,monospace\">{X.escape(t)}</text>")
        return p

    def state_circle(cx,cy,n):
        return [
            f"<circle cx=\"{cx:.1f}\" cy=\"{cy:.1f}\" r=\"{STATE_R}\" fill=\"white\" stroke=\"{C_LABEL}\" stroke-width=\"1.2\"/>",
            f"<text x=\"{cx:.1f}\" y=\"{cy+3.5:.1f}\" text-anchor=\"middle\" font-size=\"8\" font-weight=\"bold\" fill=\"{C_LABEL}\" font-family=\"ui-monospace,SFMono-Regular,Consolas,monospace\">{n}</text>"
        ]

    def panel(px,title,steps,identical):
        p=[]
        p.append(f"<text x=\"{px+PANEL_W/2:.0f}\" y=\"{TITLE_H-5}\" text-anchor=\"middle\" font-size=\"11\" font-weight=\"bold\" fill=\"{C_TITLE}\" font-family=\"ui-monospace,SFMono-Regular,Consolas,monospace\">{X.escape(title)}</text>")
        for ri,agent in enumerate(AGENTS):
            ry=rtop(ri); cy=rcy(ri)
            bg=C_IDENTICAL if ri in identical else (C_LANE_A if ri%2==0 else C_LANE_B)
            ags=steps.get(agent,[])
            p.append(f"<rect x=\"{px:.0f}\" y=\"{ry:.0f}\" width=\"{PANEL_W}\" height=\"{LANE_H}\" fill=\"{bg}\"/>")
            p.append(f"<line x1=\"{px:.0f}\" y1=\"{ry:.0f}\" x2=\"{px+PANEL_W:.0f}\" y2=\"{ry:.0f}\" stroke=\"{C_SEP}\" stroke-width=\"0.5\"/>")
            p.append(f"<rect x=\"{px:.0f}\" y=\"{ry:.0f}\" width=\"4\" height=\"{LANE_H}\" fill=\"{C_ACCENT}\"/>")
            p.append(f"<text x=\"{px+LABEL_W-8:.0f}\" y=\"{cy+3.5:.1f}\" text-anchor=\"end\" font-size=\"10\" fill=\"{C_LABEL}\" font-family=\"ui-monospace,SFMono-Regular,Consolas,monospace\">{X.escape(agent)}</text>")
            if ags:
                slots=[s for s,_,_ in ags]
                p.append(f"<line x1=\"{scx(px,min(slots)):.1f}\" y1=\"{cy:.1f}\" x2=\"{scx(px,max(slots)):.1f}\" y2=\"{cy:.1f}\" stroke=\"{C_LIFELINE}\" stroke-width=\"0.8\" stroke-dasharray=\"3,4\"/>")
            for idx,(slot,ct,lbl) in enumerate(sorted(ags)):
                cx_=scx(px,slot); bw=SLOT_W*(0.75 if ct=="LLM" else 0.60)
                nx=cx_-bw/2-1
                p+=state_circle(nx,cy,idx+1)
                p+=call_box(cx_,cy,ct,lbl,bw)
        bot=rtop(len(AGENTS))
        p.append(f"<line x1=\"{px:.0f}\" y1=\"{bot:.0f}\" x2=\"{px+PANEL_W:.0f}\" y2=\"{bot:.0f}\" stroke=\"{C_SEP}\" stroke-width=\"0.8\"/>")
        return p

    def legend(y):
        p=[]; sx=12.0
        p+=[f"<rect x=\"{sx:.0f}\" y=\"{y:.0f}\" width=\"16\" height=\"11\" fill=\"{C_IDENTICAL}\" stroke=\"{C_SEP}\" stroke-width=\"0.8\" rx=\"2\"/>",
            f"<text x=\"{sx+20:.0f}\" y=\"{y+9:.0f}\" font-size=\"8\" fill=\"{C_LABEL}\" font-family=\"ui-monospace,SFMono-Regular,Consolas,monospace\">identical in both</text>"]
        sx+=110
        for lbl,c in [("LLM call",C_LLM),("Tool call",C_TOOL),("denied",C_BLOCKED)]:
            p+=[f"<rect x=\"{sx:.0f}\" y=\"{y:.0f}\" width=\"16\" height=\"11\" fill=\"{rgba(c)}\" stroke=\"{c}\" stroke-width=\"1.2\" rx=\"2\"/>",
                f"<text x=\"{sx+20:.0f}\" y=\"{y+9:.0f}\" font-size=\"8\" fill=\"{c}\" font-family=\"ui-monospace,SFMono-Regular,Consolas,monospace\">{lbl}</text>"]
            sx+=22+len(lbl)*5.5+12
        return p

    def generate():
        p=[f"<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"{TOTAL_W}\" height=\"{TOTAL_H}\" viewBox=\"0 0 {TOTAL_W} {TOTAL_H}\">",
           f"<rect width=\"{TOTAL_W}\" height=\"{TOTAL_H}\" fill=\"{C_BG}\"/>"]
        p+=panel(0.0,"Baseline",BASELINE,IDENTICAL_ROWS)
        dx=float(PANEL_W+GAP_W//2)
        p.append(f"<line x1=\"{dx:.0f}\" y1=\"{TITLE_H}\" x2=\"{dx:.0f}\" y2=\"{rtop(len(AGENTS))}\" stroke=\"{C_LIFELINE}\" stroke-width=\"1\" stroke-dasharray=\"4,3\"/>")
        p+=panel(float(PANEL_W+GAP_W),"+Guardrail",GUARDRAIL,IDENTICAL_ROWS)
        p+=legend(float(rtop(len(AGENTS)))+5)
        p.append("</svg>")
        return "\n".join(p)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(generate(), encoding="utf-8")
    print(f"Saved → {output_path}")

class FigureTrajectoryPaperStep(PipelineStep):
    type = "figure_trajectory_paper"

    async def execute(self, ctx: "ExecutionContext") -> StepOutput:
        return execute_trace_figure(ctx, self, render)

register_step_type("figure_trajectory_paper", FigureTrajectoryPaperStep)
