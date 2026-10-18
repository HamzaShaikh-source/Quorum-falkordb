"""
Draws the README charts from the committed bench result files, so every bar is a number a script produced.

    python scripts/gen_charts.py          # writes docs/charts/*.svg

Design: the print proof room. Ink on paper, one mint accent for Quorum, grey for the baseline, red for harm. Each SVG carries its own
paper background (so it reads the same on GitHub's light and dark themes) plus a <title> and <desc> for screen readers.
"""
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "charts"
INK, PAPER, MUTED, GRID = "#14181a", "#f3f5f2", "#5c6a66", "#d6ddd9"
MINT, GREY, RED = "#17b890", "#9aa5a1", "#d6453d"
FONT = "ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"
MONO = "ui-monospace, 'JetBrains Mono', SFMono-Regular, Menlo, Consolas, monospace"


def load(name: str) -> Dict[str, Any]:
    return json.loads((ROOT / "bench" / name).read_text(encoding="utf8"))


def svg(width: int, height: int, title: str, desc: str, body: List[str]) -> str:
    return "\n".join(
        [
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}" role="img" aria-labelledby="t d">',
            f"<title id=\"t\">{escape(title)}</title><desc id=\"d\">{escape(desc)}</desc>",
            f'<rect width="{width}" height="{height}" rx="14" fill="{PAPER}"/>',
            f'<rect x="1" y="1" width="{width - 2}" height="{height - 2}" rx="13" fill="none" stroke="{INK}" stroke-width="1.5"/>',
            *body,
            "</svg>",
        ]
    )


def text(x: float, y: float, s: str, size: int = 14, weight: int = 400, fill: str = INK, anchor: str = "start", mono: bool = False) -> str:
    fam = MONO if mono else FONT
    return f'<text x="{x}" y="{y}" font-family="{fam}" font-size="{size}" font-weight="{weight}" fill="{fill}" text-anchor="{anchor}">{escape(s)}</text>'


def header(title: str, sub: str) -> List[str]:
    return [text(28, 40, title, 20, 700), text(28, 62, sub, 13, 400, MUTED)]


def hbars(x0: int, y0: int, width: int, rows: List[Tuple[str, float, str, str]], maximum: float, label_w: int = 250, bar_h: int = 22, gap: int = 12) -> Tuple[List[str], int]:
    """rows: (label, value, colour, value-label). Returns the svg parts and the y after the last bar."""
    parts: List[str] = []
    track = width - label_w - 90
    y = y0
    for label, value, colour, vlabel in rows:
        parts.append(text(x0, y + bar_h - 6, label, 13))
        parts.append(f'<rect x="{x0 + label_w}" y="{y}" width="{track}" height="{bar_h}" rx="4" fill="{GRID}" opacity="0.55"/>')
        w = max(0.0, min(1.0, value / maximum)) * track
        if w > 0:
            parts.append(f'<rect x="{x0 + label_w}" y="{y}" width="{w:.1f}" height="{bar_h}" rx="4" fill="{colour}"/>')
        parts.append(text(x0 + label_w + track + 10, y + bar_h - 6, vlabel, 13, 700, INK, mono=True))
        y += bar_h + gap
    return parts, y


def evidence() -> str:
    flavours = ["", "_access", "_quote", "_deploy"]
    flat_run = quorum_run = trials = 0
    for f in flavours:
        s = load(f"agent_eval{f}_results.json")["summary"]
        key = "unsafe_refunds_executed" if "unsafe_refunds_executed" in s["flat"] else "unsafe_actions_executed"
        flat_run += s["flat"][key]
        quorum_run += s["quorum"][key]
        trials += s["flat"]["trials"]
    ab = load("ablation_results.json")["summary"]
    corpus = load("real_corpus_results.json")["summary"]
    co = load("corroboration_results.json")["summary"]
    body = header("What changes when memory knows what depends on what", "Every bar is read from a result file in bench/. Same facts, same correction in both arms.")
    y = 96
    sections: List[Tuple[str, str, List[Tuple[str, float, str, str]], float]] = [
        (
            "A real agent (claude -p, haiku), four scenarios",
            f"Wrong action ran, out of {trials} runs per arm",
            [("Memory without causal edges", flat_run, RED, f"{flat_run} / {trials}"), ("Quorum, same memory and correction", quorum_run, MINT, f"{quorum_run} / {trials}")],
            float(trials),
        ),
        (
            f"{ab['scenarios']} generated poisoned-fact scenarios",
            f"At-risk actions that still ran, out of {ab['actions_at_risk']}",
            [("Flat memory (no edges)", ab["flat_leaked"], RED, f"{ab['flat_leaked']} / {ab['actions_at_risk']}"), ("Same nodes, edges in FalkorDB", ab["quorum_leaked"], MINT, f"{ab['quorum_leaked']} / {ab['actions_at_risk']}")],
            float(ab["actions_at_risk"]),
        ),
        (
            f"A real corpus: {corpus['corpus']['packages']} installed Python packages, {corpus['questions']['total']} generated questions",
            "Percent, higher is better",
            [
                ("Refuses a package that does not exist: plain search", corpus["answers"]["plain"]["abstained_on_nonexistent_pct"], GREY, f"{corpus['answers']['plain']['abstained_on_nonexistent_pct']:.0f}%"),
                ("Refuses a package that does not exist: Quorum", corpus["answers"]["quorum"]["abstained_on_nonexistent_pct"], MINT, f"{corpus['answers']['quorum']['abstained_on_nonexistent_pct']:.0f}%"),
                ("Names exactly what is affected: plain search", corpus["impact_question"]["plain"]["exact_set_pct"], GREY, f"{corpus['impact_question']['plain']['exact_set_pct']:.0f}%"),
                ("Names exactly what is affected: Quorum", corpus["impact_question"]["quorum"]["exact_set_pct"], MINT, f"{corpus['impact_question']['quorum']['exact_set_pct']:.0f}%"),
            ],
            100.0,
        ),
        (
            "Copied claims: a trusted writer echoes a wrong fact",
            f"Poisoned claims trusted, out of {co['poisoned']['scenarios']} (the 3 are the reworded copy with no trace: the stated boundary)",
            [("Compare origin labels only", co["poisoned"]["label_only_trusted"], RED, f"{co['poisoned']['label_only_trusted']} / {co['poisoned']['scenarios']}"), ("Lineage-aware gate", co["poisoned"]["lineage_trusted"], MINT, f"{co['poisoned']['lineage_trusted']} / {co['poisoned']['scenarios']}")],
            float(co["poisoned"]["scenarios"]),
        ),
    ]
    for title, sub, rows, maximum in sections:
        body.append(text(28, y, title, 15, 700))
        body.append(text(28, y + 19, sub, 12, 400, MUTED))
        parts, y = hbars(28, y + 32, 804, rows, maximum, label_w=330)
        body += parts
        y += 14
    body.append(text(28, y + 4, "Limits: scenarios we wrote, one small model, generated questions, one machine. See docs/HACKATHON.md.", 12, 400, MUTED))
    return svg(860, y + 28, "Evidence: what changes when memory knows what depends on what", f"A wrong action ran {flat_run} of {trials} times without causal edges and {quorum_run} of {trials} with them; 62 of 62 versus 0 of 62 in generated scenarios.", body)


def cascade() -> str:
    """How one correction travels: a static diagram of correct, quarantine, block, compensate."""
    boxes = [
        (28, "1  Correct", "A reviewer marks a fact wrong", INK),
        (232, "2  Quarantine", "One traversal in FalkorDB: every conclusion and action built on it, with hop depth", RED),
        (436, "3  Block", "Pending actions whose ancestry is no longer healthy are refused", RED),
        (640, "4  Compensate", "Actions that already ran are reversed through an outbox, or a correction notice is drafted", MINT),
    ]
    body = header("Correct, quarantine, block, compensate", "One step in; four effects out. All of it is a graph operation, none of it asks a model.")
    for x, head, sub, colour in boxes:
        body.append(f'<rect x="{x}" y="90" width="192" height="150" rx="12" fill="#fff" stroke="{colour}" stroke-width="2"/>')
        body.append(f'<rect x="{x}" y="90" width="192" height="8" rx="4" fill="{colour}"/>')
        body.append(text(x + 14, 128, head, 16, 700))
        words, line, lines = sub.split(), "", []
        for w in words:
            if len(line) + len(w) + 1 > 24:
                lines.append(line)
                line = w
            else:
                line = f"{line} {w}".strip()
        lines.append(line)
        for i, ln in enumerate(lines):
            body.append(text(x + 14, 154 + i * 18, ln, 12, 400, MUTED))
    for x in (222, 426, 630):
        body.append(f'<path d="M{x} 165 h12" stroke="{INK}" stroke-width="2"/><path d="M{x + 7} 160 l6 5 l-6 5" fill="none" stroke="{INK}" stroke-width="2"/>')
    body.append(text(28, 268, "Later retrievals never return the quarantined facts until an authorised person restores them.", 12, 400, MUTED))
    return svg(860, 290, "Correct, quarantine, block, compensate", "A four-step flow: correct a fact, quarantine everything built on it, block dependent actions, compensate those that already ran.", body)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, content in (("evidence.svg", evidence()), ("cascade.svg", cascade())):
        (OUT / name).write_bytes((content + "\n").encode("utf8"))
        print(f"Wrote docs/charts/{name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
