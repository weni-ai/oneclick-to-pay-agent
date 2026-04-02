"""
request_cvv_flow: VTEX checkout → payment-registrations → WhatsApp Flow (CVV).
Single module for Lambda/Weni (only main.py is packaged).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from typing import Any

import requests
from weni import Tool
from weni.context import Context
from weni.responses import FinalResponse, TextResponse

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_WHATSAPP_FLOW_ID = "1495814778922275"
DEFAULT_PAYMENT_REGISTRATIONS_BASE = "https://913e-177-37-184-15.ngrok-free.app"
FLOWS_BROADCAST_URL = "https://flows.stg.cloud.weni.ai/api/v2/whatsapp_broadcasts.json"
TRADE_POLICY = 1
REQUEST_TIMEOUT_S = 60


# ---------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------


def format_brl_cents(cents: Any) -> str:
    try:
        n = int(cents or 0)
    except (TypeError, ValueError):
        n = 0
    s = f"{n / 100.0:,.2f}"
    return "R$ " + s.replace(",", "X").replace(".", ",").replace("X", ".")


def payment_system_to_int(value: Any) -> int:
    if value is None:
        return 2
    try:
        return int(value)
    except (TypeError, ValueError):
        return 2


def parse_product_items(raw: Any) -> list[dict]:
    """Normalize product_items from agent (list/str/mixed) into list[dict]."""
    try:
        if isinstance(raw, list):
            if raw and all(isinstance(x, dict) for x in raw):
                return list(raw)
            out: list[dict] = []
            for item in raw:
                if isinstance(item, str):
                    try:
                        out.append(json.loads(item))
                    except json.JSONDecodeError:
                        continue
                elif isinstance(item, dict):
                    out.append(item)
            if out and all(isinstance(x, dict) for x in out):
                return out
        if isinstance(raw, str) and raw.strip().startswith("["):
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return json.loads(raw.replace("'", '"'))
    except Exception:
        pass
    return []


def http_error(response: requests.Response, action: str) -> RuntimeError:
    return RuntimeError(f"{action}: HTTP {response.status_code} {response.text}")


# ---------------------------------------------------------------------------
# VTEX checkout (until transaction + saved card profile)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CheckoutSnapshot:
    order_form_id: str
    value_cents: int
    transaction: dict[str, Any]
    payment_info: dict[str, Any]


class VtexCheckout:
    """Checkout público VTEX até criar transação; sem Vault nem gatewayCallback."""

    def __init__(self, base_url: str, app_key: str, app_token: str) -> None:
        self._base = base_url.rstrip("/")
        self._headers = {
            "X-Vtex-Api-Appkey": app_key,
            "X-Vtex-Api-Apptoken": app_token,
            "Content-Type": "application/json",
        }

    def _of(self, order_form_id: str, path: str = "") -> str:
        return (
            f"{self._base}/api/checkout/pub/orderForm/{order_form_id}{path}"
            f"?sc={TRADE_POLICY}"
        )

    def _get_json(self, url: str) -> dict[str, Any]:
        r = requests.get(url, headers=self._headers, timeout=REQUEST_TIMEOUT_S)
        if r.status_code != 200:
            raise http_error(r, "GET orderForm")
        return r.json()

    def _post_json(self, url: str, body: Any) -> dict[str, Any]:
        r = requests.post(url, headers=self._headers, json=body, timeout=REQUEST_TIMEOUT_S)
        if r.status_code != 200:
            raise http_error(r, "POST")
        return r.json()

    def order_form_value(self, order_form_id: str) -> int:
        data = self._get_json(self._of(order_form_id))
        return int(data.get("value") or 0)

    def create_order_form(self) -> str:
        url = f"{self._base}/api/checkout/pub/orderForm/?forceNewCart=True&sc={TRADE_POLICY}"
        r = requests.get(url, headers=self._headers, timeout=REQUEST_TIMEOUT_S)
        if r.status_code != 200:
            raise http_error(r, "create orderForm")
        oid = r.json().get("orderFormId")
        if not oid:
            raise RuntimeError("orderFormId missing")
        return oid

    def add_items(self, order_form_id: str, items: list[dict]) -> None:
        order_items = []
        for index, item in enumerate(items):
            pid = item.get("product_retailer_id", "")
            parts = pid.split("#")
            rid = parts[0] if parts else ""
            seller = parts[1] if len(parts) > 1 else "1"
            order_items.append(
                {
                    "id": rid,
                    "seller": seller,
                    "quantity": item.get("quantity", 1),
                    "index": index,
                }
            )
        data = self._post_json(self._of(order_form_id, "/items"), {"orderItems": order_items})
        errors = [m for m in data.get("messages", []) if m.get("status") == "error"]
        if errors:
            msg = ", ".join(f"{m.get('code')}: {m.get('text')}" for m in errors)
            raise RuntimeError(f"add items: {msg}")

    def add_shipping(
        self,
        order_form_id: str,
        first_name: str,
        last_name: str,
        postal_code: str,
        number_house: str,
        delivery_method: str,
        sla_id: str,
        address_complement: str,
    ) -> None:
        of = self._get_json(self._of(order_form_id))
        n = len(of.get("items", []))
        logistics = [
            {"itemIndex": i, "selectedDeliveryChannel": delivery_method, "selectedSla": sla_id}
            for i in range(n)
        ]
        cep_url = f"{self._base}/api/checkout/pub/postal-code/BRA/{postal_code}"
        cr = requests.get(cep_url, headers=self._headers, timeout=REQUEST_TIMEOUT_S)
        if cr.status_code != 200:
            raise RuntimeError("CEP não encontrado")
        ad = cr.json()
        if ad.get("street") == "Rua não encontrada":
            raise RuntimeError("Endereço não encontrado")
        address = {
            "addressType": "residential",
            "receiverName": f"{first_name} {last_name}".strip(),
            "isDisposable": True,
            "postalCode": postal_code,
            "city": ad.get("city", ""),
            "state": ad.get("state", ""),
            "country": "BRA",
            "street": ad.get("street", "Rua não encontrada"),
            "number": number_house,
            "neighborhood": ad.get("neighborhood", "Bairro"),
            "complement": address_complement,
            "reference": ad.get("reference"),
            "addressQuery": "",
        }
        body = {
            "logisticsInfo": logistics,
            "clearAddressIfPostalCodeNotFound": False,
            "selectedAddresses": [address],
        }
        self._post_json(self._of(order_form_id, "/attachments/shippingData"), body)

    def add_profile(
        self,
        order_form_id: str,
        email: str,
        first_name: str,
        last_name: str,
        document: str,
        phone: str,
    ) -> None:
        doc = document.replace(".", "").replace("-", "")
        doc_type = "cpf" if len(doc) == 11 else "cnpj"
        self._post_json(
            self._of(order_form_id, "/attachments/clientProfileData"),
            {
                "email": email,
                "firstName": first_name,
                "lastName": last_name,
                "documentType": doc_type,
                "document": doc,
                "phone": phone,
            },
        )

    def add_utm(self, order_form_id: str) -> None:
        self._post_json(
            self._of(order_form_id, "/attachments/marketingData"),
            {
                "utmSource": "Weni",
                "utmMedium": "WhatsApp",
                "utmCampaign": "Weni-WhatsApp",
            },
        )

    def add_payment_placeholder(self, order_form_id: str, total_cents: int) -> None:
        self._post_json(
            self._of(order_form_id, "/attachments/paymentData"),
            {
                "payments": [
                    {
                        "hasDefaultBillingAddress": True,
                        "installmentsInterestRate": None,
                        "referenceValue": total_cents,
                        "accountId": None,
                        "value": total_cents,
                        "tokenId": None,
                        "paymentSystem": "2",
                        "installments": 1,
                        "isRegexValid": True,
                    }
                ]
            },
        )

    def create_transaction(self, order_form_id: str, value_cents: int) -> dict[str, Any]:
        url = f"{self._base}/api/checkout/pub/orderForm/{order_form_id}/transaction"
        body = {
            "referenceId": order_form_id,
            "savePersonalData": False,
            "optinNewsLetter": False,
            "value": value_cents,
            "referenceValue": value_cents,
            "interestValue": 0,
        }
        r = requests.post(url, headers=self._headers, json=body, timeout=REQUEST_TIMEOUT_S)
        if r.status_code != 200:
            raise http_error(r, "create transaction")
        tj = r.json()
        mt = (tj.get("merchantTransactions") or [{}])[0]
        return {
            "id": tj.get("id"),
            "orderGroup": tj.get("orderGroup"),
            "merchantId": mt.get("id", ""),
            "merchantName": mt.get("merchantName", ""),
        }

    @staticmethod
    def fetch_saved_card(base_url: str, email: str) -> dict[str, Any] | None:
        url = f"{base_url.rstrip('/')}/api/checkout/pub/profiles?email={email}"
        r = requests.get(url, timeout=REQUEST_TIMEOUT_S)
        if r.status_code != 200:
            return None
        try:
            js = r.json()
            accounts = js.get("availableAccounts") or []
            if not accounts:
                return None
            acc = accounts[0]
            card_number = acc.get("cardNumber", "")
            last4 = card_number.replace("*", "")[-4:] if card_number else ""
            info: dict[str, Any] = {
                "accountId": acc.get("accountId", ""),
                "cardNumber": card_number,
                "lastFourDigits": last4,
                "paymentSystem": acc.get("paymentSystem"),
                "paymentSystemName": acc.get("paymentSystemName", ""),
                "bin": acc.get("bin", ""),
                "availableAddresses": acc.get("availableAddresses", []),
            }
            if acc.get("availableAddresses"):
                info["addressId"] = acc["availableAddresses"][0]
            return info
        except Exception:
            return None

    def run(
        self,
        *,
        email: str,
        document: str,
        contact_name: str,
        postal_code: str,
        number_house: str,
        address_complement: str,
        delivery_method: str,
        sla_id: str,
        product_items: Any,
        phone: str,
    ) -> CheckoutSnapshot:
        if not email:
            raise ValueError("email is required")
        items = parse_product_items(product_items)
        if not items:
            raise ValueError("product_items inválido ou vazio")

        parts = contact_name.split()
        first_name = parts[0] if parts else ""
        last_name = " ".join(parts[1:]) if len(parts) > 1 else " "

        oid = self.create_order_form()
        self.add_items(oid, items)
        self.add_shipping(
            oid, first_name, last_name, postal_code, number_house,
            delivery_method, sla_id, address_complement,
        )
        subtotal = self.order_form_value(oid)
        self.add_profile(oid, email, first_name, last_name, document, phone)
        self.add_utm(oid)
        self.add_payment_placeholder(oid, subtotal)
        total_cents = self.order_form_value(oid)
        tx = self.create_transaction(oid, total_cents)
        card = self.fetch_saved_card(self._base, email)
        if not card:
            raise RuntimeError("Could not find payment information for this email")

        return CheckoutSnapshot(
            order_form_id=oid,
            value_cents=total_cents,
            transaction=tx,
            payment_info=card,
        )


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------


class RequestCvvFlow(Tool):
    """Registra contexto de pagamento e envia Flow do WhatsApp para coletar CVV."""

    def execute(self, context: Context) -> FinalResponse | TextResponse:
        p = context.parameters
        cred = context.credentials
        contact = context.contact
        project = context.project

        err = self._validate_early(cred, contact, project)
        if err:
            return err

        snap = self._run_vtex(context)
        if isinstance(snap, TextResponse):
            return snap

        display = self._flow_display_values(p, snap)
        if isinstance(display, TextResponse):
            return display

        flow_token = self._make_flow_token(contact.get("urn", ""))
        vtex_exec = self._build_vtex_execution(
            cred, p, snap, display["card_due_date"]
        )

        reg = self._post_registration(
            cred, project, contact.get("urn", ""), flow_token, vtex_exec
        )
        if reg:
            return reg

        return self._post_whatsapp_flow(cred, project, contact, display, flow_token)

    @staticmethod
    def _validate_early(cred: dict, contact: dict, project: dict) -> TextResponse | None:
        if not cred.get("CHANNEL_UUID"):
            return TextResponse(
                data=json.dumps(
                    {"error": "CHANNEL_UUID credential is required for payment registration"}
                )
            )
        bearer = cred.get("PAYMENT_REGISTRATIONS_BEARER_TOKEN") or project.get(
            "auth_token", ""
        )
        if not bearer:
            return TextResponse(
                data=json.dumps(
                    {
                        "error": "PAYMENT_REGISTRATIONS_BEARER_TOKEN or project auth_token is required"
                    }
                )
            )
        if not contact.get("channel_uuid"):
            return TextResponse(
                data=json.dumps({"error": "channel_uuid is required on contact"})
            )
        if not contact.get("urn"):
            return TextResponse(data=json.dumps({"error": "contact urn is required"}))
        return None

    def _run_vtex(self, context: Context) -> CheckoutSnapshot | TextResponse:
        cred = context.credentials
        p = context.parameters
        phone = (context.contact.get("urn") or "").replace("whatsapp:", "")
        try:
            cx = VtexCheckout(
                cred.get("BASE_URL", ""),
                cred.get("VTEX_API_APPKEY", ""),
                cred.get("VTEX_API_APPTOKEN", ""),
            )
            return cx.run(
                email=p.get("email", ""),
                document=p.get("document", ""),
                contact_name=p.get("contact_name", ""),
                postal_code=p.get("postal_code", ""),
                number_house=p.get("number_house", ""),
                address_complement=p.get("address_complement", ""),
                delivery_method=p.get("delivery_method", ""),
                sla_id=p.get("sla_id", ""),
                product_items=p.get("product_items", []),
                phone=phone,
            )
        except Exception as e:
            return TextResponse(data=json.dumps({"error": f"Checkout VTEX: {str(e)}"}))

    @staticmethod
    def _flow_display_values(p: dict, snap: CheckoutSnapshot) -> dict | TextResponse:
        valor = (p.get("valor_pedido") or "").strip()
        final = (p.get("final_cartao") or "").strip()
        due = (p.get("card_due_date") or "").strip()
        if not valor:
            valor = format_brl_cents(snap.value_cents)
        if not final:
            final = snap.payment_info.get("lastFourDigits", "")
        if not final:
            return TextResponse(
                data=json.dumps(
                    {
                        "error": "final_cartao is empty and could not be derived from saved card profile"
                    }
                )
            )
        return {"valor_pedido": valor, "final_cartao": final, "card_due_date": due}

    @staticmethod
    def _make_flow_token(urn: str) -> str:
        ts = datetime.now().strftime("%Y%m%d%H%M%S")
        num = urn.replace("whatsapp:", "")
        return f"ft_{ts}{num}|{urn}"

    @staticmethod
    def _build_vtex_execution(
        cred: dict, p: dict, snap: CheckoutSnapshot, card_due_date: str
    ) -> dict[str, Any]:
        v = snap.value_cents
        pi = snap.payment_info
        tx = snap.transaction
        name = (p.get("contact_name") or "").strip() or " "
        return {
            "account_name": cred.get("ACCOUNT_NAME", ""),
            "transaction_id": tx.get("id", ""),
            "order_group": tx.get("orderGroup", ""),
            "merchant_name": tx.get("merchantName", ""),
            "payment_system": payment_system_to_int(pi.get("paymentSystem")),
            "installments": 1,
            "currency_code": "BRL",
            "value": int(v),
            "installments_interest_rate": 0,
            "installments_value": int(v),
            "reference_value": int(v),
            "card_holder": name,
            "card_number": pi.get("cardNumber", ""),
            "card_due_date": card_due_date,
            "bin": pi.get("bin", "") or "",
            "address_id": pi.get("addressId", "") or "",
            "account_id": pi.get("accountId", "") or "",
        }

    @staticmethod
    def _post_registration(
        cred: dict,
        project: dict,
        urn: str,
        flow_token: str,
        vtex_execution: dict[str, Any],
    ) -> TextResponse | None:
        base = (
            cred.get("PAYMENT_REGISTRATIONS_BASE_URL") or DEFAULT_PAYMENT_REGISTRATIONS_BASE
        ).rstrip("/")
        url = f"{base}/v1/payment-registrations"
        bearer = cred.get("PAYMENT_REGISTRATIONS_BEARER_TOKEN") or project.get(
            "auth_token", ""
        )
        body = {
            "flow_token": flow_token,
            "channel_uuid": cred.get("CHANNEL_UUID", ""),
            "contact_urn": urn,
            "vtex_execution": vtex_execution,
        }
        headers = {
            "Authorization": f"Bearer {bearer}",
            "Content-Type": "application/json",
            "ngrok-skip-browser-warning": "true",
        }
        r = requests.post(url, headers=headers, json=body, timeout=REQUEST_TIMEOUT_S)
        if r.status_code in (200, 201, 202, 204):
            return None
        detail: Any = r.text
        try:
            detail = r.json()
        except Exception:
            pass
        return TextResponse(
            data=json.dumps(
                {
                    "error": "Payment registration failed",
                    "status": r.status_code,
                    "detail": detail,
                }
            )
        )

    def _post_whatsapp_flow(
        self,
        cred: dict,
        project: dict,
        contact: dict,
        display: dict,
        flow_token: str,
    ) -> FinalResponse | TextResponse:
        auth_token = project.get("auth_token", "")
        if not auth_token:
            return TextResponse(
                data=json.dumps(
                    {"error": "project auth_token is required to send WhatsApp flow"}
                )
            )
        flow_id = cred.get("WHATSAPP_CVV_FLOW_ID") or DEFAULT_WHATSAPP_FLOW_ID
        payload = {
            "urns": [contact.get("urn")],
            "channel": contact.get("channel_uuid"),
            "msg": {
                "text": "Para finalizar sua compra, preencha o seguinte dado",
                "interaction_type": "flow_msg",
                "flow_message": {
                    "flow_id": str(flow_id),
                    "flow_cta": "Confirmar Agora",
                    "flow_mode": "published",
                    "flow_screen": "COLETAR_DADO",
                    "flow_data": {
                        "valor_pedido": display["valor_pedido"],
                        "final_cartao": display["final_cartao"],
                    },
                    "flow_token": flow_token,
                },
            },
        }
        flow_headers = {
            "Authorization": f"Bearer {auth_token}",
            "Content-Type": "application/json",
        }
        r = requests.post(
            FLOWS_BROADCAST_URL, headers=flow_headers, json=payload, timeout=REQUEST_TIMEOUT_S
        )
        if r.status_code not in (200, 201, 202):
            detail: Any = r.text
            try:
                detail = r.json()
            except Exception:
                pass
            return TextResponse(
                data=json.dumps(
                    {
                        "error": "Failed to send CVV WhatsApp flow",
                        "status": r.status_code,
                        "detail": detail,
                    }
                )
            )
        return FinalResponse()
