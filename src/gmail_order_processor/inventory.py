"""SQLite product catalog and seed data for local development."""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


@dataclass(frozen=True)
class Product:
    id: int
    name: str
    sku: str
    price_euros: int
    stock_quantity: int


# Fictional catalog data. Prices are whole euros.
DEMO_PRODUCTS: tuple[tuple[str, str, int, int], ...] = (
    ("Nordic Oak Dining Chair", "FUR-CHR-001", 129, 24),
    ("Linea Upholstered Chair", "FUR-CHR-002", 190, 12),
    ("Metro Mesh Office Chair", "FUR-CHR-003", 219, 18),
    ("Arco Height Adjustable Desk", "FUR-DSK-001", 489, 8),
    ("Forma Oak Writing Desk", "FUR-DSK-002", 329, 10),
    ("Studio Compact Desk", "FUR-DSK-003", 199, 15),
    ("Casa Round Dining Table", "FUR-TBL-001", 279, 7),
    ("Forum Folding Table", "FUR-TBL-002", 89, 32),
    ("Meeting Room Conference Table", "FUR-TBL-003", 699, 4),
    ("Cloud Two Seat Sofa", "FUR-SOF-001", 799, 5),
    ("Harbor Three Seat Sofa", "FUR-SOF-002", 1099, 3),
    ("Elm Five Shelf Bookcase", "FUR-STG-001", 249, 9),
    ("Workshop Storage Cabinet", "FUR-STG-002", 359, 6),
    ("Roundabout Bar Stool", "FUR-STL-001", 99, 20),
    ("Lounge Low Coffee Table", "FUR-TBL-004", 159, 11),
)


class ProductRepository:
    """Small SQLite repository for product catalog reads and demo seeding."""

    def __init__(self, database_path: str | Path | None = None) -> None:
        configured_path = database_path or os.getenv(
            "PRODUCT_DB_PATH", "data/products.sqlite3"
        )
        self.database_path = Path(configured_path).expanduser()

    def _connect(self) -> sqlite3.Connection:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        """Create the products table if it does not already exist."""
        with self._connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS products (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL CHECK (length(trim(name)) > 0),
                    sku TEXT NOT NULL UNIQUE CHECK (length(trim(sku)) > 0),
                    price_euros INTEGER NOT NULL CHECK (price_euros >= 0),
                    stock_quantity INTEGER NOT NULL CHECK (stock_quantity >= 0)
                )
                """
            )

    def seed_demo_products(self) -> int:
        """Insert fictional products that are not already present by SKU.

        Returns the number of new rows inserted. Existing product rows are left intact.
        """
        self.initialize()
        with self._connection() as connection:
            before = connection.total_changes
            connection.executemany(
                """
                INSERT OR IGNORE INTO products (name, sku, price_euros, stock_quantity)
                VALUES (?, ?, ?, ?)
                """,
                DEMO_PRODUCTS,
            )
            return connection.total_changes - before

    def list_products(self) -> list[Product]:
        self.initialize()
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT id, name, sku, price_euros, stock_quantity
                FROM products
                ORDER BY id
                """
            ).fetchall()
        return [self._to_product(row) for row in rows]

    def get_by_sku(self, sku: str) -> Product | None:
        self.initialize()
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT id, name, sku, price_euros, stock_quantity
                FROM products
                WHERE sku = ?
                """,
                (sku,),
            ).fetchone()
        return self._to_product(row) if row is not None else None

    @staticmethod
    def _to_product(row: sqlite3.Row) -> Product:
        return Product(
            id=int(row["id"]),
            name=str(row["name"]),
            sku=str(row["sku"]),
            price_euros=int(row["price_euros"]),
            stock_quantity=int(row["stock_quantity"]),
        )
