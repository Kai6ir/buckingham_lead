import dataclasses
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest

from leadgen.config import Settings


@pytest.fixture
def settings(tmp_path):
    """Settings isolated to a temp folder, sending switched on only where a test asks for it."""
    queries = tmp_path / "queries.txt"
    queries.write_text("managed futures fund\ntrend following CTA firm\n", encoding="utf-8")
    base = Settings.load()
    return dataclasses.replace(
        base,
        db_path=tmp_path / "db.sqlite",
        output_dir=tmp_path / "out",
        queries_file=queries,
        delay_range=(0, 0),
        enrich_workers=3,
        pages_per_query=2,
        target_leads=20,
        candidate_multiplier=1.5,
        use_llm=False,
        send_enabled=False,
        approval_required=True,
        postal_address="1 Test Street, London",
        public_base_url="https://example.test",
        outreach_secret="test-secret",
        brevo_key="test-brevo-key",
        sender="sales@example.test",
        sender_name="Sven",
        company_name="Buckingham",
        blocklist_domains=["linkedin.com", "x.com"],
    )
