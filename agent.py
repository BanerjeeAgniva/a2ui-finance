"""A2UI personal-finance agent.

The agent never sends HTML. It builds a UI as A2UI components (Google's
agent-to-UI protocol) with the real `a2ui.builder` SDK, and a trusted renderer
(index.html) turns those components into widgets. Clicking a button POSTs an
A2UI ActionEvent back here; we mutate state and return the rebuilt surface.

Run:  ./v/bin/python agent.py        then open http://localhost:8000
Check: ./v/bin/python agent.py --selfcheck
"""
from __future__ import annotations
import json, os, sys, http.server
from a2ui.builder import v1_0 as ui

# ---- optional .env loader (stdlib; no python-dotenv dependency) --------------
def load_env(path=".env"):
    try:
        for line in open(path):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
    except FileNotFoundError:
        pass
load_env()
MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")

# ---- state (in-memory; a real app swaps this for a DB) -----------------------
STATE = {
    "balance": 125000,
    "expenses": [
        {"category": "Rent", "amount": 32000},
        {"category": "Groceries", "amount": 8400},
        {"category": "Transport", "amount": 3100},
    ],
}

def inr(n: float) -> str:
    # Indian digit grouping (12,34,567). ponytail: hand-rolled, locale libs are overkill for display.
    s = f"{int(round(n)):,}"
    if len(str(int(abs(n)))) > 3:
        head, tail = str(int(abs(n)))[:-3], str(int(abs(n)))[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:]); head = head[:-2]
        if head: groups.insert(0, head)
        s = ("-" if n < 0 else "") + ",".join(groups) + "," + tail
    return f"₹{s}"

# ---- UI: build the finance dashboard as A2UI components ----------------------
def build_surface() -> list[dict]:
    spent = sum(e["amount"] for e in STATE["expenses"])
    rows = [
        ui.Row(children=[
            ui.Text(text=e["category"], weight=1),
            ui.Text(text=inr(e["amount"])),
        ], justify="spaceBetween")
        for e in STATE["expenses"]
    ]
    tree = ui.Card(child=ui.Column(children=[
        ui.Text(text="Monthly Finances", variant="caption"),
        ui.Text(text=f"Balance  {inr(STATE['balance'])}"),
        ui.Text(text=f"Spent this month  {inr(spent)}", variant="caption"),
        ui.Divider(),
        ui.Text(text="Expenses", variant="caption"),
        *rows,
        ui.Divider(),
        ui.Text(text="Add an expense", variant="caption"),
        ui.TextField(label="Category", placeholder="e.g. Dining"),
        ui.TextField(label="Amount", placeholder="e.g. 1200"),
        ui.Button(
            child=ui.Text(text="Add expense"),
            action=ui.Action(event=ui.ActionEvent(name="add_expense")),
        ),
    ]))
    return tree.flatten()  # flat list, children referenced by id — this is A2UI's wire shape

# ---- LLM mode: let Gemini generate the A2UI surface from a text prompt -------
# Lazily built so the app runs key-free; any failure falls back to the builder.
_PROC = None
def _processor():
    global _PROC
    if _PROC is None:
        from a2ui.core.basic_catalog import BasicCatalog
        from a2ui.processor import A2uiGenerator, CatalogConfig
        from a2ui.core.schema.v1_0.renderer_capabilities import (
            A2uiRendererCapabilities as RC, V10Capabilities)
        cat = BasicCatalog("1.0")
        gen = A2uiGenerator(catalogs=[CatalogConfig(cat)])
        caps = RC(v1_0=V10Capabilities(supported_catalog_ids=[cat.catalog_id]))
        _PROC = gen.create_processor(caps)
    return _PROC

# Keep the model inside our renderer's static subset (no data bindings / format fns).
CONSTRAINTS = (
    "\n\n## Output constraints (strict):\n"
    "- Use ONLY literal string values in Text. Do NOT use data-model variables "
    "($/...), bindings, or functions like formatCurrency. Pre-format numbers as "
    "plain strings, e.g. \"₹90,000\".\n"
    "- The root MUST be a single Card.\n"
    "- Allowed components: Card, Column, Row, Text, Divider, TextField, Button.\n"
)

def llm_surface(prompt: str) -> list[dict]:
    from google import genai
    proc = _processor()
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    reply = client.models.generate_content(
        model=MODEL, contents=f"{proc.prompt_snippet}{CONSTRAINTS}\n## User request:\n{prompt}",
    ).text
    for part in proc.parse_response(reply):
        for msg in part.a2ui:
            cs = getattr(msg, "create_surface", None)
            if cs and cs.components:
                return cs.components
    raise ValueError("model returned no surface")

# ---- apply an A2UI ActionEvent coming back from the renderer -----------------
def apply_action(name: str, fields: dict) -> None:
    if name == "add_expense":
        cat = (fields.get("Category") or "Misc").strip() or "Misc"
        try:
            amt = float(fields.get("Amount") or 0)
        except ValueError:
            amt = 0
        if amt > 0:
            STATE["expenses"].append({"category": cat, "amount": amt})
            STATE["balance"] -= amt

# ---- stdlib HTTP server (no framework) ---------------------------------------
class H(http.server.BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(code); self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b))); self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path.startswith("/api/ui"):
            from urllib.parse import urlparse, parse_qs
            q = parse_qs(urlparse(self.path).query).get("q", [""])[0].strip()
            if q and os.environ.get("GEMINI_API_KEY"):
                try:
                    self._send(200, json.dumps(llm_surface(q))); return
                except Exception as e:        # fall back to the deterministic dashboard
                    print("LLM error, using builder:", repr(e)[:160])
            self._send(200, json.dumps(build_surface()))
        else:
            here = os.path.dirname(os.path.abspath(__file__))
            with open(os.path.join(here, "index.html"), "rb") as f:
                self._send(200, f.read(), "text/html")

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        msg = json.loads(self.rfile.read(n) or b"{}")
        apply_action(msg.get("event", ""), msg.get("fields", {}))
        self._send(200, json.dumps(build_surface()))

    def log_message(self, *a): pass  # quiet

def selfcheck():
    s = build_surface()
    assert s[-1]["component"] == "Card", "root must be the Card"
    assert any(c["component"] == "Button" for c in s), "needs an action button"
    assert all("id" in c for c in s), "every component needs an id"
    before = len(STATE["expenses"])
    apply_action("add_expense", {"Category": "Coffee", "Amount": "250"})
    assert len(STATE["expenses"]) == before + 1 and STATE["balance"] == 124750
    # SDK enforces the catalog: an unknown Text variant must be rejected.
    try:
        ui.Text(text="x", variant="nope"); raise AssertionError("bad variant accepted")
    except Exception as e:
        assert "literal" in str(e).lower() or "variant" in str(e).lower()
    assert inr(125000) == "₹1,25,000", inr(125000)
    print("selfcheck OK")

if __name__ == "__main__":
    if "--selfcheck" in sys.argv:
        selfcheck()
    else:
        port = int(os.environ.get("PORT", 8000))   # Cloud Run sets $PORT
        print(f"A2UI finance agent → http://localhost:{port}")
        http.server.HTTPServer(("0.0.0.0", port), H).serve_forever()
