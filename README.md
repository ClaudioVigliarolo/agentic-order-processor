# Gmail Order Processor

A Python 3.11+ command-line project that reads recent Gmail messages, classifies email, extracts order details, checks a local product catalog, and drafts replies for human review. It requests only `gmail.readonly`; it does not change messages or send mail.

## Project layout

```text
src/gmail_order_processor/
  gmail_client.py       OAuth, read-only Gmail access, MIME/body parsing
  main.py               CLI that prints a 20-message inbox preview
  agents/
    classifier.py       Email classification agent and routing
    order_extractor.py  Order extraction agent and Pydantic schemas
    inventory_agent.py  Catalog matching and stock lookup
    response_agent.py   Customer reply drafting agent
  workflow.py           Safely orchestrates classification, extraction, inventory, and reply drafts
  workflow_cli.py       Runs the full review-only workflow on Gmail or sample emails
  classification_cli.py Inbox classification and human-labeled evaluation
sample_emails.json     20 labeled example emails for classification evaluation
tests/
  test_gmail_client.py  Mocked Gmail and parsing tests
```

## Google Cloud and OAuth setup

1. Open the [Google Cloud Console](https://console.cloud.google.com/) and create or select a project.
2. In **APIs & Services → Library**, enable **Gmail API** for that project.
3. In **Google Auth Platform** (or **APIs & Services → OAuth consent screen**), configure the consent screen. Choose **External** for a personal Gmail account unless your Workspace administrator requires a different choice. Fill in the required app name and contact details. While the app is in testing, add the Google account you will use under **Test users**.
4. Under **Clients** (or **APIs & Services → Credentials → Create credentials → OAuth client ID**), create an OAuth client with application type **Desktop app**.
5. Download its JSON file. Rename it `credentials.json` and place it in this project directory, alongside `README.md`. Keep the file private; it is excluded from Git.

The program requests only the `https://www.googleapis.com/auth/gmail.readonly` scope. On the first run, Google opens a browser for account selection and consent. After consent, the local OAuth flow writes the refresh/access credentials to `token.json` in this project directory. The token file is ignored by Git. Later runs use or refresh that local token; if access is revoked, remove `token.json` and run the program again to re-authorize.

The program reports authentication/API errors without printing token contents. A successful Gmail connection is established only after the OAuth flow succeeds and the Gmail API returns messages; before then, this setup guide does not assume the connection works.

## Install and run

From this directory:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
python -m gmail_order_processor.main
```

If `python3.11` is not the command for your Python 3.11+ installation, substitute the appropriate executable. The `.env` file is optional; the defaults are `credentials.json`, `token.json`, and 20 messages. If `.env` does not already exist, create it from `.env.example`; if it does exist, add or update settings without replacing your current file:

```dotenv
GMAIL_CREDENTIALS_FILE=credentials.json
GMAIL_TOKEN_FILE=token.json
GMAIL_MAX_RESULTS=20
```

You can also run the installed command `gmail-inbox` after installation. The client supports paginated retrieval through `iter_inbox_messages(page_size=..., max_results=...)`; the CLI displays up to the latest 20 inbox messages.

## Tests

Tests mock Gmail API responses and do not need Google credentials or a Gmail account:

```bash
python -m pytest
```

## Phase 2: email classification

The classification agent uses the OpenAI Agents SDK and returns a typed JSON result with a category, confidence, and brief rationale. Unrelated emails are ignored, uncertain emails are sent to human review, and only new orders enter order extraction. This phase does not create orders, send replies, or change Gmail messages.

For a `new_order` classification, a second agent extracts product names, quantities, color/size/model variants, the delivery address, and missing information into a Pydantic-validated result. The classification CLI includes this extraction only for `new_order` emails. The complete workflow is described below.

Install the updated dependencies in the project environment:

```bash
python -m pip install -e .
```

Add your OpenAI API key to `.env` (do not replace an existing `.env`):

```dotenv
OPENAI_API_KEY=your-api-key
OPENAI_MODEL=gpt-6-luna
```

The email sender, subject, and body are sent to the configured OpenAI model for classification; a new-order email is then sent to the extraction agent. Run the workflow on up to 20 newest inbox emails with:

```bash
python -m gmail_order_processor.classification_cli
```

To classify the 20 labeled examples and report accuracy against their `expected_category` fields:

```bash
python -m gmail_order_processor.classification_cli --sample-file sample_emails.json --evaluate
```

The sample file is a small, illustrative hand-labeled set, not a claim about accuracy on other inboxes. For a labeled evaluation of your live inbox, omit `--sample-file`; the CLI asks you to enter each message's gold label. Add `--limit N` to classify fewer or more messages. Sample classifications are printed to the terminal and are not saved to disk.

## Phase 3: SQLite product catalog

The local catalog schema contains an integer ID, product name, unique SKU, whole-euro price, and stock quantity. It starts with 15 fictional furniture products. Seeding is idempotent: it inserts missing SKUs and leaves existing rows unchanged.

Create and seed the default database (`data/products.sqlite3`), then list its products:

```bash
python -m gmail_order_processor.inventory_cli seed
python -m gmail_order_processor.inventory_cli list
```

Set `PRODUCT_DB_PATH` to use a different SQLite file. Database files are excluded from Git; the seed catalog remains in `inventory.py`.

The catalog supports read-only product matching and stock checks. It never reserves or decrements stock.

## Inventory MCP server

The Python MCP server exposes two read-only tools over stdio: `search_products(query, limit=10)` searches product names and SKUs, and `check_stock(sku, quantity=1)` reports the available quantity and whether the requested amount is in stock. It uses the same `PRODUCT_DB_PATH` setting as the catalog CLI. The local order workflow currently uses the same repository directly for deterministic product matching and stock checks; MCP remains available to external MCP hosts.

Install the MCP dependency and start the server in MCP Inspector for local tool testing:

```bash
python -m pip install -e .
mcp dev src/gmail_order_processor/inventory_mcp_server.py
```

The SDK's stdio transport is intended to be launched by an MCP host as a child process. For example, a host can launch `python -m gmail_order_processor.inventory_mcp_server` with the project directory as its working directory. The official [MCP Python SDK docs](https://py.sdk.modelcontextprotocol.io/) describe this local stdio setup.

## Phase 5: review-only order workflow

Run the complete sequence on recent Gmail messages:

```bash
python -m gmail_order_processor.workflow_cli --limit 20
```

Or run it on the sample messages without connecting to Gmail:

```bash
python -m gmail_order_processor.workflow_cli --sample-file sample_emails.json --limit 20
```

The workflow stops for unrelated and uncertain emails. Product inquiries and cancellations are not treated as new orders; they receive only a neutral reply draft marked for human review. New orders are extracted first. If products, quantities, or the delivery address are missing, the inventory lookup is skipped and a draft asks for the missing information. Inventory checks use exact catalog name/SKU matches; ambiguous names, unknown products, and requested variants the catalog cannot represent are sent for human review. A successful stock check produces a draft marked `ready_for_human_approval`.

The Response Agent returns a typed subject and body, but the program never sends it. Gmail remains read-only, and inventory checks never reserve or change stock. A failed step stops later steps for that message; the CLI emits a safe error status and continues with the next message.

