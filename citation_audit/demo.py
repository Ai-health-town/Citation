"""Clearly marked, offline examples. No fictional DOI is presented as real."""
from .engine import corrected_draft
from .models import Audit, Claim, Source
from .parsing import paragraphs_from_text

DEMO_REFERENCES = """Example Team. (2024). A fictional evaluation of appointment reminders. Demonstration document.

Example Research Group. (2023). A fictional patient access review. Demonstration document."""

DEMO_PAPER = """Appointment reminders eliminate all missed visits (Example Team, 2024).

Long travel distances can make clinic attendance difficult.

The review identified travel distance as one of several reported barriers to clinic attendance (Example Research Group, 2023)."""


def demo_audit() -> Audit:
    sources = [
        Source(1, "Example Team. (2024). A fictional evaluation of appointment reminders.", title="A fictional evaluation of appointment reminders", author="Example Team", year="2024", source_type="demo", status="success",
               text="In this fictional evaluation of eight clinics, appointment reminders were associated with fewer missed visits. The evaluation was observational and did not establish causation. Some appointments were still missed.", notes="Fictional demonstration data; no real study or DOI."),
        Source(2, "Example Research Group. (2023). A fictional patient access review.", title="A fictional patient access review", author="Example Research Group", year="2023", source_type="demo", status="success",
               text="In this fictional patient access review, travel distance was one of several reported barriers to clinic attendance. Other barriers included appointment availability and transport costs.", notes="Fictional demonstration data; no real study or DOI."),
    ]
    paragraphs = paragraphs_from_text(DEMO_PAPER)
    claims = [
        Claim(1, 1, paragraphs[0].text, "(Example Team, 2024)", "overclaim", 1, sources[0].title,
              "The example describes an association; missed visits were not eliminated.", "Some appointments were still missed.",
              "(Example Team, 2024)", "Appointment reminders were associated with fewer missed visits in an observational evaluation of eight clinics (Example Team, 2024).", "high", "Demonstration verdict, not a real audit."),
        Claim(2, 2, paragraphs[1].text, "", "missing_citation", 2, sources[1].title,
              "The example review lists travel distance as a barrier.", "travel distance was one of several reported barriers to clinic attendance.",
              "(Example Research Group, 2023)", "Long travel distances can make clinic attendance difficult (Example Research Group, 2023).", "high", "Demonstration verdict, not a real audit."),
        Claim(3, 3, paragraphs[2].text, "(Example Research Group, 2023)", "correct", 2, sources[1].title,
              "The original statement reflects the scope of the example review.", "travel distance was one of several reported barriers to clinic attendance.",
              confidence="high", notes="Demonstration verdict, not a real audit."),
    ]
    return Audit(title="Demo · Clinic attendance", mode="Offline demonstration", paper_text=DEMO_PAPER,
                 reference_input=DEMO_REFERENCES, sources=sources, paragraphs=paragraphs, claims=claims,
                 corrected_paper=corrected_draft(paragraphs, claims), reference_list=DEMO_REFERENCES,
                 warnings=["All documents, source texts, and judgments in this demo are fictional examples."], is_demo=True)
