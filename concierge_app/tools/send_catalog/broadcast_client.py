"""Build catalog message objects for Weni broadcast."""

from typing import Any, List

from weni.broadcasts.messages import (
    WeniWebChatCatalog,
    WebChatProduct,
    WebChatProductGroup,
    WhatsAppCatalog,
    WhatsAppProductGroup,
)

from entities import Product


def build_web_catalog(
    categories: List[dict],
    seller_id: str,
    message: str,
    header_text: str = "",
) -> WeniWebChatCatalog:
    """Build a WeniWebChatCatalog message from enriched product categories."""
    product_groups: List[WebChatProductGroup] = []
    for item in categories:
        cat_name = (item.get("category_name") or "Produtos").strip()
        products = item.get("products") or []
        if not cat_name or not products:
            continue
        product_groups.append(
            WebChatProductGroup(
                product=cat_name,
                product_retailer_info=[
                    _to_web_product(p, seller_id) for p in products if p
                ],
            )
        )

    kwargs: dict[str, Any] = {"text": message, "products": product_groups}
    if header_text:
        kwargs["header"] = header_text
    return WeniWebChatCatalog(**kwargs)


def build_whatsapp_catalog(
    entries: List[tuple],
    seller_id: str,
    message: str,
    header_text: str = "",
) -> WhatsAppCatalog:
    """Build a WhatsAppCatalog message from (category, raw_sku_id) entries."""
    category_skus: dict[str, list[str]] = {}
    order: list[str] = []

    for category_name, raw_id in entries:
        retailer_id = f"{raw_id}#{seller_id}"
        if category_name not in category_skus:
            category_skus[category_name] = []
            order.append(category_name)
        category_skus[category_name].append(retailer_id)

    product_groups = [
        WhatsAppProductGroup(product=cat, product_retailer_ids=category_skus[cat])
        for cat in order
    ]

    kwargs: dict[str, Any] = {"text": message, "products": product_groups}
    if header_text:
        kwargs["header"] = header_text
    return WhatsAppCatalog(**kwargs)


def _to_web_product(product: Product, seller_id: str) -> WebChatProduct:
    sku_name = product.sku_name or product.name or "Produto"
    price_str = product.list_price_formatted or product.sale_price_formatted

    kwargs: dict[str, Any] = {
        "name": sku_name,
        "price": price_str,
        "retailer_id": product.sku_id,
        "seller_id": seller_id,
    }

    if product.description:
        kwargs["description"] = product.description
    if product.image:
        kwargs["image"] = product.image
    if product.currency:
        kwargs["currency"] = product.currency

    if (
        product.sale_price is not None
        and product.list_price is not None
        and product.sale_price < product.list_price
        and product.sale_price_formatted
    ):
        kwargs["sale_price"] = product.sale_price_formatted

    return WebChatProduct(**kwargs)
