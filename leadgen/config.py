"""Load settings from config.yaml and secrets from .env."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Settings:
    # profile — who the bot is working for
    company_name: str = ""
    sender_name: str = ""
    sender_role: str = ""
    offering: str = ""
    ideal_customer: str = ""
    # discovery run
    target_leads: int = 100
    candidate_multiplier: float = 1.5
    queries_file: Path = ROOT / "queries.txt"
    pages_per_query: int = 3
    top_n: int = 20
    planned_queries: int = 20
    # discovery sources
    use_google: bool = True
    use_linkedin: bool = True
    use_x: bool = True
    gl: str = "us"
    hl: str = "en"
    # enrich
    enrich_workers: int = 6
    request_timeout: float = 8.0
    delay_range: tuple[float, float] = (0.5, 1.5)
    respect_robots: bool = True
    contact_paths: list[str] = field(default_factory=lambda: ["/contact", "/contact-us", "/about", "/about-us", "/team"])
    max_contact_pages: int = 3
    # llm (any provider)
    llm_provider: str = "anthropic"
    llm_model: str = "claude-opus-5-5"
    llm_base_url: str = ""
    use_llm: bool = True
    # storage
    db_path: Path = ROOT / "data" / "leadgen.db"
    output_dir: Path = ROOT / "outputs"
    # sheets
    sheets_enabled: bool = False
    sheets_spreadsheet_id: str = ""
    sheets_worksheet: str = "Leads"
    sheets_credentials: Path = ROOT / "service_account.json"
    # weekly top-N delivery
    delivery_channel: str = "email"
    delivery_recipient: str = ""
    delivery_enabled: bool = False
    sender: str = ""
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    # outreach (sequences)
    send_enabled: bool = False
    approval_required: bool = True
    daily_cap: int = 50
    sending_window: tuple[int, int] = (8, 18)
    provider: str = "brevo"                       # brevo | smtp
    postal_address: str = ""
    public_base_url: str = "http://127.0.0.1:5057"
    require_lawful_basis_regions: list[str] = field(default_factory=lambda: ["europe"])
    auto_enroll_min_score: int = 60
    default_sequence: str = "institutional_reengage"
    sequences: list[dict] = field(default_factory=list)
    templates: dict = field(default_factory=dict)
    # NFA firm list
    firm_target: int = 50                          # new firms to add per run
    firm_target_countries: list[str] = field(default_factory=lambda: [
        "United Kingdom", "Germany", "France", "Switzerland", "Netherlands", "Japan", "Hong Kong", "Singapore"])
    firm_weights: dict = field(default_factory=dict)
    firm_tiers: dict = field(default_factory=lambda: {"A": 60, "B": 40})
    firm_outreach_tier: str = "A"
    firm_enrich_existing: int = 40                 # master-list firms without a website to look up per run
    # filtering
    blocklist_domains: list[str] = field(default_factory=list)
    # secrets (from environment)
    serpapi_key: str = ""
    llm_key: str = ""
    smtp_user: str = ""
    smtp_password: str = ""
    brevo_key: str = ""
    imap_host: str = ""
    imap_user: str = ""
    imap_password: str = ""
    outreach_secret: str = ""
    twilio_sid: str = ""
    twilio_token: str = ""
    twilio_from: str = ""

    @classmethod
    def load(cls, config_path: Path | None = None) -> "Settings":
        load_dotenv(ROOT / ".env")
        path = Path(config_path) if config_path else ROOT / "config.yaml"
        raw = (yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}) or {}

        profile = raw.get("profile", {})
        run = raw.get("run", {})
        sources = raw.get("sources", {})
        search = raw.get("search", {})
        enrich = raw.get("enrich", {})
        llm = raw.get("llm", {})
        storage = raw.get("storage", {})
        sheets = raw.get("sheets", {})
        delivery = raw.get("delivery", {})
        smtp = raw.get("smtp", {})
        out = raw.get("outreach", {})
        firms_cfg = raw.get("firms", {})

        def p(value: str, default: Path) -> Path:
            return ROOT / value if value else default

        provider = os.getenv("LLM_PROVIDER") or llm.get("provider", "anthropic")
        llm_key = os.getenv("LLM_API_KEY") or (os.getenv("ANTHROPIC_API_KEY", "") if provider == "anthropic" else "")
        window = out.get("sending_window", [8, 18])

        return cls(
            company_name=str(profile.get("company_name", "")).strip(),
            sender_name=str(profile.get("sender_name", "")).strip(),
            sender_role=str(profile.get("sender_role", "")).strip(),
            offering=str(profile.get("offering", "")).strip(),
            ideal_customer=str(profile.get("ideal_customer", "")).strip(),
            target_leads=int(run.get("target_leads", 100)),
            candidate_multiplier=float(run.get("candidate_multiplier", 1.5)),
            queries_file=p(run.get("queries_file", "queries.txt"), ROOT / "queries.txt"),
            pages_per_query=int(run.get("pages_per_query", 3)),
            top_n=int(run.get("top_n", 20)),
            planned_queries=int(run.get("planned_queries", 20)),
            use_google=bool(sources.get("google", True)),
            use_linkedin=bool(sources.get("linkedin", True)),
            use_x=bool(sources.get("x", True)),
            gl=search.get("gl", "us"),
            hl=search.get("hl", "en"),
            enrich_workers=int(enrich.get("workers", 6)),
            request_timeout=float(enrich.get("request_timeout", 8)),
            delay_range=tuple(enrich.get("delay_range", [0.5, 1.5])),
            respect_robots=bool(enrich.get("respect_robots", True)),
            contact_paths=list(enrich.get("contact_paths", ["/contact", "/about"])),
            max_contact_pages=int(enrich.get("max_contact_pages", 3)),
            llm_provider=provider,
            llm_model=os.getenv("LLM_MODEL") or llm.get("model", "claude-opus-5-5"),
            llm_base_url=os.getenv("LLM_BASE_URL") or llm.get("base_url", "") or "",
            use_llm=bool(llm.get("enabled", True)),
            db_path=p(storage.get("db_path", "data/leadgen.db"), ROOT / "data" / "leadgen.db"),
            output_dir=p(storage.get("output_dir", "outputs"), ROOT / "outputs"),
            sheets_enabled=bool(sheets.get("enabled", False)),
            sheets_spreadsheet_id=sheets.get("spreadsheet_id", "") or "",
            sheets_worksheet=sheets.get("worksheet", "Leads"),
            sheets_credentials=p(sheets.get("credentials_file", "service_account.json"), ROOT / "service_account.json"),
            delivery_channel=delivery.get("channel", "email"),
            delivery_recipient=delivery.get("recipient", "") or "",
            delivery_enabled=bool(delivery.get("enabled", False)),
            sender=smtp.get("sender", "") or "",
            smtp_host=smtp.get("host", "smtp.gmail.com"),
            smtp_port=int(smtp.get("port", 587)),
            send_enabled=bool(out.get("send_enabled", False)),
            approval_required=bool(out.get("approval_required", True)),
            daily_cap=int(out.get("daily_cap", 50)),
            sending_window=(int(window[0]), int(window[1])),
            provider=out.get("provider", "brevo"),
            postal_address=str(out.get("postal_address", "")).strip(),
            public_base_url=str(out.get("public_base_url", "http://127.0.0.1:5057")).rstrip("/"),
            require_lawful_basis_regions=[r.lower() for r in out.get("require_lawful_basis_regions", ["europe"])],
            auto_enroll_min_score=int(out.get("auto_enroll_min_score", 60)),
            default_sequence=out.get("default_sequence", "institutional_reengage"),
            sequences=list(out.get("sequences", [])),
            templates=dict(out.get("templates", {})),
            firm_target=int(firms_cfg.get("target", 50)),
            firm_target_countries=list(firms_cfg.get("target_countries", [])) or
            ["United Kingdom", "Germany", "France", "Switzerland", "Netherlands", "Japan", "Hong Kong", "Singapore"],
            firm_weights=dict(firms_cfg.get("weights", {})),
            firm_tiers=dict(firms_cfg.get("tiers", {"A": 60, "B": 40})),
            firm_outreach_tier=str(firms_cfg.get("outreach_tier", "A")).upper(),
            firm_enrich_existing=int(firms_cfg.get("enrich_existing", 40)),
            blocklist_domains=[d.lower() for d in raw.get("blocklist_domains", [])],
            serpapi_key=os.getenv("SERPAPI_KEY", ""),
            llm_key=llm_key,
            smtp_user=os.getenv("SMTP_USER", ""),
            smtp_password=os.getenv("SMTP_PASSWORD", ""),
            brevo_key=os.getenv("BREVO_API_KEY", ""),
            imap_host=os.getenv("IMAP_HOST", ""),
            imap_user=os.getenv("IMAP_USER", ""),
            imap_password=os.getenv("IMAP_PASSWORD", ""),
            outreach_secret=os.getenv("OUTREACH_SECRET", ""),
            twilio_sid=os.getenv("TWILIO_ACCOUNT_SID", ""),
            twilio_token=os.getenv("TWILIO_AUTH_TOKEN", ""),
            twilio_from=os.getenv("TWILIO_FROM_NUMBER", ""),
        )

    @property
    def candidate_pool_size(self) -> int:
        return int(self.target_leads * self.candidate_multiplier)
