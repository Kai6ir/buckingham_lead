"""Local web dashboard: database, outbox approval, sequences & funnel, discovery, and the public
endpoints for Brevo webhooks and unsubscribe links.

Run with:  python run_dashboard.py   then open http://127.0.0.1:5057
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request, send_from_directory

from leadgen.config import Settings
from leadgen.controller import BotController
from leadgen.export import write_queue_csv

HERE = Path(__file__).resolve().parent


def create_app(settings: Settings | None = None) -> Flask:
    settings = settings or Settings.load()
    controller = BotController(settings)
    controller.ensure_outreach_secret()
    controller.resume_autopilot_if_set()

    app = Flask(__name__, template_folder=str(HERE / "templates"), static_folder=str(HERE / "static"))
    app.config["CONTROLLER"] = controller

    def ok(message: str, status: int = 200):
        return jsonify({"ok": True, "message": message}), status

    def fail(message: str, status: int = 400):
        return jsonify({"ok": False, "message": message}), status

    # ----- pages ----------------------------------------------------------------
    @app.get("/")
    def index():
        return render_template("index.html", top_n=settings.top_n, company=settings.company_name,
                               target=settings.target_leads)

    @app.get("/unsubscribe/<int:contact_id>/<token>")
    def unsubscribe(contact_id: int, token: str):
        done, msg = controller.unsubscribe(contact_id, token)
        status = 200 if done else 400
        body = f"<!doctype html><meta charset='utf-8'><title>Unsubscribe</title>" \
               f"<body style='font-family:system-ui;max-width:520px;margin:60px auto;line-height:1.5'>" \
               f"<h2>{'Unsubscribed' if done else 'Link not valid'}</h2><p>{msg}</p></body>"
        return Response(body, status=status, mimetype="text/html")

    # ----- discovery & status ------------------------------------------------------
    @app.get("/api/state")
    def state():
        return jsonify(controller.snapshot())

    @app.post("/api/start")
    def start():
        body = request.get_json(silent=True) or {}
        mode = "demo" if body.get("mode") == "demo" else "live"
        kind = "firms" if body.get("kind") == "firms" else "contacts"
        done, msg = controller.start(mode, kind)
        return (ok(msg) if done else fail(msg, 409))

    @app.post("/api/pause")
    def pause():
        done, msg = controller.pause()
        return ok(msg) if done else fail(msg, 409)

    @app.post("/api/resume")
    def resume():
        done, msg = controller.resume()
        return ok(msg) if done else fail(msg, 409)

    @app.post("/api/stop")
    def stop():
        done, msg = controller.stop()
        return ok(msg) if done else fail(msg, 409)

    @app.get("/api/logs")
    def logs():
        after = request.args.get("after", default=0, type=int)
        return jsonify({"entries": controller.logs_since(after)})

    @app.post("/api/delivery")
    def delivery():
        body = request.get_json(silent=True) or {}
        done, msg = controller.set_delivery(body.get("channel", ""), body.get("recipient", ""))
        return ok(msg) if done else fail(msg)

    # ----- contact database -----------------------------------------------------------
    @app.get("/api/contacts")
    def contacts():
        rows = controller.contacts(
            region=request.args.get("region", ""), source=request.args.get("source", ""),
            status=request.args.get("status", ""), q=request.args.get("q", ""),
            limit=min(request.args.get("limit", default=200, type=int), 1000),
            offset=request.args.get("offset", default=0, type=int))
        return jsonify({"contacts": rows})

    @app.post("/api/import")
    def import_contacts():
        upload = request.files.get("file")
        if upload is None or not upload.filename:
            return fail("Choose a CSV or Excel file first.")
        suffix = Path(upload.filename).suffix.lower()
        if suffix not in (".csv", ".xlsx", ".xlsm", ".txt"):
            return fail("Please upload a .csv or .xlsx file.")
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            upload.save(tmp)
            path = Path(tmp.name)
        try:
            done, msg, result = controller.import_contacts(path, upload.filename)
        finally:
            path.unlink(missing_ok=True)
        return jsonify({"ok": done, "message": msg, "result": result}), (200 if done else 400)

    @app.post("/api/enroll")
    def enroll():
        body = request.get_json(silent=True) or {}
        if body.get("contact_ids"):
            done, msg = controller.enroll(body["contact_ids"], body.get("sequence", ""))
        else:
            done, msg = controller.enroll_by_filter(body.get("region", ""), body.get("source", ""),
                                                    body.get("sequence", ""))
        return ok(msg) if done else fail(msg)

    # ----- NFA firm list ------------------------------------------------------------
    @app.post("/api/firms/import")
    def import_firms():
        upload = request.files.get("file")
        if upload is None or not upload.filename.lower().endswith((".xlsx", ".xlsm")):
            return fail("Upload the NFA master list as an .xlsx file.")
        with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp:
            upload.save(tmp)
            path = Path(tmp.name)
        try:
            done, msg, result = controller.import_master(path)
        finally:
            path.unlink(missing_ok=True)
        return jsonify({"ok": done, "message": msg, "result": result}), (200 if done else 400)

    def _firm_mode() -> str:
        """Demo firm runs live in their own database; the dashboard shows whichever one is selected."""
        return "demo" if request.args.get("mode") == "demo" else "live"

    @app.get("/api/firms")
    def firms():
        mode = _firm_mode()
        rows = controller.firms(
            tier=request.args.get("tier", ""), outreach=request.args.get("outreach", ""),
            ib_type=request.args.get("ib_type", ""), recommendation=request.args.get("recommendation", ""),
            decision=request.args.get("decision", ""), lead_quality=request.args.get("lead_quality", ""),
            mode=mode)
        return jsonify({"firms": rows[:1000], "counts": controller.store_for(mode).firm_counts()})

    @app.post("/api/firms/decision")
    def firm_decision():
        body = request.get_json(silent=True) or {}
        mode = "demo" if body.get("mode") == "demo" else "live"
        done, msg = controller.set_firm_decision(body.get("firm_key", ""), body.get("decision", ""),
                                                 mode=mode)
        return ok(msg) if done else fail(msg)

    @app.get("/files/NFA_Master_IB_List_latest.xlsx")
    def master_file():
        path = controller.export_master(_firm_mode())
        return send_from_directory(path.parent, path.name, as_attachment=True)

    @app.get("/api/lawful-basis/missing")
    def missing_basis():
        region = request.args.get("region", "")
        return jsonify({"missing": controller.missing_basis(region), "region": region})

    @app.post("/api/lawful-basis")
    def lawful_basis():
        body = request.get_json(silent=True) or {}
        done, msg = controller.record_lawful_basis(
            body.get("basis", ""), body.get("contact_ids") or None,
            region=body.get("region", ""), only_missing=body.get("only_missing", True))
        return ok(msg) if done else fail(msg)

    @app.get("/api/enrollments")
    def enrollments():
        return jsonify({"enrollments": controller.store_for("live").enrollments(
            status=request.args.get("status", ""), limit=300)})

    # ----- outbox ------------------------------------------------------------------------
    def _body_mode(body: dict) -> str:
        return "demo" if body.get("mode") == "demo" else "live"

    @app.get("/api/outbox")
    def outbox():
        return jsonify({"messages": controller.outbox(status=request.args.get("status", ""),
                                                      mode=_firm_mode())})

    @app.post("/api/outbox/<int:message_id>/approve")
    def approve(message_id: int):
        body = request.get_json(silent=True) or {}
        done, msg = controller.approve(message_id, mode=_body_mode(body))
        return ok(msg) if done else fail(msg)

    @app.post("/api/outbox/<int:message_id>/reject")
    def reject(message_id: int):
        body = request.get_json(silent=True) or {}
        done, msg = controller.reject(message_id, mode=_body_mode(body))
        return ok(msg) if done else fail(msg)

    @app.post("/api/outbox/<int:message_id>/edit")
    def edit(message_id: int):
        body = request.get_json(silent=True) or {}
        done, msg = controller.edit(message_id, body.get("subject", ""), body.get("body", ""),
                                    mode=_body_mode(body))
        return ok(msg) if done else fail(msg)

    @app.post("/api/outbox/approve-all")
    def approve_all():
        body = request.get_json(silent=True) or {}
        done, msg = controller.approve_all_drafts(mode=_body_mode(body))
        return ok(msg)

    @app.post("/api/tick")
    def tick():
        result = controller.tick()
        note = result.get("note", "")
        return ok(f"Drafted {result.get('drafted', 0)}, sent {result.get('sent', 0)}, "
                  f"failed {result.get('failed', 0)}. {note}".strip())

    @app.post("/api/replies/check")
    def replies():
        done, msg = controller.poll_replies()
        return ok(msg) if done else fail(msg)

    @app.post("/api/autopilot")
    def autopilot():
        on = bool((request.get_json(silent=True) or {}).get("on"))
        done, msg = controller.set_autopilot(on)
        return ok(msg)

    @app.get("/api/webhook-url")
    def webhook_url():
        return jsonify({"url": controller.webhook_url()})

    # ----- public engagement endpoints -----------------------------------------------------
    @app.post("/webhooks/brevo")
    def brevo_webhook():
        payload = request.get_json(silent=True)
        if payload is None:
            return fail("expected JSON")
        done, result = controller.webhook(payload, request.args.get("token", ""))
        return (jsonify({"ok": True, **result}), 200) if done else (jsonify(result), 401)

    # ----- files --------------------------------------------------------------------------
    @app.get("/files/outbox.csv")
    def outbox_csv():
        mode = _firm_mode()
        rows = controller.outbox(mode=mode)
        out = controller.output_dir(mode)
        path = write_queue_csv(rows, out / "outbox_latest.csv")
        return send_from_directory(path.parent, path.name, as_attachment=True)

    @app.get("/files/<path:name>")
    def files(name: str):
        allowed = {"contacts_latest.xlsx", "contacts_latest.csv"}
        out = controller.output_dir()
        if name not in allowed and not (name.startswith("top_") and name.endswith((".csv", ".txt", ".eml"))):
            return ("Not found", 404)
        return send_from_directory(out, name, as_attachment=True)

    return app


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=5057, debug=False, threaded=True)
