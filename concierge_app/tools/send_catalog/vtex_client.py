"""VTEX Intelligent Search client — fetch and parse products by SKU IDs.

Uses the ``/api/io/_v/api/intelligent-search/product_search/`` endpoint
to retrieve product data in a single batch request, then maps the
response into ``Product`` entities ready for catalog display.
"""

import time
from typing import Optional, List, Dict

import requests

from entities import Product
from helpers import clean_image_url, clean_html


def raw_sku_id(sku_id: str) -> str:
    """Strip the optional seller suffix from a SKU identifier.

    Example:
        >>> raw_sku_id("103025#1")
        '103025'
        >>> raw_sku_id("103025")
        '103025'
    """
    return sku_id.split("#")[0] if "#" in sku_id else sku_id


def fetch_products_batch(
    sku_ids: List[str], base_url: str, store_url: str,
) -> Dict[str, Product]:
    """Fetch multiple products from VTEX Intelligent Search in one HTTP call.

    Builds a ``sku.id:id1;id2;id3`` query so all SKUs are resolved in a
    single round-trip.

    Args:
        sku_ids: SKU identifiers (may contain seller suffixes like ``"123#1"``).
        base_url: VTEX store base URL (e.g. ``"https://account.myvtex.com"``).
        store_url: Public storefront URL used to build product deep-links.

    Returns:
        A dict mapping each raw SKU ID to its enriched ``Product``, keyed
        only for SKUs that were found and successfully parsed.
        Returns an empty dict on network or parsing failures.
    """
    raw_ids = [raw_sku_id(sid) for sid in sku_ids]
    query = ";".join(raw_ids)
    url = (
        f"{base_url}/api/io/_v/api/intelligent-search/product_search/"
        f"?query=sku.id:{query}&simulationBehavior=default"
    )
    print("url_intelligent_search: ", url)
    t0 = time.time()
    try:
        resp = requests.get(url, timeout=15)
        t_http = (time.time() - t0) * 1000
        print(f"[⏱ HTTP GET intelligent-search] {t_http:.0f}ms — status={resp.status_code}, {len(raw_ids)} SKUs requested")
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[⏱ HTTP GET intelligent-search] {(time.time()-t0)*1000:.0f}ms — FAILED: {e}")
        return {}

    t_parse = time.time()
    result: Dict[str, Product] = {}
    id_set = set(raw_ids)
    for product in data.get("products", []):
        for item in product.get("items", []):
            item_id = item.get("itemId", "")
            if item_id in id_set and item_id not in result:
                p = _build_product(product, item, item_id, store_url)
                if p:
                    result[item_id] = p
    print(f"[⏱ vtex:parse_response] {(time.time()-t_parse)*1000:.0f}ms — {len(result)}/{len(raw_ids)} products matched")
    return result


def _build_product(
    product: dict, item: dict, sku_id: str, store_url: str,
) -> Optional[Product]:
    """Transform raw VTEX product + item JSON into a ``Product`` entity.

    Extracts pricing, the first valid image, variation labels, and
    material/composition from specification groups.

    Args:
        product: Top-level product object from the Intelligent Search response.
        item: The specific SKU item nested under *product*.
        sku_id: Raw SKU identifier (no seller suffix).
        store_url: Storefront base URL for building the product deep-link.

    Returns:
        A populated ``Product``, or ``None`` if essential data is missing.
    """
    sale_price, list_price = _extract_prices(item)

    link = product.get("link", "")
    product_link = f"{store_url}{link}?skuId={sku_id}" #TODO solve problem of string encoding

    image = ""
    for img in item.get("images", []):
        img_url = img.get("imageUrl", "")
        if img_url and any(ext in img_url.lower() for ext in (".jpg", ".jpeg", ".png")):
            image = clean_image_url(img_url)
            break

    variations = []
    for var in item.get("variations", []):
        vname, vvals = var.get("name", ""), var.get("values", [])
        if vname and vvals:
            variations.append(f"{vname}: {', '.join(vvals)}")

    material = ""
    for group in product.get("specificationGroups", []):
        for spec in group.get("specifications", []):
            if any(k in spec.get("name", "").lower() for k in ("material", "composição", "tecido")):
                material = ", ".join(spec.get("values", []))
                break
        if material:
            break

    name = item.get("nameComplete", product.get("productName", ""))
    return Product(
        sku_id=sku_id,
        name=name,
        brand=product.get("brand", ""),
        image=image,
        variations=variations,
        material=material,
        sku_name=name,
        product_link=product_link,
        description=clean_html(product.get("description", "")),
        list_price=list_price,
        sale_price=sale_price,
    )


def _extract_prices(item: dict) -> tuple:
    """Return ``(sale_price, list_price)`` from the best available seller.

    Delegates seller selection to ``_pick_seller`` and sale-price
    resolution to ``_pick_sale_price``.
    """
    seller = _pick_seller(item.get("sellers") or [])
    if not seller:
        return (None, None)
    offer = seller.get("commertialOffer") or {}
    return (_pick_sale_price(offer.get("Installments") or []), offer.get("ListPrice"))


def _pick_seller(sellers: list) -> Optional[dict]:
    """Choose the best seller for pricing from the sellers array.

    Priority order:
        1. Default seller with stock (``sellerDefault == True``).
        2. Any seller with stock (``AvailableQuantity > 0``).
        3. First seller in the list (fallback, may have zero stock).
    """
    for s in sellers:
        if s.get("sellerDefault") and (s.get("commertialOffer") or {}).get("AvailableQuantity", 0) > 0:
            return s
    for s in sellers:
        if (s.get("commertialOffer") or {}).get("AvailableQuantity", 0) > 0:
            return s
    return sellers[0] if sellers else None


def _pick_sale_price(installments: list) -> Optional[float]:
    """Select the best sale price from the installments array.

    Priority order:
        1. Pix payment (best discount).
        2. Visa single-installment (à vista).
        3. First installment entry (fallback).

    Returns:
        The price as a float, or ``None`` if the list is empty.
    """
    for inst in installments:
        if "pix" in (inst.get("PaymentSystemName") or "").lower():
            return inst.get("Value")
    for inst in installments:
        if inst.get("PaymentSystemName") == "Visa" and inst.get("NumberOfInstallments") == 1:
            return inst.get("Value")
    return installments[0].get("Value") if installments else None
