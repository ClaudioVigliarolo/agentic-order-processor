import 'dotenv/config';
import {
  Agent,
  run,
  MCPServerStreamableHttp,
} from '@openai/agents';

// Connect to your existing furniture MCP server.
const mcpServer = new MCPServerStreamableHttp({
  name: 'Furniture catalog',
  url: 'http://localhost:3000/mcp',
     
});

// Give the agent access to that server's tools.
const agent = new Agent({
  name: 'Furniture assistant',
  model: 'gpt-4.1-mini',
  instructions:
    'Use the furniture tools to answer questions. Never invent stock quantities. Explain why you give that result.',
  mcpServers: [mcpServer],
});

try {
  await mcpServer.connect();

  // Your question goes here.
  const prompt = 'How many in stock of FUR-CHR-001?';

  // The SDK handles discovery and the tool-calling loop.
  const result = await run(agent, prompt);

  console.log(result.finalOutput);
} finally {
  await mcpServer.close();
}