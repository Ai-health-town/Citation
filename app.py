"""Citation Audit Pro — Python-only Streamlit entry point."""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
from pathlib import Path
from urllib.parse import urlsplit

import pandas as pd
import streamlit as st

from citation_audit.ai import AIClient, AIConfig, AIError
from citation_audit.demo import DEMO_PAPER, DEMO_REFERENCES, demo_audit
from citation_audit.documents import extract_document
from citation_audit.engine import MAX_PAPER_CHARS, run_audit
from citation_audit.models import Audit, LABELS, STATUSES, STYLE_LABELS
from citation_audit.parsing import parse_references, validate_references
from citation_audit.reports import (
    all_exports, attention_report, claim_rows, export_zip, failed_report,
    load_snapshot, snapshot_bytes, source_rows,
)

ROOT = Path(__file__).resolve().parent
st.set_page_config(page_title="Citation Audit Pro", page_icon="📚", layout="wide", initial_sidebar_state="expanded")


def secret(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    if value is not None:
        return value
    try:
        return str(st.secrets.get(name, default))
    except (FileNotFoundError, st.errors.StreamlitSecretNotFoundError):
        return default


def require_password() -> None:
    password = secret("APP_PASSWORD")
    # Invalidate the session if the owner changes the configured password.
    digest = hashlib.sha256(password.encode()).hexdigest() if password else ""
    if not password or st.session_state.get("authenticated_for") == digest:
        return
    st.title("Citation Audit Pro")
    st.caption("Enter the app password to open your workspace.")
    with st.form("login"):
        entered = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Open workspace", type="primary")
    if submitted:
        if hmac.compare_digest(entered.encode(), password.encode()):
            st.session_state.authenticated_for = digest
            st.rerun()
        st.error("That password is incorrect.")
    st.stop()


require_password()


@st.cache_data(show_spinner=False)
def asset_b64(name: str) -> str:
    path = ROOT / "assets" / name
    return base64.b64encode(path.read_bytes()).decode() if path.exists() else ""


st.markdown("""<style>
.stApp {font-family:Inter,system-ui,sans-serif;}
.block-container{padding-top:2rem;padding-bottom:3rem;max-width:1480px}
.cap-eyebrow{font-size:.7rem;letter-spacing:.25em;color:#f1bf79;font-weight:700;margin-bottom:10px}
.cap-title{font-size:clamp(2rem,4vw,3.4rem);letter-spacing:-.055em;line-height:1.04;color:#f7f7fa;font-weight:800;margin:0 0 12px}
.cap-sub{color:#a8b3c7;font-size:1rem;max-width:670px;line-height:1.65}
.cap-hero{border:1px solid #352e36;background:linear-gradient(110deg,#19151e 0%,#101a29 70%);border-radius:22px;padding:32px 36px;margin:6px 0 22px;position:relative;overflow:hidden}
.cap-orb{float:right;width:105px;height:105px;object-fit:cover;mix-blend-mode:screen;opacity:.88;margin-left:24px;border-radius:50%}
.cap-pill{display:inline-block;background:#2c2330;border:1px solid #5e3b3d;color:#edc79e;border-radius:20px;padding:5px 12px;font-size:.7rem;margin-top:16px;letter-spacing:.06em}
[data-testid='stMetric']{background:#121c2d;border:1px solid #26334a;padding:17px 20px;border-radius:14px}
[data-testid='stMetricLabel']{color:#a8b3c7}
[data-testid='stMetricValue']{font-size:1.8rem}
[data-testid='stSidebar']{border-right:1px solid #253047}
div.stButton>button[kind='primary']{background:linear-gradient(105deg,#9d2737,#c16b35);border:0;box-shadow:0 4px 20px #7c1f2525}
div.stButton>button{border-radius:9px;font-weight:600;min-height:2.7rem}
div[data-testid='stExpander']{border-color:#2a374c;border-radius:12px}
.cap-footer{color:#7f8ba3;font-size:.74rem;margin-top:40px;border-top:1px solid #243148;padding-top:18px}
@media(max-width:700px){.cap-hero{padding:23px}.cap-orb{width:70px;height:70px}.block-container{padding-top:1rem}}
</style>""", unsafe_allow_html=True)

for key, value in {"audits": [], "current_id": None, "exports": {}, "paper_input": "", "references_input": "",
                   "workspace": "New audit", "reference_mode": "Paste references", "paper_mode": "Paste paper"}.items():
    if key not in st.session_state:
        st.session_state[key] = value


def store_audit(audit: Audit) -> None:
    st.session_state.audits = [a for a in st.session_state.audits if a.id != audit.id] + [audit]
    # Keep memory bounded on Community Cloud; users can export/import backups.
    st.session_state.audits = st.session_state.audits[-20:]
    retained = {a.id for a in st.session_state.audits}
    st.session_state.exports = {key: value for key, value in st.session_state.exports.items() if key in retained}
    st.session_state.current_id = audit.id


def current_audit() -> Audit | None:
    return next((a for a in st.session_state.audits if a.id == st.session_state.current_id), None)


def go(view: str) -> None:
    st.session_state.next_view = view
    st.rerun()


def demo() -> None:
    store_audit(demo_audit())
    st.session_state.next_view = "Overview"


def navigate(view: str) -> None:
    """Widget callback: Streamlit already schedules the rerun."""
    st.session_state.next_view = view


def load_demo_inputs() -> None:
    st.session_state.paper_input = DEMO_PAPER
    st.session_state.references_input = DEMO_REFERENCES
    st.session_state.paper_mode = "Paste paper"
    st.session_state.reference_mode = "Paste references"
    st.session_state.demo_inputs = True


with st.sidebar:
    st.markdown("### 📚 Citation Audit Pro")
    st.caption("A clearer path from claims to evidence.")
    st.button("Open offline demo", on_click=demo, use_container_width=True)
    st.divider()
    configured_key = secret("AI_API_KEY") or secret("ZAI_API_KEY")
    configured_url = secret("AI_BASE_URL", "https://api.z.ai/api/paas/v4")
    configured_model = secret("AI_MODEL", "glm-4.7")
    api_key = configured_key
    with st.expander("AI connection", expanded=not bool(configured_key)):
        if configured_key:
            st.success("API key configured")
        else:
            api_key = st.text_input("API key", type="password", help="Use a standard Z.AI API key. For another compatible provider, configure AI_BASE_URL and AI_MODEL in Streamlit Secrets.")
            st.caption("No key? Structure checks and the offline demo still work.")
        st.caption("Model: " + configured_model)
        st.caption("Endpoint: " + (urlsplit(configured_url).hostname or "Check AI_BASE_URL"))
    mode = st.radio("Audit mode", ["AI evidence review", "Structure only"], index=0 if api_key else 1,
                    help="Structure mode flags candidate statements and matches author/year patterns; it does not verify support.")
    citation_style = st.selectbox("Citation style", list(STYLE_LABELS), format_func=STYLE_LABELS.get)
    with st.expander("Retrieval settings"):
        retrieve_online = st.checkbox("Retrieve online source text", value=True)
        search_missing = st.checkbox("Find missing DOIs with Crossref", value=False, disabled=not retrieve_online,
                                     help="Scholarly title/author/year lookup for references without links. This is not a general web search.")
        max_sources = st.number_input("Maximum references", min_value=5, max_value=80, value=30, step=5)
        st.caption("Blocked or paywalled sources can be attached as documents in New audit.")
    st.divider()
    st.caption("History stays in this browser session. Download a JSON backup to reopen audits later.")
    if secret("APP_PASSWORD") and st.button("Sign out", use_container_width=True):
        st.session_state.clear()
        st.rerun()

helmet = asset_b64("ironman-helmet.png")
hero = '<img class="cap-orb" src="data:image/png;base64,' + helmet + '" alt="Citation Audit Pro helmet">' if helmet else ""
st.markdown('<div class="cap-hero">' + hero + '<div class="cap-eyebrow">READ. VERIFY. REFINE.</div><div class="cap-title">Citation Audit Pro</div><div class="cap-sub">Trace your claims to their sources. Review the evidence, refine your citations, and export a paper you can stand behind.</div><div class="cap-pill">PYTHON + STREAMLIT EDITION</div></div>', unsafe_allow_html=True)

if "next_view" in st.session_state:
    st.session_state.workspace = st.session_state.pop("next_view")
views = ["New audit", "Overview", "Sources", "Claim audit", "Proposed draft", "References", "Downloads", "History"]
view = st.radio("Workspace", views, horizontal=True, key="workspace", label_visibility="collapsed")
st.write("")
audit = current_audit()


def render_new_audit() -> None:
    st.subheader("Start with your paper and references")
    st.caption("Paste text or upload PDF, DOCX, TXT, or Markdown files. Maximum 15 MB per document.")
    title = st.text_input("Audit title", placeholder="e.g. MPH capstone · final draft")
    left, right = st.columns([1, 1], gap="large")
    paper, references, reference_url = "", "", ""
    with left:
        st.markdown("#### 01 · Your paper")
        paper_mode = st.radio("Paper input", ["Paste paper", "Upload paper"], horizontal=True, key="paper_mode")
        if paper_mode == "Paste paper":
            paper = st.text_area("Paper text", height=350, key="paper_input", placeholder="Paste your paper here, keeping blank lines between paragraphs.", max_chars=MAX_PAPER_CHARS)
        else:
            upload = st.file_uploader("Paper document", type=["pdf", "docx", "txt", "md"], key="paper_upload")
            if upload:
                try:
                    paper = extract_document(upload.getvalue(), upload.name)
                    st.success(f"Read {len(paper):,} characters")
                    with st.expander("Preview paper text"):
                        st.text(paper[:12000])
                except ValueError as exc:
                    st.error(str(exc))
    with right:
        st.markdown("#### 02 · Your references")
        reference_mode = st.radio("Reference input", ["Paste references", "Reference list URL", "Upload references"], key="reference_mode", horizontal=True)
        if reference_mode == "Paste references":
            references = st.text_area("Reference list", height=350, key="references_input", max_chars=150_000,
                                      placeholder="One reference per paragraph. Include DOI or source links when available.")
        elif reference_mode == "Reference list URL":
            reference_url = st.text_input("Public reference-list URL", placeholder="https://…")
            st.info("Use a public page or document containing a bibliography. For private or dynamic pages, paste or upload the references.")
        else:
            upload = st.file_uploader("Reference-list document", type=["pdf", "docx", "txt", "md"], key="references_upload")
            if upload:
                try:
                    references = extract_document(upload.getvalue(), upload.name)
                    st.success("Reference document read")
                except ValueError as exc:
                    st.error(str(exc))
    parsed_sources = []
    if references.strip():
        try:
            parsed_sources = parse_references(references, int(max_sources))
            with st.expander(f"Check reference fields · {len(parsed_sources)} sources"):
                st.dataframe(pd.DataFrame(validate_references(parsed_sources)), hide_index=True, use_container_width=True)
                st.caption("These are parsing checks. Bibliographic fields and source content still need verification.")
        except ValueError as exc:
            st.error(str(exc))
    attachments = {}
    with st.expander("03 · Attach source documents (optional)"):
        st.caption("Supply the text of books, paywalled articles, or other inaccessible sources. Match each file to the reference number shown above. For a URL bibliography, numbers follow the extracted reference order; pasted references are easier to map.")
        files = st.file_uploader("Source files", type=["pdf", "docx", "txt", "md"], accept_multiple_files=True, key="source_uploads")
        if len(files) > 20:
            st.error("Attach no more than 20 source documents per audit.")
        for index, file in enumerate(files[:20]):
            number = st.number_input(f"Reference number for {file.name}", min_value=1, max_value=int(max_sources),
                                     value=min(index + 1, int(max_sources)), key=f"source_mapping_{index}_{file.name}")
            if number in attachments:
                st.error(f"Reference #{number} has more than one file. Choose a different reference number.")
            attachments[number] = (file.name, file.getvalue())
    if mode == "AI evidence review":
        st.caption("AI review sends your paper and selected source excerpts to the configured AI provider.")
    else:
        st.info("Structure-only mode checks citation patterns. It leaves evidence judgments for review.")
    run_col, demo_col, _ = st.columns([1, 1, 2])
    run = run_col.button("Run citation audit", type="primary", use_container_width=True)
    demo_col.button("Load example text", on_click=load_demo_inputs, use_container_width=True)
    if st.session_state.get("demo_inputs"):
        st.caption("Example text is fictional. Use “Open offline demo” for prebuilt sample results.")
    if run:
        if mode == "AI evidence review" and not api_key:
            st.error("Add an API key in the sidebar, or choose Structure only.")
            return
        if files and (len(files) > 20 or len(attachments) != len(files)):
            st.error("Resolve the source attachment limit or duplicate reference numbers before running.")
            return
        ai = None
        try:
            if mode == "AI evidence review":
                ai = AIClient(AIConfig(api_key=api_key, base_url=configured_url, model=configured_model))
            bar = st.progress(0, text="Preparing audit")
            with st.status("Running citation audit", expanded=True) as status:
                latest = st.empty()
                def progress(fraction: float, message: str):
                    bar.progress(min(1.0, max(0.0, fraction)), text=message)
                    latest.caption(message)
                result = run_audit(paper_text=paper, reference_text=references, reference_url=reference_url,
                                   citation_style=citation_style, ai=ai, retrieve=retrieve_online,
                                   search_missing=search_missing, max_sources=int(max_sources),
                                   attachments=attachments, progress=progress, title=title)
                store_audit(result)
                status.update(label="Audit complete", state="complete", expanded=False)
            go("Overview")
        except (ValueError, AIError) as exc:
            st.error(str(exc))
        except Exception:
            st.error("The audit could not finish. Try a smaller paper or use Structure only. Your inputs are still available.")


def render_overview(a: Audit) -> None:
    st.subheader(a.title)
    st.caption(f"{a.mode} · {STYLE_LABELS[a.citation_style]} · {a.duration_seconds:g} seconds · {a.created_at}")
    metrics = st.columns(4)
    metrics[0].metric("Sources", len(a.sources))
    metrics[1].metric("Readable sources", sum(s.status == "success" for s in a.sources))
    metrics[2].metric("Audited statements", len(a.claims))
    metrics[3].metric("Requires attention", a.needs_attention)
    st.write("")
    left, right = st.columns([1.25, 1], gap="large")
    with left:
        st.markdown("#### Claim status")
        counts = pd.DataFrame([{"Status": LABELS[k], "Claims": v} for k, v in a.summary.items() if v])
        if not counts.empty:
            st.bar_chart(counts.set_index("Status"), color="#d1965f", horizontal=True)
        else:
            st.info("No citation-requiring statements were identified. Review the audit notes.")
    with right:
        st.markdown("#### Audit notes")
        for warning in a.warnings:
            st.info(warning)
        st.caption("AI judgments use selected retrieved excerpts. Review evidence and proposed changes before submission.")
        st.button("Review claim details →", on_click=navigate, args=("Claim audit",))
    st.markdown("#### Paragraph review map")
    rows = []
    for p in a.paragraphs:
        claims = [c for c in a.claims if c.paragraph == p.number]
        flagged = sum(c.status != "correct" for c in claims)
        rows.append({"Paragraph": p.number, "Statements": len(claims), "Requires attention": flagged,
                     "Excerpt": p.text[:140] + ("…" if len(p.text) > 140 else "")})
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)


def render_sources(a: Audit) -> None:
    st.subheader("Source library")
    query = st.text_input("Search sources", placeholder="Title, author, DOI, or URL").casefold()
    rows = source_rows(a)
    visible = [r for r in rows if not query or query in " ".join(str(v) for v in r.values()).casefold()]
    st.dataframe(pd.DataFrame(visible), hide_index=True, use_container_width=True)
    for source in a.sources:
        if query and query not in " ".join([source.title, source.author, source.url, source.doi]).casefold():
            continue
        with st.expander(f"{source.number:02d} · {source.title} · {source.status.replace('_', ' ')}"):
            st.write(f"**Author:** {source.author or 'Unknown'} · **Year:** {source.year or 'Unknown'}")
            if source.url and urlsplit(source.url).scheme in {"http", "https"}:
                st.link_button("Open source", source.url)
            if source.notes:
                st.caption(source.notes)
            if source.error:
                st.warning(source.error)
            if source.text:
                st.text(source.text[:18000])
                if len(source.text) > 18000:
                    st.caption("Preview shows the first 18,000 characters. The audit backup contains the retrieved text.")


def render_claims(a: Audit) -> None:
    st.subheader("Claims and evidence")
    left, middle, right = st.columns([2, 2, 3])
    selected = left.multiselect("Status", list(STATUSES), format_func=LABELS.get)
    confidence = middle.selectbox("Confidence", ["All", "high", "medium", "low"])
    query = right.text_input("Search claims", placeholder="Find text, sources, or notes").casefold()
    visible = [c for c in a.claims if (not selected or c.status in selected)
               and (confidence == "All" or c.confidence == confidence)
               and (not query or query in " ".join([c.text, c.evidence, c.source_title, c.notes]).casefold())]
    st.caption(f"Showing {len(visible)} of {len(a.claims)} audited statements")
    compact = pd.DataFrame([{"Claim": c.id, "Paragraph": c.paragraph, "Statement": c.text,
                             "Status": LABELS[c.status], "Source #": c.source_number, "Confidence": c.confidence} for c in visible])
    st.dataframe(compact, hide_index=True, use_container_width=True)
    if not visible:
        return
    choice = st.selectbox("Inspect a claim", visible, format_func=lambda c: f"{c.id} · Paragraph {c.paragraph} · {c.text[:100]}")
    st.markdown(f"#### {LABELS[choice.status]} · {choice.confidence} confidence")
    st.write(choice.text)
    first, second = st.columns([1, 1], gap="large")
    with first:
        st.markdown("**Current citation**")
        st.write(choice.current_citation or "None detected")
        st.markdown("**Evidence assessment**")
        st.write(choice.evidence or "Not assessed")
        if choice.notes:
            st.info(choice.notes)
    with second:
        st.markdown("**Source evidence**")
        st.write(f"Source {choice.source_number}: {choice.source_title}" if choice.source_number else "No source identified")
        if choice.evidence_quote:
            st.text(choice.evidence_quote)
            st.caption("This quoted text was checked against the retrieved excerpt during the audit.")
        else:
            st.caption("No verified source quote is available.")
        if choice.suggested_rewrite:
            st.markdown("**Proposed rewrite**")
            st.write(choice.suggested_rewrite)
        if choice.suggested_citation:
            st.markdown("**Suggested citation**")
            st.write(choice.suggested_citation)
    with st.expander("See the paragraph in context"):
        p = next((p for p in a.paragraphs if p.number == choice.paragraph), None)
        if p:
            st.write(p.text)


def render_draft(a: Audit) -> None:
    st.subheader("Proposed corrected draft")
    st.caption("Only exact, nonoverlapping claim rewrites with a checked evidence quote are applied. Unresolved statements remain in place. Review every proposal before using it.")
    split = st.toggle("Compare original and proposed text", value=True)
    if split:
        left, right = st.columns(2, gap="large")
        with left:
            st.markdown("#### Original")
            st.text_area("Original paper", a.paper_text, height=600, disabled=True, key="original_" + a.id, label_visibility="collapsed")
        with right:
            st.markdown("#### Proposed")
            st.text_area("Proposed paper", a.corrected_paper, height=600, disabled=True, key="proposed_" + a.id, label_visibility="collapsed")
    else:
        st.write(a.corrected_paper)
    if a.paper_text.strip() == a.corrected_paper.strip():
        st.info("No verified, safely applicable rewrite was available. The proposed draft preserves your original text.")
    st.download_button("Download proposed paper.txt", a.corrected_paper, "proposed_paper.txt", "text/plain")


def render_references(a: Audit) -> None:
    st.subheader("References and review reports")
    reference_tab, attention_tab, source_tab = st.tabs([STYLE_LABELS[a.citation_style] + " references", "Claims to review", "Source access"])
    with reference_tab:
        st.caption("Check the formatted bibliography against the original references and source records.")
        st.text_area("Reference list", a.reference_list, height=500, disabled=True, key="reference_result_" + a.id)
        st.download_button("Download references.txt", a.reference_list, "references.txt", "text/plain")
    with attention_tab:
        st.markdown(attention_report(a))
    with source_tab:
        st.markdown(failed_report(a))


def render_downloads(a: Audit) -> None:
    st.subheader("Take your work with you")
    st.caption("Download a full report, a proposed paper, the audit tables, or a JSON backup that can be reopened in History.")
    if a.id not in st.session_state.exports:
        with st.spinner("Preparing report files…"):
            try:
                exports = all_exports(a)
                exports["complete_audit.zip"] = export_zip(a, exports)
                st.session_state.exports[a.id] = exports
            except Exception:
                st.error("Report generation failed. You can still download the JSON backup below.")
                st.download_button("Download audit backup", snapshot_bytes([a]), "audit_backup.json", "application/json")
                return
    exports = st.session_state.exports[a.id]
    st.download_button("Download everything · ZIP", exports["complete_audit.zip"], "complete_audit.zip", "application/zip", type="primary")
    left, right = st.columns(2)
    types = {".pdf": "application/pdf", ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
             ".csv": "text/csv", ".json": "application/json", ".html": "text/html", ".txt": "text/plain", ".md": "text/markdown"}
    for index, (filename, data) in enumerate(exports.items()):
        if filename == "complete_audit.zip":
            continue
        with left if index % 2 == 0 else right:
            st.download_button(filename, data, filename, types[Path(filename).suffix], key="download_" + filename, use_container_width=True)


def render_history() -> None:
    st.subheader("Your audit history")
    audits = st.session_state.audits
    if audits:
        selected = st.selectbox("Open a saved audit", list(reversed(audits)), format_func=lambda a: f"{a.created_at} · {a.title}")
        left, middle, _ = st.columns([1, 1, 2])
        if left.button("Open selected audit", type="primary"):
            st.session_state.current_id = selected.id
            go("Overview")
        if middle.button("Remove selected audit"):
            st.session_state.audits = [a for a in audits if a.id != selected.id]
            st.session_state.exports.pop(selected.id, None)
            if st.session_state.current_id == selected.id:
                st.session_state.current_id = st.session_state.audits[-1].id if st.session_state.audits else None
            st.rerun()
        st.download_button("Back up all audits · JSON", snapshot_bytes(audits), "citation_audit_history.json", "application/json")
        if len(audits) >= 2:
            st.markdown("#### Compare audits")
            comparisons = st.multiselect("Select two audits", list(reversed(audits)), format_func=lambda a: f"{a.title} · {a.created_at}", max_selections=2)
            if len(comparisons) == 2:
                rows = [{"Audit": a.title, "Mode": a.mode, "Sources": len(a.sources), "Statements": len(a.claims),
                         **{LABELS[s]: a.summary[s] for s in STATUSES}} for a in comparisons]
                st.dataframe(pd.DataFrame(rows).T, use_container_width=True)
    else:
        st.info("Run an audit, open the demo, or import an existing backup.")
    st.markdown("#### Restore an audit backup")
    upload = st.file_uploader("Citation Audit Pro JSON backup", type=["json"], key="history_import")
    if upload and st.button("Import backup"):
        try:
            restored = load_snapshot(upload.getvalue())
            for item in restored:
                st.session_state.exports.pop(item.id, None)
                store_audit(item)
            st.success(f"Imported {len(restored)} audits. Choose one above to open it.")
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))
    st.caption("The latest 20 audits are kept in this browser session. Export a backup before closing the tab or rebooting the app.")


if audit and audit.is_demo and view not in {"New audit", "History"}:
    st.warning("DEMO · These source documents and judgments are fictional examples. No live verification was performed.")

if view == "New audit":
    render_new_audit()
elif view == "History":
    render_history()
elif not audit:
    st.info("Start a new audit or open the offline demo to explore the workspace.")
elif view == "Overview":
    render_overview(audit)
elif view == "Sources":
    render_sources(audit)
elif view == "Claim audit":
    render_claims(audit)
elif view == "Proposed draft":
    render_draft(audit)
elif view == "References":
    render_references(audit)
elif view == "Downloads":
    render_downloads(audit)

st.markdown('<div class="cap-footer">Citation Audit Pro · Evidence first. Author review always.</div>', unsafe_allow_html=True)
