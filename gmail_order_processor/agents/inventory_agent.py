"""Deterministic product matching and stock lookup for new orders."""

from __future__ import annotations

import re
from enum import Enum

from pydantic import BaseModel, Field

from .order_extractor import OrderExtraction
from ..inventory import Product, ProductRepository


class InventoryItemStatus(str, Enum):
    MISSING_QUANTITY = "missing_quantity"
    NOT_FOUND = "not_found"
    AMBIGUOUS = "ambiguous"
    VARIANT_REVIEW = "variant_review"
    AVAILABLE = "available"
    INSUFFICIENT_STOCK = "insufficient_stock"


class InventoryItemResult(BaseModel):
    requested_name: str
    quantity: int
    status: InventoryItemStatus
    product_name: str | None = None
    sku: str | None = None
    price_euros: int | None = None
    available_quantity: int | None = None
    in_stock: bool | None = None
    note: str | None = None


class InventoryCheck(BaseModel):
    items: list[InventoryItemResult] = Field(default_factory=list)


class InventoryAgent:
    """Match extracted items to exact catalog entries and check their stock."""

    def __init__(self, repository: ProductRepository | None = None) -> None:
        self.repository = repository or ProductRepository()

    @staticmethod
    def _normalize(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()

    def check(self, extraction: OrderExtraction) -> InventoryCheck:
        results: list[InventoryItemResult] = []
        for requested in extraction.products:
            if requested.quantity is None:
                results.append(InventoryItemResult(
                    requested_name=requested.name,
                    quantity=0,
                    status=InventoryItemStatus.MISSING_QUANTITY,
                    note="A quantity is needed before checking stock.",
                ))
                continue

            search_name = requested.name.strip()
            candidates = self.repository.search_products(search_name, limit=50)
            if not candidates and search_name.casefold().endswith("s"):
                candidates = self.repository.search_products(search_name[:-1], limit=50)
            normalized_name = self._normalize(requested.name)
            exact: list[Product] = []
            for product in candidates:
                normalized_product = self._normalize(product.name)
                normalized_sku = self._normalize(product.sku)
                singular_request = normalized_name[:-1] if normalized_name.endswith("s") else normalized_name
                singular_product = normalized_product[:-1] if normalized_product.endswith("s") else normalized_product
                if normalized_sku == normalized_name or singular_product == singular_request:
                    exact.append(product)

            if len(exact) != 1:
                status = (
                    InventoryItemStatus.NOT_FOUND
                    if not candidates
                    else InventoryItemStatus.AMBIGUOUS
                )
                results.append(InventoryItemResult(
                    requested_name=requested.name,
                    quantity=requested.quantity,
                    status=status,
                    note=(
                        "No matching catalog product was found."
                        if status is InventoryItemStatus.NOT_FOUND
                        else "Several catalog products could match; human review is needed."
                    ),
                ))
                continue

            product = exact[0]
            variants = requested.variants
            if variants.color or variants.size or variants.model:
                results.append(InventoryItemResult(
                    requested_name=requested.name,
                    quantity=requested.quantity,
                    status=InventoryItemStatus.VARIANT_REVIEW,
                    product_name=product.name,
                    sku=product.sku,
                    price_euros=product.price_euros,
                    available_quantity=product.stock_quantity,
                    in_stock=None,
                    note="The catalog has no variant data, so a person must verify the requested variant.",
                ))
                continue

            available = product.stock_quantity >= requested.quantity
            results.append(InventoryItemResult(
                requested_name=requested.name,
                quantity=requested.quantity,
                status=(
                    InventoryItemStatus.AVAILABLE
                    if available
                    else InventoryItemStatus.INSUFFICIENT_STOCK
                ),
                product_name=product.name,
                sku=product.sku,
                price_euros=product.price_euros,
                available_quantity=product.stock_quantity,
                in_stock=available,
            ))
        return InventoryCheck(items=results)
