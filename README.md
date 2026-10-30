# Gmail Order Processor — Phase 1

A Python 3.11+ command-line project that reads recent messages from the Gmail inbox using the official Gmail API, classifies them, and extracts order details from new-order emails with the OpenAI Agents SDK. It requests only `gmail.readonly`; it does not change messages or send mail. Inventory, MCP servers, and a frontend are not implemented.

## Project layout

```text
src/gmail_order_processor/
  gmail_client.py       OAuth, read-only Gmail access, MIME/body parsing
  main.py               CLI that prints a 20-message inbox preview
  classification.py     Typed email classification agent and routing
  order_extraction.py   Pydantic schema and order extraction agent
  workflow.py           Runs extraction only for new_order classifications
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

The classification agent uses the OpenAI Agents SDK and returns a typed JSON result with a category, confidence, and brief rationale. Routing is deterministic: `unrelated` is marked `ignored`, `uncertain` is marked `human_review`, and the other three labels are routed to the order workflow. This phase does not create orders, send replies, or change Gmail messages.

For a `new_order` classification, a second agent extracts product names, quantities, color/size/model variants, the delivery address, and missing information into a Pydantic-validated result. The CLI includes this extraction in the JSON output only for `new_order` emails. It does not create or confirm an order.

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

This phase only creates and reads the catalog. It does not yet compare extracted order items with the catalog or reserve/decrement stock.

## Inventory MCP server

The Python MCP server exposes two read-only tools over stdio: `search_products(query, limit=10)` searches product names and SKUs, and `check_stock(sku, quantity=1)` reports the available quantity and whether the requested amount is in stock. It uses the same `PRODUCT_DB_PATH` setting as the catalog CLI. It does not reserve or change stock, and it is not yet connected to an AI agent.

Install the MCP dependency and start the server in MCP Inspector for local tool testing:

```bash
python -m pip install -e .
mcp dev src/gmail_order_processor/inventory_mcp_server.py
```

The SDK's stdio transport is intended to be launched by an MCP host as a child process. For example, a host can launch `python -m gmail_order_processor.inventory_mcp_server` with the project directory as its working directory. The official [MCP Python SDK docs](https://py.sdk.modelcontextprotocol.io/) describe this local stdio setup.

