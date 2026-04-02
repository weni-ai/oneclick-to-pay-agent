"""Product entity used throughout the catalog pipeline.

Holds enriched product data fetched from VTEX Intelligent Search,
including pricing, images, and formatted display fields populated
during the web-channel flow.
"""

from dataclasses import dataclass
from typing import Optional, List


@dataclass
class Product:
    """Represents a single SKU ready for catalog display.

    Required fields are populated directly from the VTEX API response.
    Optional/display fields (``price_display``, ``*_formatted``,
    ``currency``) are set later by the enrichment step in
    ``SendCatalog._send_web``.

    Attributes:
        sku_id: VTEX SKU identifier (numeric string, no seller suffix).
        name: Full product name (``nameComplete`` or ``productName``).
        brand: Product brand as returned by VTEX.
        image: Cleaned URL of the first valid JPG/PNG image.
        variations: Human-readable variation strings, e.g. ``["Cor: Azul"]``.
        material: Composition / material extracted from specification groups.
        sku_name: Display name used in catalog cards (usually equals *name*).
        product_link: Deep-link to the product page including ``skuId`` param.
        price_display: Human-readable price string (e.g. ``"9.90 (de 12.90)"``).
        sale_price_formatted: Sale price as a decimal string (e.g. ``"9.90"``).
        list_price_formatted: List price as a decimal string, empty when equal to sale price.
        list_price: Original list price in float, or ``None`` if unavailable.
        sale_price: Best sale price in float (Pix > Visa > first installment).
        currency: ISO 4217 currency code (e.g. ``"BRL"``).
        description: Plain-text product description (HTML tags stripped).
    """

    sku_id: str
    name: str
    brand: str
    image: str
    variations: List[str]
    material: str
    sku_name: str
    product_link: str
    price_display: str = ""
    sale_price_formatted: str = ""
    list_price_formatted: str = ""
    list_price: Optional[float] = None
    sale_price: Optional[float] = None
    currency: str = ""
    description: str = ""
