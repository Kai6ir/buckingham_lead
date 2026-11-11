"""Sequence engine, compliance, tracking and unsubscribe — no network: sending is stubbed."""

import dataclasses
from datetime import datetime, timedelta

import pytest

from leadgen import engine as engine_mod
from leadgen import events, mailer
from leadgen.controller import BotController
from leadgen.engine import SequenceEngine
from leadgen.storage import Store


@pytest.fixture
def store(settings):
    return Store(settings.db_path)


def add_contact(store, **kw):
    data = {"email": "jane@acme.com", "full_name": "Jane Doe", "first_name": "Jane", "last_name": "Doe",
            "company": "Acme Capital", "region": "americas", "source": "import", "channel": "import",
            "status": "active", "lawful_basis": "existing relationship"}
    data.update(kw)
    return store.insert_contact(data)


def test_enrol_creates_first_draft_waiting_for_approval(settings, store):
    eng = SequenceEngine(settings, store)
    eng.sync_sequences()
    cid = add_contact(store)
    assert eng.enroll(cid) > 0
    assert eng.create_drafts() == 1
    drafts = store.messages(status="draft")
    assert len(drafts) == 1 and drafts[0]["step"] == 1 and drafts[0]["variant"] == "default"
    assert "Jane" in drafts[0]["body"] and "Unsubscribe:" in drafts[0]["body"]
    assert "https://example.test/unsubscribe/" in drafts[0]["body"]
    assert "1 Test Street" in drafts[0]["body"]


def test_no_email_or_suppressed_contacts_cannot_be_enrolled(settings, store):
    eng = SequenceEngine(settings, store)
    eng.sync_sequences()
    no_mail = add_contact(store, email=None, full_name="No Mail")
    assert eng.enroll(no_mail) == 0
    store.suppress("jane@acme.com", "test")
    cid = add_contact(store, email="jane@acme.com")
    assert eng.enroll(cid) == 0


def test_approval_gate_blocks_sending(settings, store, monkeypatch):
    sent = []
    monkeypatch.setattr(mailer, "send", lambda *a, **k: sent.append(a) or ("brevo", "id-1"))
    s = dataclasses.replace(settings, send_enabled=True)
    eng = SequenceEngine(s, store)
    eng.sync_sequences()
    eng.enroll(add_contact(store))
    eng.create_drafts()
    assert eng.send_due(datetime.now().replace(hour=12))["sent"] == 0  # draft, not approved
    mid = store.messages(status="draft")[0]["id"]
    assert eng.approve(mid)[0]
    result = eng.send_due(datetime.now().replace(hour=12))
    assert result["sent"] == 1 and sent


def test_sending_respects_switch_window_and_cap(settings, store, monkeypatch):
    monkeypatch.setattr(mailer, "send", lambda *a, **k: ("brevo", "id-x"))
    s = dataclasses.replace(settings, send_enabled=True, daily_cap=1)
    eng = SequenceEngine(s, store)
    eng.sync_sequences()
    for i in range(2):
        eng.enroll(add_contact(store, email=f"p{i}@acme.com", full_name=f"P {i}"))
    eng.create_drafts()
    for m in store.messages(status="draft"):
        eng.approve(m["id"])
    assert eng.send_due(datetime.now().replace(hour=12))["sent"] == 1  # cap of 1
    assert eng.send_due(datetime.now().replace(hour=12))["sent"] == 0  # cap reached
    eng2 = SequenceEngine(dataclasses.replace(s, daily_cap=50), store)
    # outside the 08:00-18:00 window in the contact's region nothing goes out
    assert eng2.send_due(datetime.now().replace(hour=3)) ["sent"] == 0


def test_next_email_waits_for_send_and_branches_on_engagement(settings, store, monkeypatch):
    counter = {"n": 0}

    def fake_send(s, to, name, subject, body, unsub):
        counter["n"] += 1
        return "brevo", f"pid-{counter['n']}"

    monkeypatch.setattr(mailer, "send", fake_send)
    s = dataclasses.replace(settings, send_enabled=True, approval_required=False)
    eng = SequenceEngine(s, store)
    eng.sync_sequences()
    cid = add_contact(store)
    noon = datetime.now().replace(hour=12, minute=0, second=0, microsecond=0)
    eng.enroll(cid, now=noon.isoformat(timespec="seconds"))
    eng.create_drafts(now=noon.isoformat(timespec="seconds"))
    assert eng.send_due(noon)["sent"] == 1
    # the second email is not drafted until its delay (7 days) has passed
    later = noon + timedelta(days=3)
    assert eng.create_drafts(now=later.isoformat(timespec="seconds")) == 0
    # the contact opened the first email -> second email uses the "engaged" variant
    first = store.messages(status="sent")[0]
    store.add_event(cid, first["id"], "open", later.isoformat(timespec="seconds"), "test")
    due = noon + timedelta(days=7, hours=1)
    assert eng.create_drafts(now=due.isoformat(timespec="seconds")) == 1
    second = store.messages(limit=1)[0]
    assert second["step"] == 2 and second["variant"] == "engaged"


def test_silent_contact_gets_silent_variant(settings, store, monkeypatch):
    monkeypatch.setattr(mailer, "send", lambda *a, **k: ("brevo", "pid-s"))
    s = dataclasses.replace(settings, send_enabled=True, approval_required=False)
    eng = SequenceEngine(s, store)
    eng.sync_sequences()
    cid = add_contact(store)
    noon = datetime.now().replace(hour=12, minute=0, second=0, microsecond=0)
    eng.enroll(cid, now=noon.isoformat(timespec="seconds"))
    eng.create_drafts(now=noon.isoformat(timespec="seconds"))
    eng.send_due(noon)
    eng.create_drafts(now=(noon + timedelta(days=8)).isoformat(timespec="seconds"))
    assert store.messages(limit=1)[0]["variant"] == "silent"


def test_reply_stops_the_sequence(settings, store):
    eng = SequenceEngine(settings, store)
    eng.sync_sequences()
    cid = add_contact(store)
    eng_id = eng.enroll(cid)
    events.record_reply(store, cid, None, datetime.now().isoformat(timespec="seconds"), "Re: hello")
    assert store.get_enrollment(eng_id)["status"] == "replied"
    assert store.get_contact(cid)["status"] == "replied"


def test_brevo_webhook_tracks_open_and_hard_bounce_suppresses(settings, store, monkeypatch):
    monkeypatch.setattr(mailer, "send", lambda *a, **k: ("brevo", "<abc@smtp.test>"))
    s = dataclasses.replace(settings, send_enabled=True, approval_required=False)
    eng = SequenceEngine(s, store)
    eng.sync_sequences()
    cid = add_contact(store, email="bounce@acme.com")
    eng_id = noon = datetime.now().replace(hour=12, minute=0, second=0, microsecond=0)
    eng_id = eng.enroll(cid, now=noon.isoformat(timespec="seconds"))
    eng.create_drafts(now=noon.isoformat(timespec="seconds"))
    eng.send_due(noon)
    ctl = BotController.__new__(BotController)  # use the real handler without starting the app
    ctl.base = s
    ctl._lock = None
    ctl._stores = {"live": store}
    payload = [{"event": "opened", "email": "bounce@acme.com", "message-id": "<abc@smtp.test>",
                "date": "2026-11-05 10:00:00"},
               {"event": "hard_bounce", "email": "bounce@acme.com", "message-id": "<abc@smtp.test>",
                "date": "2026-11-05 10:01:00"}]
    for ev in events.parse_brevo(payload):
        events.apply_event(store, ev)
    assert store.has_event(store.messages(status="sent")[0]["id"], "open")
    assert store.is_suppressed("bounce@acme.com")
    assert store.get_enrollment(eng_id)["status"] == "bounced"


def test_unsubscribe_token_and_suppression(settings, store):
    token = mailer.unsubscribe_token(7, "test-secret")
    assert mailer.verify_unsubscribe(7, token, "test-secret")
    assert not mailer.verify_unsubscribe(8, token, "test-secret")
    assert mailer.webhook_token("test-secret") != token


def test_compliance_blocks_europe_without_lawful_basis(settings, store):
    cid = add_contact(store, region="europe", lawful_basis="")
    reason = mailer.compliance_block(settings, store, store.get_contact(cid))
    assert "lawful basis" in reason
    cid2 = add_contact(store, email="x@eu.test", region="europe", lawful_basis="legitimate interest")
    assert mailer.compliance_block(settings, store, store.get_contact(cid2)) == ""


def test_sending_window_uses_regional_time():
    now_utc = datetime(2026, 11, 4, 12, 0)  # noon UTC
    from zoneinfo import ZoneInfo
    assert mailer.local_now("asia-pacific", now_utc.replace(tzinfo=ZoneInfo("UTC"))).hour == 20


def test_unsubscribe_via_controller_marks_contact_and_suppresses(settings, store):
    ctl = BotController.__new__(BotController)
    ctl.base = settings
    ctl._lock = None
    ctl._stores = {"live": store}
    cid = add_contact(store)
    token = mailer.unsubscribe_token(cid, settings.outreach_secret)
    ok, _ = BotController.unsubscribe(ctl, cid, token)
    assert ok and store.is_suppressed("jane@acme.com")
    assert store.get_contact(cid)["status"] == "unsubscribed"


def test_bulk_lawful_basis_unblocks_europe_only_where_missing(settings, store):
    from leadgen.controller import BotController
    ctl = BotController.__new__(BotController)
    ctl.base = settings
    ctl._lock = None
    ctl._stores = {"live": store}
    ctl._engines = {}
    ctl._engine_log = lambda *a, **k: None
    eu1 = add_contact(store, email="a@eu.test", region="europe", lawful_basis="")
    eu2 = add_contact(store, email="b@eu.test", region="europe", lawful_basis="consent")
    us = add_contact(store, email="c@us.test", region="americas", lawful_basis="")
    assert ctl.missing_basis("europe") == 1
    ok, _ = ctl.record_lawful_basis("nonsense")
    assert not ok
    ok, msg = ctl.record_lawful_basis("legitimate interest (B2B)", region="europe")
    assert ok and "1 contact" in msg
    assert store.get_contact(eu1)["lawful_basis"] == "legitimate interest (B2B)"
    assert store.get_contact(eu2)["lawful_basis"] == "consent"  # already set, untouched
    assert store.get_contact(us)["lawful_basis"] == ""          # other region untouched
    assert mailer.compliance_block(settings, store, store.get_contact(eu1)) == ""
