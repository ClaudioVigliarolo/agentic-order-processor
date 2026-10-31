# Express MCP example

A standalone JavaScript MCP server for learning how clients discover and call tools over Streamable HTTP. It is independent of the Python order-processing project.

The server contains a small in-memory catalog of five fictional furniture products and exposes:

- `search_products(query)` — search product names and SKUs.
- `check_stock(product)` — return available quantity by exact product name or SKU.
- `get_price(product)` — return the whole-euro price by exact product name or SKU.

## Start the server

From this directory:

```bash
npm install
npm start
```

The MCP endpoint is `http://localhost:3000/mcp`. Leave this terminal open to see server logs.

## Try it with the included MCP client

In a second terminal, from this directory:

```bash
npm run test:client
```

The client connects, lists tools, calls `check_stock` for `FUR-CHR-001`, prints the result, and disconnects. Watch the server terminal to see the matching request and tool logs.

## Try it with MCP Inspector

In a second terminal, start the Inspector:

```bash
npx @modelcontextprotocol/inspector
```

In the browser window it opens:

1. Choose **Streamable HTTP** as the transport.
2. Enter `http://localhost:3000/mcp` and connect.
3. Open **Tools** to see `check_stock`, `search_products`, and `get_price`.
4. Select a tool, enter its arguments, and run it.

## Watch the MCP lifecycle

The included client performs this sequence:

1. `client.connect()` sends MCP `initialize`.
2. `client.listTools()` sends `tools/list`.
3. `client.callTool()` sends `tools/call`.
4. The server runs the matching JavaScript function and returns a tool result.

The server logs each incoming MCP method, tool discovery, tool invocation, and tool result. Stateless mode creates a fresh MCP server/transport for each HTTP POST and does not issue or store MCP session IDs.

## Files

- `server.js` — Express endpoint, stateless Streamable HTTP transport, catalog, and three tools.
- `client.js` — small client using the official MCP TypeScript SDK's JavaScript API.
- `package.json` — Express, Zod, and official MCP SDK dependencies.
