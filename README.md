# Buckingham Lead Bot

## What it does

- **Screens introducing brokers (IBs):** reads each firm's website and labels it **Qualified** (raises capital for CTAs / managed futures), **Uncertain** (needs a human look), or **Irrelevant** (hedging-only / execution-only). Every label comes with the quote from the website and a link that proves it.
- **Finds new firms online**, skips duplicates, and scores every firm 0–100 with written reasons.
- **Drafts a partner email** for qualified firms only. Drafts wait in the Outbox — a person must approve each one. No performance figures or win rates ever appear in emails.
- **Exports to Excel** with separate sheets: full list, Qualified, Needs review, Excluded.
- Also keeps the **investor contact database** (import, dedupe, email sequences) — fully separate from the IB list.

## Current status

- Works end to end in **Demo mode** (practice data, no outside services, nothing sent). All tests pass.
- **Live** website scanning and email sending need API keys and have not been used with real accounts yet.
- Sending is **off by default**; even when on, every email needs human approval.

## How to use

Open Terminal and type:

```
cd ~/Desktop/buckingham_lead_gen
source .venv/bin/activate
python run_dashboard.py
```

Open **http://127.0.0.1:5057** in your browser. Then:

1. Go to the **NFA firms** tab, set Mode to **Demo**, click **Screen IBs & draft partner emails**.
2. Watch the results fill in; filter by **Qualified** to see the best prospects.
3. Click **Download list (Excel)** to get the workbook.
4. Open the **Outbox** tab (switch to *Demo outbox*) to review the drafted emails.

To stop the app, press `Ctrl+C` in Terminal.

## How scoring works

All rules live in **`config.yaml`** — open it in any text editor to see or change every number.

- **IB firms** (0–100; tier **A ≥ 60**, **B ≥ 40**, else C). Main points: evidence of raising capital for CTAs **+45**, strong managed-futures relevance **+20**, serves HNW/institutional **+8**, NFA registrations (CTA +25, CPO +20), target-market country **+10**, work email **+10**. Hedging-only **−45** and execution-only **−25**, and both are capped at tier C. Every point is written to the **SCORE REASONS** column.
- **Investor contacts** are scored against the *ideal customer* description in the same file — edit that paragraph to change the targeting.

## Connect your API keys (going live)

Demo mode needs no keys. For live runs:

1. Copy `.env.example` and rename the copy to **`.env`**
2. Paste your keys after the `=` signs:
   - `SERPAPI_KEY` — web search (serpapi.com)
   - `LLM_API_KEY` — the AI model that reads websites and writes drafts
   - `BREVO_API_KEY` — email sending with open/click tracking (brevo.com)
   - `IMAP_USER` / `IMAP_PASSWORD` — the sending mailbox, for reply detection
3. Save and restart the app

A blank key just switches that feature off. `.env` is never committed to this repository.

## Upload an existing lead list

- **People (investor contacts):** **Database** tab → choose the CSV/Excel file → **Import**. Duplicates (same email, LinkedIn, or name + company) and unsubscribed addresses are skipped automatically — re-uploading an old list is safe.
- **Firms (NFA list):** **NFA firms** tab → **Load master list**. Must be the Excel file with the standard 20 NFA columns; they are preserved exactly, and firms already loaded are left untouched.
