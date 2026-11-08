# Agentic Order Processor

A Python project that reads Gmail, classifies order requests, checks a local furniture catalog, and creates orders for owner review. Customer replies remain drafts until an owner edits and explicitly sends them from the dashboard.

Developed with the assistance of AI tools.

## Setup

Create a Google Cloud project, enable the Gmail API, configure the OAuth consent screen, and create a **Desktop app** OAuth client. Add the `gmail.readonly` and `gmail.send` scopes to the OAuth consent screen's Data Access settings. Download the OAuth client JSON as `credentials.json` into the project root. The first Gmail access opens a browser for authorization; the app saves the granted scopes in `token.json`. Both files are ignored by Git. If you previously authorized the read-only version, the app will request consent again for sending.

From the project root, create the virtual environment and install dependencies:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
```

If you do not already have `.env`, copy `.env.example`; otherwise add these settings without replacing your existing file. Set `OPENAI_API_KEY` for AI features. For dashboard login, set the owner email and a password of at least 12 characters. Generate `DASHBOARD_SECRET_KEY` with `openssl rand -hex 32`. Do not commit `.env` or real credentials.

## Run

```bash
# Preview recent Gmail messages
.venv/bin/python -m gmail_order_processor.main

# Initialize the furniture catalog and list its products
.venv/bin/python -m gmail_order_processor.inventory_cli seed
.venv/bin/python -m gmail_order_processor.inventory_cli list

# Process sample emails (AI API calls are made)
.venv/bin/python -m gmail_order_processor.workflow_cli --sample-file samples/sample_emails.json --limit 20

# Run the Python MCP inventory server in MCP Inspector
.venv/bin/mcp dev src/gmail_order_processor/inventory_mcp_server.py
```

The workflow creates a draft order only when a new order has enough information and its products match available catalog items. Reprocessing the same Gmail message won’t create a duplicate. Drafts do not reserve stock.

## Owner dashboard

The dashboard shows saved orders and human review cases. On a review case, inspect the recipient, edit the suggested reply, then click **Send reply** to send it through the authorized Gmail account. Order approval rechecks and deducts stock; rejecting an order cancels it. Dashboard actions require owner authentication.

Start the API from the project root:

```bash
.venv/bin/python -m uvicorn gmail_order_processor.dashboard_api:app --reload --host 127.0.0.1 --port 8000
```

In another terminal:

```bash
cd frontend
npm install
npm run dev
```

Open `http://127.0.0.1:5173`. Run `.venv/bin/python -m pytest -q` for the tests; they use mocked services and do not require a Gmail account.

`mcp_express_example/` is a separate JavaScript Express MCP learning project; it is not connected to this Python app.
