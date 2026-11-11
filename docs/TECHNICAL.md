# Lead Bot · Business Development

**An AI-assisted prospecting and outreach system for institutional sales teams.** It finds prospects on the web, checks them against your existing database so nothing is contacted twice, scores them against the attributes you care about, writes a tailored email for each one, and sends the emails in sequence only after a human has approved them. A local web dashboard shows every step.

Built for alternative-investment business development (managed futures, CTA allocation, liquid alternatives, fund-of-funds, family offices), but the core is generic: any team with a prospect list and a clear ideal-customer profile can use it.

> Status: working end to end in demo mode. Live Google / LinkedIn / X search, live LLM calls, and live email sending depend on API keys you provide, and are not yet verified against real accounts. See [What is not yet verified](#what-is-not-yet-verified).

---

## Table of contents

- [What it does](#what-it-does)
- [Features](#features)
- [Who it is for](#who-it-is-for)
- [Business scenarios](#business-scenarios)
- [How it works](#how-it-works)
- [Quick start](#quick-start)
- [Using the dashboard](#using-the-dashboard)
- [Command line](#command-line)
- [Configuration](#configuration)
- [Where to change things](#where-to-change-things)
- [APIs and where they are spent](#apis-and-where-they-are-spent)
- [Adding a new API or data source](#adding-a-new-api-or-data-source)
- [Compliance and responsible use](#compliance-and-responsible-use)
- [Project structure](#project-structure)
- [Testing](#testing)
- [What is not yet verified](#what-is-not-yet-verified)
- [Roadmap and limitations](#roadmap-and-limitations)
- [Contributing](#contributing)
- [License](#license)

---

## What it does

The bot runs two independent workflows that share one database and one approval queue.

**1. Work the existing contact database.** You import a contact list (CSV or Excel). The bot enrols contacts into a *sequence*: email 1 on day 0, email 2 on day 7, email 3 on day 14. Each follow-up depends on what the contact did with the previous email: opened or clicked → an "engaged" version; not opened → a "silent" version; replied → the sequence stops and the team is alerted; bounced, unsubscribed or complained → the address is suppressed for good.

**2. Find and qualify new firms.** The bot searches Google, LinkedIn and X for companies and people that look like your best prospects. Each result is checked against the database first, so duplicates are skipped. New ones are added with their source recorded, scored by their attributes, and, if they are strong enough, given a tailored email for approval.

There is also a **master-list workflow**: you can load an existing structured list (for example a regulator's registry export), and the bot fills its columns from public web pages, scores each row by its attributes with a written reason, and exports the list back in exactly the original layout, with the bot's columns appended.

Every outbound email passes through the same **Outbox**. Nothing is sent until a person approves it (this is on by default), and sending itself is off by default.

---

## Features

| Area | What you get |
| --- | --- |
| **Contact database** | SQLite store for contacts, firms, sequences, messages, engagement events, suppression list and activity log. Import CSV or Excel with loose column matching (`Work E-mail`, `Email`, `email address` all work). Deduplication by email, LinkedIn profile, X handle, domain, or name + company. |
| **Discovery** | Google (company websites), LinkedIn (public profile results), and X (public profile results), each through a search API. Search queries are planned by an LLM from your offering and ideal customer, or taken from a seed keyword file. Round-robin across sources so every channel is searched early. |
| **Enrichment** | Reads each company website and its contact / about / team pages (respecting `robots.txt`, with polite delays). Extracts work emails (free-mail addresses excluded), phone numbers, company name and description. Matches a person to a work email on their employer's domain by name. |
| **Scoring** | Keyword match on the lead's own pages, plus an LLM fit score with a one-line reason. For registry-style lists: attribute scoring with an explanation for every point (see [Scoring](#scoring)). Tiers A / B / C or HOT / WARM / COLD. |
| **Sequences** | Configurable steps and delays. A new email is drafted only after the previous one was actually sent, and only when its delay has passed. |
| **Personalisation** | Templates for each step and variant, adapted per contact by the LLM while keeping every fact from the template. Master-list emails use only facts from the contact's own row. |
| **Approval & safety** | Outbox with preview, edit, approve, reject (which pauses that contact's sequence), and approve-all. Sending is off until enabled. Daily cap. Sending window in the recipient's local time. |
| **Compliance** | Unsubscribe link in every email, one-click unsubscribe page, suppression list, lawful-basis gate for chosen regions (default: Europe), postal address in the footer, bulk recording of lawful basis with confirmation. |
| **Tracking** | Brevo webhooks for delivered, opened, clicked, bounced, unsubscribed and complaints. IMAP reply detection that matches replies to the sent email and stops the sequence. Auto-replies are ignored. |
| **Funnel & reporting** | Funnel: sent → delivered → opened → clicked → replied → bounced / unsubscribed. Run history, per-stage progress, live activity log. Excel and CSV exports. Optional Google Sheets mirror. |
| **Weekly list** | Top-N contacts by fit score, delivered by email or SMS to a recipient you choose. |
| **Dashboard** | Local web app: Overview, Database, Outbox, NFA firms, Sequences & funnel, Discovery, Settings. No build step. |
| **Model-agnostic LLM** | Claude through the official Anthropic SDK, or any OpenAI-compatible model: OpenAI, Gemini (its OpenAI endpoint), Groq, OpenRouter, DeepSeek, Mistral, Ollama, LM Studio, vLLM. Switching is a config change. |
| **Demo mode** | Full pipeline on synthetic data with no API calls and a separate database, so you can try everything safely. |
| **Tests** | pytest suite (no network) covering sequences, compliance, tracking, discovery, import, firm workflow and the API. |

---

## Who it is for

**Primary users**

- **Business development and sales teams** at asset managers, CTAs, commodity pool operators, allocators, family offices, fund-of-funds and placement agents who need a steady, well-targeted pipeline without buying more data.
- **Small teams without a sales-ops function**, where one person is both researcher and outreach owner.
- **Analysts and compliance-minded teams** who need a record of every prospect, the source of each fact, why it scored as it did, and what was sent.

**Secondary users**

- **Developers** who want a working reference for a sequenced, approval-gated outreach system with a model-agnostic LLM layer.
- **Ops teams** that enrich and maintain a registry or master list (regulator exports, membership lists, partner lists) and need it kept current.

---

## Business scenarios

| Scenario | How the bot helps |
| --- | --- |
| **Re-engage a dormant client or prospect database.** You have thousands of old contacts, some institutional, some stale. | Import, deduplicate, and enrol the segment you choose. The sequence adapts to engagement, so warm contacts get a different follow-up from cold ones. |
| **Expand into new markets** (for example Europe or Asia) where the database is thin. | Discovery plans queries around your target markets, finds firms with work emails, and scores them so the best go to the top of the review list. |
| **Keep a regulated master list current.** You maintain a list of registered firms and need to know which ones have a website, a contact, and a reason to be prioritised. | Load the list, let the bot look up missing websites and contacts, score every row with written reasons, and export it in the same layout. |
| **Weekly pipeline review for a senior person.** A leader wants a short list of prospects each week without reading every record. | The weekly top-N is ranked by fit and delivered by email or SMS, with the reasons available in the database. |
| **Personalised first outreach at scale** without sending mass email. | Each message is drafted per contact from its own facts, reviewed in the Outbox, and sent in small daily batches. |
| **Measuring what outreach actually does.** | The funnel shows opens, clicks, replies and unsubscribes per sequence, so you can change the wording or timing with evidence. |
| **Building a proprietary prospect dataset** over time, with outcomes recorded alongside each contact, for later scoring models. | Every contact, score, message and event is stored in one SQLite file you own. |

---

## How it works

### Overview

```
                  ┌────────────────────────── Discovery (Part 1) ──────────────────────────┐
  Search APIs ──► │ plan queries → search → de-duplicate → read site → extract fields      │
  (SerpApi)       │   → work-contact check → score → store as NEW contact or firm          │
                  └───────────────────────────────────┬────────────────────────────────────┘
                                                      │
  Your list ───────► Import / master list ────────────┤
  (CSV, Excel)        (de-duplicate, keep layout)     │
                                                      ▼
                                       ┌──────── Database (SQLite) ────────┐
                                       │ contacts · firms · sequences      │
                                       │ enrolments · messages · events    │
                                       │ suppression · runs · logs         │
                                       └────────────────┬──────────────────┘
                                                        │
                  ┌──────────── Sequence engine & composer (Part 2) ─────────┐
                  │ due step? → pick variant (default/engaged/silent)        │
                  │   → write email (template + LLM) → footer + unsubscribe  │
                  └────────────────┬─────────────────────────────────────────┘
                                   ▼
                       Outbox  (draft → approved → sent)   ◄── human review
                                   │
                                   ▼
             Sending (Brevo API or SMTP) — checks: suppression, lawful basis,
             sending window, daily cap, approval, enrolment still active
                                   │
                                   ▼
             Events back in: Brevo webhooks (open, click, bounce, unsubscribe)
                             IMAP replies (stop sequence)
                                   │
                                   └──► branch the next step, update the funnel
```

### Discovery (contacts)

1. **Plan.** The LLM reads your `offering` and `ideal_customer` and proposes search queries for each channel (Google for firms, LinkedIn and X for people). Without an LLM, the seed keywords in `queries.txt` are used on every channel. Queries are interleaved across channels.
2. **Search.** Each query is sent to SerpApi, one page at a time. LinkedIn and X queries are restricted to their public profile pages with a `site:` operator.
3. **De-duplicate.** Each candidate is checked against the database (domain, LinkedIn key, X handle, name + company) and against candidates already seen in this run. Duplicates are counted and skipped.
4. **Read.** For companies, the bot reads the homepage and up to a few contact / about / team pages. For people, it first finds the employer's website with one search, then reads it. Robots rules and polite delays apply.
5. **Check for a work contact.** A company email must be on the company's own domain. A person's email must match their name (for example `jane.doe@` or `jdoe@`).
6. **Qualify.** The keyword score measures how many query words appear on the page. If an LLM is configured, it returns a fit score, a reason, and whether the prospect is relevant at all. Final score is `0.7 × LLM + 0.3 × keyword %`, plus a small bonus for a work contact. Irrelevant results are rejected.
7. **Store and enrol.** New contacts are saved with their source, channel, fit score and reason. Those at or above `auto_enroll_min_score` with an email enter the default sequence, where they still need approval.

### Master-list workflow (firms)

1. **Load** the list. The 20 original columns are stored exactly. Excel's floating-point IDs are cleaned.
2. **Plan** searches from the list itself: the registration types it contains, crossed with the cities, states and target markets it concentrates on. Round-robin, so each location is covered early.
3. **Search** Google and the registry's public pages (`site:` restricted).
4. **Read and fill** each new firm's columns from the page text. The LLM (if configured) answers only from the text; pattern matching fills any gaps. A column stays blank unless the text states it. The NFA ID is taken when it appears.
5. **Look up** existing firms that have no website, up to a per-run limit. A website is accepted only when its domain contains a significant word from the firm's name, which avoids attaching the wrong company.
6. **Classify** each screened IB from its website text: `capital_raiser`, `hedger`, `execution_only` or `uncertain`. Evidence beats terminology — a site can say "futures" a hundred times and still be an agricultural hedging desk, and a real capital-raising IB may never write "CTA". Every classification stores the website excerpt and the page URL that support it (EVIDENCE / EVIDENCE URL columns), so each decision can be checked against the source.
7. **Separate** the piles with LEAD QUALITY: `qualified` (capital-raising IB with supporting evidence), `uncertain` (investment language, no proof — human review), `irrelevant` (hedging-only, execution-only, or no supporting match). A manual **Keep / Exclude** decision overrides the bot.
8. **Score** every row by its attributes, with reasons (see below). Hedging-only and execution-only firms can never rank A or B.
9. **Export** the list in its original layout plus the bot's columns. The workbook now has four sheets: the full list, **Qualified IBs**, **Needs review**, and **Excluded (hedging-exec)** — so qualified partner prospects never mix with the review and exclusion piles.
10. **Draft** a tailored partner email only for High-priority capital-raisers with a work email (or firms a person marked Keep). The email uses only facts from that row and contains no performance figures.

> **IB prospects vs investor leads.** The firms table is the *distribution partner* pipeline; the contact database is the *prospective investor* pipeline. They are stored, scored and qualified separately and never mix.

### Sequence engine

For each enrolment, the engine runs a loop on each tick:

1. Skip anything not due, or whose previous email is still waiting for approval or sending.
2. If the contact is suppressed, stop. If the sequence is finished, mark it complete.
3. Choose the variant: `default` for the first email; afterwards `engaged` if the previous email was opened or clicked, else `silent`.
4. Compose the email (template, optional LLM personalisation, footer with sender, postal address and unsubscribe link). Save it as a draft (or as approved if approval is off).
5. Send only when approved, enabled, inside the sending window, under the daily cap, and when the contact still passes compliance checks. After a send, schedule the next step at the send time plus the configured gap.

A reply, bounce, unsubscribe or complaint at any point marks the enrolment and stops it.

### Scoring

**Contacts** (fit score, 0–100): `0.7 × LLM fit + 0.3 × keyword match`, +5 for a work contact. Tiers: HOT ≥ 70, WARM ≥ 40, COLD otherwise. Irrelevant results are rejected.

**Master-list firms** (points per attribute, every point explained):

| Attribute | Default points | Why it matters |
| --- | ---: | --- |
| Registered CTA | +25 | Runs managed-futures programmes |
| Registered CPO | +20 | Runs pooled funds |
| Introducing broker | +8 | Distribution |
| Futures commission merchant | +10 | Larger, clearing-level firm |
| Forex registration | +5 | Adjacent activity |
| Swap registration | +5 | Adjacent activity |
| NFA member approved | +10 | Clean standing |
| NFA membership pending | −8 | Not yet cleared |
| Independent (not guaranteed) | +4 | Own clearing relationship |
| Target market | +10 | Your priority geographies |
| Website found | +5 | Reachable |
| Work email found | +10 | Reachable directly |
| Named contact | +4 | Personalisation possible |
| Senior contact title | +8 | Decision-maker |

Tiers: A ≥ 60, B ≥ 40, else C. All of these are in `config.yaml` under `firms:`.

### Engagement tracking

- **Brevo** posts events to `/webhooks/brevo`. Each event is matched to the sent message by its message ID. Opens and clicks are stored and drive the next variant. Hard bounces, unsubscribes and spam complaints suppress the address.
- **IMAP** (Gmail, Outlook, any IMAP host) is polled for unread mail from the last seven days. A reply is matched to the sent email through its `In-Reply-To` / `References` headers, or by sender address as a fallback. Out-of-office and other automatic replies are ignored.
- **Unsubscribe links** are signed per contact. The one-click page suppresses the address and stops every sequence for that contact.

### Autopilot

Settings → *Autopilot* runs drafting, sending and reply checks every ten minutes while the dashboard is open. It respects every rule above, including approval.

---

## Quick start

**Requirements:** Python 3.10 or newer, and an internet connection for live use.

```bash
git clone <this-repo> buckingham_lead_gen
cd buckingham_lead_gen

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env               # add the keys you have (all are optional for demo mode)
python run_dashboard.py            # opens http://127.0.0.1:5057
```

**Try it safely first.** In the **Discovery** tab, set the mode to *Demo* and press **Start discovery**. Demo runs use synthetic data, make no API calls, and write to their own database (`data/demo.db`). The **NFA firms** tab has a demo mode too — with the mode set to *Demo*, the firms table, counts, Excel download and the Outbox all show the demo database, so a full dry run is visible end to end.

**Then go live**, in this order:

1. Add `SERPAPI_KEY` and an `LLM_API_KEY` to `.env`.
2. Edit the `profile:` section of `config.yaml` (company, sender, offering, ideal customer).
3. Import a small test list (20 contacts) in the **Database** tab.
4. Replace the draft email copy in `config.yaml` (`outreach.templates`).
5. Set `outreach.postal_address` and `profile.sender_name`.
6. Add Brevo and IMAP credentials when you are ready to send.
7. Keep `approval_required: true` and `send_enabled: false` until you have reviewed several drafts.

---

## Using the dashboard

| Tab | Use it to |
| --- | --- |
| **Overview** | See contact totals, emails sent in the last 24 hours against the daily cap, opens, clicks, replies, emails waiting for approval, the discovery run's progress and the live log. |
| **Database** | Import a list, search and filter contacts, record lawful basis, and enrol contacts into a sequence (ticked rows or everything matching the filters). |
| **Outbox** | Read each drafted email, edit it, approve or reject it, approve all drafts, or draft and send what is due now. Export the outbox as CSV. |
| **NFA firms** | Load a master list, run the firm workflow, filter by tier and outreach status, and download the list in its original layout. |
| **Sequences & funnel** | See the funnel, the sequence definitions, and every enrolment with its status and next action. |
| **Discovery** | Start, pause, resume or stop a discovery run. See the search plan, the newest contacts, and the run history. |
| **Settings** | Turn autopilot on or off, copy the Brevo webhook URL, set the weekly-list recipient (email or SMS), and check which integrations are configured. |

Unsubscribe pages are served at `/unsubscribe/<contact>/<token>`, and the Brevo webhook at `/webhooks/brevo`. Both must be reachable from the internet for production use.

---

## Command line

```bash
python -m leadgen.cli run --demo            # discovery, offline
python -m leadgen.cli run                   # discovery, live (needs SERPAPI_KEY)
python -m leadgen.cli run --target 50       # override the number of new contacts
python -m pytest -q                         # tests
```

---

## Configuration

Everything you are likely to change lives in two places: `config.yaml` (behaviour, copy and rules) and `.env` (secrets). `config.yaml` is commented section by section.

### `config.yaml` at a glance

| Section | Controls |
| --- | --- |
| `profile` | Company name, sender name and role, offering, ideal customer. The LLM uses these to plan searches and write emails. |
| `run` | Target number of new contacts, candidate pool size, planned queries, pages per query, weekly top-N. |
| `sources` | Turn Google, LinkedIn and X discovery on or off. |
| `search` | Google country and language for SerpApi. |
| `enrich` | Concurrent workers, timeouts, delays between requests, `robots.txt` handling, contact page paths, page limit. |
| `llm` | Enable or disable the LLM, provider, model, base URL. |
| `storage` | Database path and output folder. |
| `sheets` | Optional Google Sheets mirror. |
| `delivery` | Channel (email or SMS) and default recipient for the weekly list, and whether sending is enabled. |
| `smtp` | Host, port and From address for SMTP delivery. |
| `outreach` | Sending switch, approval, daily cap, sending window, provider (Brevo or SMTP), postal address, public URL, lawful-basis regions, auto-enrol score, default sequence, **sequences**, **templates**. |
| `firms` | Master-list workflow: target, target countries, outreach tier, tier cut-offs, scoring weights, existing-firm lookup limit. |
| `blocklist_domains` | Domains never treated as prospects (social networks, news, directories). |

### `.env` at a glance

| Variable | Used for |
| --- | --- |
| `SERPAPI_KEY` | Google, LinkedIn and X search results |
| `LLM_API_KEY` | The LLM (`ANTHROPIC_API_KEY` also works for Claude) |
| `LLM_PROVIDER`, `LLM_MODEL`, `LLM_BASE_URL` | Optional overrides of `config.yaml` |
| `BREVO_API_KEY` | Sending sequence emails through Brevo |
| `IMAP_HOST`, `IMAP_USER`, `IMAP_PASSWORD` | Reply detection |
| `SMTP_USER`, `SMTP_PASSWORD` | SMTP sending and weekly-list email |
| `OUTREACH_SECRET` | Signs unsubscribe links and the webhook URL. Any long random string. |
| `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER` | Weekly list by SMS |

---

## Where to change things

| I want to… | Change this |
| --- | --- |
| Change what the bot is for, or the wording the LLM uses to describe us | `profile:` in `config.yaml` |
| Change the search keywords | `queries.txt` (used without an LLM) and the planner prompt in `leadgen/planner.py` |
| Search more or fewer results | `run:` (`target_leads`, `pages_per_query`, `planned_queries`) |
| Add or remove a discovery source | `sources:` in `config.yaml`; the channel logic is in `leadgen/search.py` and `leadgen/pipeline.py` (`_candidate_from_hit`) |
| Change the sequence timing (for example 3 emails over 21 days) | `outreach.sequences` |
| Change the email copy | `outreach.templates`. Each step can have `default`, `engaged` and `silent` variants. Placeholders: `{first_name}`, `{full_name}`, `{company}`, `{title}`, `{region}`, `{segment_hint}`, `{company_name}`, `{sender_name}`. |
| Change how the LLM personalises emails | `leadgen/composer.py` (`personalise`) |
| Change how contacts are scored | `leadgen/qualify.py` (`combine_score`, thresholds) and the LLM prompt in `LLMQualifier.assess` |
| Change master-list scoring | `firms:` → `weights` and `tiers` in `config.yaml`; logic in `leadgen/firm_scoring.py` |
| Change which firms get emails, and when | `firms.outreach_tier` in `config.yaml`; logic in `leadgen/firm_pipeline.py` (`_queue_outreach`) |
| Change the email for master-list firms | `leadgen/firm_email.py` (`angle`, `fallback_draft`, and the LLM prompt in `draft`) |
| Change which fields are extracted from a web page | `FIRM_FIELDS` and the prompt in `leadgen/firm_search.py`; the column layout is in `leadgen/firms.py` (`MASTER_COLUMNS`, `EXTRA_COLUMNS`) |
| Change the columns in the master export | `leadgen/firms.py`. Keep `MASTER_COLUMNS` in the original order. |
| Change import column names that are recognised | `ALIASES` in `leadgen/importer.py` |
| Add a region, or change timezones | `REGION_BY_COUNTRY` in `leadgen/importer.py`, `REGION_TZ` in `leadgen/mailer.py` |
| Change the sending window, daily cap, or approval rule | `outreach:` in `config.yaml` |
| Change the lawful-basis options | `LAWFUL_BASES` in `leadgen/controller.py` |
| Change the unsubscribe or footer wording | `footer()` in `leadgen/composer.py` |
| Change the blocked domains | `blocklist_domains` in `config.yaml` |
| Change the dashboard look or add a tab | `dashboard/templates/index.html`, `dashboard/static/app.js`, `dashboard/static/style.css`, routes in `dashboard/app.py` |
| Change the weekly list format | `leadgen/notify.py` (`build_text`, `build_email`) |
| Change the export columns for contacts | `CONTACT_COLUMNS` in `leadgen/export.py` |

---

## APIs and where they are spent

Costs come from four kinds of external service. Everything else (reading websites, `robots.txt`, the webhook, the unsubscribe page) is ordinary HTTP with no per-call charge, though it is rate-limited on purpose.

### Summary

| Service | Module and function | When it is called | What drives the volume | Typical cost driver |
| --- | --- | --- | --- | --- |
| **SerpApi** (Google engine) | `leadgen/search.py` → `SerpApiSearch.search` | Every search page: discovery queries, the company-domain lookup for a person, the firm workflow's search and existing-firm lookup | `planned_queries × pages_per_query`, plus one lookup per person and per existing firm | Per search (one call per results page) |
| **LLM** (any provider) | `leadgen/llm.py` → `complete_json` / `complete_text` | Query planning, contact qualification, email personalisation, firm-page extraction, firm email drafting | Number of candidates checked, number of drafts, number of firm pages read | Per call, scaled by input and output tokens |
| **Brevo** (email API) | `leadgen/mailer.py` → `send_brevo` | Each approved email that is sent | Approved emails and daily cap | Per email sent |
| **Twilio** (SMS) | `leadgen/notify.py` → `send_sms` | Weekly list, once per SMS chunk | Length of the list | Per SMS segment |

Other integrations with their own costs or limits: SMTP (`mailer.send_smtp`, `notify.send_email_msg`), IMAP (`events.poll_replies`, free but polled every ten minutes in autopilot), and Google Sheets (`export.sync_google_sheet`, via `gspread`, free within Google's quotas).

### 1. SerpApi: search results

- **Where:** `SerpApiSearch.search()` in `leadgen/search.py`. The HTTP call is one GET to `serpapi.com/search.json` per page.
- **Discovery:** `pipeline.py` → `_search()` calls it once per query and page, up to `pages_per_query` pages. Results are 10 per page.
- **Person → employer website:** `pipeline.py` → `_company_domain()` makes one search per employer, cached for the run.
- **Master-list firms:** `firm_pipeline.py` → `_search()` (per query and page) and `_complete_existing()` (one search per firm without a website, up to `firms.enrich_existing`).
- **How to control it:** lower `run.planned_queries`, `run.pages_per_query`, `run.target_leads`, and `firms.enrich_existing`. Turn a source off in `sources:`.

### 2. LLM: planning, scoring, writing

Every LLM call goes through `complete_json()` or `complete_text()` in `leadgen/llm.py`, which calls the provider's API (the Anthropic SDK, or an OpenAI-compatible HTTP endpoint). The call sites:

| Step | Function | Calls per run |
| --- | --- | --- |
| Plan contact search queries | `planner.plan_queries` | 1 |
| Qualify each candidate (fit score, reason, country, relevance) | `qualify.LLMQualifier.assess` | 1 per candidate checked (the biggest driver) |
| Personalise each sequence email | `composer.compose` → `personalise` | 1 per email drafted |
| Read a firm's web page into master-list columns | `firm_search.extract_fields` | 1 per firm page read, and 1 per existing-firm lookup |
| Draft a master-list firm's email | `firm_email.draft` | 1 per firm emailed |

- **Cost drivers:** the number of candidates (`run.target_leads × run.candidate_multiplier`), the page text sent (capped at about 4,000–5,000 characters per call), and the number of drafts.
- **Cheaper runs:** set `llm.enabled: false` for keyword-only scoring and template emails, use a smaller `llm.model`, or lower the candidate multiplier.
- **Failures are handled:** if an LLM call fails, the bot falls back to keyword scoring or the template, and logs a warning. Rate-limit errors are retried.

### 3. Brevo: sending

- **Where:** `mailer.send_brevo()`, called from `engine.send_due()` for each approved message that passes the compliance checks.
- **Cost driver:** the number of approved emails that are actually sent. Drafts that are not approved cost nothing.
- **Limits you control:** `outreach.daily_cap`, `outreach.sending_window`, `outreach.send_enabled`, `outreach.approval_required`.
- **Inbound:** Brevo's webhooks arrive at `/webhooks/brevo`. Receiving them costs nothing. They are verified by a token derived from `OUTREACH_SECRET`.

### 4. Twilio: SMS (weekly list only)

- **Where:** `notify.send_sms()`, called from `deliver_top_leads()` when `delivery.channel` is `sms` and `delivery.enabled` is true.
- **Cost driver:** the number of text segments. The list is split into chunks of about 1,500 characters.

### Where the APIs are called in one run (contact discovery)

```
Plan ──► [LLM: 1 call]
  │
  for each query and page:
  │   ──► [SerpApi: 1 call per page]
  │
  for each candidate (up to the pool size):
  │   ──► [company site or employer lookup: SerpApi 0–1 call; HTTP reads]
  │   ──► [LLM: 1 call per candidate]
  │
  for each enrolled contact whose step is due:
  │   ──► [LLM: 1 call per email, if personalisation is on]
  │
  for each approved email, when sending is on and it is due:
  │   ──► [Brevo: 1 call]
  │
  ◄── Brevo webhooks and IMAP polling cost no API calls to receive
```

---

## Adding a new API or data source

The code is organised so that a new source needs one new function, and a change to where results are used.

### A. A new search provider (for example Bing, Apollo or a data vendor)

1. **Write the client** in `leadgen/search.py` (or a new module). It needs a `search(query, page, channel)` method that returns a list of `SearchHit` objects, as `SerpApiSearch` does. Raise `SearchError` on failure so the pipeline logs it and carries on.
   ```python
   class BingSearch:
       def __init__(self, api_key: str, session=None): ...
       def search(self, query: str, page: int, channel: str = "google") -> list[SearchHit]:
           # call the API, map each result to SearchHit(title, url, snippet, query, channel)
           ...
   ```
2. **Read the key from `.env`** in `leadgen/config.py` (add a field to `Settings`, and read it in `Settings.load`).
3. **Build it in `build_services()`** in `leadgen/controller.py`, next to `SerpApiSearch`, and pass it as `Services.search`. Add a demo version in `leadgen/demo.py` if you want demo mode to cover it.
4. **Add a channel** if the results are a new kind of thing: add a `use_<name>` flag in `Settings` and `sources:` in `config.yaml`, add it to `Pipeline._channels()`, and map results to candidates in `Pipeline._candidate_from_hit()`.
5. **Teach the planner** the new channel's query style in `leadgen/planner.py` (the prompt describes what each channel finds).

Use the same rule as the existing sources: only public data, and only where the provider's terms allow automated access.

### B. A new LLM provider

- **If it speaks the OpenAI Chat Completions format:** no code needed. Set `llm.provider: openai_compatible`, the `base_url`, `model` and `LLM_API_KEY`.
- **Otherwise:** add a class with `model`, `provider` and `complete_text(system, user, max_tokens) -> str` in `leadgen/llm.py`, and add a branch to `build_llm()`. `complete_json()` then works automatically.

### C. A new email or SMS provider

- **Email:** write `send_<name>(settings, to_email, to_name, subject, body, unsub_url) -> provider_message_id` in `leadgen/mailer.py`, and add it to `send()`. Return the provider's message ID, so webhook events can be matched to the sent message. Set `List-Unsubscribe` as the other providers do.
- **SMS:** add a function to `leadgen/notify.py` like `send_sms`, and route to it in `deliver_top_leads()`.

### D. A new engagement source (for example a different ESP's webhooks)

Add a parser in `leadgen/events.py` that turns the payload into the normalised event dictionaries (`type`, `email`, `provider_id`, `occurred_at`, `detail`), and a route in `dashboard/app.py` that calls `controller.webhook()`-style handling. `apply_event()` does the rest.

### E. A new enrichment step (for example company size, or a LinkedIn company page)

Add the step inside `Pipeline._process()` (contacts) or `FirmPipeline._build_row()` (firms), store the result in a column, and include it in the score in `leadgen/qualify.py` or `leadgen/firm_scoring.py`. Remember that any API call there is charged per candidate, so decide whether it should run for every candidate or only for those that pass the first score.

### F. Checklist for any new API

- [ ] Key read from `.env`, not from code or `config.yaml`
- [ ] Demo version so the workflow can still be tested offline
- [ ] Failure handled: raise a typed error, log it, carry on
- [ ] Call count bounded by a config value you can lower
- [ ] Added to the [APIs table](#summary) above and to `.env.example`
- [ ] A test in `tests/` that uses a fake client, not the network

---

## Compliance and responsible use

This tool sends email to people. Read this section before turning sending on.

- **Lawful basis.** Email is only sent to a contact that has a lawful basis recorded, in regions that require one (Europe by default; see `outreach.require_lawful_basis_regions`). The bot cannot decide that you have a basis. It records the label you choose, and the choice is yours to make honestly.
- **Unsubscribe.** Every email carries a working unsubscribe link. Unsubscribing stops all sequences for that contact at once and suppresses the address.
- **Suppression.** Bounces, unsubscribes and complaints are suppressed permanently. Imports and discovery respect the list.
- **Identity.** Every email shows the sender and your postal address (`outreach.postal_address`). Set it before sending.
- **Approval.** Approval is on by default. Keep it on until you have reviewed a good sample of drafts, and check every template change.
- **Rate and time.** Sending is capped per day and limited to the recipient's business hours.
- **Public data only.** Discovery uses search results and public web pages. It respects `robots.txt` and does not log in to LinkedIn, X or any registry. Do not add scraping of logged-in or access-restricted pages.
- **Registry data.** Data from a regulator's public registry is subject to that regulator's terms of use. Check them before using the data for outreach.
- **Data protection.** The database holds personal data. Keep `data/` and `.env` out of version control (they are in `.gitignore`), restrict access to the machine running the dashboard, and honour deletion requests by removing the contact and adding the address to the suppression list.
- **Consult your compliance team** before using this in a regulated business. The rules differ by country (GDPR and PECR in the UK and EU, CAN-SPAM in the US, and others).

---

## Project structure

```
buckingham_lead_gen/
├── config.yaml              Profile, sources, LLM, sequences and copy, compliance, firm scoring
├── queries.txt              Seed keywords (used when no LLM is configured)
├── .env.example             Secret template: copy to .env
├── requirements.txt
├── run_dashboard.py         Start the dashboard
├── PROJECT_BRIEF.md         Project direction and open questions
├── leadgen/
│   ├── config.py            Loads config.yaml and .env into Settings
│   ├── storage.py           SQLite: contacts, firms, sequences, enrolments, messages, events, suppression
│   ├── models.py            Candidate, Lead, SearchHit and identity helpers
│   ├── importer.py          CSV / Excel import, column matching, de-duplication, regions
│   ├── pipeline.py          Contact discovery: plan → search → de-duplicate → read → qualify → store
│   ├── planner.py           Search query planning, interleaved across channels
│   ├── search.py            SerpApi client (Google, LinkedIn-restricted, X-restricted, NFA-restricted)
│   ├── enrich.py            Website reading, work emails, person-to-email matching, robots.txt
│   ├── qualify.py           Keyword score, LLM fit score, final score and tier
│   ├── composer.py          Template filling, LLM personalisation, footer and unsubscribe link
│   ├── engine.py            Sequence engine: drafts, approval, sending, scheduling
│   ├── mailer.py            Compliance checks, unsubscribe tokens, Brevo and SMTP sending
│   ├── events.py            Brevo webhook parsing, suppression, IMAP reply detection
│   ├── firms.py             Master-list columns, parsing and export layout
│   ├── firm_search.py       Firm query planning and extraction of list columns from pages
│   ├── firm_scoring.py      Attribute scoring with reasons
│   ├── firm_email.py        Tailored emails from firm facts
│   ├── firm_pipeline.py     Master-list workflow: search, fill, look up, score, export, draft
│   ├── controller.py        Background runs, Outbox actions, autopilot, snapshots for the dashboard
│   ├── llm.py               Model-agnostic LLM client (Anthropic SDK and OpenAI-compatible HTTP)
│   ├── notify.py            Weekly top-N delivery by email or SMS
│   ├── export.py            Excel and CSV exports, optional Google Sheets
│   ├── demo.py              Offline stand-ins for search, sites and the LLM
│   └── cli.py               Command-line runner
├── dashboard/
│   ├── app.py               Flask routes: JSON API, webhooks, unsubscribe, file downloads
│   ├── templates/index.html
│   └── static/              app.js, style.css
├── tests/                   pytest suite (no network)
├── data/                    Created on first run: SQLite databases (git-ignored)
└── outputs/                 Created on first run: exports (git-ignored)
```

---

## Testing

```bash
python -m pytest -q
```

The suite runs without network access and without keys. It covers:

- **Sequences:** approval gate, sending window and daily cap, waiting for a send before the next step, engaged and silent branches, reply stops the sequence.
- **Compliance:** suppression, lawful-basis gate, unsubscribe tokens, bulk lawful-basis recording.
- **Tracking:** Brevo events, bounce suppression, webhook token checks.
- **Discovery:** deduplication across runs, all three sources, auto-enrolment, CSV import and column matching.
- **Master-list workflow:** column layout preserved on export, Excel ID handling, attribute scoring with reasons, target-market bonus, pattern extraction that does not invent fields, emails that use only column facts, and an end-to-end firm run.
- **API:** import, approval, webhook and unsubscribe through the Flask test client.

---

## What is not yet verified

These parts are built and unit-tested with stand-ins, but have not been run against live services:

- Google, LinkedIn and X search through SerpApi (the parsing is tested with recorded-shape fixtures, not live responses)
- Live LLM output quality for personalisation and extraction
- Brevo sending, Brevo webhooks, and IMAP reply detection against real mailboxes
- Twilio SMS and Google Sheets sync

Run every one of these with a small test list before scaling up.

---

## Roadmap and limitations

- **Engagement signals are only as good as the sender.** SMTP sending gives no open or click data, so every follow-up uses the "silent" wording. Use Brevo, or another sender that provides tracking, for the engaged/silent branching.
- **Replies are detected by IMAP polling**, not by the Gmail or Outlook APIs. Polling runs on the autopilot timer, so replies are noticed within about ten minutes.
- **The dashboard and autopilot run in one process on one machine.** Public webhooks and unsubscribe pages need a publicly reachable address. There is no multi-user login yet; restrict access at the network level.
- **Discovery depends on search results.** Coverage is limited to what the search provider indexes, and LinkedIn and X results are only what search engines show publicly.
- **The LLM can be wrong.** Relevance, extracted fields and drafts are suggestions for a person to check. Approval exists for this reason.
- **Scoring weights are a starting point.** They encode one team's view of what matters. Review them against outcomes.
- **The data model is opinionated** for institutional prospecting. Adapting it to, for example, consumer leads means changing the columns, the scoring and the templates.
- Possible next steps: multi-user access, a Gmail/Outlook API for replies, a bulk editor for contact fields, scheduled exports, and a feedback loop that uses outcomes to tune the scoring.

---

## Contributing

Issues and pull requests are welcome.

1. Fork the repository and create a branch.
2. Install the requirements and run `python -m pytest -q` before you start, so you know the baseline.
3. Keep changes focused, add or update a test for each behaviour you change, and do not add dependencies without a reason.
4. Never commit `.env`, `data/`, `outputs/`, or real contact data. Test data belongs in `tests/` as small synthetic fixtures.
5. Describe in the pull request what changed, why, and how you tested it.

---

## License

Add a license file before publishing. This repository does not yet include one.
