"""Pure helpers — price formatting, URL cleaning, HTML stripping, account-name extraction.

All functions in this module are stateless (no side-effects beyond HTTP calls
for ``get_currency_code``) and are shared by both the WhatsApp and Web
catalog flows.
"""

import html
import re
import time
from typing import Optional, Tuple

import requests

DEFAULT_CURRENCY = "BRL"
_VTEX_API = "https://api.vtexcommercestable.com.br"


def format_price_pair(price: Optional[float], list_price: Optional[float]) -> Tuple[str, str]:
    """Return a ``(sale_str, list_str)`` tuple of decimal-formatted prices.

    Args:
        price: Current sale price (may be ``None`` or ``0``).
        list_price: Original list price before discount.

    Returns:
        A 2-tuple of strings.  ``list_str`` is empty when *list_price* is
        ``None`` or not greater than *price*, indicating no discount.
        Both strings are empty when *price* is falsy.
    """
    if not price:
        return ("", "")
    sale_str = f"{price:.2f}"
    list_str = f"{list_price:.2f}" if list_price and list_price > price else ""
    return (sale_str, list_str)


def format_price_display(price: Optional[float], list_price: Optional[float]) -> str:
    """Build a human-readable price label for web-chat product cards.

    Examples:
        >>> format_price_display(9.90, 12.90)
        '9.90 (de 12.90)'
        >>> format_price_display(9.90, 9.90)
        '9.90'
        >>> format_price_display(None, None)
        'Price not available'
    """
    sale_str, list_str = format_price_pair(price, list_price)
    if not sale_str:
        return "Price not available"
    return f"{sale_str} (de {list_str})" if list_str and list_str != sale_str else sale_str


def get_currency_code(account_name: str) -> str:
    """Fetch the ISO 4217 currency code for a VTEX account via the Sales Channel API.

    Args:
        account_name: VTEX account name (e.g. ``"account"``).

    Returns:
        The currency code string (e.g. ``"BRL"``).
        Falls back to ``DEFAULT_CURRENCY`` on network errors or missing data.
    """
    url = f"{_VTEX_API}/api/catalog_system/pub/saleschannel/default?an={account_name}"
    t0 = time.time()
    try:
        resp = requests.get(url, timeout=10)
        print(f"[⏱ HTTP GET currency_code] {(time.time()-t0)*1000:.0f}ms — status={resp.status_code}")
        resp.raise_for_status()
        code = resp.json().get("CurrencyCode")
        return code if isinstance(code, str) else DEFAULT_CURRENCY
    except (requests.RequestException, ValueError) as e:
        print(f"[⏱ HTTP GET currency_code] {(time.time()-t0)*1000:.0f}ms — FAILED: {e}")
        return DEFAULT_CURRENCY


def get_account_name(base_url: str) -> str:
    """Extract the VTEX account name from a base URL.

    Example:
        >>> get_account_name("https://account.myvtex.com")
        'account'
    """
    try:
        return base_url.split("//")[-1].split(".")[0].replace("www.", "")
    except Exception:
        return ""


def clean_image_url(url: str) -> str:
    """Strip query-string and fragment from an image URL.

    Ensures a clean, cache-friendly URL is stored on the ``Product`` entity.
    Returns an empty string when *url* is falsy.
    """
    if not url:
        return ""
    return url.split("?")[0].split("#")[0]


def clean_html(text: str) -> str:
    """Convert an HTML-rich string to plain text.

    Unescapes HTML entities, removes all tags, and collapses whitespace.
    Returns an empty string when *text* is falsy or not a string.
    """
    if not text or not isinstance(text, str):
        return ""
    s = html.unescape(text)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", s).strip()
