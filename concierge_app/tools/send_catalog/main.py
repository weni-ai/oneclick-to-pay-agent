"""Send Catalog Tool — Weni Tool entry point.

Orchestrates the catalog-sending flow:

1. **Parse** the incoming ``product_skus`` parameter into normalised
   ``[{category_name, skus_ids}]`` groups (handles JSON strings,
   flat key-value lists, plain SKU arrays, etc.).
2. **Resolve** the VTEX account name and storefront URL in parallel
   via the RetailSetup API.
3. **Dispatch** to the appropriate channel handler:
   - *WhatsApp* — returns product data as ``TextResponse`` for the
     collaborator agent to relay to the manager (no broadcast).
   - *Web* — fetches full product details from VTEX Intelligent Search,
     enriches pricing / currency, builds rich catalog cards, and
     broadcasts the assembled catalog message to the contact.
"""

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, Future
from pathlib import Path
from typing import List, Tuple

_root = Path(__file__).resolve().parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

import requests

from weni import Tool
from weni.context import Context
from weni.responses import TextResponse, FinalResponse
from weni.broadcasts import Broadcast, Text

import vtex_client
import catalog_builder
from helpers import format_price_pair, format_price_display, get_currency_code, get_account_name

RETAILSETUP_URL = "https://retailsetup.weni.ai"
MAX_PRODUCTS = 7
SELLER_ID = "1"


# ---------------------------------------------------------------------------
# Input parsing
# ---------------------------------------------------------------------------

def _parse_skus_value(raw) -> list[str]:
    """Coerce a raw SKU value into a flat list of SKU-ID strings.

    Handles three input shapes:
    - A Python ``list`` — each element is stringified and stripped.
    - A JSON-encoded array string (e.g. ``'["123", "456"]'``).
    - A comma-separated string (e.g. ``"123, 456"``).

    Returns an empty list for any other type or empty input.
    """
    if isinstance(raw, list):
        return [str(s).strip() for s in raw if str(s).strip()]
    if not isinstance(raw, str):
        return []
    raw = raw.strip()
    if raw.startswith("["):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(s).strip() for s in parsed if str(s).strip()]
        except json.JSONDecodeError:
            pass
    return [s.strip() for s in raw.split(",") if s.strip()]


def _parse_flat_kv_strings(items: list) -> list[dict]:
    """Parse a flat list of ``"key:value"`` strings into category groups.

    Expected alternating pattern::

        ["category_name:Bebidas", "skus_ids:[101,102]",
         "category_name:Laticínios", "skus_ids:[201]"]

    Returns a list of ``{"category_name": str, "skus_ids": list[str]}`` dicts.
    Pairs where either field is missing or empty are silently dropped.
    """
    result, current = [], ""
    for item in items:
        if not isinstance(item, str):
            continue
        if item.startswith("category_name:"):
            current = item[len("category_name:"):].strip()
        elif item.startswith("skus_ids:"):
            skus = _parse_skus_value(item[len("skus_ids:"):].strip())
            if current and skus:
                result.append({"category_name": current, "skus_ids": skus})
            current = ""
    return result


def _parse_product_skus(product_skus) -> list[dict]:
    """Normalise the ``product_skus`` tool parameter into a canonical form.

    The agent may supply SKUs in several formats depending on how the
    LLM serialised them.  This function accepts all of:

    - A JSON string encoding any of the formats below.
    - A ``list[dict]`` with keys ``category_name`` / ``skus_ids``
      (or their camelCase variants).
    - A flat list of ``"key:value"`` strings (see ``_parse_flat_kv_strings``).
    - A plain list of SKU-ID strings (assigned to a default category).

    Returns:
        ``[{"category_name": str, "skus_ids": list[str]}, ...]``,
        or an empty list when the input is invalid / empty.
    """
    if not product_skus:
        return []
    if isinstance(product_skus, str):
        try:
            product_skus = json.loads(product_skus)
        except json.JSONDecodeError:
            return []
    if not isinstance(product_skus, list):
        return []

    has_dicts = any(isinstance(i, dict) for i in product_skus)
    has_kv = any(
        isinstance(i, str) and (i.startswith("category_name:") or i.startswith("skus_ids:"))
        for i in product_skus
    )

    if has_kv and not has_dicts:
        return _parse_flat_kv_strings(product_skus)

    if not has_dicts:
        plain = [str(s).strip() for s in product_skus if isinstance(s, str) and str(s).strip()]
        return [{"category_name": "Produtos", "skus_ids": plain}] if plain else []

    result = []
    for item in product_skus:
        if not isinstance(item, dict):
            continue
        name = str(item.get("category_name") or item.get("categoryName") or "").strip()
        skus = item.get("skus_ids") or item.get("skusIds") or []
        if isinstance(skus, str):
            skus = _parse_skus_value(skus)
        if name and skus:
            result.append({"category_name": name, "skus_ids": list(skus)})
    return result


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------

class SendCatalog(Tool):
    """Weni Tool that handles product catalog delivery per channel.

    Supports two channel strategies:
    - **WhatsApp**: returns product data as text for the collaborator
      agent to relay to the manager (no component broadcast).
    - **Web**: rich path — full product details are fetched from VTEX
      Intelligent Search and enriched with pricing and currency data,
      then broadcast as a catalog component.
    """

    def execute(self, context: Context) -> TextResponse or FinalResponse:
        """Parse SKUs, resolve VTEX account, and dispatch to the appropriate channel.

        Reads ``product_skus``, ``simple_message``, ``header_text`` from the
        tool parameters.  Determines the channel from ``contact.urn``
        (``whatsapp:`` prefix → WhatsApp text response, otherwise Web component).

        Returns:
            ``TextResponse`` for WhatsApp (data for the collaborator to relay),
            ``FinalResponse`` for Web (after broadcasting the catalog component),
            or ``TextResponse`` with an ``error`` key on validation failures.
        """
        t_total = time.time()
        print(f"[⏱ execute_start] tool code entered at {time.strftime('%H:%M:%S', time.localtime())}.{int(time.time()*1000)%1000:03d}")

        product_skus = context.parameters.get("product_skus", [])
        message = context.parameters.get("simple_message", "")
        header_text = context.parameters.get("header_text", "")
        auth_token = context.credentials.get("auth_token", "")
        contact_urn = context.contact.get("urn", "")
        t0 = time.time()
        parsed = _parse_product_skus(product_skus)
        print(f"[⏱ parse_product_skus] {(time.time()-t0)*1000:.0f}ms — {len(parsed)} groups")
        if not parsed:
            print(f"[SendCatalog] Invalid product_skus. Raw: {product_skus}")
            return TextResponse(data={"error": "No valid product_skus provided."})
        if not contact_urn:
            return TextResponse(data={"error": "Contact URN not available"})

        t0 = time.time()
        with ThreadPoolExecutor(max_workers=2) as pool:
            fut_account: Future = pool.submit(_fetch_vtex_account, auth_token)
            fut_store: Future = pool.submit(_fetch_store_url, auth_token)
            vtex_account = fut_account.result()
            store_url = fut_store.result()
        print(f"[⏱ parallel:vtex_account+store_url] {(time.time()-t0)*1000:.0f}ms — account={vtex_account}, store_url={store_url[:50] if store_url else 'N/A'}")

        if not vtex_account:
            print("[SendCatalog] Could not resolve VTEX account")
            return TextResponse(data={"error": "Could not resolve VTEX account"})

        base_url = f"https://{vtex_account}.myvtex.com"

        entries = _flatten_entries(parsed)
        is_whatsapp = contact_urn.startswith("whatsapp:")
        channel = "whatsapp" if is_whatsapp else "web"
        print(f"[⏱ setup] channel={channel}, entries={len(entries)}")
    
        if is_whatsapp:
            result = self._send_whatsapp(
                entries, message, header_text,
            )
        else:
            result = self._send_web(
                entries, base_url, store_url, message, header_text,
            )

        print(f"[⏱ TOTAL send_catalog] {(time.time()-t_total)*1000:.0f}ms")
        return result

    def _send_whatsapp(
        self,
        entries: List[Tuple[str, str]],
        message: str,
        header_text: str,
    ) -> TextResponse:
        """Return product data as text for the collaborator to relay to the manager.

        Unlike the web flow (which broadcasts a rich component), the WhatsApp
        path simply returns structured product data so the agent can present
        it as a text message.

        Args:
            entries: Flat ``(category_name, sku_id)`` pairs.
            message: Body text describing what was found.
            header_text: Optional header context.

        Returns:
            ``TextResponse`` with categorised product SKU data.
        """
        t0 = time.time()
        products_by_category: dict[str, list[str]] = {}
        cat_order: list[str] = []

        for cat, sku in entries:
            raw_id = vtex_client.raw_sku_id(sku)
            if cat not in products_by_category:
                products_by_category[cat] = []
                cat_order.append(cat)
            products_by_category[cat].append(raw_id)

        categories = [
            {"category_name": c, "sku_ids": products_by_category[c]}
            for c in cat_order
        ]
        total_products = sum(len(c["sku_ids"]) for c in categories)
        print(f"[⏱ whatsapp:build_text_response] {(time.time()-t0)*1000:.0f}ms — {total_products} products in {len(categories)} categories")

        return TextResponse(data={
            "_agent_hint": (
                "The catalog component was NOT sent because this is a WhatsApp channel. "
                "YOU must present these products to the customer as a well-formatted text message. "
                "Use the product data from Search Products results to describe each product with name, price, and relevant details. "
                "Do NOT mention 'catalog' or any internal tool names to the customer. Just present the products naturally."
            )
        })

    def _send_web(
        self,
        entries: List[Tuple[str, str]],
        base_url: str,
        store_url: str,
        message: str,
        header_text: str,
    ) -> TextResponse or FinalResponse:
        """Fetch full product details from VTEX and broadcast a rich web catalog.

        Runs two parallel requests — product batch fetch and currency lookup —
        then enriches each ``Product`` with formatted prices before building
        the ``WeniWebChatCatalog`` message.

        Args:
            entries: Flat ``(category_name, sku_id)`` pairs.
            base_url: VTEX API base (e.g. ``"https://account.myvtex.com"``).
            store_url: Public storefront URL for product deep-links.
            message: Body text sent with the catalog.
            header_text: Optional catalog header.

        Returns:
            ``FinalResponse`` on success, or ``TextResponse`` with an
            ``error`` key when no valid products are found.
        """
        sku_ids = [sku for _, sku in entries]
        account_name = get_account_name(base_url)

        t0 = time.time()
        with ThreadPoolExecutor(max_workers=2) as pool:
            fut_products: Future = pool.submit(
                vtex_client.fetch_products_batch, sku_ids, base_url, store_url,
            )
            fut_currency: Future = pool.submit(get_currency_code, account_name)
            products_map = fut_products.result()
            currency = fut_currency.result()
        print(f"[⏱ parallel:fetch_products+currency] {(time.time()-t0)*1000:.0f}ms — {len(products_map)} products found, currency={currency}")

        t0 = time.time()
        categories: dict[str, list] = {}
        cat_order: list[str] = []
        loaded = 0

        for cat_name, sku_id in entries:
            if loaded >= MAX_PRODUCTS:
                break
            raw_id = vtex_client.raw_sku_id(sku_id)
            product = products_map.get(raw_id)
            if not product or (not product.sale_price and not product.list_price):
                continue

            sale_str, list_str = format_price_pair(product.sale_price, product.list_price)
            product.price_display = format_price_display(product.sale_price, product.list_price)
            product.sale_price_formatted = sale_str
            product.list_price_formatted = list_str
            product.currency = currency

            if cat_name not in categories:
                categories[cat_name] = []
                cat_order.append(cat_name)
            categories[cat_name].append(product)
            loaded += 1

        cats_with_products = [
            {"category_name": c, "products": categories[c]}
            for c in cat_order if categories[c]
        ]
        if not cats_with_products:
            print("[SendCatalog] No valid products after VTEX fetch")
            return TextResponse(data={"error": "No valid products found for the provided SKUs"})

        all_products = [p for cat in cats_with_products for p in cat["products"]]

        catalog_msg = catalog_builder.build_web_catalog(
            cats_with_products, SELLER_ID, message, header_text,
        )
        print(f"[⏱ web:enrich+build_catalog] {(time.time()-t0)*1000:.0f}ms — {len(all_products)} products in {len(cats_with_products)} categories")

        t0 = time.time()
        self.send_broadcast(catalog_msg)
        print(f"[⏱ web:broadcast] {(time.time()-t0)*1000:.0f}ms")

        return FinalResponse()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _flatten_entries(parsed: list[dict]) -> List[Tuple[str, str]]:
    """Flatten parsed category groups into ``(category_name, sku_id)`` tuples.

    Iterates through each group's ``skus_ids`` list in order and stops as
    soon as ``MAX_PRODUCTS`` entries have been collected, ensuring the
    catalog never exceeds the allowed product limit.
    """
    entries: List[Tuple[str, str]] = []
    for group in parsed:
        cat = group.get("category_name", "").strip() or "Produtos"
        for sku_id in group.get("skus_ids", []) or []:
            if len(entries) >= MAX_PRODUCTS:
                return entries
            entries.append((cat, sku_id))
    return entries


def _fetch_vtex_account(auth_token: str) -> str:
    """Resolve the VTEX account name via the RetailSetup API.

    Args:
        auth_token: Bearer token for the RetailSetup service.

    Returns:
        The VTEX account name (e.g. ``"account"``), or an empty
        string on failure.
    """
    t0 = time.time()
    headers = {"Authorization": f"Bearer {auth_token}", "Content-Type": "application/json"}
    try:
        resp = requests.get(f"{RETAILSETUP_URL}/api/projects/vtex-account", headers=headers, timeout=15)
        print(f"[⏱ HTTP GET vtex-account] {(time.time()-t0)*1000:.0f}ms — status={resp.status_code}")
        if resp.status_code == 200:
            return resp.json().get("vtex_account", "")
    except Exception as e:
        print(f"[⏱ HTTP GET vtex-account] {(time.time()-t0)*1000:.0f}ms — FAILED: {e}")
    return ""


def _fetch_store_url(auth_token: str) -> str:
    """Fetch the public storefront URL via the RetailSetup API.

    Used in the web-channel flow to build product deep-links.

    Args:
        auth_token: Bearer token for the RetailSetup service.

    Returns:
        The store URL string, or an empty string on failure.
    """
    t0 = time.time()
    headers = {"Authorization": f"Bearer {auth_token}", "Content-Type": "application/json"}
    try:
        resp = requests.get(f"{RETAILSETUP_URL}/vtex/projects/store-url/", headers=headers, timeout=15)
        print(f"[⏱ HTTP GET store-url] {(time.time()-t0)*1000:.0f}ms — status={resp.status_code}")
        if resp.status_code == 200:
            return resp.json().get("store_url", "")
    except Exception as e:
        print(f"[⏱ HTTP GET store-url] {(time.time()-t0)*1000:.0f}ms — FAILED: {e}")
    return ""
