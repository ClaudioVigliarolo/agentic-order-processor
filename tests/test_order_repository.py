import sqlite3

import pytest

from gmail_order_processor.inventory import ProductRepository
from gmail_order_processor.orders import (
    DuplicateOrderError,
    InsufficientStockError,
    OrderAuthorizationError,
    OrderItemRequest,
    OrderRepository,
    OrderStatus,
)


@pytest.fixture
def catalog(tmp_path):
    database = tmp_path / "orders.sqlite3"
    products = ProductRepository(database)
    products.seed_demo_products()
    return database, products, OrderRepository(database)


def create_draft(repository, *, message_id="gmail-001", sku="FUR-TBL-001", quantity=2):
    return repository.create_draft_order(
        gmail_message_id=message_id,
        customer_email="customer@example.com",
        delivery_address="Via Roma 10, Rome",
        products=[OrderItemRequest(sku=sku, quantity=quantity)],
    )


def test_create_draft_order_stores_customer_items_price_and_address(catalog):
    _database, _products, repository = catalog
    order = create_draft(repository)
    
    # 1. Fetch a fresh copy from the repository/database
    fetched_order = repository.get_order(order.id)
    
    # 2. Assert directly against the fetched database fields
    assert fetched_order.status is OrderStatus.DRAFT
    assert fetched_order.gmail_message_id == "gmail-001"
    assert fetched_order.customer_email == "customer@example.com"
    assert fetched_order.delivery_address == "Via Roma 10, Rome"
    assert fetched_order.items[0].product_name == "Casa Round Dining Table"
    assert fetched_order.items[0].sku == "FUR-TBL-001"
    assert fetched_order.items[0].quantity == 2
    assert fetched_order.items[0].unit_price_euros == 279
    assert fetched_order.total_euros == 558
    
    # 3. Keep the list checks to ensure queries work too
    assert repository.list_orders() == [fetched_order]
    assert repository.list_orders(OrderStatus.DRAFT) == [fetched_order]


def test_duplicate_gmail_message_cannot_create_second_order(catalog):
    _database, _products, repository = catalog
    first = create_draft(repository)

    with pytest.raises(DuplicateOrderError):
        create_draft(repository)

    assert repository.list_orders() == [first]
    assert repository.get_order_by_message_id("gmail-001") == first


def test_confirmation_requires_authorization_and_deducts_stock_transactionally(catalog):
    _database, products, repository = catalog
    order = create_draft(repository, quantity=3)
    initial_stock = products.get_by_sku("FUR-TBL-001").stock_quantity

    with pytest.raises(OrderAuthorizationError):
        repository.confirm_order(order.id)

    confirmed = repository.confirm_order(order.id, authorized=True)

    assert confirmed.status is OrderStatus.CONFIRMED
    assert products.get_by_sku("FUR-TBL-001").stock_quantity == initial_stock - 3


def test_cancellation_requires_authorization_and_restores_confirmed_stock(catalog):
    _database, products, repository = catalog
    order = create_draft(repository, quantity=3)
    initial_stock = products.get_by_sku("FUR-TBL-001").stock_quantity
    confirmed = repository.confirm_order(order.id, authorized=True)

    with pytest.raises(OrderAuthorizationError):
        repository.cancel_order(confirmed.id)

    cancelled = repository.cancel_order(confirmed.id, authorized=True)

    assert cancelled.status is OrderStatus.CANCELLED
    assert products.get_by_sku("FUR-TBL-001").stock_quantity == initial_stock


def test_confirmation_rechecks_stock_and_rolls_back_when_insufficient(catalog):
    database, products, repository = catalog
    order = create_draft(repository, quantity=3)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE products SET stock_quantity = 2 WHERE sku = ?",
            ("FUR-TBL-001",),
        )

    with pytest.raises(InsufficientStockError) as error:
        repository.confirm_order(order.id, authorized=True)

    assert error.value.available == 2
    assert repository.get_order(order.id).status is OrderStatus.DRAFT
    assert products.get_by_sku("FUR-TBL-001").stock_quantity == 2


def test_draft_rejects_duplicate_lines_whose_combined_quantity_exceeds_stock(catalog):
    _database, products, repository = catalog

    with pytest.raises(InsufficientStockError) as error:
        repository.create_draft_order(
            gmail_message_id="gmail-overstock",
            customer_email="customer@example.com",
            delivery_address="Via Roma 10, Rome",
            products=[
                OrderItemRequest(sku="FUR-TBL-001", quantity=4),
                OrderItemRequest(sku="FUR-TBL-001", quantity=4),
            ],
        )

    assert error.value.requested == 8
    assert products.get_by_sku("FUR-TBL-001").stock_quantity == 7
    assert repository.list_orders() == []


def test_cancelling_a_draft_does_not_change_stock(catalog):
    _database, products, repository = catalog
    order = create_draft(repository, quantity=3)
    initial_stock = products.get_by_sku("FUR-TBL-001").stock_quantity

    cancelled = repository.cancel_order(order.id, authorized=True)

    assert cancelled.status is OrderStatus.CANCELLED
    assert products.get_by_sku("FUR-TBL-001").stock_quantity == initial_stock


def test_order_schema_adds_response_columns_to_an_existing_database(tmp_path):
    database = tmp_path / "previous-orders.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TABLE orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                gmail_message_id TEXT NOT NULL UNIQUE,
                customer_email TEXT NOT NULL,
                delivery_address TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE order_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER NOT NULL,
                product_name TEXT NOT NULL,
                sku TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                unit_price_euros INTEGER NOT NULL
            )
            """
        )

    OrderRepository(database).initialize()

    with sqlite3.connect(database) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(orders)").fetchall()
        }
    assert {"response_subject", "response_body"} <= columns
