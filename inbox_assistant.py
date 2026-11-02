"""
Smart Inbox Assistant
=====================
A lightweight, dependency-free email triage tool.

It scores each email against weighted keyword rules and sender-domain
signals, assigns a category (Career / Academic / Promotion / Normal),
extracts deadlines, detects which emails are waiting on a reply, and
drafts a reply for each one for you to review before sending.

Usage:
    python inbox_assistant.py                      # run on bundled sample data
    python inbox_assistant.py -i my_emails.json    # run on your own emails
    python inbox_assistant.py -d                   # also print draft replies
    python inbox_assistant.py -o report.json       # also export a JSON report
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import textwrap
from dataclasses import dataclass, field, asdict
from pathlib import Path

# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------

# keyword -> weight. Positive pushes priority up, negative pushes it down.
KEYWORD_WEIGHTS: dict[str, int] = {
    "urgent": 5, "asap": 4, "immediately": 4, "action required": 4,
    "deadline": 4, "due": 3, "extended": 2, "reminder": 2,
    "interview": 5, "offer": 4, "internship": 3, "recruiter": 3,
    "application": 2, "assignment": 3, "exam": 4, "grade": 2,
    "sale": -3, "% off": -4, "unsubscribe": -3, "limited time": -3,
    "newsletter": -3, "deal": -2, "promo": -3,
}

# sender domain suffix -> weight
DOMAIN_WEIGHTS: dict[str, int] = {
    ".edu": 3,
    "noreply": -1,
    "newsletter": -3,
    "marketing": -3,
}

CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "Career": ("interview", "offer", "internship", "recruiter", "application"),
    "Academic": ("assignment", "exam", "grade", "professor", "course", "project"),
    "Promotion": ("sale", "% off", "deal", "promo", "unsubscribe", "newsletter"),
}

URGENT_THRESHOLD = 8
IMPORTANT_THRESHOLD = 3

WEEKDAYS = r"(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
MONTHS = (r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|"
          r"july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|"
          r"dec(?:ember)?)")
DEADLINE_PATTERNS = [
    re.compile(rf"\b(?:by|before|due|until)\s+({WEEKDAYS}(?:\s+(?:midnight|noon|morning|evening))?)", re.I),
    re.compile(rf"\b(?:by|before|due|until|on)\s+({MONTHS}\.?\s+\d{{1,2}}(?:st|nd|rd|th)?)", re.I),
    re.compile(r"\b(?:by|before|due|until|on)\s+(\d{1,2}/\d{1,2}(?:/\d{2,4})?)", re.I),
    re.compile(r"\b(tomorrow|tonight|today|next week|end of (?:the )?day)\b", re.I),
]

# phrases that mean the sender is waiting on an answer
REPLY_SIGNALS = (
    "please reply", "reply with", "let me know", "let us know", "your availability",
    "can you", "could you", "would you", "please confirm", "rsvp", "want to",
)
NO_REPLY_SENDERS = ("noreply", "no-reply", "newsletter", "marketing", "notifications")

REPLY_TEMPLATES: dict[str, str] = {
    "Career": (
        "Hi {name},\n\nThank you for reaching out about \"{subject}\". I'm very interested "
        "and would be glad to move forward.{deadline_line}\n\nI'm generally available "
        "[ADD TIMES]. Please let me know what works best for you.\n\nBest regards,\n[Your Name]"
    ),
    "Academic": (
        "Hi {name},\n\nThanks for your email about \"{subject}\".{deadline_line}\n\n"
        "[YOUR ANSWER]\n\nBest,\n[Your Name]"
    ),
    "Normal": (
        "Hi {name},\n\nThanks for your message about \"{subject}\".{deadline_line}\n\n"
        "[YOUR ANSWER]\n\nBest,\n[Your Name]"
    ),
}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Email:
    sender: str
    subject: str
    body: str


@dataclass
class TriageResult:
    sender: str
    subject: str
    score: int
    priority: str
    category: str
    deadlines: list[str] = field(default_factory=list)
    matched: list[str] = field(default_factory=list)
    needs_reply: bool = False
    draft: str = ""


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def score_email(email: Email) -> tuple[int, list[str]]:
    """Return (score, matched_signals) for one email."""
    text = f"{email.subject} {email.body}".lower()
    subject = email.subject.lower()
    score, matched = 0, []

    for kw, weight in KEYWORD_WEIGHTS.items():
        if kw in text:
            # keywords in the subject line count double
            w = weight * 2 if kw in subject else weight
            score += w
            matched.append(f"{kw}({w:+d})")

    sender = email.sender.lower()
    for marker, weight in DOMAIN_WEIGHTS.items():
        if marker in sender:
            score += weight
            matched.append(f"sender:{marker}({weight:+d})")

    if email.subject.isupper() or "!!" in email.subject:
        # shouty subjects are usually marketing, not real urgency
        score -= 2
        matched.append("shouty-subject(-2)")

    return score, matched


def categorize(email: Email) -> str:
    text = f"{email.sender} {email.subject} {email.body}".lower()
    hits = {cat: sum(kw in text for kw in kws) for cat, kws in CATEGORY_KEYWORDS.items()}
    best = max(hits, key=hits.get)
    return best if hits[best] > 0 else "Normal"


def extract_deadlines(text: str) -> list[str]:
    found: list[str] = []
    for pattern in DEADLINE_PATTERNS:
        for m in pattern.finditer(text):
            value = m.group(1).strip()
            if value.lower() not in (f.lower() for f in found):
                found.append(value)
    return found


def needs_reply(email: Email, category: str) -> bool:
    """True when a real person is asking us something and expects an answer."""
    sender = email.sender.lower()
    if category == "Promotion" or any(s in sender for s in NO_REPLY_SENDERS):
        return False
    text = f"{email.subject} {email.body}".lower()
    return "?" in text or any(sig in text for sig in REPLY_SIGNALS)


def sender_name(sender: str) -> str:
    """'professor.lee@university.edu' -> 'Professor Lee'."""
    local = sender.split("@")[0]
    return " ".join(part.capitalize() for part in re.split(r"[._\-]+", local) if part) or "there"


def draft_reply(email: Email, category: str, deadlines: list[str]) -> str:
    template = REPLY_TEMPLATES.get(category, REPLY_TEMPLATES["Normal"])
    deadline_line = f" I've noted the timeline ({deadlines[0]})." if deadlines else ""
    return template.format(name=sender_name(email.sender), subject=email.subject,
                           deadline_line=deadline_line)


def priority_label(score: int) -> str:
    if score >= URGENT_THRESHOLD:
        return "URGENT"
    if score >= IMPORTANT_THRESHOLD:
        return "IMPORTANT"
    if score < 0:
        return "LOW"
    return "NORMAL"


def triage(emails: list[Email]) -> list[TriageResult]:
    results = []
    for e in emails:
        score, matched = score_email(e)
        category = categorize(e)
        deadlines = extract_deadlines(f"{e.subject} {e.body}")
        reply = needs_reply(e, category)
        results.append(TriageResult(
            sender=e.sender,
            subject=e.subject,
            score=score,
            priority=priority_label(score),
            category=category,
            deadlines=deadlines,
            matched=matched,
            needs_reply=reply,
            draft=draft_reply(e, category, deadlines) if reply else "",
        ))
    return sorted(results, key=lambda r: r.score, reverse=True)


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------

COLORS = {"URGENT": "\033[91m", "IMPORTANT": "\033[93m",
          "NORMAL": "\033[0m", "LOW": "\033[90m"}
RESET = "\033[0m"


def load_emails(path: Path) -> list[Email]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [Email(d.get("sender", ""), d.get("subject", ""), d.get("body", "")) for d in data]


def print_report(results: list[TriageResult], color: bool = True) -> None:
    print("=" * 72)
    print(" SMART INBOX ASSISTANT - triage report")
    print("=" * 72)
    for r in results:
        c, reset = (COLORS[r.priority], RESET) if color else ("", "")
        print(f"{c}[{r.priority:<9}] score {r.score:+3d} | {r.category:<9} | {r.subject}{reset}")
        print(f"             from: {r.sender}")
        if r.deadlines and r.priority != "LOW":
            print(f"             deadline: {', '.join(r.deadlines)}")
        if r.needs_reply:
            print("             >> needs reply - draft ready")
    print("-" * 72)
    counts = {p: sum(r.priority == p for r in results) for p in COLORS}
    print(" Summary: " + " | ".join(f"{k}: {v}" for k, v in counts.items())
          + f" | replies needed: {sum(r.needs_reply for r in results)}")
    print("=" * 72)


def print_drafts(results: list[TriageResult]) -> None:
    for r in results:
        if r.draft:
            print(f"\n--- Draft reply to {r.sender} | Re: {r.subject} ---")
            for para in r.draft.split("\n"):
                print(textwrap.fill(para, width=72) if para else "")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Triage an inbox by priority and category.")
    parser.add_argument("-i", "--input", type=Path,
                        default=Path(__file__).with_name("sample_emails.json"),
                        help="JSON file with a list of {sender, subject, body}")
    parser.add_argument("-o", "--output", type=Path, help="write the report as JSON")
    parser.add_argument("-d", "--drafts", action="store_true",
                        help="print draft replies for emails that need one")
    parser.add_argument("--no-color", action="store_true", help="disable ANSI colors")
    args = parser.parse_args(argv)

    if not args.input.exists():
        print(f"Input file not found: {args.input}", file=sys.stderr)
        return 1

    results = triage(load_emails(args.input))
    print_report(results, color=not args.no_color)
    if args.drafts:
        print_drafts(results)

    if args.output:
        args.output.write_text(json.dumps([asdict(r) for r in results], indent=2), encoding="utf-8")
        print(f"JSON report written to {args.output}")
    return 0


if __name__ == "__main__":
    if sys.platform == "win32":
        import os
        os.system("")  # enable ANSI colors in Windows terminals
    sys.exit(main())
