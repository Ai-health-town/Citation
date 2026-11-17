# Citation Audit Pro — Python + Streamlit

A Python conversion of your Citation Audit Pro desktop/web app, ready for a
GitHub repository and Streamlit Community Cloud. No Node.js, Next.js, Prisma,
desktop installer, or bundled development token is needed.

## Deploy from GitHub

1. Unzip `Citation-Audit-Pro-Streamlit-Python.zip`.
2. Create a GitHub repository, for example `citation-audit-pro`.
3. Upload **the extracted files and folders** to the repository root. You should
   see `app.py`, `requirements.txt`, `citation_audit/`, and `assets/` at the top
   level. Include `.streamlit/` for the app's dark theme and upload settings.
   Do not upload the ZIP itself as the app.
4. Open **https://share.streamlit.io/** and sign in with GitHub.
5. Choose **Create app → Yup, I have an app**, then select your repository.

   | Setting | Value |
   | --- | --- |
   | Repository | Your new GitHub repository |
   | Branch | `main` (or the branch containing your files) |
   | Main file path | `app.py` |
   | Advanced settings → Python version | `3.12` |

6. For AI verification, paste the following into **Advanced settings → Secrets**,
   replacing the placeholder with your **standard Z.AI API key**:

   ```toml
   AI_API_KEY = "YOUR_STANDARD_ZAI_API_KEY"
   AI_BASE_URL = "https://api.z.ai/api/paas/v4"
   AI_MODEL = "glm-4.7"
   ```

7. Click **Deploy**. Your app opens at a `streamlit.app` address.

The app also opens without an API key. You can use **Structure only** to check
citation patterns or **Open offline demo** to explore every screen and export.
An API key is required for actual AI evidence judgments and AI bibliography
formatting. A standard Z.AI API key is separate from the temporary session token
that was bundled in the original development app.

You can update Secrets later through the app's settings. Keep real keys out of
GitHub. `.streamlit/secrets.toml.example` contains the same setup template.

## What is included

| Workflow | Python version |
| --- | --- |
| Paper input | Paste text or upload PDF, DOCX, TXT, or Markdown |
| Reference input | Paste, upload a document, or supply a public bibliography URL |
| Reference validation | Author, year, title, URL/DOI parsing and duplicate removal |
| Source retrieval | Public HTML, PDF, and DOCX; explicit access failures |
| Inaccessible sources | Upload source documents and assign them to reference numbers |
| Missing scholarly links | Optional Crossref title/author/year lookup |
| Evidence review | AI claim extraction, source excerpt selection, and quote checking |
| Citation styles | APA 7, APA 6, MLA 9, Chicago 17 author–date, Harvard |
| Review interface | Search, status/confidence filters, source text, paragraph context |
| Draft review | Original and proposed text side by side |
| Reports | Bibliography, claims to review, source access report |
| Downloads | Word, PDF, HTML, Markdown, TXT, CSV, JSON, complete report ZIP |
| History | Up to 20 audits per browser session, comparison, JSON backup/import |
| Existing desktop history | Optional local SQLite-to-JSON migration tool |
| Access control | Optional shared app password |
| Automated checks | Unit/integration tests and GitHub Actions workflow |

The interface uses native Streamlit controls with the original app's helmet
artwork and a dark red/gold theme. Desktop-only installers, React animations,
and the JavaScript network graph are replaced by a browser app, source/claim
tables, status charts, and a paragraph review map.

## How to use it

1. Choose **AI evidence review** or **Structure only** in the sidebar.
2. In **New audit**, add your paper and reference list. Keep a blank line between
   paragraphs and between references.
3. Review **Check reference fields**. If a source is paywalled, attach a readable
   copy and select its reference number.
4. Click **Run citation audit**.
5. Open **Claim audit** to inspect each decision, source evidence, and proposed
   rewrite. Open **Sources** for access errors and the retrieved source text.
6. Review **Proposed draft**, then use **Downloads** to export the results.
7. Use **History → Back up all audits** before closing your session. Import the
   JSON file to continue in another session.

## AI configuration

Z.AI is configured by default. `ZAI_API_KEY` is accepted as an alias for
`AI_API_KEY`. You can use another provider that supports the standard
`POST /chat/completions` interface by setting its HTTPS base URL and model:

```toml
AI_API_KEY = "YOUR_PROVIDER_KEY"
AI_BASE_URL = "https://your-provider.example/v1"
AI_MODEL = "your-chat-model"
```

Use a model that supports system messages, JSON responses, `max_tokens`, and
at least roughly 32K context tokens. Provider-specific interfaces, models that
only accept the Responses API, and local Ollama URLs are not supported by this
cloud configuration. Provider billing and model access are managed by your
AI account. Only the Z.AI-compatible payload is covered by the included client
tests; another provider may require an adapter.

Paper text and selected source excerpts are sent to that provider when you
choose AI evidence review. Structure-only mode does not make AI requests.
Online source retrieval and Crossref lookup have separate switches.

To require a password before anyone opens the workspace, add this to Secrets:

```toml
APP_PASSWORD = "choose-a-long-private-password"
```

This is a shared app password, not a system of individual user accounts.

## Run on your Mac or another computer

Install Python 3.12, open Terminal in the extracted project folder, then run:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
streamlit run app.py
```

For AI use locally, copy `.streamlit/secrets.toml.example` to
`.streamlit/secrets.toml` and replace the placeholder key. The real secrets file
is excluded from Git commits.

On Windows, activate the virtual environment with
`.venv\Scripts\activate` instead of `source .venv/bin/activate`.

## History and original desktop data

Audits and exports live in each browser's Streamlit session; there is no shared
server database. Closing the session, rebooting, or redeploying can clear them.
JSON backup/import is the persistence mechanism in this version. Documents and
old audit databases from the Mac application are not bundled in the GitHub
package.

If you need history from the original SQLite app, run the included converter
locally against its `db/custom.db`:

```bash
python tools/import_legacy_history.py "/path/to/original/project/db/custom.db" --output desktop_audits.json
```

Import `desktop_audits.json` in the new app's **History** screen. The converter
reads the database without modifying it and includes the latest 20 completed
audits. Imported desktop decisions are retained with an explicit legacy note;
they have not been reverified by the Python audit pipeline.

## Accuracy and extraction limits

- Structure-only checks never label evidence as verified. A matched author/year
  is a bibliographic clue, not proof that a source supports a claim.
- Positive AI support judgments need an exact quotation found in the supplied
  source excerpt. Invalid quotes or unknown source numbers are downgraded to
  **Needs review**. That check establishes quotation provenance, not that the
  AI's interpretation is correct.
- Selected source excerpts may omit relevant evidence. Inaccessible sources
  and incomplete evidence produce review items, not a verdict that a claim is
  false. Abstract-only retrieval is identified in the source notes.
- AI bibliography formatting and rewrites are proposals. Verify author names,
  dates, page numbers, source identity, and meaning before submission. Without
  AI formatting, references are retained as supplied where possible.
- Only exact, nonoverlapping, unambiguous claim replacements with a checked
  quote are applied. Unresolved claims stay in the proposed draft.
- Default limit: 30 references; configurable up to 80. Paper: 120,000 characters,
  with no single paragraph above 12,000 characters; up to 600 candidate claims.
  Each document or online download is limited to 15 MB. PDFs are limited to
  250 pages. Retrieved source text is capped at 120,000 characters per source,
  with truncation shown in the source notes. Attach up to 20 source documents.
- Scanned PDFs need OCR first. Browser-only pages, paywalls, CAPTCHAs, and
  private URLs may need a user-supplied document.
- Non-APA styles rely primarily on the AI review. The deterministic pattern
  checker concentrates on author/date and simple numbered citations; it does
  not fully parse MLA page citations or Chicago footnotes.
- No Turnitin integration or plagiarism similarity score is claimed.
- PDF export includes a Unicode font. Complex right-to-left typography is best
  reviewed in the Word or HTML export.

## Checks and project structure

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

Tests cover the end-to-end pipeline using a fake AI provider, source-access
failures, invented quotations/source numbers, document extraction, native
export readers, backup validation, API errors/retries, and the actual Streamlit
screens. No real API key or external network is required by the tests.

During conversion, the automated checks passed locally. A real AI request could
not be tested because no standard provider API key was supplied. Live external
retrieval was also restricted in the conversion environment; public-site and
Crossref retrieval still need to be checked from your deployed app. Source and
provider failures are shown in the interface and reports.

| File or folder | Purpose |
| --- | --- |
| `app.py` | Streamlit entry point |
| `citation_audit/` | Parsing, retrieval, AI, audit pipeline, and report exports |
| `requirements.txt` | Runtime dependencies |
| `.streamlit/config.toml` | Theme and server configuration |
| `.streamlit/secrets.toml.example` | AI/password setup template |
| `assets/` | Original artwork and PDF font |
| `tests/` | Automated checks |
| `tools/import_legacy_history.py` | Optional original-app history migration |
| `.github/workflows/test.yml` | GitHub Actions checks |

## Official documentation

- [Deploy a Streamlit app from GitHub](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy)
- [Streamlit Secrets](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/secrets-management)
- [Z.AI quick start and API keys](https://docs.z.ai/guides/overview/quick-start)
- [Z.AI chat-completion API](https://docs.z.ai/api-reference/llm/chat-completion)
- [Crossref REST API](https://www.crossref.org/documentation/retrieve-metadata/rest-api/)

The original artwork is from your supplied application. The bundled DejaVu Sans
font is distributed under the license included in `assets/FONT-LICENSE.txt`.
