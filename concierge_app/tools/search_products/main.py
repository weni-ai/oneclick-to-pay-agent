"""Search Products Tool — Weni Tool entry point.

Orchestrates the product-search flow:

1. **Parse** incoming ``product_names`` (supports lists, JSON strings,
   bracket-delimited text).
2. **Resolve** the VTEX account via RetailSetup and build the
   Intelligent Search URL.
3. **Search** each product name against the VTEX Intelligent Search API,
   aggregating results into a single dict keyed by product name.
4. **WhatsApp-specific** — when the contact is on WhatsApp, sync found
   SKUs to the Meta catalog (with one automatic retry) and fire a CAPI
   lead event.
5. **Return** a ``TextResponse`` containing structured product data for
   the agent to present to the customer.
"""

import ast
import base64
import json
import time
from concurrent.futures import ThreadPoolExecutor
from time import sleep
from typing import Dict, List, Optional

import requests

from weni import Tool
from weni.context import Context
from weni.events import Event
from weni.responses import TextResponse


RETAILSETUP_URL = "https://retailsetup.weni.ai"
SEARCH_ENDPOINT = "/api/io/_v/api/intelligent-search/product_search/"
SYNC_URL = "https://integrations-engine.weni.ai/api/v1/apptypes/vtex/sync-on-demand/"
CAPI_URL = "https://flows.weni.ai/conversion/"

DEFAULT_SELLER = "1"
MAX_SKUS_PER_PRODUCT = 3
MAX_DESCRIPTION_LEN = 600
DEFAULT_SYNC_WAIT = 2  # seconds to wait for Meta catalog propagation (Whatsapp only)


def _is_whatsapp_channel(contact_urn: str) -> bool:
    """Check whether the contact URN belongs to a WhatsApp channel."""
    return contact_urn.startswith("whatsapp:")


class SearchProducts(Tool):
    """Search for products on VTEX and return structured results for the agent.

    Handles both WhatsApp and Web channels. On WhatsApp, an extra
    Meta-catalog sync step is performed so the customer can see product
    cards natively in the chat.
    """

    # ── Entry Point ──────────────────────────────────────────────────────

    def execute(self, context: Context) -> TextResponse:
        """Parse product names, search VTEX, and optionally sync to WhatsApp.

        Reads ``product_names`` from tool parameters.  For WhatsApp contacts
        the found SKUs are synced to the Meta catalog and a CAPI lead event
        is fired.

        Returns:
            ``TextResponse`` with a ``products`` dict keyed by product name,
            or an ``error`` / ``message`` payload when validation fails.
        """
        t_total = time.time()
        print(f"[⏱ execute] started at {time.strftime('%H:%M:%S')}.{int(time.time()*1000)%1000:03d}")

        t0 = time.time()
        product_names = self._parse_product_names(
            context.parameters.get("product_names", "")
        )
        print(f"[⏱ parse_product_names] {(time.time()-t0)*1000:.0f}ms — {len(product_names)} names: {product_names}")
        if not product_names:
            return TextResponse(data={
                "error": True,
                "message": "No valid product names provided",
            })

        project_uuid = context.project.get("uuid", "")
        token = context.credentials.get("auth_token", "")
        contact_urn = context.contact.get("urn", "")
        channel_uuid = context.contact.get("channel_uuid", "")
        is_whatsapp = _is_whatsapp_channel(contact_urn)

        t0 = time.time()
        vtex_segment_b64 = self._encode_segment_to_base64(
            context.contact.get("fields", {}).get("segment", "")
        )
        print(f"[⏱ encode_segment] {(time.time()-t0)*1000:.0f}ms — segment={'yes' if vtex_segment_b64 else 'no'}")

        t0 = time.time()
        vtex_account = self._get_vtex_account(token)
        print(f"[⏱ get_vtex_account] {(time.time()-t0)*1000:.0f}ms — account={vtex_account or 'FAILED'}")
        if not vtex_account or not project_uuid:
            return TextResponse(data={
                "error": True,
                "message": "Missing configuration: could not resolve VTEX account or project UUID",
            })

        search_url = f"https://{vtex_account}.myvtex.com{SEARCH_ENDPOINT}"

        t0 = time.time()
        all_products = self._search_all(product_names, search_url, vtex_segment_b64)
        print(f"[⏱ search_all] {(time.time()-t0)*1000:.0f}ms — {len(all_products)} products for {len(product_names)} queries")

        self._register_search_event(product_names)

        if is_whatsapp and project_uuid and all_products:
            t0 = time.time()
            sync_error = self._handle_whatsapp_sync(all_products, token)
            print(f"[⏱ whatsapp_sync] {(time.time()-t0)*1000:.0f}ms — error={sync_error or 'none'}")
            if sync_error:
                print(f"[⏱ TOTAL] {(time.time()-t_total)*1000:.0f}ms")
                return TextResponse(data={
                    "_agent_hint": "Product data is in the store's default language. You MUST respond to the customer in THEIR language.",
                    "status": "sync_failed",
                    "message": sync_error,
                    "products": all_products,
                })
            t0 = time.time()
            self._send_capi(token, channel_uuid, contact_urn, "lead")
            print(f"[⏱ send_capi] {(time.time()-t0)*1000:.0f}ms")

        print(f"[⏱ TOTAL] {(time.time()-t_total)*1000:.0f}ms")
        return TextResponse(data={
            "_agent_hint": "Product data is in the store's default language. You MUST respond to the customer in THEIR language, not the store's language.",
            "products": all_products,
        })

    # ── Input Parsing ────────────────────────────────────────────────────

    @staticmethod
    def _parse_product_names(raw) -> List[str]:
        """Coerce the ``product_names`` parameter into a flat list of strings.

        Handles three input shapes the LLM may produce:

        - A Python ``list`` — each element is stringified and stripped.
        - A bracket-delimited string (``'["leite", "pão"]'``) — parsed
          via ``ast.literal_eval``; falls back to comma-split on failure.
        - A plain string (``"leite"``') — wrapped in a single-element list.

        Returns:
            A list of non-empty product-name strings, or ``[]`` if
            *raw* is empty / invalid.
        """
        if isinstance(raw, list):
            return [str(n).strip() for n in raw if str(n).strip()]

        if not isinstance(raw, str) or not raw.strip():
            return []

        text = raw.strip()

        if text.startswith("[") and text.endswith("]"):
            try:
                parsed = ast.literal_eval(text)
                if isinstance(parsed, list):
                    return [str(n).strip() for n in parsed if str(n).strip()]
            except (ValueError, SyntaxError):
                content = text[1:-1].strip()
                if content:
                    return [item.strip() for item in content.split(",") if item.strip()]
                return []

        return [text]

    # ── Segment Encoding ─────────────────────────────────────────────────

    @staticmethod
    def _encode_segment_to_base64(vtex_segment_raw: str) -> str:
        """Encode the VTEX segment JSON into a base64 string for the cookie header.

        The ``vtex_segment`` cookie lets VTEX personalise search results
        (e.g. regional pricing / availability).

        Args:
            vtex_segment_raw: A JSON string (or already-parsed object)
                representing the segment payload.

        Returns:
            A base64-encoded compact JSON string, or ``""`` when the
            input is empty or malformed.
        """
        if not vtex_segment_raw:
            return ""
        try:
            segment_json = (
                json.loads(vtex_segment_raw)
                if isinstance(vtex_segment_raw, str)
                else vtex_segment_raw
            )
            segment_str = json.dumps(segment_json, separators=(",", ":"))
            return base64.b64encode(segment_str.encode("utf-8")).decode("utf-8")
        except (json.JSONDecodeError, TypeError) as e:
            print(f"[SearchProducts] Error encoding vtex_segment to base64: {e}")
            return ""

    # ── VTEX Account Resolution ──────────────────────────────────────────

    @staticmethod
    def _get_vtex_account(auth_token: str) -> str:
        """Resolve the VTEX account name via the RetailSetup API.

        Args:
            auth_token: Bearer token for the RetailSetup service.

        Returns:
            The VTEX account name (e.g. ``"prezunic"``), or ``""`` on failure.
        """
        headers = {
            "Authorization": f"Bearer {auth_token}",
            "Content-Type": "application/json",
        }
        t0 = time.time()
        try:
            response = requests.get(
                f"{RETAILSETUP_URL}/api/projects/vtex-account",
                headers=headers,
                timeout=15,
            )
            print(f"[⏱ HTTP GET vtex-account] {(time.time()-t0)*1000:.0f}ms — status={response.status_code}")
            if response.status_code == 200:
                return response.json().get("vtex_account", "")
        except Exception as e:
            print(f"[⏱ HTTP GET vtex-account] {(time.time()-t0)*1000:.0f}ms — FAILED: {e}")
        return ""

    # ── Product Search ───────────────────────────────────────────────────

    def _search_all(
        self,
        product_names: List[str],
        search_url: str,
        vtex_segment_b64: str,
    ) -> Dict:
        """Run Intelligent Search queries in parallel and merge results.

        Each product name is searched concurrently using a thread pool.
        Later queries can overwrite earlier ones when product names overlap,
        which is acceptable because the data is identical.

        Returns:
            A single dict mapping ``productName → formatted product data``.
        """
        all_products: Dict = {}
        with ThreadPoolExecutor(max_workers=len(product_names)) as pool:
            futures = {
                pool.submit(self._intelligent_search, name, search_url, vtex_segment_b64): name
                for name in product_names
            }
            for future in futures:
                all_products.update(future.result())
        return all_products

    def _intelligent_search(
        self,
        product_name: str,
        url: str,
        vtex_segment_b64: str = "",
    ) -> Dict:
        """Query VTEX Intelligent Search for a single product name.

        Sends ``hideUnavailableItems=true`` so only in-stock products are
        returned.  When a ``vtex_segment`` cookie is available it is
        forwarded to enable regional pricing.

        Args:
            product_name: Free-text search term (e.g. ``"leite integral"``).
            url: Full Intelligent Search endpoint URL (including account).
            vtex_segment_b64: Optional base64-encoded segment cookie value.

        Returns:
            A dict mapping ``productName → formatted product dict`` for
            every product that passes ``_format_product`` validation.
            Returns ``{}`` on any HTTP or decoding error.
        """
        search_url = f"{url}?query={product_name}&hideUnavailableItems=true"

        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if vtex_segment_b64:
            headers["Cookie"] = f"vtex_segment={vtex_segment_b64}"

        t0 = time.time()
        try:
            response = requests.get(search_url, headers=headers, timeout=15)
            response.raise_for_status()
            products = response.json().get("products", [])
            print(f"[⏱ HTTP GET intelligent-search] {(time.time()-t0)*1000:.0f}ms — query='{product_name}', status={response.status_code}, {len(products)} raw products")
        except requests.exceptions.RequestException as e:
            print(f"[⏱ HTTP GET intelligent-search] {(time.time()-t0)*1000:.0f}ms — query='{product_name}' FAILED: {e}")
            return {}
        except (json.JSONDecodeError, ValueError) as e:
            print(f"[⏱ HTTP GET intelligent-search] {(time.time()-t0)*1000:.0f}ms — query='{product_name}' DECODE ERROR: {e}")
            return {}

        return {
            product.get("productName", ""): self._format_product(product)
            for product in products
            if self._format_product(product) is not None
        }

    @staticmethod
    def _format_product(product: dict) -> Optional[dict]:
        """Transform a raw VTEX product into a minimal output for the agent.

        Returns only what the agent needs to present results:
        a product-level ``description`` and a ``skus`` list where each
        entry carries ``sku_id``, ``sku_name``, ``sale_price``, and
        ``list_price``.

        Args:
            product: A single product object from the Intelligent Search
                response (``data["products"][i]``).

        Returns:
            A dict with ``description`` (str) and ``skus`` (list); or
            ``None`` when the product has no valid items.
        """
        items = product.get("items", [])
        if not items:
            return None

        skus = []
        for item in items:
            item_id = item.get("itemId")
            if not item_id:
                continue
            sale_price, list_price = SearchProducts._extract_item_prices(item)
            skus.append({
                "sku_id": f"{item_id}#{DEFAULT_SELLER}",
                "sku_name": item.get("name", ""),
                "sale_price": sale_price,
                "list_price": list_price,
            })

        if not skus:
            return None

        description = product.get("description", "") or ""
        if len(description) > MAX_DESCRIPTION_LEN:
            description = description[:MAX_DESCRIPTION_LEN] + "..."

        return {
            "description": description,
            "brand": product.get("brand", ""),
            "skus": skus[:MAX_SKUS_PER_PRODUCT],
        }

    @staticmethod
    def _extract_item_prices(item: dict) -> tuple[Optional[float], Optional[float]]:
        """Extract ``(sale_price, list_price)`` from the best available seller.

        Seller priority:
            1. Default seller with stock.
            2. Any seller with stock.

        Returns ``(None, None)`` when no seller has stock.
        """
        for seller in item.get("sellers") or []:
            if not seller.get("sellerDefault"):
                continue
            offer = seller.get("commertialOffer") or {}
            if offer.get("AvailableQuantity", 0) > 0:
                return (offer.get("Price"), offer.get("ListPrice"))

        for seller in item.get("sellers") or []:
            offer = seller.get("commertialOffer") or {}
            if offer.get("AvailableQuantity", 0) > 0:
                return (offer.get("Price"), offer.get("ListPrice"))

        return (None, None)

    # ── Analytics ─────────────────────────────────────────────────────────

    @staticmethod
    def _register_search_event(product_names: List[str]) -> None:
        """Fire a ``weni_nexus_data`` event recording the searched terms."""
        Event.register(
            Event(
                event_name="weni_nexus_data",
                key="search_product",
                value_type="string",
                value=str(product_names),
                metadata={"agent_name": "Search Product"},
            )
        )

    # ── WhatsApp Meta Sync ───────────────────────────────────────────────

    def _handle_whatsapp_sync(
        self,
        all_products: Dict,
        token: str,
    ) -> Optional[str]:
        """Sync found SKUs to the Meta catalog so WhatsApp can display product cards.

        Extracts SKU/seller pairs from the search results, groups them by
        seller, syncs via the integrations engine, and waits briefly for
        Meta propagation.

        Args:
            all_products: Merged search results (``productName → data``).
            token: Bearer token for the sync API.

        Returns:
            An error message string if the sync fails after retries,
            or ``None`` on success.
        """
        product_details = self._extract_product_details(all_products)
        by_seller = self._group_by_seller(product_details)

        if not by_seller:
            return None

        t0 = time.time()
        sync_error = self._sync_and_retry(by_seller, token)
        print(f"[⏱ sync_and_retry] {(time.time()-t0)*1000:.0f}ms — sellers={list(by_seller.keys())}, error={sync_error or 'none'}")
        if sync_error:
            return sync_error

        if DEFAULT_SYNC_WAIT > 0:
            print(f"[⏱ sleep] waiting {DEFAULT_SYNC_WAIT}s for Meta sync propagation...")
            sleep(DEFAULT_SYNC_WAIT)

        return None

    @staticmethod
    def _extract_product_details(all_products: Dict) -> List[dict]:
        """Flatten search results into ``[{"sku_id", "seller_id"}, ...]`` pairs.

        Splits compound SKU IDs (``"12345#1"``) to extract the seller
        component, defaulting to ``DEFAULT_SELLER`` when absent.
        """
        details = []
        for data in all_products.values():
            for sku in data.get("skus", []):
                sku_id = sku.get("sku_id", "")
                parts = sku_id.split("#", 1)
                details.append({
                    "sku_id": sku_id,
                    "seller_id": parts[1] if len(parts) == 2 else DEFAULT_SELLER,
                })
        return details

    @staticmethod
    def _group_by_seller(product_details: List[dict]) -> Dict[str, dict]:
        """Group extracted SKU details by seller ID for batch sync.

        Returns:
            ``{seller_id: {"sku_ids": [...], "seller": seller_id}, ...}``
            ready to be POSTed to the sync endpoint.
        """
        by_seller: Dict[str, dict] = {}
        for p in product_details:
            seller = p.get("seller_id", DEFAULT_SELLER)
            original_sku = p.get("sku_id", "").split("#")[0]
            if not original_sku:
                continue
            if seller not in by_seller:
                by_seller[seller] = {"sku_ids": [], "seller": seller}
            by_seller[seller]["sku_ids"].append(original_sku)
        return by_seller

    def _sync_and_retry(
        self,
        products_by_seller: Dict[str, dict],
        token: str,
    ) -> Optional[str]:
        """POST SKUs to the Meta sync endpoint with one automatic retry on failure.

        Args:
            products_by_seller: Seller-grouped payloads from ``_group_by_seller``.
            token: Bearer token for the sync API.

        Returns:
            ``None`` on full success, or a human-readable error string
            listing the sellers that still failed after the retry.
        """
        results = self._sync_to_meta(products_by_seller, token)

        if "global_error" in results:
            return results["global_error"].get("error_message", "Unknown sync error")

        failed = {sid for sid, r in results.items() if r.get("status") == "error"}
        if not failed:
            return None

        print(f"[SearchProducts] Sync failed for sellers {failed}, retrying...")
        retry_batch = {s: products_by_seller[s] for s in failed if s in products_by_seller}
        retry_results = self._sync_to_meta(retry_batch, token)
        results.update(retry_results)

        still_failed = [sid for sid, r in results.items() if r.get("status") == "error"]
        if still_failed:
            errors = [
                f"Seller {sid}: {results[sid].get('error_message', 'Unknown')}"
                for sid in still_failed
            ]
            return f"Sync failed after 2 attempts: {'; '.join(errors)}"

        return None

    @staticmethod
    def _sync_to_meta(
        products_by_seller: Dict[str, dict],
        token: str,
        sync_url: str = SYNC_URL,
    ) -> Dict:
        """Execute one sync-on-demand POST per seller to the integrations engine.

        Args:
            products_by_seller: Seller-grouped payloads.
            token: Bearer auth token.
            sync_url: Override for testing; defaults to ``SYNC_URL``.

        Returns:
            ``{seller_id: {"status": "success"|"error", ...}, ...}``.
            On empty input, returns a ``global_error`` sentinel.
        """
        if not products_by_seller:
            return {"global_error": {"status": "error", "error_message": "No products to sync"}}

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        }

        results: Dict = {}
        for seller_id, products_data in products_by_seller.items():
            t0 = time.time()
            try:
                response = requests.post(
                    sync_url, json=products_data, timeout=30, headers=headers,
                )
                status = "success" if 200 <= response.status_code < 300 else "error"
                print(f"[⏱ HTTP POST sync-meta] {(time.time()-t0)*1000:.0f}ms — seller={seller_id}, status={response.status_code}, result={status}")
                results[seller_id] = {
                    "status": status,
                    "status_code": response.status_code,
                    "response": response.json(),
                }
            except Exception as e:
                print(f"[⏱ HTTP POST sync-meta] {(time.time()-t0)*1000:.0f}ms — seller={seller_id}, FAILED: {e}")
                results[seller_id] = {"status": "error", "error_message": str(e)}

        return results

    # ── CAPI (Conversion API) ────────────────────────────────────────────

    @staticmethod
    def _send_capi(
        auth_token: str,
        channel_uuid: str,
        contact_urn: str,
        event_type: str,
    ) -> bool:
        """Fire a Meta Conversions API (CAPI) event for attribution tracking.

        Used to report a *lead* event after a successful WhatsApp product
        search, enabling downstream conversion measurement.

        Args:
            auth_token: Bearer token for the Weni Flows CAPI proxy.
            channel_uuid: UUID of the WhatsApp channel.
            contact_urn: Full contact URN (e.g. ``"whatsapp:5521999..."``).
            event_type: CAPI event name (typically ``"lead"``).

        Returns:
            ``True`` if the event was accepted (HTTP 200), ``False`` otherwise.
        """
        if not all([auth_token, channel_uuid, contact_urn, event_type]):
            print("[SearchProducts] Missing CAPI parameters, skipping event")
            return False

        headers = {
            "Authorization": f"Bearer {auth_token}",
            "Content-Type": "application/json",
        }
        payload = {
            "channel_uuid": channel_uuid,
            "contact_urn": contact_urn,
            "event_type": event_type,
        }
        t0 = time.time()
        try:
            response = requests.post(CAPI_URL, headers=headers, json=payload, timeout=15)
            print(f"[⏱ HTTP POST capi] {(time.time()-t0)*1000:.0f}ms — status={response.status_code}")
            if response.status_code == 200:
                return True
            print(f"[SearchProducts] CAPI event failed: {response.status_code}")
        except requests.exceptions.RequestException as e:
            print(f"[⏱ HTTP POST capi] {(time.time()-t0)*1000:.0f}ms — FAILED: {e}")
        return False
