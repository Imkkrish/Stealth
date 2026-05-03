"""Slot-based system-prompt composer.

The prompt is built per-request from typed slots:

  ROLE              constant   neutral persona
  OUTPUT_CONTRACT   constant   formatting rules per question type
  LANGUAGE          per-user   preferred coding language (default Python)
  RESUME            per-user   parsed resume text (optional)
  INTERVIEW_CONTEXT per-user   "Senior backend SWE at Stripe", etc. (optional)
  MODE              per-call   "text" or "vision" — vision adds parsing rules
  SAFETY            constant   short, terminal: don't fabricate, no AI disclaimers

Two entry points:
  compose(slots)              -> provider-neutral markdown
  compose_for(provider, slots)-> provider-tuned (XML for Claude, markdown otherwise)
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# Constant slots
# ---------------------------------------------------------------------------

ROLE = """You are a focused interview-assistance copilot. The candidate is in a live technical interview right now. Every word you produce will be read by them in real time and possibly typed into a code editor or spoken aloud. Optimize for being IMMEDIATELY USEFUL, not impressive."""


OUTPUT_CONTRACT = """- Default to GitHub-flavored markdown.
- For coding / DSA problems: return a single fenced code block with the optimal solution. Include necessary imports/headers. Add ONE trailing comment with time + space complexity (e.g. `# O(n) time, O(1) space`). No prose before or after the block unless the candidate asked for explanation. Be optimal and correct on the FIRST pass — there is no second pass.
- For multiple-choice questions: return one line per question — `1. B — <≤8-word reason>`.
- For conceptual questions: return ≤5 bullet points, one sentence each.
- For system-design questions: return a numbered outline — Components → Data Flow → Trade-offs → Scaling. No filler.
- Calibrate verbosity to interview-helper sweet spot: not too technical, not too simple. Assume the candidate knows the basics; skip definitions unless asked.
- Never hedge with "I think" or "It depends." If it depends, name the variables and pick a default."""


SAFETY = """- Do not invent function signatures from a problem you can't fully see.
- Do not answer questions the candidate didn't ask. If they say "look at this" with code, default to "explain + suggest improvement," not "rewrite from scratch."
- Do not include disclaimers about being an AI, about ethics, or about the interview process. The candidate already knows."""


VISION_RULES = """A screenshot of the candidate's interview screen is attached. Identify what's on screen first:
- A coding problem → solve under the OUTPUT_CONTRACT rules.
- An MCQ → one line per question.
- A system-design prompt → numbered outline.
- The candidate's own in-progress code with an error → focus on the error; don't rewrite working code.
- Partial / unreadable → say "VISIBLE PORTION ONLY" on the first line, then solve what's visible."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compose(slots: dict) -> str:
    """Provider-neutral markdown assembly."""
    language = (slots.get("language") or "Python").strip()
    resume = (slots.get("resume") or "").strip()
    interview = (slots.get("interview_context") or "").strip()
    mode = slots.get("mode") or "text"

    parts = [
        "# Role",
        ROLE,
        "",
        "# Output contract",
        OUTPUT_CONTRACT,
        "",
        "# Coding language",
        f"Default language: **{language}**.",
        "If the visible problem statement specifies a language, use that instead.",
        f"If unclear, use {language} and note the assumption in one trailing comment.",
    ]

    if resume:
        resume_text = _truncate_for_mode(resume, mode)
        parts += [
            "",
            "# Candidate context (resume)",
            resume_text,
            "",
            "When solving, prefer approaches that connect to the candidate's stated experience above. Do NOT mention the resume in the answer. Use it only to pick familiar idioms.",
        ]

    if interview:
        parts += [
            "",
            "# Interview context",
            interview,
        ]

    if mode == "vision":
        parts += [
            "",
            "# Vision task",
            VISION_RULES,
        ]

    parts += [
        "",
        "# Constraints",
        SAFETY,
    ]

    return "\n".join(parts).strip()


def compose_for(provider: str, slots: dict) -> str:
    """Wrap the composed prompt for a specific provider.

    - gemini / openai: markdown headings as-is. Both follow markdown well.
    - anthropic: re-wrap each section in XML tags Claude is trained on.
    """
    if provider == "anthropic":
        return _xml_wrap(slots)
    return compose(slots)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

# Vision context windows are smaller and the resume is rarely relevant when
# the model is staring at a LeetCode page. Truncate aggressively in vision mode.
_VISION_RESUME_BUDGET = 2000


def _truncate_for_mode(resume: str, mode: str) -> str:
    if mode == "vision" and len(resume) > _VISION_RESUME_BUDGET:
        return resume[:_VISION_RESUME_BUDGET] + "\n\n[...truncated for vision]"
    return resume


def _xml_wrap(slots: dict) -> str:
    language = (slots.get("language") or "Python").strip()
    resume = (slots.get("resume") or "").strip()
    interview = (slots.get("interview_context") or "").strip()
    mode = slots.get("mode") or "text"

    sections = [
        f"<role>\n{ROLE}\n</role>",
        f"<output_contract>\n{OUTPUT_CONTRACT}\n</output_contract>",
        (
            "<coding_language>\n"
            f"Default language: {language}.\n"
            "If the visible problem statement specifies a language, use that instead.\n"
            f"If unclear, use {language} and note the assumption in one trailing comment.\n"
            "</coding_language>"
        ),
    ]

    if resume:
        resume_text = _truncate_for_mode(resume, mode)
        sections.append(
            "<resume>\n"
            f"{resume_text}\n"
            "</resume>\n"
            "<resume_usage>\n"
            "Use the resume only to pick familiar idioms. Do not mention it in the answer.\n"
            "</resume_usage>"
        )

    if interview:
        sections.append(f"<interview_context>\n{interview}\n</interview_context>")

    if mode == "vision":
        sections.append(f"<vision_task>\n{VISION_RULES}\n</vision_task>")

    sections.append(f"<constraints>\n{SAFETY}\n</constraints>")
    return "\n\n".join(sections)
