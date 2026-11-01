import sqlite3

import pytest
from fastapi.testclient import TestClient

from gmail_order_processor.dashboard_api import create_app
from gmail_order_processor.inventory import ProductRepository
from gmail_order_processor.orders import OrderItemRequest, OrderRepository, OrderStatus


@pytest.fixture
def dashboard(tmp_path):
    database = tmp_path / "dashboard.sqlite3"
    products = ProductRepository(database)
    products.seed_demo_products()
    orders = OrderRepository(database)
    order = orders.create_draft_order(
        gmail_message_id="dashboard-message-1",
        customer_email="customer@example.com",
        delivery_address="Via Roma 10, Rome",
        products=[OrderItemRequest(sku="FUR-TBL-001", quantity=2)],
    )
    orders.save_draft_response(
        order.id,
        subject="We received your order request",
        body="We are reviewing your delivery request.",
    )
    app = create_app(
        orders,
        owner_email="owner@example.com",
        owner_password="local-owner-password-123",
        jwt_secret="test-dashboard-secret-key-with-at-least-32-bytes",
    )
    return TestClient(app), orders, products, order


def login(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/api/login",
        json={"email": "owner@example.com", "password": "local-owner-password-123"},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}
    


def test_dashboard_requires_owner_authentication_and_shows_saved_reply(dashboard):
    client, _orders, _products, order = dashboard

    assert client.get("/api/orders").status_code == 401
    assert client.post(f"/api/orders/{order.id}/approve").status_code == 401
    assert client.post(
        "/api/login",
        json={"email": "owner@example.com", "password": "wrong-password-123"},
    ).status_code == 401

    headers = login(client)
    response = client.get("/api/orders", headers=headers)

    assert response.status_code == 200
    record = response.json()[0]
    assert record["id"] == order.id
    assert record["status"] == "draft"
    assert record["response_subject"] == "We received your order request"
    assert record["response_body"] == "We are reviewing your delivery request."
    assert client.get("/api/me", headers=headers).json()["role"] == "owner"


def test_owner_approval_confirms_order_and_deducts_stock(dashboard):
    client, orders, products, order = dashboard
    headers = login(client)

    response = client.post(f"/api/orders/{order.id}/approve", headers=headers)

    assert response.status_code == 200
    assert response.json()["status"] == "confirmed"
    assert orders.get_order(order.id).status is OrderStatus.CONFIRMED
    assert products.get_by_sku("FUR-TBL-001").stock_quantity == 5
    assert client.get("/api/orders?status=draft", headers=headers).json() == []


def test_owner_rejection_cancels_draft_without_changing_stock(dashboard):
    client, orders, products, order = dashboard
    headers = login(client)

    response = client.post(f"/api/orders/{order.id}/reject", headers=headers)

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert orders.get_order(order.id).status is OrderStatus.CANCELLED
    assert products.get_by_sku("FUR-TBL-001").stock_quantity == 7


def test_dashboard_approval_rechecks_stock_and_keeps_order_draft_on_failure(dashboard):
    client, orders, products, order = dashboard
    headers = login(client)
    with sqlite3.connect(products.database_path) as connection:
        connection.execute(
            "UPDATE products SET stock_quantity = 1 WHERE sku = ?",
            ("FUR-TBL-001",),
        )

    response = client.post(f"/api/orders/{order.id}/approve", headers=headers)

    assert response.status_code == 409
    assert response.json()["detail"]["available"] == 1
    assert orders.get_order(order.id).status is OrderStatus.DRAFT
    assert products.get_by_sku("FUR-TBL-001").stock_quantity == 1


def test_dashboard_cannot_reject_an_already_confirmed_order(dashboard):
    client, orders, _products, order = dashboard
    headers = login(client)
    orders.confirm_order(order.id, authorized=True)

    response = client.post(f"/api/orders/{order.id}/reject", headers=headers)

    assert response.status_code == 409
    assert orders.get_order(order.id).status is OrderStatus.CONFIRMED
