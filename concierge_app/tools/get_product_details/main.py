"""Get Product Details Tool — Weni Tool entry point.

Fetches detailed information for a list of SKU IDs from VTEX
Intelligent Search.  Returns rich data the agent can use to answer
specific questions about products: variations, specification groups,
images, materials, and pricing.

This tool complements ``search_products`` (which returns a lightweight
summary) by providing the full product profile on demand.
"""

import html
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional, Tuple

import requests

from weni import Tool
from weni.context import Context
from weni.responses import TextResponse

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

RETAILSETUP_URL = "https://retailsetup.weni.ai"
SEARCH_ENDPOINT = "/api/io/_v/api/intelligent-search/product_search/"


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------

class GetProductDetails(Tool):
    """Fetch rich product data from VTEX for a given list of SKU IDs.

    Resolves the VTEX account, fetches products in a single batch
    request via Intelligent Search, and returns detailed information
    including variations, specification groups, images, and pricing.
    """

    def execute(self, context: Context) -> TextResponse:
        """Parse SKU IDs, resolve the VTEX account, and fetch product details.

        Reads ``sku_ids`` from tool parameters.  Resolves the VTEX
        account and storefront URL in parallel, then batch-fetches all
        products in a single Intelligent Search call.

        Returns:
            ``TextResponse`` with a ``products`` dict keyed by raw SKU ID,
            or an ``error`` payload when validation or resolution fails.
        """
        raw_sku_ids = context.parameters.get("sku_ids", [])
        auth_token = context.project.get("auth_token", "")

        sku_ids = _parse_sku_ids(raw_sku_ids)
        if not sku_ids:
            return TextResponse(data={
                "error": True,
                "message": "No valid SKU IDs provided.",
            })

        with ThreadPoolExecutor(max_workers=2) as pool:
            fut_account = pool.submit(_fetch_vtex_account, auth_token)
            fut_store = pool.submit(_fetch_store_url, auth_token)
            vtex_account = fut_account.result()
            store_url = fut_store.result()

        if not vtex_account:
            return TextResponse(data={
                "error": True,
                "message": "Could not resolve VTEX account.",
            })

        base_url = f"https://{vtex_account}.myvtex.com"
        products = _fetch_products_batch(sku_ids, base_url, store_url)

        if not products:
            return TextResponse(data={
                "error": True,
                "message": "No products found for the provided SKU IDs.",
            })

        return TextResponse(data={
            "_agent_hint": "Product data is in the store's default language. You MUST respond to the customer in THEIR language.",
            "products": products,
        })


# ---------------------------------------------------------------------------
# Input parsing
# ---------------------------------------------------------------------------

def _parse_sku_ids(raw) -> List[str]:
    """Coerce the ``sku_ids`` parameter into a flat list of SKU-ID strings.

    Accepts a Python list, a JSON-encoded array string, or a
    comma-separated string.  Each ID is stripped of whitespace; empty
    values are dropped.

    Returns:
        A list of non-empty SKU-ID strings, or ``[]`` on invalid input.
    """
    if isinstance(raw, list):
        return [str(s).strip() for s in raw if str(s).strip()]

    if not isinstance(raw, str) or not raw.strip():
        return []

    text = raw.strip()
    if text.startswith("["):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [str(s).strip() for s in parsed if str(s).strip()]
        except json.JSONDecodeError:
            pass

    return [s.strip() for s in text.split(",") if s.strip()]


# ---------------------------------------------------------------------------
# VTEX account resolution
# ---------------------------------------------------------------------------

def _fetch_vtex_account(auth_token: str) -> str:
    """Resolve the VTEX account name via the RetailSetup API.

    Args:
        auth_token: Bearer token for the RetailSetup service.

    Returns:
        The VTEX account name (e.g. ``"account"``), or ``""`` on failure.
    """
    headers = {
        "Authorization": f"Bearer {auth_token}",
        "Content-Type": "application/json",
    }
    t0 = time.time()
    try:
        resp = requests.get(
            f"{RETAILSETUP_URL}/api/projects/vtex-account",
            headers=headers,
            timeout=15,
        )
        print(f"[⏱ HTTP GET vtex-account] {(time.time()-t0)*1000:.0f}ms — status={resp.status_code}")
        if resp.status_code == 200:
            return resp.json().get("vtex_account", "")
    except Exception as e:
        print(f"[⏱ HTTP GET vtex-account] {(time.time()-t0)*1000:.0f}ms — FAILED: {e}")
    return ""


def _fetch_store_url(auth_token: str) -> str:
    """Fetch the public storefront URL via the RetailSetup API.

    Used to build product deep-links (``store_url + link + ?skuId=...``).

    Args:
        auth_token: Bearer token for the RetailSetup service.

    Returns:
        The store URL string, or ``""`` on failure.
    """
    headers = {
        "Authorization": f"Bearer {auth_token}",
        "Content-Type": "application/json",
    }
    t0 = time.time()
    try:
        resp = requests.get(
            f"{RETAILSETUP_URL}/vtex/projects/store-url/",
            headers=headers,
            timeout=15,
        )
        print(f"[⏱ HTTP GET store-url] {(time.time()-t0)*1000:.0f}ms — status={resp.status_code}")
        if resp.status_code == 200:
            return resp.json().get("store_url", "")
    except Exception as e:
        print(f"[⏱ HTTP GET store-url] {(time.time()-t0)*1000:.0f}ms — FAILED: {e}")
    return ""


# ---------------------------------------------------------------------------
# VTEX product fetch
# ---------------------------------------------------------------------------

def _raw_id(sku_id: str) -> str:
    """Strip the optional seller suffix from a SKU identifier.

    Example:
        >>> _raw_id("103025#1")
        '103025'
    """
    return sku_id.split("#")[0] if "#" in sku_id else sku_id


def _fetch_products_batch(
    sku_ids: List[str],
    base_url: str,
    store_url: str,
) -> Dict[str, dict]:
    """Batch-fetch products from VTEX Intelligent Search by SKU ID.

    Builds a ``sku.id:id1;id2;id3`` query so all SKUs are resolved in
    a single HTTP round-trip, then extracts detailed data for each
    matched item.

    Args:
        sku_ids: SKU identifiers (may contain seller suffixes).
        base_url: VTEX API base (e.g. ``"https://account.myvtex.com"``).
        store_url: Public storefront URL for building product deep-links.

    Returns:
        A dict mapping raw SKU IDs to their detailed product dicts.
        Returns ``{}`` on network or parsing failures.
    """
    raw_ids = [_raw_id(sid) for sid in sku_ids]
    query = ";".join(raw_ids)
    url = (
        f"{base_url}{SEARCH_ENDPOINT}"
        f"?query=sku.id:{query}&simulationBehavior=default"
    )

    t0 = time.time()
    try:
        resp = requests.get(url, timeout=15)
        print(f"[⏱ HTTP GET intelligent-search] {(time.time()-t0)*1000:.0f}ms — status={resp.status_code}, {len(raw_ids)} SKUs")
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[⏱ HTTP GET intelligent-search] {(time.time()-t0)*1000:.0f}ms — FAILED: {e}")
        return {}

    id_set = set(raw_ids)
    result: Dict[str, dict] = {}
    for product in data.get("products", []):
        for item in product.get("items", []):
            item_id = item.get("itemId", "")
            if item_id in id_set and item_id not in result:
                detail = _build_product_detail(product, item, item_id, store_url)
                if detail:
                    result[item_id] = detail

    print(f"[⏱ parse_response] {len(result)}/{len(raw_ids)} products matched")
    return result


def _build_product_detail(
    product: dict,
    item: dict,
    sku_id: str,
    store_url: str,
) -> Optional[dict]:
    """Extract detailed product information from a VTEX product + item pair.

    Extracts pricing (best seller), image, variation attributes,
    material / composition, specification groups, and builds the
    deep-link to the product page.

    Args:
        product: Top-level product object from the Intelligent Search response.
        item: The specific SKU item nested under *product*.
        sku_id: Raw SKU identifier (no seller suffix).
        store_url: Storefront base URL for the product deep-link.

    Returns:
        A dict with detailed product data, or ``None`` if the item has
        no sellers at all.
    """
    sale_price, list_price = _extract_prices(item)

    link = product.get("link", "")
    product_link = f"{store_url}{link}?skuId={sku_id}"

    image = _extract_image(item)
    variations = _extract_variations(item)
    material = _extract_material(product)
    spec_groups = _extract_spec_groups(product)

    name = item.get("nameComplete", product.get("productName", ""))
    description = _clean_html(product.get("description", ""))

    return {
        "sku_id": sku_id,
        "sku_name": name,
        "brand": product.get("brand", ""),
        "description": description,
        "image": image,
        "product_link": product_link,
        "sale_price": sale_price,
        "list_price": list_price,
        "variations": variations,
        "material": material,
        "specification_groups": spec_groups,
    }


# ---------------------------------------------------------------------------
# Field extractors
# ---------------------------------------------------------------------------

def _extract_prices(item: dict) -> Tuple[Optional[float], Optional[float]]:
    """Return ``(sale_price, list_price)`` from the best available seller.

    Seller priority:
        1. Default seller with stock.
        2. Any seller with stock.
        3. First seller (fallback).

    Sale-price priority within the chosen seller:
        1. Pix payment (best discount).
        2. Visa single-installment (à vista).
        3. First installment entry.
    """
    seller = _pick_seller(item.get("sellers") or [])
    if not seller:
        return (None, None)

    offer = seller.get("commertialOffer") or {}
    sale_price = _pick_sale_price(offer.get("Installments") or [])
    list_price = offer.get("ListPrice")
    return (sale_price, list_price)


def _pick_seller(sellers: list) -> Optional[dict]:
    """Choose the best seller from the sellers array."""
    for s in sellers:
        if s.get("sellerDefault") and (s.get("commertialOffer") or {}).get("AvailableQuantity", 0) > 0:
            return s
    for s in sellers:
        if (s.get("commertialOffer") or {}).get("AvailableQuantity", 0) > 0:
            return s
    return sellers[0] if sellers else None


def _pick_sale_price(installments: list) -> Optional[float]:
    """Select the best sale price from the installments array."""
    for inst in installments:
        if "pix" in (inst.get("PaymentSystemName") or "").lower():
            return inst.get("Value")
    for inst in installments:
        if inst.get("PaymentSystemName") == "Visa" and inst.get("NumberOfInstallments") == 1:
            return inst.get("Value")
    return installments[0].get("Value") if installments else None


def _extract_image(item: dict) -> str:
    """Return the cleaned URL of the first valid JPG/PNG image, or ``""``."""
    for img in item.get("images", []):
        img_url = img.get("imageUrl", "")
        if img_url and any(ext in img_url.lower() for ext in (".jpg", ".jpeg", ".png")):
            return img_url.split("?")[0].split("#")[0]
    return ""


def _extract_variations(item: dict) -> List[str]:
    """Build human-readable variation strings (e.g. ``"Cor: Azul, Verde"``)."""
    variations = []
    for var in item.get("variations", []):
        vname = var.get("name", "")
        vvals = var.get("values", [])
        if vname and vvals:
            variations.append(f"{vname}: {', '.join(vvals)}")
    return variations


def _extract_material(product: dict) -> str:
    """Extract material / composition from specification groups, or ``""``."""
    keywords = ("material", "composição", "tecido")
    for group in product.get("specificationGroups", []):
        for spec in group.get("specifications", []):
            if any(k in spec.get("name", "").lower() for k in keywords):
                return ", ".join(spec.get("values", []))
    return ""


def _extract_spec_groups(product: dict) -> List[dict]:
    """Return up to 3 specification groups with up to 5 specs each."""
    groups = []
    for group in product.get("specificationGroups", [])[:3]:
        specs = group.get("specifications", [])[:5]
        if specs:
            groups.append({
                "name": group.get("name", ""),
                "specifications": [
                    {"name": s.get("name", ""), "values": s.get("values", [])}
                    for s in specs
                ],
            })
    return groups


def _clean_html(text: str) -> str:
    """Convert an HTML-rich string to plain text.

    Unescapes HTML entities, removes all tags, and collapses whitespace.
    """
    if not text or not isinstance(text, str):
        return ""
    s = html.unescape(text)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", s).strip()
