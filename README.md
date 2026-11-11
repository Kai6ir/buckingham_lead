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
