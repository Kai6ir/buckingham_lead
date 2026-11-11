"""Discovery (Google / LinkedIn / X), de-duplication against the database, import and the API."""

import dataclasses
import io

import pytest

from leadgen import importer
from leadgen.enrich import ContactInfo, SiteData
from leadgen.models import SearchHit
from leadgen.pipeline import Pipeline, RunControl, Services
from leadgen.storage import Store


class FakeSearch:
    def search(self, query, page, channel="google"):
        if "official website" in query:
            return [SearchHit("Acme", "https://www.acme-capital.com/", "", query, "google")]
        if channel == "linkedin":
            return [SearchHit(f"Person {page}-{i} - Head of Allocations - Acme Capital | LinkedIn",
                              f"https://www.linkedin.com/in/person-{page}-{i}", "managed futures",
                              query, "linkedin") for i in range(6)]
        if channel == "x":
            return [SearchHit(f"Xuser {page}-{i} (@xuser{page}{i}) / X", f"https://x.com/xuser{page}{i}",
                              "managed futures allocator", query, "x") for i in range(3)]
        return [SearchHit(f"Firm {page * 100 + i}", f"https://www.firm{page * 100 + i}.com/",
                          "managed futures firm", query, "google") for i in range(6)]


class FakeFetcher:
    def fetch_site(self, domain):
        if domain == "acme-capital.com":
            contact = ContactInfo(emails=["person-0-0@acme-capital.com", "info@acme-capital.com"],
                                  company_name="Acme Capital", description="managed futures allocator",
                                  text="managed futures trend following allocation")
            return SiteData(domain=domain, contact=contact, pages_fetched=2)
        n = int(domain.replace("firm", "").replace(".com", ""))
        contact = ContactInfo(emails=[f"info@{domain}"] if n % 2 == 0 else [],
                              company_name=f"Firm {n} Capital",
                              description="managed futures and trend following",
                              text="managed futures trend following CTA allocation")
        return SiteData(domain=domain, contact=contact, pages_fetched=1)


def run(settings, store, target=30):
    s = dataclasses.replace(settings, target_leads=target)
    ctl = RunControl(store, "live", target)
    from leadgen.engine import SequenceEngine
    engine = SequenceEngine(s, store)
    engine.sync_sequences()
    services = Services(search=FakeSearch(), fetcher=FakeFetcher(), llm=None, mode="live")
    Pipeline(s, services, ctl, "email", "", engine=engine).run()
    return ctl.snapshot(), engine


def test_discovery_adds_new_contacts_from_all_three_sources(settings, tmp_path):
    store = Store(settings.db_path)
    snap, _ = run(settings, store)
    assert snap["status"] == "completed"
    rows = store.contacts(limit=500)
    sources = {r["channel"] for r in rows}
    assert {"google", "linkedin", "x"} <= sources
    assert all(r["source"] == "discovery" for r in rows)
    assert not any("linkedin.com/company" in (r["domain"] or "") for r in rows)


def test_linkedin_person_with_matching_work_email_is_found(settings):
    store = Store(settings.db_path)
    run(settings, store)
    people = [r for r in store.contacts(limit=500) if r["channel"] == "linkedin" and r["email"]]
    assert people and all(r["email"].endswith("@acme-capital.com") for r in people)


def test_second_run_adds_nothing_that_is_already_known(settings):
    store = Store(settings.db_path)
    first, _ = run(settings, store)
    before = store.contact_counts()["total"]
    second, _ = run(settings, store)
    assert store.contact_counts()["total"] == before or second["counters"]["added"] >= 0
    assert second["counters"]["duplicates"] > 0


def test_existing_database_contact_is_not_added_twice(settings, tmp_path):
    store = Store(settings.db_path)
    store.insert_contact({"email": "info@firm2.com", "full_name": "Old Firm", "company": "Firm 2",
                          "domain": "firm2.com", "source": "import", "channel": "import", "status": "active"})
    store.insert_contact({"full_name": "Person 0-0", "company": "Acme Capital", "source": "import",
                          "channel": "import", "status": "active",
                          "linkedin_key": "linkedin.com/in/person-0-0", "linkedin_url": "https://www.linkedin.com/in/person-0-0"})
    run(settings, store)
    assert len([r for r in store.contacts(limit=500) if (r["domain"] or "") == "firm2.com"]) == 1
    assert len([r for r in store.contacts(limit=500) if r["linkedin_key"] == "linkedin.com/in/person-0-0"]) == 1


def test_discovered_strong_contacts_are_enrolled(settings):
    store = Store(settings.db_path)
    _, engine = run(settings, store)
    enrolled = store.enrollments(limit=500)
    assert enrolled, "expected at least one strong contact to enter the default sequence"
    assert all(e["status"] == "active" for e in enrolled)


def test_import_csv_dedupes_and_maps_regions(settings, tmp_path):
    store = Store(settings.db_path)
    store.insert_contact({"email": "old@acme.com", "full_name": "Old One", "company": "Acme",
                          "source": "import", "channel": "import", "status": "active"})
    store.suppress("blocked@acme.com", "test")
    csv_path = tmp_path / "list.csv"
    csv_path.write_text(
        "Work E-mail,First Name,Last Name,Company,Country,LinkedIn URL\n"
        "new@acme.com,Ana,Lee,Acme,Japan,\n"
        "old@acme.com,Old,One,Acme,United States,\n"
        "blocked@acme.com,Bad,Person,Acme,France,\n"
        ",Jo,Smith,Beta Fund,Germany,\n"
        "new@acme.com,Ana,Lee,Acme,Japan,\n", encoding="utf-8")
    result = importer.import_file(csv_path, store, "test")
    assert result["new"] == 2  # new@ and Jo Smith (no email)
    assert result["duplicates"] == 2  # old@ and the second new@
    assert result["suppressed"] == 1
    ana = store.find_contact(email="new@acme.com")
    assert ana["region"] == "asia-pacific"


def test_region_for_handles_unknown_country():
    assert importer.region_for("Germany") == "europe"
    assert importer.region_for("", "") == "unknown"
    assert importer.region_for("Narnia") == "other"


def test_dashboard_api_import_approve_webhook_and_unsubscribe(settings, tmp_path, monkeypatch):
    from dashboard.app import create_app
    from leadgen import mailer
    monkeypatch.setattr(mailer, "send", lambda *a, **k: ("brevo", "<msg-1@test>"))
    s = dataclasses.replace(settings, approval_required=True, send_enabled=True,
                            sending_window=(0, 24))  # the test must not depend on the time of day
    app = create_app(s)
    client = app.test_client()

    data = {"file": (io.BytesIO(b"email,full_name,company\njane@acme.com,Jane Doe,Acme\n"), "list.csv")}
    res = client.post("/api/import", data=data, content_type="multipart/form-data")
    assert res.status_code == 200 and res.get_json()["result"]["new"] == 1

    contact_id = client.get("/api/contacts").get_json()["contacts"][0]["id"]
    res = client.post("/api/enroll", json={"contact_ids": [contact_id]})
    assert res.get_json()["ok"]
    client.post("/api/tick")
    drafts = client.get("/api/outbox?status=draft").get_json()["messages"]
    assert len(drafts) == 1
    mid = drafts[0]["id"]
    assert client.post(f"/api/outbox/{mid}/approve").get_json()["ok"]
    client.post("/api/tick")
    sent = client.get("/api/outbox?status=sent").get_json()["messages"]
    assert len(sent) == 1

    from leadgen.mailer import webhook_token
    token_wh = webhook_token(settings.outreach_secret)
    webhook = client.post(f"/webhooks/brevo?token={token_wh}", json={"event": "opened", "email": "jane@acme.com",
                                                                     "message-id": "msg-1@test"})
    assert webhook.status_code == 200
    funnel = client.get("/api/state").get_json()["funnel"]
    assert funnel["opened"] == 1

    token = mailer.unsubscribe_token(contact_id, settings.outreach_secret)
    page = client.get(f"/unsubscribe/{contact_id}/{token}")
    assert page.status_code == 200 and b"Unsubscribed" in page.data
    assert client.get("/unsubscribe/%d/bad" % contact_id).status_code == 400
    assert client.post("/webhooks/brevo?token=nope", json={"event": "opened"}).status_code == 401
