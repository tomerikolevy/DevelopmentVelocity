"""Local dashboard server (stdlib only): velocity page, calibrate page and their JSON API."""
from __future__ import annotations

import dataclasses
import errno
import json
import re
import threading
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from . import calibrate as cal
from .config import EFFORTS, MODELS, WEB_DIR, Config, set_scoring
from .github import GitHub
from .pipeline import diff_report, pending_labels, run_scoring, select_targets, write_labels
from .scorer import load_rubric
from .store import Store

CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml"}
PR_FIELDS = ("key", "repo", "number", "title", "url", "merged_at", "score", "llm_score", "score_source",
             "dimensions", "rationale", "summary", "confidence", "rubric_version", "model", "effort", "label_status")


def dashboard_data(store: Store, cfg: Config) -> dict:
    prs = store.load_prs()
    rows = []
    for pr in prs.values():
        row = {k: pr.get(k) for k in PR_FIELDS}
        m = pr.get("metrics") or {}
        row["additions"] = m.get("total_additions")
        row["deletions"] = m.get("total_deletions")
        row["history"] = [{"score": h["score"], "rubric_version": h["rubric_version"]}
                          for h in pr.get("score_history", [])]
        rows.append(row)
    outdated = len(select_targets(prs, "outdated", cfg.rubric_version, model=cfg.model, effort=cfg.effort))
    unscored = len(select_targets(prs, "unscored", cfg.rubric_version))
    return {"prs": rows, "rubric_version": cfg.rubric_version, "outdated": outdated - unscored,
            "unscored": unscored, "pending_labels": len(pending_labels(prs)),
            "scoring": scoring_settings(cfg)}


def scoring_settings(cfg: Config) -> dict:
    return {"model": cfg.model, "effort": cfg.effort, "models": MODELS, "efforts": EFFORTS}


class Jobs:
    """One background job at a time (sync + score, rescore phase 1, or label writing)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.state = {"state": "idle"}

    def start(self, kind: str, fn) -> bool:
        with self.lock:
            if self.state.get("state") == "running":
                return False
            self.state = {"state": "running", "kind": kind, "done": 0, "total": 0, "log": []}

        def runner():
            try:
                result = fn(self)
                with self.lock:
                    self.state.update(state="done", result=result)
            except Exception as e:  # noqa: BLE001 - surfaced to the page
                traceback.print_exc()
                with self.lock:
                    self.state.update(state="error", error=str(e))

        threading.Thread(target=runner, daemon=True).start()
        return True

    def progress(self, i, total, key, msg):
        with self.lock:
            self.state.update(done=i, total=total)
            self.state["log"] = (self.state["log"] + [f"{key}: {msg}"])[-8:]

    def snapshot(self) -> dict:
        with self.lock:
            return json.loads(json.dumps(self.state))


def make_handler(cfg: Config, store: Store, jobs: Jobs):
    def rescore_job(job: Jobs):
        from .scorer import ClaudeScorer

        # Freeze the settings so a change in the dashboard can't switch model mid-run.
        run_cfg = dataclasses.replace(cfg)
        keys = select_targets(store.load_prs(), "outdated", run_cfg.rubric_version,
                              model=run_cfg.model, effort=run_cfg.effort)
        with job.lock:
            job.state.update(total=len(keys), model=run_cfg.model, effort=run_cfg.effort)
        run = run_scoring(keys, ClaudeScorer(run_cfg), store, run_cfg.model, progress=job.progress,
                          effort=run_cfg.effort)
        return diff_report(run)

    def sync_score_job(job: Jobs):
        """Same as `sync` then `score` on the command line, minus the label prompt:
        new scores show up in the labels banner for the user to apply."""
        from .scorer import ClaudeScorer
        from .sync import sync

        def sync_log(msg: str):
            m = re.search(r"\[(\d+)/(\d+)\]\s+(\S+)", msg)
            if m:
                job.progress(int(m[1]), int(m[2]), m[3], msg.split(m[3], 1)[1].strip())
            else:
                with job.lock:
                    job.state["log"] = (job.state["log"] + [msg])[-8:]

        run_cfg = dataclasses.replace(cfg)  # settings can't change mid-run
        with job.lock:
            job.state.update(phase="sync", model=run_cfg.model, effort=run_cfg.effort)
        synced = sync(run_cfg, store, GitHub(), log=sync_log)
        keys = select_targets(store.load_prs(), "unscored", run_cfg.rubric_version)
        with job.lock:
            job.state.update(phase="score", done=0, total=len(keys))
        report = diff_report(run_scoring(keys, ClaudeScorer(run_cfg), store, run_cfg.model,
                                         progress=job.progress, effort=run_cfg.effort)) if keys else None
        return {"sync": synced, "score": report}

    def labels_job(include_manual: bool):
        def fn(job: Jobs):
            keys = pending_labels(store.load_prs(), include_manual)
            with job.lock:
                job.state["total"] = len(keys)
            return write_labels(keys, cfg, store, GitHub(), include_manual,
                                log=lambda m: job.progress(0, len(keys), "labels", m))
        return fn

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # quieter console
            if "/api/rescore/status" not in self.path:
                super().log_message(fmt, *args)

        def _send(self, status: int, body: bytes, ctype: str):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, data, status: int = 200):
            self._send(status, json.dumps(data).encode(), "application/json")

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(length) or b"{}") if length else {}

        def _static(self, name: str):
            path = (WEB_DIR / name).resolve()
            if WEB_DIR.resolve() not in path.parents or not path.is_file():
                return self._json({"error": "not found"}, 404)
            self._send(200, path.read_bytes(), CONTENT_TYPES.get(path.suffix, "application/octet-stream"))

        def do_GET(self):
            path = urlparse(self.path).path
            try:
                if path in ("/", "/index.html"):
                    return self._static("index.html")
                if path in ("/calibrate", "/calibrate.html"):
                    return self._static("calibrate.html")
                if path.startswith("/static/"):
                    return self._static(path[len("/static/"):])
                if path == "/api/data":
                    return self._json(dashboard_data(store, cfg))
                if path == "/api/rescore/status":
                    return self._json(jobs.snapshot())
                if path == "/api/rubric":
                    return self._json({"version": cfg.rubric_version, "text": load_rubric(cfg.rubric_version)})
                if path == "/api/calibrate/pair":
                    prs = store.load_prs()
                    pair = cal.pick_pair(prs, store.load_calibration(), cfg.rubric_version)
                    if pair is None:
                        return self._json({"pair": None, "rubric_version": cfg.rubric_version})
                    return self._json({"pair": {"score": pair["score"], "a": cal.pr_card(prs[pair["a"]]),
                                                "b": cal.pr_card(prs[pair["b"]])},
                                       "rubric_version": cfg.rubric_version})
                if path == "/api/calibrate/state":
                    data = store.load_calibration()
                    prs = store.load_prs()
                    title = lambda k: prs.get(k, {}).get("title", k)  # noqa: E731
                    disputes = [{**r, "a_title": title(r["a"]), "b_title": title(r["b"])}
                                for r in data["reviews"] if r["status"] == "open"]
                    proposals = [p for p in data["proposals"] if p["status"] in ("proposed", "saved")]
                    return self._json({"stats": cal.stats(data), "open_disputes": disputes,
                                       "proposals": proposals, "rubric_version": cfg.rubric_version,
                                       "dispute_types": cal.DISPUTE_TYPES})
                return self._json({"error": "not found"}, 404)
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                return self._json({"error": str(e)}, 500)

        def do_POST(self):
            path = urlparse(self.path).path
            try:
                body = self._body()
                if path == "/api/calibrate/review":
                    review = cal.record_review(
                        store, cfg.rubric_version, body["a"], body["b"], int(body["score"]), body["verdict"],
                        body.get("dispute_type"), body.get("explanation", ""), body.get("user_scores"))
                    out = {"review": review}
                    if review["verdict"] == "dispute" and body.get("analyze", True):
                        out["proposal"] = cal.analyze(store, cfg, [review["id"]])
                    return self._json(out)
                if path == "/api/calibrate/analyze":
                    return self._json({"proposal": cal.analyze(store, cfg, body["review_ids"])})
                m = re.fullmatch(r"/api/calibrate/proposal/(\w+)/(accept|reject|save)", path)
                if m:
                    pid, action = m.groups()
                    if action == "accept":
                        version = cal.accept_proposal(store, cfg, pid, body.get("rubric"), body.get("note", ""))
                        return self._json({"rubric_version": version})
                    fn = cal.reject_proposal if action == "reject" else cal.save_proposal
                    return self._json({"proposal": fn(store, pid)})
                if path == "/api/settings/scoring":
                    if jobs.snapshot().get("state") == "running":
                        return self._json({"error": "Wait for the running job to finish."}, 409)
                    model = body.get("model", cfg.model)
                    effort = body.get("effort", cfg.effort)
                    set_scoring(model, effort)  # validates; raises ValueError on unknown values
                    cfg.model, cfg.effort = model, effort
                    return self._json(scoring_settings(cfg))
                if path == "/api/rescore/start":
                    ok = jobs.start("rescore", rescore_job)
                    return self._json({"started": ok}, 200 if ok else 409)
                if path == "/api/sync-score/start":
                    ok = jobs.start("sync-score", sync_score_job)
                    return self._json({"started": ok}, 200 if ok else 409)
                if path == "/api/labels/apply":
                    ok = jobs.start("labels", labels_job(bool(body.get("include_manual"))))
                    return self._json({"started": ok}, 200 if ok else 409)
                return self._json({"error": "not found"}, 404)
            except ValueError as e:
                return self._json({"error": str(e)}, 400)
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                return self._json({"error": str(e)}, 500)

    return Handler


def _dashboard_running(url: str) -> bool:
    """True if a velocity dashboard already answers at url."""
    import urllib.request

    try:
        with urllib.request.urlopen(f"{url}/api/data", timeout=2) as r:
            return r.status == 200 and "prs" in json.loads(r.read())
    except Exception:  # noqa: BLE001 - anything else on the port is "not ours"
        return False


def serve(cfg: Config, store: Store, port: int, open_browser: bool = True) -> None:
    url = f"http://localhost:{port}"
    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(cfg, store, Jobs()))
    except OSError as e:
        if e.errno != errno.EADDRINUSE:
            raise
        if _dashboard_running(url):
            print(f"The dashboard is already running at {url}; opening it.")
            if open_browser:
                webbrowser.open(url)
            return
        raise SystemExit(f"Port {port} is in use by another program. "
                         f"Stop it, or run `velocity serve --port <other>`.") from e
    print(f"Dashboard running at {url}  (Ctrl+C to stop)")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
