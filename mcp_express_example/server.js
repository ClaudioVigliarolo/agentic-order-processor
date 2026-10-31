// Minimal Express + MCP server. Each POST gets a fresh server and transport,
// so this example does not keep MCP sessions or client state in memory.

import express from 'express';
import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { createMcpExpressApp } from '@modelcontextprotocol/sdk/server/express.js';
import { StreamableHTTPServerTransport } from '@modelcontextprotocol/sdk/server/streamableHttp.js';
import { z } from 'zod/v4';

const PORT = 3000;

// Fictional sample catalog. Product lookup is case-insensitive.
const products = [
  { name: 'Nordic Oak Dining Chair', sku: 'FUR-CHR-001', price: 129, stock: 24 },
  { name: 'Forma Oak Writing Desk', sku: 'FUR-DSK-002', price: 329, stock: 10 },
  { name: 'Cloud Two Seat Sofa', sku: 'FUR-SOF-001', price: 799, stock: 5 },
  { name: 'Forum Folding Table', sku: 'FUR-TBL-002', price: 89, stock: 32 },
  { name: 'Elm Five Shelf Bookcase', sku: 'FUR-STG-001', price: 249, stock: 9 },
];

function findProduct(name) {
  const normalized = name.trim().toLocaleLowerCase();
  return products.find(
    (product) =>
      product.name.toLocaleLowerCase() === normalized ||
      product.sku.toLocaleLowerCase() === normalized,
  );
}

function formatMcpResponse(value, isError = false) {
  const result = { content: [{ type: 'text', text: value }] };
  if (isError) result.isError = true;
  return result;
}

function createMcpServer() {
  const server = new McpServer({
    name: 'furniture-catalog-example',
    version: '1.0.0',
  });

  server.registerTool(
    'check_stock',
    {
      title: 'Check product stock',
      description: 'Look up a furniture product by its exact name or SKU and return its available quantity.',
      inputSchema: {
        product: z.string().min(1).regex(/^(FUR-[A-Z]{3}-\d{3}|.+)$/i).describe('Exact product name or SKU, for example FUR-CHR-001.'),
      },
    },
    async ({ product }) => {
      console.log(`[tool] check_stock executing for: ${product}`);
      const match = findProduct(product);
      if (!match) {
        const message = `Product not found: ${product}`;
        console.log(`[tool] check_stock result: ${message}`);
        return formatMcpResponse(message, true);
      }
      const message = `${match.name} (${match.sku}) has ${match.stock} units available.`;
      console.log(`[tool] check_stock result: ${message}`);
      return formatMcpResponse(message);
    },
  );

  server.registerTool(
    'search_products',
    {
      title: 'Search furniture products',
      description: 'Search the fictional furniture catalog by a word or phrase in a product name or SKU.',
      inputSchema: {
        query: z.string().min(1).describe('Text to search for, such as chair, oak, or FUR-TBL.'),
      },
    },
    async ({ query }) => {
      console.log(`[tool] search_products executing for: ${query}`);
      const normalized = query.trim().toLocaleLowerCase();
      const matches = products.filter(
        (product) =>
          product.name.toLocaleLowerCase().includes(normalized) ||
          product.sku.toLocaleLowerCase().includes(normalized),
      );
      const message = matches.length
        ? JSON.stringify(matches.map(({ name, sku, price, stock }) => ({
            name,
            sku,
            price_eur: price,
            stock_quantity: stock,
          })), null, 2)
        : `No products matched: ${query}`;
      console.log(`[tool] search_products result: ${matches.length} match(es)`);
      return formatMcpResponse(message);
    },
  );

  server.registerTool(
    'get_price',
    {
      title: 'Get product price',
      description: 'Look up the price in whole euros for a furniture product by its exact name or SKU.',
      inputSchema: {
        product: z.string().min(1).describe('Exact product name or SKU, for example Forma Oak Writing Desk.'),
      },
    },
    async ({ product }) => {
      console.log(`[tool] get_price executing for: ${product}`);
      const match = findProduct(product);
      if (!match) {
        const message = `Product not found: ${product}`;
        console.log(`[tool] get_price result: ${message}`);
        return formatMcpResponse(message, true);
      }
      const message = `${match.name} (${match.sku}) costs €${match.price}.`;
      console.log(`[tool] get_price result: ${message}`);
      return formatMcpResponse(message);
    },
  );

  return server;
}

// The SDK's Express helper includes localhost host-header validation.
const app = createMcpExpressApp({ host: '127.0.0.1' });
app.use(express.json());

// Log the MCP lifecycle methods before handing each request to the SDK.
app.post('/mcp', async (req, res) => {
  const method = req.body?.method;
  console.log(`[http] MCP request arrived: ${method ?? 'unknown method'}`);
  if (method === 'tools/list') {
    console.log('[mcp] Client requested tools/list');
  } else if (method === 'tools/call') {
    console.log(`[mcp] Client invoked tools/call: ${req.body?.params?.name ?? 'unknown tool'}`);
  }

  const server = createMcpServer();
  const transport = new StreamableHTTPServerTransport({
    // undefined means stateless: the server does not issue or track session IDs.
    sessionIdGenerator: undefined,
  });

  try {
    await server.connect(transport);
    await transport.handleRequest(req, res, req.body);
  } catch (error) {
    console.error('[http] Error handling MCP request:', error);
    if (!res.headersSent) {
      res.status(500).json({
        jsonrpc: '2.0',
        error: { code: -32603, message: 'Internal server error' },
        id: req.body?.id ?? null,
      });
    }
  } finally {
    await transport.close();
    await server.close();
  }
});

// Stateless mode does not keep a GET event stream or DELETE-able session.
app.get('/mcp', (_req, res) => res.sendStatus(405));
app.delete('/mcp', (_req, res) => res.sendStatus(405));

app.listen(PORT, '127.0.0.1', () => {
  console.log(`Furniture MCP server listening at http://localhost:${PORT}/mcp`);
});
