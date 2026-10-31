// Tiny SDK client that shows initialization, tools/list, and tools/call.

import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StreamableHTTPClientTransport } from '@modelcontextprotocol/sdk/client/streamableHttp.js';

const client = new Client({ name: 'furniture-demo-client', version: '1.0.0' });
const transport = new StreamableHTTPClientTransport(
  new URL('http://localhost:3000/mcp'),
);

try {
  console.log('[client] Connecting: this sends MCP initialize');
  await client.connect(transport);

  console.log('\n[client] Sending tools/list');
  
  const { tools } = await client.listTools();
  console.log(tools.map(({ name, description, inputSchema }) => ({
    name,
    description,
    inputSchema,
  })));

  console.log('\n[client] Sending tools/call: check_stock');
  const stock = await client.callTool({
    name: 'check_stock',
    arguments: { product: 'FUR-CHR-001' },
  });


 console.log('\n[client] Sending tools/call: check_stock  2');
  const stock2 = await client.callTool({
    name: 'check_stock',
    arguments: { product: 'FUR-CHR-INEXISTENT' },
  });
  
  console.log('[client] Tool result:', stock2);
} finally {
  await client.close();
}
