"""Local stdio MCP server exposing read-only product catalog tools."""

from __future__ import annotations

import json
from typing import Annotated

from dotenv import load_dotenv
from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from .inventory import Product, ProductRepository

load_dotenv()


def _product_data(product: Product) -> dict[str, int | str]:
    return {
        "id": product.id,
        "name": product.name,
        "sku": product.sku,
        "price_euros": product.price_euros,
        "currency": "EUR",
        "stock_quantity": product.stock_quantity,
    }


def create_inventory_mcp_server(
    repository: ProductRepository | None = None,
) -> MCPServer:
    """Create an MCP server; repository injection keeps its tools testable."""
    catalog = repository or ProductRepository()
    server = MCPServer("inventory-catalog")

    @server.tool(
        title="Search products",
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
    )
    def search_products(
        query: Annotated[
            str,
            Field(min_length=1, max_length=100, description="Product name or SKU substring."),
        ],
        limit: Annotated[
            int,
            Field(ge=1, le=50, description="Maximum number of products to return."),
        ] = 10,
    ) -> str:
        """Search furniture products by name or SKU; returns euro price and stock."""
        products = catalog.search_products(query, limit)
        return json.dumps(
            {"products": [_product_data(product) for product in products]},
            ensure_ascii=False,
        )

    @server.tool(
        title="Check stock",
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
    )
    def check_stock(
        sku: Annotated[str, Field(min_length=1, max_length=50, description="Exact product SKU.")],
        quantity: Annotated[
            int,
            Field(ge=1, le=10000, description="Quantity requested for this stock check."),
        ] = 1,
    ) -> str:
        """Check whether a product SKU has the requested quantity in stock."""
        product = catalog.get_by_sku(sku.strip())
        if product is None:
            result = {
                "found": False,
                "sku": sku,
                "requested_quantity": quantity,
                "in_stock": False,
                "available_quantity": None,
                "product": None,
            }
        else:
            result = {
                "found": True,
                "sku": product.sku,
                "requested_quantity": quantity,
                "in_stock": product.stock_quantity >= quantity,
                "available_quantity": product.stock_quantity,
                "product": _product_data(product),
            }
        return json.dumps(result, ensure_ascii=False)

    return server


mcp = create_inventory_mcp_server()


def main() -> None:
    """Run the MCP server over stdio for a local MCP host to launch."""
    mcp.run()


if __name__ == "__main__":
    main()
