# Project brief — Buckingham business development automation

## One-line purpose

Use AI to turn Buckingham's institutional contact database into **individual, adaptive email sequences** that re-engage prospects and bring them back to us for meetings, and keep discovering and deduplicating new prospects so the database keeps growing.

## Starting point

- **Database:** about 20,000 raw leads over 15–20 years (paid databases such as With Intelligence, LinkedIn research, past outreach). An AI pass cleaned it to a little over **6,000** usable contacts: bad records and people who changed companies removed.
- **Mix:** about 90%+ institutional (family offices, funds, allocators, consultants). Roughly half added this year. Roughly half US, half overseas (Europe and Asia, including Japan and Hong Kong, are the growth opportunity).
- **Existing campaigns:** about 12,000 contacts receive the monthly Buckingham and Hulson emails through Brevo, with opens and clicks already tracked. Some overlap exists between the two lists.

## The bot has two functions

### Function 1 — Work the existing database (core)

1. Import the contact list (CSV / Excel) and deduplicate it.
2. Each contact is enrolled in a **sequence**: email 1, then email 2 and 3 on a schedule (for example one email per week).
3. Each email is **written individually** by the LLM from the template, the contact's profile and their region, and shown in an **Outbox** for preview and approval before it is sent. Approval is required in early tests.
4. The **engagement engine** tracks what happened after each send and chooses the next step:

| Signal | Source | Next action |
| --- | --- | --- |
| Opened / clicked, no reply | Brevo webhook | Send the "engaged" variant of the next email |
| Nothing opened | Brevo webhook | Send the "silent" variant (shorter, different subject) |
| Replied | Reply watcher (IMAP; Gmail or Outlook API later) | Stop the sequence and alert the team |
| Hard bounce | Brevo webhook | Stop and suppress the address |
| Unsubscribed or spam complaint | Unsubscribe link or webhook | Stop and suppress permanently |
| Sequence finished, no response | Internal | Mark for a later re-engagement round |

5. Every event is stored, so the **funnel** (sent → opened → clicked → replied → meeting) can be seen per contact, per sequence, per region.

### Function 2 — Discover new prospects

1. Search Google, LinkedIn and X for prospects matching the keywords: fund of funds, alternatives, hedge funds, managed futures, commodity trading advisors, liquid alternatives, options and futures, family office.
2. Check every result against the database (email, domain, LinkedIn profile, and name plus company).
3. If there is no match, insert it as a **new contact** with its source recorded, then score it for relevance.
4. Each week, the top 10–20 new and existing contacts are sent to Vishal for review.

## Compliance (required, especially for Europe and Asia)

- Every email has a working **unsubscribe link**, and unsubscribes are honoured immediately across all sequences.
- A **suppression list** blocks sending to anyone who unsubscribed, complained or hard-bounced.
- Each contact records its **lawful basis / source** (for example "existing client relationship" or "legitimate interest, B2B"). UK and EU contacts are flagged so the team can review them under GDPR and PECR.
- Every email carries the sender's identity and a postal address.
- Send only within business hours in the recipient's time zone, with a daily cap.
- A data-deletion path: remove a person's record and suppress them, on request.

## Firm context for the messages

- Strategy: CTA and managed futures, plus options-based hedging. The book is mostly short volatility, roughly 90/10 in normal years and more long volatility in volatile years. The messages must make clear that this is not a traditional short seller, and that P&L volatility is managed systematically. Hedge budgets are predetermined per position.
- Regions: prioritise Europe and Asia (Japan, Hong Kong), with region-appropriate timing and language.

## Open questions (defaults are set in config, can be changed)

1. **Sending:** Brevo is the default, because it already tracks opens and clicks. A plain SMTP sender is the fallback.
2. **Reply detection:** IMAP with an app password (works with Gmail and Outlook). The Gmail and Outlook APIs can replace it later.
3. **LinkedIn:** no automated messaging. Discovery reads public search results only. LinkedIn official API access would need to be paid for and approved.
4. **X:** discovery through Google `site:x.com` search, since X's own API is paid.
5. **Overlap** between the Buckingham and Hulson lists is handled by deduplication, so a contact receives one sequence at a time.

## Team

- Jason: sponsor, strategy and review.
- Kai: builds the bot and runs the first sequence test (a few hundred contacts, three emails, once a week).
- Bishoy: co-builds the lead bot.
- Vishal: receives the weekly top list and reviews potential clients.
- Charles: joins for one-on-one support in about three weeks.
