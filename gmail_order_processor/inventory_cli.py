"""Initialize and inspect the local fictional SQLite product catalog."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from .inventory import ProductRepository


def cervino() -> int:
    parser = argparse.ArgumentParser(description="Manage the local SQLite product catalog.")
    parser.add_argument(
        "--db",
        type=Path,
        default=os.getenv("PRODUCT_DB_PATH", "data/products.sqlite3"),
        help="SQLite database path (default: data/products.sqlite3)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("seed", help="create the table and insert fictional demo products")
    subparsers.add_parser("list", help="list products in the database")
    args = parser.parse_args()

    repository = ProductRepository(args.db)
    if args.command == "seed":
        inserted = repository.seed_demo_products()
        print(f"Database: {repository.database_path}")
        print(f"Inserted {inserted} demo products ({len(repository.list_products())} total).")
        return 0

    if args.command == "list":
        products = repository.list_products()
        if not products:
            print("No products found. Run `python -m gmail_order_processor.inventory_cli seed` first.")
            return 0
        print(f"{'ID':>3}  {'SKU':<14} {'PRICE (€)':>11} {'STOCK':>5}  NAME")
        for product in products:
            price = f"€{product.price_euros:,}"
            print(
                f"{product.id:>3}  {product.sku:<14} "
                f"{price:>11} {product.stock_quantity:>5}  {product.name}"
            )
        return 0


if __name__ == "__main__":
    cervino()
