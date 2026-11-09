# A2UI Finance

A tiny personal-finance dashboard built on **Google's A2UI** (agent-to-UI) protocol.
The agent emits UI as A2UI *components* (not HTML) using the real `a2ui-agent-sdk`
builder; a trusted renderer draws them and sends button clicks back as A2UI
ActionEvents. No LLM / API key needed.

## Run
```bash
./v/bin/python agent.py        # http://localhost:8000
./v/bin/python agent.py --selfcheck
```

## Deploy (Render, free, no card)
`render.yaml` is a Render Blueprint. On [render.com](https://render.com):
New → Blueprint → connect this repo → it reads `render.yaml`. Set `GEMINI_API_KEY`
in the dashboard (it's `sync: false`, so it's never committed). The app binds
`$PORT` automatically. Without the key it still serves the deterministic dashboard.

## How it works
- `agent.py` — builds the dashboard with `a2ui.builder.v1_0` (Card/Column/Row/Text/
  Divider/TextField/Button), flattens it to the A2UI wire shape (flat list, children
  by id), and serves it over stdlib `http.server`. POSTed actions mutate in-memory
  state and return the rebuilt surface.
- `index.html` — ~60-line renderer mapping each A2UI component name to a widget.
  It only draws components it knows → the agent can't inject arbitrary markup
  (A2UI's security model).

## LLM mode (optional, wired up)
Type a request in the top bar (e.g. *"summarize my spending as a card with rows"*)
and Gemini generates the A2UI surface. Blank box = the deterministic live dashboard.

How it works (in `agent.py` → `llm_surface`):
1. `A2uiGenerator` + `BasicCatalog("1.0")` → `proc.prompt_snippet` is the system
   prompt that teaches the model the catalog (as the "A2UI Express" DSL).
2. Gemini returns `<a2ui>…</a2ui>`; `proc.parse_response(...)` compiles + validates
   it into a `CreateSurface` whose `.components` is the same flat shape the renderer
   already draws — so one renderer serves both paths.
3. Any model/parse error falls back to the deterministic dashboard, so it never
   hard-fails.

AI-generated buttons are display-only here (clicking re-runs the prompt); the
stateful write-back loop is the deterministic dashboard's `add_expense`.

### Setup
```bash
./v/bin/pip install google-genai        # already installed in ./v
# .env (git-ignored, never committed):
#   GEMINI_API_KEY=...        from https://aistudio.google.com
#   GEMINI_MODEL=gemini-3.5-flash   (optional; this is the default)
```
Without a key, the app still runs — it just serves the deterministic dashboard.
