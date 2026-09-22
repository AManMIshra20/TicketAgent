"""Synthetic corpus generator for the Meridian service desk.

Why synthetic: a real ticket queue cannot be committed to a public repo, and
a demo that depends on a live Jira is a demo that breaks on submission day.
This produces a corpus with the same shape as the real thing -- including its
defects -- from a seed, so every run is reproducible and the tests are stable.

What it plants on purpose:

  * Clusters. Several open tickets that are near-duplicates of an earlier
    resolved one, so Agent A has real matches to find rather than a corpus
    where every ticket is unique.
  * Gaps. Topics with resolved tickets but no SOP, so Agent B has something
    genuine to write. The SOP pack ships covering only some of the clusters;
    the gaps are the demo.
  * Five defects in the raw feed (see PLANTED_DEFECTS). The data you get is
    never the data you want, and cleaning is an agent task that reports what
    it changed -- not a silent preprocessing step.

Ground truth (which topic a ticket really belongs to, which SOP really covers
it) is written to `_truth.json` and stored on the ticket in `truth_*` fields.
It is never exposed through a read tool. It is what we grade against, not
what we tell the agent.

    python -m src.ticket_agent.seed --seed 42
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from .models import Category, Comment, Requester, SOP, Severity, Ticket, TicketStatus, Tier
from .store import SQLiteStore

# The corpus is generated relative to a fixed date so a given seed always
# yields the same timestamps. Today's date would make every run differ.
EPOCH = datetime(2026, 3, 2, 9, 0)

PLANTED_DEFECTS = {
    "missing_requester": 14,
    "duplicate_ticket": 3,
    "wrong_category": 8,
    "blank_description": 4,
    # Date formats are applied across the whole file, not to a fixed count.
    "date_formats": 3,
}


# ---------------------------------------------------------------------------
# The company
# ---------------------------------------------------------------------------

RAW_REQUESTERS = [
    ("MER-001", "Anita Rao",        "Finance",            Tier.GOLD),
    ("MER-002", "Devang Shah",      "Finance",            Tier.GOLD),
    ("MER-003", "Priya Menon",      "Supply Chain",       Tier.GOLD),
    ("MER-004", "Karan Iyer",       "Supply Chain",       Tier.SILVER),
    ("MER-005", "Sneha Kulkarni",   "Manufacturing",      Tier.SILVER),
    ("MER-006", "Rahul Bose",       "Manufacturing",      Tier.SILVER),
    ("MER-007", "Farah Qureshi",    "Human Resources",    Tier.SILVER),
    ("MER-008", "Vikram Nair",      "Human Resources",    Tier.BRONZE),
    ("MER-009", "Meera Joshi",      "Sales",              Tier.BRONZE),
    ("MER-010", "Arjun Pillai",     "Sales",              Tier.BRONZE),
    ("MER-011", "Nikhil Varma",     "IT Operations",      Tier.SILVER),
    ("MER-012", "Tara D'Souza",     "Legal & Compliance", Tier.GOLD),
]

AGENTS = ["S. Banerjee", "M. Krishnan", "J. Fernandes"]


@dataclass
class Topic:
    """One recurring kind of problem, and how it actually gets solved."""

    key: str
    category: Category
    severity: Severity
    titles: list[str]
    bodies: list[str]
    resolution: str
    resolution_steps: list[str]
    has_sop: bool
    sop_id: str | None = None
    sop_title: str | None = None
    is_access_grant: bool = False
    open_count: int = 3
    resolved_count: int = 1


TOPICS: list[Topic] = [
    # ---- Covered by an SOP: Agent A should resolve these from the pack ----
    Topic(
        key="vpn_mfa_lockout",
        category=Category.PLATFORM_SUPPORT_ACCESS,
        severity=Severity.HIGH,
        titles=[
            "Cannot connect to VPN after MFA reset",
            "VPN login failing since authenticator re-enrolment",
            "GlobalConnect VPN rejects credentials after MFA change",
            "Unable to VPN in - MFA prompt loops",
        ],
        bodies=[
            "I reset my authenticator app yesterday and now the VPN client rejects my login. "
            "It shows 'authentication failed' straight after the push notification.",
            "Since re-enrolling MFA on my new phone I cannot get on the VPN. The push arrives, "
            "I approve it, and the client still times out.",
            "VPN has not worked since my MFA was reset by the helpdesk. Getting error AUTH-5521.",
            "MFA prompt keeps looping on VPN sign-in. Approved it four times, no luck.",
        ],
        resolution="Stale VPN device certificate after MFA re-enrolment.",
        resolution_steps=[
            "Confirmed the MFA re-enrolment timestamp in the identity console.",
            "Cleared the cached device certificate from the VPN client profile.",
            "Had the user sign out fully and re-authenticate to reissue the certificate.",
            "Verified connection from the user's machine before closing.",
        ],
        has_sop=True,
        sop_id="SOP-ACC-01",
        sop_title="VPN authentication failure after MFA re-enrolment",
        open_count=6,
    ),
    Topic(
        key="sap_batch_failure",
        category=Category.BUG,
        severity=Severity.CRITICAL,
        titles=[
            "Nightly SAP batch job failed - month end blocked",
            "SAP job Z_FI_CLOSE aborted overnight",
            "Month-end close batch did not complete",
        ],
        bodies=[
            "The overnight finance batch aborted at 02:40. Month-end close is blocked and "
            "we need this before the reporting cut-off today.",
            "Z_FI_CLOSE shows status ABORTED in SM37. No output produced. This is blocking close.",
            "Batch did not finish last night. Finance cannot proceed with close.",
        ],
        resolution="Batch aborted on a table lock held by an unfinished posting session.",
        resolution_steps=[
            "Checked SM37 for the job log and identified the abort point.",
            "Found an orphaned posting session holding a lock in SM12.",
            "Released the lock after confirming with the posting user that the session was dead.",
            "Restarted the job from the failed step and confirmed completion.",
        ],
        has_sop=True,
        sop_id="SOP-PLT-02",
        sop_title="Overnight SAP batch abort - diagnosis and restart",
        open_count=5,
    ),
    Topic(
        key="shared_drive_access",
        category=Category.PLATFORM_SUPPORT_ACCESS,
        severity=Severity.MEDIUM,
        titles=[
            "Request access to Finance shared drive",
            "Need permissions for FIN-Reporting folder",
            "Please grant access to the month-end folder",
            "Access request: Finance reporting share",
        ],
        bodies=[
            "I have joined the reporting team this week and need read/write access to the "
            "FIN-Reporting share to pick up the month-end pack.",
            "Could you please grant me access to \\\\meridian\\finance\\reporting. My manager "
            "has approved over email.",
            "Requesting access to the finance month-end folder for my new role.",
            "Need permission on the FIN-Reporting drive. Starting on the close process Monday.",
        ],
        resolution="Access granted after manager approval and entitlement review.",
        resolution_steps=[
            "Verified the request against the joiner record and the approving manager.",
            "Confirmed the role mapping for the requested share in the entitlement matrix.",
            "Raised the grant through the access management workflow for sign-off.",
            "Added the user to the correct security group once sign-off was recorded.",
        ],
        has_sop=True,
        sop_id="SOP-ACC-04",
        sop_title="Shared drive access requests - approval and entitlement check",
        is_access_grant=True,
        open_count=6,
    ),
    Topic(
        key="password_expiry",
        category=Category.PLATFORM_SUPPORT_ACCESS,
        severity=Severity.MEDIUM,
        titles=[
            "Password expired, locked out of workstation",
            "Cannot log in - password expiry on return from leave",
            "Account locked after too many attempts",
        ],
        bodies=[
            "I was on leave for three weeks and my password expired while I was away. "
            "I cannot log in to my workstation to change it.",
            "Back from leave and locked out. The self-service reset page says my account is locked.",
            "Locked out after entering the old password too many times. Need an unlock.",
        ],
        resolution="Account unlocked and password reset through verified self-service path.",
        resolution_steps=[
            "Verified identity against the callback number on the HR record.",
            "Unlocked the account in the directory.",
            "Walked the user through a self-service reset so the new password is not known to IT.",
            "Confirmed successful sign-in before closing.",
        ],
        has_sop=True,
        sop_id="SOP-ACC-03",
        sop_title="Account lockout and expired password recovery",
        open_count=5,
    ),
    Topic(
        key="printer_queue",
        category=Category.PLATFORM_SUPPORT_ACCESS,
        severity=Severity.LOW,
        titles=[
            "Print queue stuck on floor 3",
            "Nothing printing from the third floor MFD",
            "Printer jobs queue but never print",
        ],
        bodies=[
            "Jobs sit in the queue on the floor 3 printer and never come out. Three of us "
            "are affected.",
            "The MFD near the east stairwell accepts jobs but prints nothing since this morning.",
            "Print jobs pile up and the device shows ready. Restarting my machine did not help.",
        ],
        resolution="Print spooler service hung on the print server; queue cleared and restarted.",
        resolution_steps=[
            "Confirmed the device was online and reachable from the print server.",
            "Found the spooler service in a hung state on the server hosting that queue.",
            "Cleared the stuck job from the spool directory.",
            "Restarted the spooler and confirmed a test page from the affected floor.",
        ],
        has_sop=True,
        sop_id="SOP-PLT-05",
        sop_title="Stuck print queue - spooler recovery",
        open_count=5,
    ),
    Topic(
        key="laptop_slow",
        category=Category.BUG,
        severity=Severity.MEDIUM,
        titles=[
            "Laptop extremely slow after the September update",
            "Machine unusable since last patch cycle",
            "Very high disk usage after update",
        ],
        bodies=[
            "Since the update pushed last week my laptop takes about ten minutes to become "
            "usable after login. Disk sits at 100 percent.",
            "The patch cycle has made my machine crawl. Task manager shows constant disk activity.",
            "Everything is slow since the update. Opening Outlook takes minutes.",
        ],
        resolution="Post-update search re-index saturating disk on machines with small SSDs.",
        resolution_steps=[
            "Confirmed the update build number and the machine's disk model.",
            "Identified the search indexer rebuilding its catalogue after the update.",
            "Paused indexing, then rebuilt the catalogue out of hours.",
            "Confirmed normal boot-to-usable time with the user the following morning.",
        ],
        has_sop=True,
        sop_id="SOP-PLT-07",
        sop_title="Severe slowdown following a patch cycle",
        open_count=5,
    ),

    # ---- NOT covered by an SOP: Agent B should write one from the thread ----
    Topic(
        key="teams_recording_missing",
        category=Category.PLATFORM_SUPPORT_ACCESS,
        severity=Severity.MEDIUM,
        titles=[
            "Teams meeting recording not appearing in SharePoint",
            "Recorded a session but cannot find the file",
            "Meeting recording missing from the channel",
        ],
        bodies=[
            "I recorded the supplier review on Tuesday. The chat says the recording is saved "
            "but nothing appears in the channel's Files tab.",
            "Cannot locate a recording from last week. It is not in my OneDrive or the channel.",
            "Recording finished but the link in chat gives a permission error.",
        ],
        resolution="Recording saved to the organiser's OneDrive, not the channel, because the "
        "meeting was created outside the channel.",
        resolution_steps=[
            "Established who created the meeting and whether it was scheduled in the channel.",
            "Found the recording under the organiser's OneDrive Recordings folder.",
            "Explained that only channel-scheduled meetings save to the channel's document library.",
            "Had the organiser move the file to the channel and reshare the link.",
        ],
        has_sop=False,
        open_count=2,
        resolved_count=2,
    ),
    Topic(
        key="expense_tool_timeout",
        category=Category.BUG,
        severity=Severity.HIGH,
        titles=[
            "Expense tool times out when submitting a claim",
            "Cannot submit expenses - session expires",
            "Expense submission fails at the final step",
        ],
        bodies=[
            "Every time I hit submit on an expense claim the page spins and then returns me "
            "to the login screen. Claim is not saved.",
            "The expense system times out at submission. I have tried twice today, claim lost both times.",
            "Submission step fails with a session error. Draft disappears after.",
        ],
        resolution="Claims with more than twenty line items exceeded the gateway timeout.",
        resolution_steps=[
            "Reproduced with a large claim and captured the gateway timeout in the logs.",
            "Confirmed small claims submit normally, so the fault is size-dependent.",
            "Advised splitting the claim across two submissions as an immediate workaround.",
            "Raised the timeout threshold with the vendor as a change request.",
        ],
        has_sop=False,
        open_count=2,
        resolved_count=2,
    ),
    Topic(
        key="onboarding_kt_handover",
        category=Category.KNOWLEDGE_TRANSFER,
        severity=Severity.MEDIUM,
        titles=[
            "KT handover documentation for new joiner",
            "Need the knowledge transfer pack for the incoming analyst",
            "Handover notes missing for the reporting role",
        ],
        bodies=[
            "A new analyst joins the reporting team on Monday and there is no handover pack "
            "from the person who left. What is the process to reconstruct it?",
            "The previous owner has left and we have no KT notes for the weekly reporting run.",
            "Who owns producing handover documentation when someone exits mid-cycle?",
        ],
        resolution="Reconstructed the handover from the leaver's runbook and recorded sessions.",
        resolution_steps=[
            "Identified the leaver's documented runbooks in the team space.",
            "Pulled the last three recorded working sessions covering the weekly run.",
            "Drafted a handover pack and had the receiving analyst walk it end to end.",
            "Recorded the gaps the walkthrough exposed and closed them with the manager.",
        ],
        has_sop=False,
        open_count=2,
        resolved_count=2,
    ),
    Topic(
        key="license_reallocation",
        category=Category.PROCESS_TRANSFORMATION,
        severity=Severity.LOW,
        titles=[
            "Reassign analytics licence from a leaver",
            "Unused licence needs reallocating",
            "Licence still assigned to someone who left",
        ],
        bodies=[
            "One of our analysts left last month and their analytics licence is still assigned. "
            "We need it for the replacement.",
            "We are at our licence cap but one seat belongs to a leaver. How do we reclaim it?",
            "Licence reallocation request - seat held by a departed employee.",
        ],
        resolution="Seat reclaimed through the leaver process after confirming no pending content.",
        resolution_steps=[
            "Checked the leaver record and the date of exit.",
            "Confirmed with the team that no unpublished content sat under that account.",
            "Exported the account's saved content to the team workspace before removal.",
            "Released the seat and assigned it to the replacement analyst.",
        ],
        has_sop=False,
        open_count=1,
        resolved_count=2,
    ),
]


# ---------------------------------------------------------------------------
# SOP bodies
# ---------------------------------------------------------------------------

def _sop_body(topic: Topic) -> str:
    """Render an SOP in the pack's house format.

    Every SOP carries its id in the heading, because the id is what the agent
    must cite. A document the agent cannot name is a document it cannot use.
    """
    steps = "\n".join(f"{i}. {s}" for i, s in enumerate(topic.resolution_steps, 1))
    return f"""# [{topic.sop_id}] {topic.sop_title}

**Category:** {topic.category.value}
**Applies to:** {topic.key.replace('_', ' ')}

## Symptom

{topic.bodies[0]}

## Root cause

{topic.resolution}

## Resolution steps

{steps}

## Before you close

- Confirm the outcome with the requester, in their words, not yours.
- If the requester's tier is Gold, hand the close to the named owner for that
  business unit rather than closing directly.
"""


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------

@dataclass
class Corpus:
    requesters: list[Requester]
    tickets: list[Ticket]
    sops: list[SOP]
    raw_rows: list[dict] = field(default_factory=list)
    truth: dict = field(default_factory=dict)


def generate(seed: int = 42) -> Corpus:
    """Build the whole corpus deterministically from `seed`."""
    rng = random.Random(seed)

    requesters = [
        Requester(requester_id=r[0], name=r[1], business_unit=r[2], tier=r[3])
        for r in RAW_REQUESTERS
    ]

    sops = [
        SOP(
            sop_id=t.sop_id,
            title=t.sop_title,
            category=t.category,
            topic=t.key,
            body=_sop_body(t),
            updated_at=EPOCH - timedelta(days=rng.randint(40, 120)),
            revision=rng.randint(1, 3),
        )
        for t in TOPICS
        if t.has_sop
    ]

    tickets: list[Ticket] = []
    counter = 1000

    for topic in TOPICS:
        # The origin tickets: already resolved, with a thread that narrates
        # what was actually done. Agent B can only work from these if the
        # resolution comments say something -- so they do.
        for r in range(topic.resolved_count):
            counter += 1
            created = EPOCH - timedelta(days=rng.randint(25, 70), hours=rng.randint(0, 8))
            resolved = created + timedelta(hours=rng.randint(2, 30))
            requester = rng.choice(requesters)
            agent = rng.choice(AGENTS)

            comments = [
                Comment(
                    author=agent,
                    body=f"Picked this up. {topic.resolution_steps[0]}",
                    created_at=created + timedelta(hours=1),
                ),
                Comment(
                    author=requester.name,
                    body="Thanks - let me know if you need anything from my side.",
                    created_at=created + timedelta(hours=2),
                ),
                Comment(
                    author=agent,
                    body="Root cause: "
                    + topic.resolution
                    + " Steps taken: "
                    + " ".join(topic.resolution_steps[1:]),
                    created_at=resolved,
                ),
            ]

            tickets.append(
                Ticket(
                    ticket_id=f"MERT-{counter}",
                    title=topic.titles[r % len(topic.titles)],
                    body=topic.bodies[r % len(topic.bodies)],
                    category=topic.category,
                    severity=topic.severity,
                    requester_id=requester.requester_id,
                    status=TicketStatus.RESOLVED,
                    created_at=created,
                    resolved_at=resolved,
                    comments=comments,
                    resolution_notes=topic.resolution,
                    triaged=True,
                    truth_topic=topic.key,
                    truth_sop_id=topic.sop_id,
                )
            )

        # The cluster: open near-duplicates of the origin.
        for i in range(topic.open_count):
            counter += 1
            created = EPOCH - timedelta(days=rng.randint(0, 6), hours=rng.randint(0, 9))
            requester = rng.choice(requesters)
            tickets.append(
                Ticket(
                    ticket_id=f"MERT-{counter}",
                    title=topic.titles[(i + 1) % len(topic.titles)],
                    body=topic.bodies[(i + 1) % len(topic.bodies)],
                    category=topic.category,
                    severity=topic.severity,
                    requester_id=requester.requester_id,
                    status=TicketStatus.OPEN,
                    created_at=created,
                    truth_topic=topic.key,
                    truth_sop_id=topic.sop_id,
                )
            )

    tickets.sort(key=lambda t: t.created_at)

    corpus = Corpus(requesters=requesters, tickets=tickets, sops=sops)
    corpus.raw_rows, corpus.truth = _plant_defects(tickets, rng)
    corpus.truth["seed"] = seed
    corpus.truth["counts"] = {
        "tickets": len(tickets),
        "raw_rows": len(corpus.raw_rows),
        "sops": len(sops),
        "requesters": len(requesters),
        "topics_with_sop": sum(1 for t in TOPICS if t.has_sop),
        "topics_without_sop": sum(1 for t in TOPICS if not t.has_sop),
    }
    return corpus


# The three date formats the raw feed mixes. Any time-based filter written
# against this file without normalising first will silently drop rows.
_DATE_FORMATS = ["%Y-%m-%d %H:%M", "%d/%m/%Y %H:%M", "%b %d, %Y %I:%M %p"]


def _plant_defects(tickets: list[Ticket], rng: random.Random) -> tuple[list[dict], dict]:
    """Produce the messy raw feed, and record exactly what was broken.

    The tickets themselves stay clean in memory -- these defects live only in
    the exported CSV, which is what the cleaning pass reads. The truth record
    is what the tests assert the cleaning pass found.
    """
    rows = [
        {
            "ticket_id": t.ticket_id,
            "title": t.title,
            "description": t.body,
            "category": t.category.value,
            "severity": t.severity.value,
            "requester_id": t.requester_id or "",
            "status": t.status.value,
            "created_at": t.created_at.strftime(_DATE_FORMATS[i % 3]),
            "resolution_notes": t.resolution_notes,
        }
        for i, t in enumerate(tickets)
    ]

    truth: dict[str, list] = {
        "missing_requester": [],
        "duplicate_ticket": [],
        "wrong_category": [],
        "blank_description": [],
        "date_formats": _DATE_FORMATS,
    }

    indexes = list(range(len(rows)))

    # 1. Missing plan tier -- the agent cannot check entitlement without it.
    for i in rng.sample(indexes, PLANTED_DEFECTS["missing_requester"]):
        rows[i]["requester_id"] = ""
        truth["missing_requester"].append(rows[i]["ticket_id"])

    # 2. Wrong category -- labelled 'question', describes an outage.
    outage_candidates = [
        i for i in indexes if rows[i]["severity"] in ("high", "critical")
    ]
    for i in rng.sample(outage_candidates, min(PLANTED_DEFECTS["wrong_category"], len(outage_candidates))):
        rows[i]["category"] = Category.QUESTION.value
        truth["wrong_category"].append(rows[i]["ticket_id"])

    # 3. Blank description -- a subject line and nothing in the body.
    blank_candidates = [i for i in indexes if rows[i]["ticket_id"] not in truth["wrong_category"]]
    for i in rng.sample(blank_candidates, PLANTED_DEFECTS["blank_description"]):
        rows[i]["description"] = ""
        truth["blank_description"].append(rows[i]["ticket_id"])

    # 4. Duplicates -- same id twice, different descriptions. Appended at the
    #    end so they are not adjacent to their twin; a cleaner that only
    #    compares neighbouring rows will miss them.
    for i in rng.sample(indexes, PLANTED_DEFECTS["duplicate_ticket"]):
        twin = dict(rows[i])
        twin["description"] = (
            "Reported again by the user - " + (twin["description"] or twin["title"]).lower()
        )
        twin["created_at"] = twin["created_at"]
        rows.append(twin)
        truth["duplicate_ticket"].append(rows[i]["ticket_id"])

    return rows, truth


# ---------------------------------------------------------------------------
# Export and ingest
# ---------------------------------------------------------------------------

def write_corpus(corpus: Corpus, out_dir: Path) -> None:
    """Write the raw feed, the requester table, the SOP pack and the truth."""
    out_dir.mkdir(parents=True, exist_ok=True)

    with (out_dir / "tickets_raw.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(corpus.raw_rows[0].keys()))
        writer.writeheader()
        writer.writerows(corpus.raw_rows)

    with (out_dir / "requesters.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["requester_id", "name", "business_unit", "tier"])
        for r in corpus.requesters:
            writer.writerow([r.requester_id, r.name, r.business_unit, r.tier.value])

    sop_dir = out_dir / "sops"
    if sop_dir.exists():
        shutil.rmtree(sop_dir)
    sop_dir.mkdir(parents=True)
    for sop in corpus.sops:
        (sop_dir / f"{sop.sop_id}.md").write_text(sop.body, encoding="utf-8")

    (out_dir / "_truth.json").write_text(
        json.dumps(corpus.truth, indent=2, sort_keys=True), encoding="utf-8"
    )


def ingest(corpus: Corpus, store: SQLiteStore) -> None:
    """Load the corpus into the store.

    Tickets go in as generated. The defects stay in the CSV, which is what
    the cleaning pass operates on -- the store holds the records the app
    serves so the UI has content on first boot.
    """
    for requester in corpus.requesters:
        store.upsert_requester(requester)
    for sop in corpus.sops:
        store.upsert_sop(sop)
    for ticket in corpus.tickets:
        store.upsert_ticket(ticket)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the synthetic corpus.")
    parser.add_argument("--seed", type=int, default=42, help="Determines the corpus.")
    parser.add_argument(
        "--out", default="data/seed", help="Where to write the CSVs and SOP pack."
    )
    parser.add_argument("--db", default=None, help="SQLite path. Defaults to config.")
    parser.add_argument(
        "--no-ingest", action="store_true", help="Write files only; do not touch the store."
    )
    args = parser.parse_args()

    corpus = generate(args.seed)
    out_dir = Path(args.out)
    write_corpus(corpus, out_dir)

    print(f"seed={args.seed}")
    print(f"  {len(corpus.tickets)} tickets ({len(corpus.raw_rows)} raw rows with defects)")
    print(f"  {len(corpus.sops)} SOPs covering {corpus.truth['counts']['topics_with_sop']} topics")
    print(f"  {corpus.truth['counts']['topics_without_sop']} topics have no SOP - Agent B's work")
    print(f"  written to {out_dir}/")

    if not args.no_ingest:
        db_path = args.db or "ticket_agent.db"
        store = SQLiteStore(db_path)
        ingest(corpus, store)
        store.close()
        print(f"  ingested into {db_path}")


if __name__ == "__main__":
    main()
