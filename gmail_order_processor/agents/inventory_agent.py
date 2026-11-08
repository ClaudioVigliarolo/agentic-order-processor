"""Deterministic product matching and stock lookup for new orders."""

from __future__ import annotations

import re
from difflib import SequenceMatcher
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

    @classmethod
    def _tokens(cls, value: str) -> frozenset[str]:
        """Normalize common English plurals without changing product identity."""
        tokens = cls._normalize(value).split()
        normalized = []
        for token in tokens:
            if len(token) > 4 and token.endswith("ies"):
                token = token[:-3] + "y"
            elif len(token) > 4 and token.endswith(("ches", "shes", "sses", "xes", "zes")):
                token = token[:-2]
            elif len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
                token = token[:-1]
            normalized.append(token)
        return frozenset(normalized)

    @classmethod
    def _matching_products(cls, requested_name: str, products: list[Product]) -> list[Product]:
        """Return the unique best catalog match, allowing safe name variations."""
        requested = cls._tokens(requested_name)
        if not requested:
            return []
        scored: list[tuple[float, Product]] = []
        request_text = " ".join(sorted(requested))
        for product in products:
            product_tokens = cls._tokens(product.name)
            sku_tokens = cls._tokens(product.sku)
            if requested == product_tokens or requested == sku_tokens:
                score = 1.0
            elif len(requested) >= 2 and requested < product_tokens:
                # A shorter, specific phrase such as “dining table” may match
                # a catalog name, but all candidates remain subject to tie checks.
                score = 0.94
            else:
                product_text = " ".join(sorted(product_tokens))
                score = SequenceMatcher(None, request_text, product_text).ratio()
                if score < 0.88:
                    continue
            scored.append((score, product))
        if not scored:
            return []
        best_score = max(score for score, _ in scored)
        return [product for score, product in scored if score == best_score]

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

            catalog = self.repository.list_products()
            matches = self._matching_products(requested.name, catalog)

            if len(matches) != 1:
                status = (
                    InventoryItemStatus.NOT_FOUND
                    if not matches
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

            product = matches[0]
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
