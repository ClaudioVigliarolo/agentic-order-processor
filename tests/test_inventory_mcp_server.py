import asyncio
import json

from mcp import Client

from gmail_order_processor.inventory import ProductRepository
from gmail_order_processor.inventory_mcp_server import create_inventory_mcp_server


def _call_tool(server, name, arguments):
    async def call():
        async with Client(server, raise_exceptions=True) as client:
            return await client.call_tool(name, arguments)

    return asyncio.run(call())


def test_search_products_tool_returns_catalog_matches(tmp_path):
    repository = ProductRepository(tmp_path / "products.sqlite3")
    repository.seed_demo_products()
    server = create_inventory_mcp_server(repository)

    result = _call_tool(server, "search_products", {"query": "blue"})
    payload = json.loads(result.content[0].text)

    assert result.is_error is False
    assert payload["products"] == []
    result = _call_tool(server, "search_products", {"query": "chair"})
    payload = json.loads(result.content[0].text)
    assert len(payload["products"]) == 3
    assert payload["products"][0]["currency"] == "EUR"


def test_check_stock_tool_reports_requested_and_available_quantity(tmp_path):
    repository = ProductRepository(tmp_path / "products.sqlite3")
    repository.seed_demo_products()
    server = create_inventory_mcp_server(repository)

    result = _call_tool(
        server,
        "check_stock",
        {"sku": "FUR-CHR-001", "quantity": 30},
    )
    payload = json.loads(result.content[0].text)
    assert payload["found"] is True
    assert payload["in_stock"] is False
    assert payload["available_quantity"] == 24
    assert payload["product"]["price_euros"] == 129


def test_check_stock_tool_handles_unknown_sku(tmp_path):
    repository = ProductRepository(tmp_path / "products.sqlite3")
    repository.seed_demo_products()
    server = create_inventory_mcp_server(repository)

    result = _call_tool(server, "check_stock", {"sku": "UNKNOWN-1"})
    payload = json.loads(result.content[0].text)
    assert payload["found"] is False
    assert payload["product"] is None
