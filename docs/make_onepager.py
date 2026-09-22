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
RENDER_URL = "<paste your Render URL here>"

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
        "An agentic AI system that drafts service-desk replies grounded in "
        "documented procedure — and cannot send anything without a human.",
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
        "An internal IT service desk takes roughly 1,200 tickets a month. Most are "
        "repeats of something already solved and documented — but documentation "
        "only helps if someone remembers it exists. This closes that loop both "
        "ways.", body))
    s.append(Paragraph(
        "<b>Agent A (resolver)</b> reads a new ticket, looks up the requester's "
        "support tier, searches the procedure library, and drafts a reply that "
        "cites the procedure by its document ID. <b>Agent B (author)</b> takes the "
        "opposite case: when a ticket was resolved that no procedure covered, it "
        "reads how the human actually fixed it and drafts the missing procedure.", body))
    s.append(Paragraph(
        "The problem is modelled on enterprise IT support work — Jira for tickets, "
        "Confluence for procedures. In this build those are stand-ins: a local "
        "ticket store and a folder of markdown, so the demonstration has no "
        "external dependency. The interface labels them as stand-ins rather than "
        "claiming a live connection.", body))

    s.append(Paragraph("The core design decision", head))
    s.append(Paragraph(
        "The human-approval gate is <b>a code boundary, not a prompt instruction</b>. "
        "Asking a model to seek permission is a request it can be argued out of — "
        "including by text inside a ticket. Here it is structural, in four "
        "independent layers:", body))

    layers = Table([
        [Paragraph('<b>1. Rule</b>', small),
         Paragraph("Stated in CLAUDE.md and the system prompt. A request.", small)],
        [Paragraph('<b>2. Gate</b>', small),
         Paragraph("Runs in code after the model speaks, reading the ticket and "
                   "requester record — not the model's opinion.", small)],
        [Paragraph('<b>3. Boundary</b>', small),
         Paragraph("The model is never given a write-capable tool. Its only "
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
        "Four conditions always stop a draft for a person: a request for access or "
        "permissions, critical severity, a Gold-tier requester (an unknown "
        "requester counts as Gold), and — the important one — <b>no procedure "
        "covering the ticket</b>. The agent reporting that it cannot answer is a "
        "correct outcome, not a failure: it routes the ticket to Agent B to write "
        "what is missing.", body))
    s.append(Paragraph(
        "This guards against the failure that made Air Canada liable for its "
        "chatbot — a confident, well-formed policy that did not exist. Citations "
        "are verified in code after the model speaks; an ID that does not exist is "
        "stripped and the proposal downgraded.", body))

    s.append(Paragraph("How it works", head))
    flow = Table([
        [Paragraph('<b>Read</b>', small),
         Paragraph("The agent picks its own inputs: the ticket, the requester "
                   "record, the procedure library. Nothing is pasted in for it.", small)],
        [Paragraph('<b>Score</b>', small),
         Paragraph("A match score computed in code, not the model's opinion of "
                   "its own confidence. The reviewer sees the same number.", small)],
        [Paragraph('<b>Propose</b>', small),
         Paragraph("The model's only terminal move: a structured object, not an "
                   "action. A second instance writes the reply from three inputs "
                   "— ticket, tier, clause.", small)],
        [Paragraph('<b>Hold</b>', small),
         Paragraph("The gate re-decides independently and stamps HOLD where any "
                   "condition applies.", small)],
        [Paragraph('<b>Approve</b>', small),
         Paragraph("A person decides. This is the only path that writes anything.", small)],
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
        "The repository runs offline with no API key. Every figure opposite is "
        "reproducible:", small))
    s.append(Spacer(1, 3))
    s.append(Paragraph("pip install -r requirements.txt", mono))
    s.append(Paragraph("python -m src.ticket_agent.seed --seed 42", mono))
    s.append(Paragraph("pytest -q", mono))
    s.append(Paragraph("uvicorn src.ticket_agent.web.app:app", mono))
    s.append(Spacer(1, 3))
    s.append(Paragraph(
        "The corpus is seeded deterministically: 53 tickets, 6 procedures, and "
        "five defects planted on purpose, because real queues carry all five.", small))

    s.append(FrameBreak())

    # ---------------- right column ----------------
    s.append(Paragraph("Using the website", head))
    s.append(Paragraph(
        "<b>1.</b> <b>Overview.</b> Three panels show the systems: Jira (where "
        "tickets come from), Confluence (where procedures live), and the agent "
        "itself. Press <b>Activate</b> — the agent panel turns green and begins "
        "picking up tickets on its own.", step))
    s.append(Paragraph(
        "<b>2.</b> <b>Review queue.</b> Each row is something the agent wants to "
        "do and has not done. A red spine means it stopped; green means it is "
        "ready to send.", step))
    s.append(Paragraph(
        "<b>3.</b> <b>Open a red row.</b> It carries a HOLD: HUMAN REVIEW stamp "
        "and states which condition stopped it. Try approving it with the note "
        "field empty — <b>it refuses to send</b>. That is the system working.", step))
    s.append(Paragraph(
        "<b>4.</b> <b>Open a green row.</b> The right column shows which procedure "
        "matched and the score it matched at. Edit the reply if needed, then "
        "approve — only now does it appear on the ticket.", step))
    s.append(Paragraph(
        "<b>5.</b> <b>Back to Overview.</b> The decision appears in the counts. "
        "Edits are kept alongside what the agent originally wrote: repeated edits "
        "of the same kind indicate a missing rule, not three separate mistakes.", step))
    s.append(Paragraph(
        "Browse tickets and procedures from the left rail at any time. "
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
        "The match threshold (0.37) was calibrated against known ground truth, not "
        "guessed. It costs 13% recall and buys zero false citations — a deliberate "
        "asymmetry, since a false refusal costs a reviewer thirty seconds while a "
        "false citation is a policy stated in writing to someone who will act on it.", small))
    s.append(Spacer(1, 6))

    s.append(Paragraph("Declaration of AI use", head))
    s.append(Paragraph(
        "This project was built with Claude (Anthropic), used as a coding agent "
        "throughout. The extent of that use is material and is stated in full.", body))
    s.append(Paragraph(
        "<b>Generated by AI:</b> substantially all source code, the test suite, the "
        "interface and its styling, the repository documentation, and the text of "
        "this document. AI also proposed the retrieval-scoring method, calibrated "
        "the confidence threshold, and identified the thread-safety defect in the "
        "background worker.", body))
    s.append(Paragraph(
        "<b>My contribution:</b> defining the problem from my own service-desk "
        "experience at TCS; the governing constraint that the approval gate must "
        "be structural rather than a prompt instruction; the design decisions the "
        "agent raised for a human to settle — data backend, deployment target, "
        "interface scope, how the systems are labelled; direction of the build and "
        "review of each stage; and deployment.", body))
    s.append(Paragraph(
        "<b>Verification:</b> every factual claim above is reproducible from the "
        "repository. The tests run offline without an API key.", body))
    s.append(Paragraph(
        "<b>Stated limitation:</b> the results above were produced in demo mode, "
        "where the language model is replaced by a fixed decision procedure over "
        "the same retrieval results. The safety architecture, retrieval and "
        "interface figures are real; live-model accuracy and token cost are <b>not "
        "measured</b>, and are recorded as unmeasured in docs/EVIDENCE.md rather "
        "than estimated.", body))

    doc.build(s)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    build()
