"""Build the one-page project summary as a PDF.

Kept in the repo so the document can be regenerated when the project changes,
rather than existing only as a file someone has to remember to update. Every
figure in it is one that `docs/EVIDENCE.md` can reproduce.

    python docs/make_onepager.py
"""
from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    FrameBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

OUT = Path(__file__).parent / "Meridian_Agent_OnePager.pdf"

# The project's own palette, so the document and the product look related.
INK = colors.HexColor("#1B2A24")
SOFT = colors.HexColor("#5A6660")
RULE = colors.HexColor("#C6CCC3")
HOLD = colors.HexColor("#A6321E")
CLEAR = colors.HexColor("#2F6B4F")
WASH = colors.HexColor("#F2F3F0")

PAGE_W, PAGE_H = A4
MARGIN = 14 * mm
GUTTER = 7 * mm
COL_W = (PAGE_W - 2 * MARGIN - GUTTER) / 2

GITHUB_URL = "https://github.com/AManMIshra20/TicketAgent"
RENDER_URL = "https://ticketagent.onrender.com"

body = ParagraphStyle(
    "body", fontName="Helvetica", fontSize=7.9, leading=10.4,
    textColor=INK, alignment=TA_JUSTIFY, spaceAfter=5,
)
head = ParagraphStyle(
    "head", fontName="Helvetica-Bold", fontSize=9.4, leading=11.5,
    textColor=INK, spaceBefore=6, spaceAfter=3.5,
)
small = ParagraphStyle(
    "small", fontName="Helvetica", fontSize=7.1, leading=9.2, textColor=SOFT,
)
step = ParagraphStyle(
    "step", fontName="Helvetica", fontSize=7.9, leading=10.2,
    textColor=INK, leftIndent=11, firstLineIndent=-11, spaceAfter=3.5,
)
mono = ParagraphStyle(
    "mono", fontName="Courier", fontSize=7.2, leading=9.4, textColor=INK,
)


def header(canvas, doc):
    """Title block, drawn once, spanning both columns."""
    canvas.saveState()
    y = PAGE_H - MARGIN

    canvas.setFillColor(INK)
    canvas.setFont("Helvetica-Bold", 16.5)
    canvas.drawString(MARGIN, y - 13, "Meridian Service Desk — Triage & SOP Agent")

    canvas.setFont("Helvetica", 9)
    canvas.setFillColor(SOFT)
    canvas.drawString(
        MARGIN, y - 25.5,
        "An agentic AI system that drafts service-desk replies citing the procedure "
        "they came from, and cannot send any of them on its own.",
    )

    canvas.setFont("Helvetica", 7.6)
    canvas.drawString(
        MARGIN, y - 37,
        "Aman Mishra  ·  IIM Udaipur  ·  Building with Agentic AI, capstone  ·  September 2026",
    )

    # Links strip
    box_y = y - 56
    canvas.setFillColor(WASH)
    canvas.setStrokeColor(RULE)
    canvas.rect(MARGIN, box_y, PAGE_W - 2 * MARGIN, 14, fill=1, stroke=1)
    canvas.setFillColor(INK)
    canvas.setFont("Helvetica-Bold", 7.4)
    canvas.drawString(MARGIN + 5, box_y + 4.4, "Live:")
    canvas.setFont("Helvetica", 7.4)
    canvas.drawString(MARGIN + 24, box_y + 4.4, RENDER_URL)
    canvas.setFont("Helvetica-Bold", 7.4)
    canvas.drawString(MARGIN + 250, box_y + 4.4, "Code:")
    canvas.setFont("Helvetica", 7.4)
    canvas.drawString(MARGIN + 273, box_y + 4.4, GITHUB_URL)

    canvas.restoreState()


def rule_row(label, value, colour=INK):
    return [
        Paragraph(f'<font size="7.4" color="#5A6660">{label}</font>', small),
        Paragraph(
            f'<font size="7.6" color="#{colour.hexval()[2:]}"><b>{value}</b></font>', small
        ),
    ]


def build() -> None:
    top = PAGE_H - MARGIN - 62
    frame_h = top - MARGIN

    doc = BaseDocTemplate(
        str(OUT), pagesize=A4,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=MARGIN, bottomMargin=MARGIN,
        title="Meridian Service Desk — Triage & SOP Agent",
        author="Aman Mishra",
    )
    left = Frame(MARGIN, MARGIN, COL_W, frame_h, id="left",
                 leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    right = Frame(MARGIN + COL_W + GUTTER, MARGIN, COL_W, frame_h, id="right",
                  leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    doc.addPageTemplates([PageTemplate(id="two", frames=[left, right], onPage=header)])

    s = []

    # ---------------- left column ----------------
    s.append(Paragraph("What this is", head))
    s.append(Paragraph(
        "A service desk this size takes about 1,200 tickets a month, and most are "
        "repeats. The fix is usually written down already. That helps nobody if the "
        "engineer on shift has never read it.", body))
    s.append(Paragraph(
        "Two agents work opposite ends of the same problem. <b>Agent A</b> reads an "
        "incoming ticket, checks the requester's support tier, searches the "
        "procedure library, and drafts a reply citing whichever procedure it used, "
        "by ID. <b>Agent B</b> takes the other case. When a ticket gets resolved "
        "that no procedure covered, it reads the thread to see what the engineer "
        "actually did, then drafts the procedure that was missing.", body))
    s.append(Paragraph(
        "The setting is enterprise IT support: Jira for tickets, Confluence for "
        "procedures. Both are stand-ins, backed by a local ticket store and a folder "
        "of markdown, so nothing external can break on submission day. Each panel "
        "says so, instead of putting a green 'connected' badge over a SQLite file.",
        body))

    s.append(Paragraph("The core design decision", head))
    s.append(Paragraph(
        "The approval gate is <b>a code boundary, not a prompt instruction</b>. "
        "Asking a model to check before it acts is a request, and requests can be "
        "argued out of, including by text sitting inside a ticket. Four layers make "
        "it structural instead:", body))

    layers = Table([
        [Paragraph('<b>1. Rule</b>', small),
         Paragraph("Stated in CLAUDE.md and the system prompt. A request.", small)],
        [Paragraph('<b>2. Gate</b>', small),
         Paragraph("Runs in code once the model has spoken, reading the ticket and "
                   "the requester record. It never consults the model's opinion.", small)],
        [Paragraph('<b>3. Boundary</b>', small),
         Paragraph("The model holds no write-capable tool at all. Its only "
                   "terminal move produces a proposal object.", small)],
        [Paragraph('<b>4. Hook</b>', small),
         Paragraph("Refuses any code edit that would widen the tool surface.", small)],
    ], colWidths=[54, COL_W - 54])
    layers.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
        ("TOPPADDING", (0, 0), (-1, -1), 1),
        ("LINEBELOW", (0, 0), (-1, -2), 0.4, RULE),
    ]))
    s.append(layers)
    s.append(Spacer(1, 5))

    s.append(Paragraph(
        "Four conditions stop a draft: a request for access or permissions, "
        "critical severity, a Gold-tier requester, and no procedure covering the "
        "ticket. An unknown requester counts as Gold, since the safe assumption "
        "there is the expensive one. The last condition matters most. When the agent "
        "reports that it cannot answer, the system is working: the ticket goes to "
        "Agent B to write what was missing.", body))
    s.append(Paragraph(
        "Air Canada's chatbot invented a refund policy and a tribunal held the "
        "airline liable. Citations here are checked in code after the model speaks: "
        "an ID that does not resolve is stripped and the proposal downgraded.", body))

    s.append(Paragraph("How it works", head))
    flow = Table([
        [Paragraph('<b>Read</b>', small),
         Paragraph("The agent chooses its own inputs: ticket, requester record, "
                   "procedure library. Nothing is pasted in for it.", small)],
        [Paragraph('<b>Score</b>', small),
         Paragraph("Computed in code, not the model's estimate of its own "
                   "confidence. The reviewer sees the number the agent saw.", small)],
        [Paragraph('<b>Propose</b>', small),
         Paragraph("The model's only terminal move: a structured object, never an "
                   "action. A second instance writes the reply from three inputs.", small)],
        [Paragraph('<b>Hold</b>', small),
         Paragraph("The gate decides again, independently, and stamps HOLD where "
                   "a condition applies.", small)],
        [Paragraph('<b>Approve</b>', small),
         Paragraph("A person decides. Nothing else in the system writes anything.", small)],
    ], colWidths=[54, COL_W - 54])
    flow.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
        ("TOPPADDING", (0, 0), (-1, -1), 1),
        ("LINEBELOW", (0, 0), (-1, -2), 0.4, RULE),
    ]))
    s.append(flow)
    s.append(Spacer(1, 5))

    s.append(Paragraph("Checking it yourself", head))
    s.append(Paragraph(
        "Everything runs offline without an API key, so the figures opposite can "
        "be checked rather than trusted:", small))
    s.append(Spacer(1, 3))
    s.append(Paragraph("python -m src.ticket_agent.seed --seed 42", mono))
    s.append(Paragraph("pytest -q", mono))
    s.append(Paragraph("uvicorn src.ticket_agent.web.app:app", mono))
    s.append(Spacer(1, 3))
    s.append(Paragraph(
        "The corpus comes from a fixed seed: 53 tickets, 6 procedures, and five "
        "defects planted on purpose, because real queues arrive carrying all five.",
        small))

    s.append(FrameBreak())

    # ---------------- right column ----------------
    s.append(Paragraph("Using the website", head))
    s.append(Paragraph(
        "<b>1.</b> <b>Overview.</b> Three panels: Jira, Confluence, and the agent "
        "itself. Press <b>Activate</b>. The agent panel turns green and starts "
        "picking up tickets by itself.", step))
    s.append(Paragraph(
        "<b>2.</b> <b>Review queue.</b> Every row is something the agent wants to "
        "do and has not done. A red spine means it stopped. Green means it is ready "
        "to go.", step))
    s.append(Paragraph(
        "<b>3.</b> <b>Open a red row.</b> It carries a HOLD: HUMAN REVIEW stamp and "
        "names the condition that stopped it. Try approving with the note field "
        "empty. <b>It refuses.</b> That is the whole point of the thing.", step))
    s.append(Paragraph(
        "<b>4.</b> <b>Open a green row.</b> The right column shows the matched "
        "procedure and the score it matched at. Edit the reply if it needs it, then "
        "approve. Only now does it reach the ticket.", step))
    s.append(Paragraph(
        "<b>5.</b> <b>Back to Overview.</b> Your decision lands in the counts. Edits "
        "are stored beside the agent's original wording, so the same correction "
        "recurring points at a missing rule, not three unrelated mistakes.", step))
    s.append(Paragraph(
        "Tickets and procedures are browsable from the left rail at any time. "
        "<b>Deactivate</b> stops the agent.", small))
    s.append(Spacer(1, 4))

    s.append(Paragraph("Measured results", head))
    figures = Table([
        rule_row("Retrieval precision@1 (38 covered tickets)", "100%", CLEAR),
        rule_row("Uncovered tickets wrongly matched", "0", CLEAR),
        rule_row("Open tickets held for a human / ready", "29 / 10"),
        rule_row("Correct matches routed to a human anyway", "5 of 38", HOLD),
        rule_row("Automated tests, offline, no API key", "83 passing", CLEAR),
    ], colWidths=[COL_W - 58, 58])
    figures.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5),
        ("LINEBELOW", (0, 0), (-1, -2), 0.4, RULE),
    ]))
    s.append(figures)
    s.append(Spacer(1, 4))
    s.append(Paragraph(
        "The 0.37 threshold was calibrated against known ground truth rather than "
        "picked by feel. It costs 13% recall and buys zero false citations. That "
        "asymmetry is deliberate: a false refusal wastes a reviewer thirty seconds, "
        "while a false citation is a policy stated in writing to someone who will "
        "act on it.", small))
    s.append(Spacer(1, 6))

    s.append(Paragraph("Declaration of AI use", head))
    s.append(Paragraph(
        "This project was built with Claude (Anthropic) acting as a coding agent. "
        "The extent of that is substantial, and worth stating precisely.", body))
    s.append(Paragraph(
        "<b>What the AI produced:</b> essentially all of the source code, the tests, "
        "the interface and its styling, the repository documentation, and the text "
        "on this page. It also proposed the retrieval-scoring approach and "
        "calibrated the 0.37 threshold against the seeded corpus. It caught a "
        "thread-safety bug in the background worker that I would not have found "
        "until it crashed something.", body))
    s.append(Paragraph(
        "<b>What I brought:</b> the problem itself, from service-desk work at TCS; "
        "the constraint that the approval gate had to be structural rather than a "
        "line in a prompt, which shaped most of what followed; the decisions the "
        "agent stopped to ask about, including the data backend, the deployment "
        "target and how the stand-in systems should be labelled; review at each "
        "stage; and the deployment.", body))
    s.append(Paragraph(
        "Anyone can check the claims above against the repository. The test suite "
        "runs without an API key.", body))
    s.append(Paragraph(
        "<b>One limitation, stated plainly:</b> every figure here comes from demo "
        "mode, where a fixed decision procedure stands in for the language model "
        "over the same retrieval results. The safety architecture, the retrieval "
        "numbers and the interface are real. Live-model accuracy and token cost are "
        "not measured at all, and docs/EVIDENCE.md records them as unmeasured "
        "instead of estimating them.", body))

    doc.build(s)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    build()
