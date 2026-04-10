from weni import Tool
from weni.context import Context
from weni.responses import TextResponse
from weni.broadcasts import OneClickPayment
import requests
import json
import time


class CreateCart(Tool):
    def execute(self, context: Context) -> TextResponse:
        email = context.parameters.get("email", "")
        product_items = context.parameters.get("product_items", [])
        base_url = context.credentials.get("BASE_URL", "")
        shipping_value = int(context.parameters.get("shipping_value", 0))

        try:
            if not email:
                raise Exception("Email é obrigatório para buscar informações de pagamento")

            payment_info = self._get_payment_info(email, base_url)
            if not payment_info:
                return TextResponse(data=json.dumps({"error": "Could not find payment information"}))

            parsed_items = _parse_product_items(product_items)
            items, subtotal = self._build_order_items(parsed_items)

            if not items or subtotal <= 0:
                return TextResponse(data=json.dumps({
                    "error": "Nenhum item válido encontrado. Verifique se os itens possuem 'item_price' em centavos e 'name'."
                }))

            last_four = payment_info.get("lastFourDigits", "")
            account_id = payment_info.get("accountId", "")
            ps_name = payment_info.get("paymentSystemName", "cartão")
            card_display = f"{ps_name} terminado em {last_four}" if last_four else ps_name
            total_amount = subtotal + shipping_value

            self.send_broadcast(OneClickPayment(
                text=f"Encontramos um {card_display} salvo na sua conta. Deseja usar este cartão para finalizar o pagamento?",
                reference_id=f"order_{int(time.time())}",
                last_four_digits=str(last_four),
                credential_id=str(account_id),
                total_amount=total_amount,
                items=items,
                subtotal=subtotal,
                tax_value=0,
                discount_value=0,
                shipping_value=shipping_value,
            ))

            return TextResponse(data=json.dumps({
                "status": "success",
                "payment_info": payment_info,
                "message": "Mensagem enviada ao usuário. Aguardando confirmação para processar pagamento."
            }))

        except Exception as e:
            return TextResponse(data=json.dumps({
                "error": f"Erro durante busca de informações de pagamento: {str(e)}"
            }))

    @staticmethod
    def _build_order_items(parsed_items: list[dict]) -> tuple[list[dict], int]:
        items = []
        subtotal = 0
        for item in parsed_items:
            if not isinstance(item, dict):
                continue
            item_value = item.get("item_price") or item.get("price", 0)
            if isinstance(item_value, (int, float)) and item_value < 100:
                item_value = int(item_value * 100)
            else:
                item_value = int(item_value)
            if item_value <= 0:
                continue
            quantity = int(item.get("quantity", 1))
            items.append({
                "retailer_id": item.get("product_retailer_id", ""),
                "name": item.get("name", "Produto"),
                "amount": item_value,
                "quantity": quantity,
            })
            subtotal += item_value * quantity
        return items, subtotal

    @staticmethod
    def _get_payment_info(email: str, base_url: str) -> dict | None:
        """Fetches saved card information from VTEX profiles API."""
        url = f"{base_url}/api/checkout/pub/profiles?email={email}"
        response = requests.get(url)
        if response.status_code != 200:
            return None
        try:
            data = response.json()
            accounts = data.get("availableAccounts", [])
            if not accounts:
                return None
            account = accounts[0]
            card_number = account.get("cardNumber", "")
            last_four = card_number.replace("*", "")[-4:] if card_number else ""
            return {
                "accountId": account.get("accountId", ""),
                "cardNumber": card_number,
                "lastFourDigits": last_four,
                "paymentSystem": account.get("paymentSystem"),
                "paymentSystemName": account.get("paymentSystemName", ""),
                "bin": account.get("bin", ""),
                "availableAddresses": account.get("availableAddresses", []),
            }
        except Exception:
            return None


def _parse_product_items(raw) -> list[dict]:
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
