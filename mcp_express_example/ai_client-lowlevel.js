import OpenAI from 'openai';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StreamableHTTPClientTransport } from '@modelcontextprotocol/sdk/client/streamableHttp.js';
import 'dotenv/config';

const openai = new OpenAI();

const client = new Client({
  name: 'furniture-demo-client',
  version: '1.0.0',
});

const transport = new StreamableHTTPClientTransport(
  new URL('http://localhost:3000/mcp'),
);

const prompt = 'How many in stock of FUR-CHR-001?';

try {
  await client.connect(transport);

  // 1. Discover the tools provided by your MCP server.
  const { tools } = await client.listTools();

  // 2. Describe those tools in the format OpenAI expects.
  const modelTools = tools.map((tool) => ({
    type: 'function',
    name: tool.name,
    description: tool.description,
    parameters: tool.inputSchema,
    strict: false,
  }));

  // 3. Ask the model your question and let it choose a tool.
  let response = await openai.responses.create({
    model: 'gpt-4.1-mini',
    instructions: 'Use the tools to check inventory. Never invent stock quantities.',
    input: prompt,
    tools: modelTools,
  });

  while (true) {
    const calls = response.output.filter(
      (item) => item.type === 'function_call',
    );

    if (calls.length === 0) {
      console.log('[model]', response.output_text);
      break;
    }

    const results = [];

    for (const call of calls) {
      console.log('[model requested]', call.name, call.arguments);

      // 4. Execute the tool chosen by the model through MCP.
      const result = await client.callTool({
        name: call.name,
        arguments: JSON.parse(call.arguments),
      });

      console.log('[MCP result]', result);

      results.push({
        type: 'function_call_output',
        call_id: call.call_id,
        output: JSON.stringify(result),
      });
    }

    // 5. Give the results back so the model can answer.
    response = await openai.responses.create({
      model: 'gpt-4.1-mini',
      instructions: 'Use the tools to check inventory. Never invent stock quantities.',
      previous_response_id: response.id,
      input: results,
      tools: modelTools,
    });
  }
} finally {
  await client.close();
}