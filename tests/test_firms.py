"""NFA firm list: master-list layout, scoring by attributes, tailored emails, and the two-part run."""

import dataclasses
from pathlib import Path

import pytest
from openpyxl import load_workbook

from leadgen import firm_email
from leadgen.demo import DemoFetcher, DemoSearch
from leadgen.firm_pipeline import FirmPipeline
from leadgen.firm_scoring import score_firm
from leadgen.firm_search import extract_fields, firm_queries
from leadgen.firms import ALL_COLUMNS, MASTER_COLUMNS, clean_id, firm_key, parse_master, write_master
from leadgen.pipeline import RunControl, Services
from leadgen.storage import Store

MASTER = Path("/Users/svenz/Downloads/NFA Master IB List.xlsx")


def make_master(tmp_path: Path) -> Path:
    """A small master file with the same 20 headers as the boss's list."""
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.append([h for _, h in MASTER_COLUMNS])
    ws.append([556980.0, "24X BERMUDA LIMITED", "HAMILTON", None, None, "BERMUDA", "APPROVED", None,
               "REGISTERED INDEPENDENT", None, None, None, None, None, None, None, None, None, None])
    ws.append([515272.0, "303 SECURITIES LP", "CHICAGO", "ILLINOIS", None, "UNITED STATES", "APPROVED", None,
               "REGISTERED INDEPENDENT", "REGISTERED", None, None, None, None, None, None, None, None, None])
    path = tmp_path / "master.xlsx"
    wb.save(path)
    return path


def test_clean_id_handles_excel_floats():
    assert clean_id(556980.0) == "556980"
    assert clean_id("476094") == "476094"
    assert clean_id("hello") == ""


def test_parse_master_reads_the_boss_layout(tmp_path):
    rows = parse_master(make_master(tmp_path))
    assert len(rows) == 2
    assert rows[0]["firm_id"] == "556980" and rows[0]["introducing_broker"] == "REGISTERED INDEPENDENT"
    assert rows[1]["cta"] == "REGISTERED" and rows[1]["us_state"] == "ILLINOIS"


def test_export_keeps_the_master_columns_in_order(tmp_path):
    rows = parse_master(make_master(tmp_path))
    out = write_master(rows, tmp_path / "out.xlsx")
    headers = [c.value for c in load_workbook(out).active[1]]
    assert headers[:len(MASTER_COLUMNS)] == [h for _, h in MASTER_COLUMNS]
    assert headers[len(MASTER_COLUMNS):] == [h for _, h in ALL_COLUMNS[len(MASTER_COLUMNS):]]
    assert len(headers) == len(ALL_COLUMNS)


def test_scoring_uses_attributes_and_explains_itself():
    cta_cpo = {"cta": "REGISTERED", "cpo": "REGISTERED", "nfa_member": "APPROVED", "country": "UNITED STATES"}
    ib_only = {"introducing_broker": "REGISTERED INDEPENDENT", "nfa_member": "APPROVED", "country": "UNITED STATES"}
    a = score_firm(cta_cpo, target_countries=["Japan"])
    b = score_firm(ib_only, target_countries=["Japan"])
    assert a.score > b.score and a.tier == "B"  # 55: CTA + CPO + approved is B; A needs 60
    assert any("CTA registered" in r for r in a.reasons)
    assert score_firm({"cta": "PENDING", "nfa_member": "PENDING"}).tier == "C"


def test_target_market_and_contact_add_points():
    base = {"cta": "REGISTERED", "nfa_member": "APPROVED", "country": "JAPAN"}
    with_market = score_firm(base, target_countries=["Japan"]).score
    without = score_firm(base, target_countries=["Germany"]).score
    assert with_market == without + 10
    with_email = score_firm({**base, "contact_email": "a@b.jp"}, target_countries=["Japan"]).score
    assert with_email == with_market + 10


def test_pattern_extraction_fills_only_what_the_page_says():
    fields = extract_fields(
        "Acme Capital is an NFA Member and a registered Commodity Trading Advisor. NFA ID: 0456789. "
        "Offices in Chicago, Illinois, United States.", "Acme Capital | Managed futures", "https://acme.com", None)
    assert fields["firm_id"] == "0456789" and fields["cta"] == "REGISTERED"
    assert fields["city"] == "CHICAGO" and fields["us_state"] == "ILLINOIS"
    assert "cpo" not in fields and "swap_dealer" not in fields  # not stated, so not invented


def test_queries_come_from_the_list_and_target_markets(tmp_path):
    rows = parse_master(make_master(tmp_path))
    queries = firm_queries(rows, 10, ["Japan"])
    assert queries and any("Japan" in q for _, q in queries)
    assert any(ch == "nfa" for ch, _ in queries)


def test_partner_email_frames_a_partnership_without_performance_claims(settings):
    firm = {"firm_name": "303 SECURITIES LP", "ib_type": "capital_raiser", "raises_for_cta": "yes",
            "client_segment": "HNW / Institutional", "city": "CHICAGO", "us_state": "ILLINOIS",
            "country": "UNITED STATES", "nfa_member": "APPROVED"}
    s = dataclasses.replace(settings, sender_name="Sven", company_name="Buckingham", postal_address="1 Test St")
    d = firm_email.draft(firm, s, {"first_name": "Ana"}, None, "https://example.test/unsubscribe/1/x")
    assert "Ana" in d["body"]
    assert "managed-futures" in d["body"].lower() or "managed futures" in d["body"].lower()
    assert "Unsubscribe:" in d["body"] and "1 Test St" in d["body"]
    # Compliance: no performance figures, win rates or returns may appear
    import re as _re
    assert not _re.search(r"\d+\s?%|win rate|return|outperform", d["body"], _re.I)
    assert "Japan" not in d["body"]  # no invented facts


def test_firm_run_adds_new_firms_scores_them_and_queues_emails(settings, tmp_path):
    s = dataclasses.replace(settings, firm_target=12, firm_outreach_tier="B", pages_per_query=1,
                            firm_enrich_existing=0, planned_queries=6, approval_required=True)
    store = Store(s.db_path)
    store.import_path = None
    for r in parse_master(make_master(tmp_path)):  # imported like controller.import_master does
        store.upsert_firm({**r, "firm_key": firm_key(r["firm_id"], r["firm_name"], r["city"]),
                           "date_added": "imported"})
    ctl = RunControl(store, "demo", 12)
    services = Services(search=DemoSearch(), fetcher=DemoFetcher(), llm=None, mode="demo")
    FirmPipeline(s, services, ctl, target_firms=12, engine=None).run()
    snap = ctl.snapshot()
    assert snap["status"] == "completed", snap
    added = [f for f in store.firms(limit=500) if f["date_added"] != "imported"]
    assert added and all(f["lead_score"] is not None and f["tier"] in ("A", "B", "C") for f in added)
    assert all(f["score_reasons"] for f in added)
    # Every firm is classified as an IB type
    assert all(f["ib_type"] in ("capital_raiser", "hedger", "execution_only", "uncertain") for f in added)
    # Outreach only goes to High-priority capital-raisers with a work email — never hedgers
    drafted = [f for f in added if f["outreach_status"] == "draft"]
    for f in drafted:
        assert f["contact_email"] and f["recommendation"] == "High-priority review"
        assert f["ib_type"] == "capital_raiser" and f["hedging_only"] != "yes"
    assert len(store.messages(status="draft")) == len(drafted)
    assert all("Unsubscribe:" in m["body"] for m in store.messages(status="draft"))
    assert (s.output_dir / "demo" / "NFA_Master_IB_List_latest.xlsx").exists()


# --------------------------------------------------------------------------- IB screening

from leadgen import ib_classify
from leadgen.firm_scoring import score_firm

RAISER_TEXT = ("Northgate Partners is an introducing broker that raises capital for leading CTAs and "
               "managed futures programs. We introduce accredited and high-net-worth investors and family "
               "offices to third-party commodity trading advisors. Capital introduction for qualified investors.")
HEDGER_TEXT = ("Midwest Grain Brokerage provides agricultural hedging and price risk management for "
               "producers, farmers and grain elevators. We help commercial clients manage basis and input "
               "costs with futures and options. Cash grain and livestock execution.")
EXEC_TEXT = ("FastRoute Futures offers execution-only order routing and direct market access for active "
             "futures traders. Low-latency trade execution, give-up and clearing, competitive commission rates.")
# Mentions futures a lot but it is pure hedging — terminology must not fool the classifier.
TERMINOLOGY_TRAP = ("AgFutures Inc. futures futures futures. We trade futures and options every day for our "
                    "farmer clients to hedge crop and livestock price risk. Agricultural risk management and "
                    "grain marketing. No investment advice.")


def test_classifier_separates_raisers_hedgers_execution():
    assert ib_classify.classify_by_patterns(RAISER_TEXT, "Northgate Partners").ib_type == "capital_raiser"
    assert ib_classify.classify_by_patterns(HEDGER_TEXT, "Midwest Grain Brokerage").ib_type == "hedger"
    assert ib_classify.classify_by_patterns(EXEC_TEXT, "FastRoute Futures").ib_type == "execution_only"


def test_classifier_uses_evidence_not_terminology():
    c = ib_classify.classify_by_patterns(TERMINOLOGY_TRAP, "AgFutures Inc")
    assert c.ib_type == "hedger" and c.hedging_only == "yes"
    assert c.recommendation == ib_classify.RECO_EXCLUDE


def test_classifier_records_evidence_and_recommendation():
    c = ib_classify.classify_by_patterns(RAISER_TEXT, "Northgate Partners", "https://ng.example")
    assert c.raises_for_cta == "yes" and c.recommendation == ib_classify.RECO_HIGH
    assert c.evidence_text and c.evidence_url == "https://ng.example"
    assert "HNW" in c.client_segment or "Institutional" in c.client_segment


def test_scoring_rewards_raisers_and_pushes_hedgers_to_c():
    raiser = {"ib_type": "capital_raiser", "raises_for_cta": "yes", "mf_relevance": "high",
              "client_segment": "HNW / Institutional", "nfa_member": "APPROVED",
              "introducing_broker": "REGISTERED INDEPENDENT"}
    hedger = {"ib_type": "hedger", "raises_for_cta": "no", "hedging_only": "yes",
              "nfa_member": "APPROVED", "introducing_broker": "REGISTERED GUARANTEED",
              "cta": "REGISTERED"}  # even with a CTA reg, hedging-only must not rank A/B
    a = score_firm(raiser)
    b = score_firm(hedger)
    assert a.tier == "A" and a.score > b.score
    assert b.tier == "C"
    assert any("Hedging-only" in r for r in b.reasons)


def test_classify_falls_back_without_llm_and_handles_empty():
    assert ib_classify.classify("", "X", "u", None).ib_type == "uncertain"
    assert ib_classify.classify(RAISER_TEXT, "X", "u", None).ib_type == "capital_raiser"


def test_manual_keep_exclude_override(settings, tmp_path):
    store = Store(settings.db_path)
    for r in parse_master(make_master(tmp_path)):
        store.upsert_firm({**r, "firm_key": firm_key(r["firm_id"], r["firm_name"], r["city"])})
    key = store.firms(limit=1)[0]["firm_key"]
    assert store.set_firm_decision(key, "keep")
    assert store.firm_by_key(key)["decision"] == "keep"
    assert store.firms(decision="keep")
    assert not store.set_firm_decision(key, "banana")  # invalid decision rejected


def test_lead_quality_separates_qualified_uncertain_irrelevant():
    q = ib_classify.classify_by_patterns(RAISER_TEXT, "Northgate Partners")
    # Talks managed futures but shows no capital-raising evidence → uncertain, for human review
    u = ib_classify.classify_by_patterns("A brokerage active in managed futures markets.", "Vague Corp")
    h = ib_classify.classify_by_patterns(HEDGER_TEXT, "Midwest Grain Brokerage")
    assert q.lead_quality == ib_classify.QUALITY_QUALIFIED
    assert u.lead_quality == ib_classify.QUALITY_UNCERTAIN
    assert h.lead_quality == ib_classify.QUALITY_IRRELEVANT


def test_lead_quality_is_stored_filtered_and_counted(settings):
    store = Store(settings.db_path)
    for name, text in (("NORTHGATE", RAISER_TEXT), ("MIDWEST GRAIN", HEDGER_TEXT)):
        c = ib_classify.classify_by_patterns(text, name, "https://ex.test")
        store.upsert_firm({"firm_key": f"name:{name.lower()}", "firm_name": name, **c.as_fields()})
    assert [f["firm_name"] for f in store.firms(lead_quality="qualified")] == ["NORTHGATE"]
    assert [f["firm_name"] for f in store.firms(lead_quality="irrelevant")] == ["MIDWEST GRAIN"]
    counts = store.firm_counts()
    assert counts["qualified"] == 1 and counts["irrelevant"] == 1


def test_export_separates_quality_sheets(tmp_path):
    rows = [
        {"firm_name": "NORTHGATE", "lead_quality": "qualified"},
        {"firm_name": "VAGUE CORP", "lead_quality": "uncertain"},
        {"firm_name": "NOT SCREENED YET"},
        {"firm_name": "MIDWEST GRAIN", "lead_quality": "irrelevant"},
        {"firm_name": "RESCUED BY HAND", "lead_quality": "irrelevant", "decision": "keep"},
    ]
    wb = load_workbook(write_master(rows, tmp_path / "sep.xlsx"))
    names = {ws.title: [r[1].value for r in ws.iter_rows(min_row=2)] for ws in wb.worksheets}
    assert sorted(names["Qualified IBs"]) == ["NORTHGATE", "RESCUED BY HAND"]  # manual keep overrides
    assert sorted(names["Needs review"]) == ["NOT SCREENED YET", "VAGUE CORP"]
    assert names["Excluded (hedging-exec)"] == ["MIDWEST GRAIN"]
    assert len(names[wb.worksheets[0].title]) == 5  # first sheet still holds everyone


def test_rescore_backfills_lead_quality(settings):
    from leadgen.firm_scoring import rescore_all
    store = Store(settings.db_path)
    store.upsert_firm({"firm_key": "name:old", "firm_name": "OLD ROW", "ib_type": "capital_raiser",
                       "raises_for_cta": "yes", "hedging_only": "no", "mf_relevance": "high",
                       "recommendation": "High-priority review"})  # screened before the column existed
    rescore_all(store, settings)
    assert store.firm_by_key("name:old")["lead_quality"] == "qualified"


def test_migration_adds_ib_columns_to_old_db(tmp_path):
    import sqlite3
    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.executescript("CREATE TABLE firms (firm_key TEXT PRIMARY KEY, firm_name TEXT);")
    con.commit(); con.close()
    store = Store(db)  # opening it should ALTER TABLE to add the new columns
    store.upsert_firm({"firm_key": "k1", "firm_name": "Test", "ib_type": "capital_raiser",
                       "raises_for_cta": "yes"})
    assert store.firm_by_key("k1")["ib_type"] == "capital_raiser"
