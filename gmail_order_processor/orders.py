"""SQLite persistence and explicit state changes for customer orders."""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Iterator

from pydantic import BaseModel, Field

from .inventory import ProductRepository


class OrderStatus(str, Enum):
    DRAFT = "draft"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"


class OrderRepositoryError(Exception):
    """Base class for order persistence and state errors."""


class DuplicateOrderError(OrderRepositoryError):
    """An order already exists for the Gmail message ID."""


class OrderNotFoundError(OrderRepositoryError):
    """The requested order does not exist."""


class OrderAuthorizationError(OrderRepositoryError):
    """A state-changing operation requires explicit authorization."""


class InvalidOrderStateError(OrderRepositoryError):
    """The requested state change is not valid for the current order status."""


class ProductNotFoundError(OrderRepositoryError):
    """An order line refers to a product SKU that is not in the catalog."""


class InsufficientStockError(OrderRepositoryError):
    """The catalog does not have enough stock for an order line."""

    def __init__(self, sku: str, requested: int, available: int) -> None:
        self.sku = sku
        self.requested = requested
        self.available = available
        super().__init__(
            f"Insufficient stock for {sku}: requested {requested}, available {available}."
        )


class OrderItemRequest(BaseModel):
    sku: str = Field(min_length=1)
    quantity: int = Field(ge=1)


class OrderItem(BaseModel):
    product_name: str
    sku: str
    quantity: int = Field(ge=1)
    unit_price_euros: int = Field(ge=0)


class Order(BaseModel):
    id: int
    gmail_message_id: str
    customer_email: str
    delivery_address: str
    status: OrderStatus
    created_at: str
    updated_at: str
    response_subject: str | None = None
    response_body: str | None = None
    items: list[OrderItem]

    @property
    def total_euros(self) -> int:
        return sum(item.quantity * item.unit_price_euros for item in self.items)


class OrderRepository:
    """Store orders in the same SQLite database as the product catalog."""

    def __init__(self, database_path: str | Path | None = None) -> None:
        configured_path = database_path or os.getenv(
            "PRODUCT_DB_PATH", "data/products.sqlite3"
        )
        self.database_path = Path(configured_path).expanduser()

    def _connect(self) -> sqlite3.Connection:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        """Create order tables and ensure the shared product table exists."""
        ProductRepository(self.database_path).initialize()
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    gmail_message_id TEXT NOT NULL UNIQUE,
                    customer_email TEXT NOT NULL CHECK (length(trim(customer_email)) > 0),
                    delivery_address TEXT NOT NULL CHECK (length(trim(delivery_address)) > 0),
                    status TEXT NOT NULL CHECK (status IN ('draft', 'confirmed', 'cancelled')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    response_subject TEXT,
                    response_body TEXT
                );

                CREATE TABLE IF NOT EXISTS order_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
                    product_name TEXT NOT NULL,
                    sku TEXT NOT NULL,
                    quantity INTEGER NOT NULL CHECK (quantity > 0),
                    unit_price_euros INTEGER NOT NULL CHECK (unit_price_euros >= 0)
                );

                CREATE INDEX IF NOT EXISTS idx_orders_status_created
                    ON orders(status, created_at);
                """
            )
            columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(orders)").fetchall()
            }
            if "response_subject" not in columns:
                connection.execute("ALTER TABLE orders ADD COLUMN response_subject TEXT")
            if "response_body" not in columns:
                connection.execute("ALTER TABLE orders ADD COLUMN response_body TEXT")

    def create_draft_order(
        self,
        *,
        gmail_message_id: str,
        customer_email: str,
        delivery_address: str,
        products: list[OrderItemRequest | dict[str, object]],
    ) -> Order:
        """Create a draft, snapshotting current catalog names/prices without reserving stock."""
        self.initialize()
        gmail_message_id = gmail_message_id.strip()
        customer_email = customer_email.strip()
        delivery_address = delivery_address.strip()
        if not gmail_message_id or not customer_email or not delivery_address:
            raise ValueError("Gmail message ID, customer email, and delivery address are required")
        requests = [OrderItemRequest.model_validate(item) for item in products]
        if not requests:
            raise ValueError("A draft order must contain at least one product")

        timestamp = _utc_now()
        with self._connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                cursor = connection.execute(
                    """
                    INSERT INTO orders (
                        gmail_message_id, customer_email, delivery_address, status,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(gmail_message_id) DO NOTHING
                    """,
                    (
                        gmail_message_id,
                        customer_email,
                        delivery_address,
                        OrderStatus.DRAFT.value,
                        timestamp,
                        timestamp,
                    ),
                )
                if cursor.rowcount == 0:
                    raise DuplicateOrderError(
                        f"An order already exists for Gmail message {gmail_message_id}."
                    )
                order_id = int(cursor.lastrowid)

                requested_totals: dict[str, tuple[str, int]] = {}
                for item in requests:
                    lookup_sku = item.sku.strip()
                    key = lookup_sku.casefold()
                    previous_sku, previous_quantity = requested_totals.get(
                        key, (lookup_sku, 0)
                    )
                    requested_totals[key] = (
                        previous_sku,
                        previous_quantity + item.quantity,
                    )

                catalog_products: dict[str, sqlite3.Row] = {}
                for key, (sku, total_quantity) in requested_totals.items():
                    product = connection.execute(
                        """
                        SELECT name, sku, price_euros, stock_quantity
                        FROM products WHERE sku = ? COLLATE NOCASE
                        """,
                        (sku,),
                    ).fetchone()
                    if product is None:
                        raise ProductNotFoundError(f"Product SKU not found: {sku}")
                    if int(product["stock_quantity"]) < total_quantity:
                        raise InsufficientStockError(
                            str(product["sku"]),
                            total_quantity,
                            int(product["stock_quantity"]),
                        )
                    catalog_products[key] = product

                for item in requests:
                    product = catalog_products[item.sku.strip().casefold()]
                    connection.execute(
                        """
                        INSERT INTO order_items (
                            order_id, product_name, sku, quantity, unit_price_euros
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            order_id,
                            str(product["name"]),
                            str(product["sku"]),
                            item.quantity,
                            int(product["price_euros"]),
                        ),
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            return self._get_order_on_connection(connection, order_id)

    def get_order(self, order_id: int) -> Order | None:
        self.initialize()
        with self._connection() as connection:
            return self._get_order_on_connection(connection, order_id)

    def get_order_by_message_id(self, gmail_message_id: str) -> Order | None:
        """Find an existing order by its source Gmail message ID."""
        self.initialize()
        with self._connection() as connection:
            row = connection.execute(
                "SELECT id FROM orders WHERE gmail_message_id = ?",
                (gmail_message_id,),
            ).fetchone()
            return (
                self._get_order_on_connection(connection, int(row["id"]))
                if row is not None
                else None
            )

    def list_orders(self, status: OrderStatus | str | None = None) -> list[Order]:
        self.initialize()
        with self._connection() as connection:
            if status is None:
                rows = connection.execute(
                    "SELECT id FROM orders ORDER BY created_at, id"
                ).fetchall()
            else:
                selected_status = OrderStatus(status).value
                rows = connection.execute(
                    "SELECT id FROM orders WHERE status = ? ORDER BY created_at, id",
                    (selected_status,),
                ).fetchall()
            return [
                self._get_order_on_connection(connection, int(row["id"]))
                for row in rows
            ]

    def save_draft_response(
        self, order_id: int, *, subject: str, body: str
    ) -> Order:
        """Persist an AI reply draft on a still-pending order; never send it."""
        subject = subject.strip()
        body = body.strip()
        if not subject or not body:
            raise ValueError("Response subject and body are required")
        self.initialize()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT status FROM orders WHERE id = ?", (order_id,)
                ).fetchone()
                if row is None:
                    raise OrderNotFoundError(f"Order not found: {order_id}")
                if row["status"] != OrderStatus.DRAFT.value:
                    raise InvalidOrderStateError(
                        "A reply draft can only be saved while the order is a draft."
                    )
                connection.execute(
                    """
                    UPDATE orders
                    SET response_subject = ?, response_body = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (subject, body, _utc_now(), order_id),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            saved = self._get_order_on_connection(connection, order_id)
            assert saved is not None
            return saved

    def confirm_order(self, order_id: int, *, authorized: bool = False) -> Order:
        """Explicitly confirm a draft and deduct all stock in one transaction."""
        if authorized is not True:
            raise OrderAuthorizationError("Confirmation requires authorized=True")
        self.initialize()
        with self._connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                order = self._get_order_on_connection(connection, order_id)
                if order is None:
                    raise OrderNotFoundError(f"Order not found: {order_id}")
                if order.status is not OrderStatus.DRAFT:
                    raise InvalidOrderStateError(
                        f"Cannot confirm an order with status '{order.status.value}'."
                    )

                # Check every line before changing any stock; BEGIN IMMEDIATE keeps
                # competing writers from consuming the same quantities meanwhile.
                for item in order.items:
                    product = connection.execute(
                        "SELECT stock_quantity FROM products WHERE sku = ? COLLATE NOCASE",
                        (item.sku,),
                    ).fetchone()
                    if product is None:
                        raise ProductNotFoundError(f"Product SKU not found: {item.sku}")
                    available = int(product["stock_quantity"])
                    if available < item.quantity:
                        raise InsufficientStockError(item.sku, item.quantity, available)

                for item in order.items:
                    updated = connection.execute(
                        """
                        UPDATE products SET stock_quantity = stock_quantity - ?
                        WHERE sku = ? COLLATE NOCASE AND stock_quantity >= ?
                        """,
                        (item.quantity, item.sku, item.quantity),
                    )
                    if updated.rowcount != 1:
                        raise InsufficientStockError(item.sku, item.quantity, 0)

                connection.execute(
                    "UPDATE orders SET status = ?, updated_at = ? WHERE id = ?",
                    (OrderStatus.CONFIRMED.value, _utc_now(), order_id),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            confirmed = self._get_order_on_connection(connection, order_id)
            assert confirmed is not None
            return confirmed

    def cancel_order(self, order_id: int, *, authorized: bool = False) -> Order:
        """Explicitly cancel a draft, or cancel a confirmed order and restore its stock."""
        if authorized is not True:
            raise OrderAuthorizationError("Cancellation requires authorized=True")
        self.initialize()
        with self._connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                order = self._get_order_on_connection(connection, order_id)
                if order is None:
                    raise OrderNotFoundError(f"Order not found: {order_id}")
                if order.status is OrderStatus.CANCELLED:
                    raise InvalidOrderStateError("Order is already cancelled.")

                if order.status is OrderStatus.CONFIRMED:
                    for item in order.items:
                        updated = connection.execute(
                            """
                            UPDATE products SET stock_quantity = stock_quantity + ?
                            WHERE sku = ? COLLATE NOCASE
                            """,
                            (item.quantity, item.sku),
                        )
                        if updated.rowcount != 1:
                            raise ProductNotFoundError(f"Product SKU not found: {item.sku}")

                connection.execute(
                    "UPDATE orders SET status = ?, updated_at = ? WHERE id = ?",
                    (OrderStatus.CANCELLED.value, _utc_now(), order_id),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            cancelled = self._get_order_on_connection(connection, order_id)
            assert cancelled is not None
            return cancelled

    @staticmethod
    def _get_order_on_connection(
        connection: sqlite3.Connection, order_id: int
    ) -> Order | None:
        row = connection.execute(
            "SELECT * FROM orders WHERE id = ?", (order_id,)
        ).fetchone()
        if row is None:
            return None
        item_rows = connection.execute(
            """
            SELECT product_name, sku, quantity, unit_price_euros
            FROM order_items WHERE order_id = ? ORDER BY id
            """,
            (order_id,),
        ).fetchall()
        return Order(
            id=int(row["id"]),
            gmail_message_id=str(row["gmail_message_id"]),
            customer_email=str(row["customer_email"]),
            delivery_address=str(row["delivery_address"]),
            status=OrderStatus(str(row["status"])),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            response_subject=(
                str(row["response_subject"])
                if row["response_subject"] is not None
                else None
            ),
            response_body=(
                str(row["response_body"]) if row["response_body"] is not None else None
            ),
            items=[
                OrderItem(
                    product_name=str(item["product_name"]),
                    sku=str(item["sku"]),
                    quantity=int(item["quantity"]),
                    unit_price_euros=int(item["unit_price_euros"]),
                )
                for item in item_rows
            ],
        )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
