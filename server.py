"""Tiny web server for the on-stage demo. Standard library only.

    python server.py            -> http://localhost:8000
"""
import json
import os
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from app import bot, llm
from evals import graders, runner

ROOT = Path(__file__).resolve().parent
HUMAN_FILE = runner.RESULTS_DIR / "human_labels.json"
PORT = int(os.environ.get("PORT", 8000))

progress = {"running": False, "version": None, "rows": [], "total": 0, "error": None}
lock = threading.Lock()


def friendly(e):
    if isinstance(e, llm.LLMError):
        return str(e)
    return f"{type(e).__name__}: {e}"


def read_human():
    return json.loads(HUMAN_FILE.read_text()) if HUMAN_FILE.exists() else {}


def background_run(version):
    def on_row(row):
        with lock:
            progress["rows"].append(row)
    try:
        runner.run_eval(version, on_row=on_row)
        # A fresh run makes old human labels for this version stale.
        labels = read_human()
        labels.pop(version, None)
        runner.RESULTS_DIR.mkdir(exist_ok=True)
        HUMAN_FILE.write_text(json.dumps(labels, indent=2))
    except Exception as e:  # surface anything (bad key, no network) in the UI
        with lock:
            progress["error"] = friendly(e)
    finally:
        with lock:
            progress["running"] = False


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/how-it-works"):
            page = "index.html" if path == "/" else "how-it-works.html"
            body = (ROOT / "web" / page).read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)
        elif path == "/api/meta":
            versions = bot.list_prompt_versions()
            self.send_json({
                "versions": versions,
                "prompts": {v: (bot.APP_DIR / "prompts" / f"{v}.txt").read_text() for v in versions},
                "dataset": runner.load_dataset(),
                "thresholds": runner.THRESHOLDS,
                "has_key": llm.has_key(),
                "provider": llm.PROVIDER,
            })
        elif path.startswith("/api/results/"):
            version = path.rsplit("/", 1)[1]
            self.send_json({"result": runner.load(version), "human": read_human().get(version, {})})
        elif path == "/api/progress":
            with lock:
                self.send_json(dict(progress))
        else:
            self.send_error(404)

    def do_POST(self):
        data = self.read_body()
        if self.path == "/api/run":
            with lock:
                if progress["running"]:
                    return self.send_json({"error": "A run is already in progress"}, 409)
                progress.update(running=True, version=data["version"], rows=[],
                                total=len(runner.load_dataset()), error=None)
            threading.Thread(target=background_run, args=(data["version"],), daemon=True).start()
            self.send_json({"ok": True})
        elif self.path == "/api/compare":
            base, cand = runner.load(data["baseline"]), runner.load(data["candidate"])
            if not base or not cand:
                return self.send_json({"error": "Run both versions first"}, 400)
            self.send_json({"regressions": runner.compare(base, cand)})
        elif self.path == "/api/judge_prompt":
            # Everything the judge saw for one saved row: {version, id}
            result = runner.load(data["version"]) or {"cases": []}
            row = next((r for r in result["cases"] if r["id"] == data["id"]), None)
            if not row or "answer" not in row:
                return self.send_json({"error": "No saved answer for this case"}, 404)
            self.send_json({
                "model": result["models"]["judge"],
                "system": graders.JUDGE_INSTRUCTIONS,
                "user": graders.judge_prompt(row, row["answer"]),
                "reply": row.get("judge"),
            })
        elif self.path == "/api/human":
            # A human reviewer disagreeing with the judge: {version, id, metric, pass}
            labels = read_human()
            case_labels = labels.setdefault(data["version"], {}).setdefault(data["id"], {})
            if data.get("pass") is None:
                case_labels.pop(data["metric"], None)
            else:
                case_labels[data["metric"]] = data["pass"]
            runner.RESULTS_DIR.mkdir(exist_ok=True)
            HUMAN_FILE.write_text(json.dumps(labels, indent=2))
            self.send_json({"ok": True})
        elif self.path == "/api/ask":
            try:
                answer = bot.ask(data["version"], data["question"])
                self.send_json({"answer": answer})
            except Exception as e:
                self.send_json({"error": friendly(e)}, 500)
        elif self.path == "/api/add_case":
            # The learning loop: a bad answer in the playground becomes a permanent eval case.
            dataset = runner.load_dataset()
            n = 1 + sum(c["id"].startswith("PB-") for c in dataset)
            case = {
                "id": f"PB-{n:02d}",
                "category": "production_bug",
                "question": data["question"],
                "expected": data["expected"],
                "checks": {},
            }
            dataset.append(case)
            (runner.EVALS_DIR / "dataset.json").write_text(json.dumps(dataset, indent=2, ensure_ascii=False) + "\n")
            self.send_json({"case": case})
        else:
            self.send_error(404)


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://localhost:{PORT}"
    print(f"Eval demo running at {url}  (Ctrl+C to stop)")
    threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
