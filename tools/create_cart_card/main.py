from weni import Tool
from weni.context import Context
from weni.responses import TextResponse
import requests
import json
import time


class CreateCart(Tool):
    def _parse_product_items(self, product_items_str):
        """Parse product items from string format to proper dictionary format"""
        try:
            if isinstance(product_items_str, list):
                parsed_items = []
                for item in product_items_str:
                    if isinstance(item, str):
                        try:
                            parsed_item = json.loads(item)
                            parsed_items.append(parsed_item)
                        except json.JSONDecodeError:
                            parsed_items.append(item)
                    elif isinstance(item, dict):
                        parsed_items.append(item)
                
                if parsed_items and all(isinstance(item, dict) for item in parsed_items):
                    return parsed_items
                if all(isinstance(item, dict) for item in product_items_str):
                    return product_items_str
            
            if isinstance(product_items_str, str):
                try:
                    if product_items_str.strip().startswith('[') and '{' in product_items_str:
                        try:
                            return json.loads(product_items_str)
                        except json.JSONDecodeError:
                            json_fixed = product_items_str.replace("'", '"')
                            return json.loads(json_fixed)
                except:
                    pass
            
            return []
        except Exception:
            return []
        
    def execute(self, context: Context) -> TextResponse:
        email = context.parameters.get("email", "")
        product_items = context.parameters.get("product_items", [])

        base_url = context.credentials.get("BASE_URL", "")
        auth_token = context.project.get("auth_token", "")

        channel_uuid = context.contact.get("channel_uuid", "")
        urn = context.contact.get("urn", "")
        project_uuid = context.project.get("uuid", "")

        shipping_value = context.parameters.get("shipping_value", 0)
        
        try:
            if not email:
                raise Exception("Email é obrigatório para buscar informações de pagamento")
            
            payment_info = self.get_payment_info(email, base_url)
            
            if not payment_info:
                return TextResponse(data=json.dumps({"error": "Could not find payment information"}))
            
            whatsapp_response = self.send_message_broadcast_wpp(
                payment_info, 
                product_items, 
                auth_token, 
                channel_uuid, 
                urn,
                project_uuid,
                shipping_value
            )
            
            return TextResponse(data=json.dumps({
                "status": "success",
                "payment_info": payment_info,
                "whatsapp": whatsapp_response,
                "message": "Mensagem enviada ao usuário. Aguardando confirmação para processar pagamento."
            }))
            
        except Exception as e:
            return TextResponse(data=json.dumps({"error": f"Erro durante busca de informações de pagamento: {str(e)}"}))

    def get_payment_info(self, email, base_url):
        """Fetches payment information from the user's profile"""
        url = f"{base_url}/api/checkout/pub/profiles?email={email}" 
        
        response = requests.get(url)
        
        if response.status_code != 200:
            return None
        
        try:
            response_json = response.json()
            
            if "availableAccounts" not in response_json:
                return None
            
            available_accounts = response_json.get("availableAccounts", [])
            
            if len(available_accounts) == 0:
                return None
            
            account = available_accounts[0]
            card_number = account.get("cardNumber", "")
            last_four_digits = card_number.replace("*", "")[-4:] if card_number else ""
            
            payment_info = {
                "accountId": account.get("accountId", ""),
                "cardNumber": card_number,
                "lastFourDigits": last_four_digits,
                "paymentSystem": account.get("paymentSystem"),
                "paymentSystemName": account.get("paymentSystemName", ""),
                "bin": account.get("bin", ""),
                "availableAddresses": account.get("availableAddresses", [])
            }
            
            return payment_info
            
        except Exception:
            return {"error": "Could not find payment information"}

    def send_message_broadcast_wpp(self, payment_info, product_items, auth_token, channel_uuid, urn, project_uuid, shipping_value):
        """Sends a message broadcast to the user via WhatsApp asking if they want to use the saved card"""
        if not payment_info:
            return {"error": "Could not find payment information"}
        
        account_id = payment_info.get("accountId", "")
        last_four_digits = payment_info.get("lastFourDigits", "")
        
        items = []
        subtotal = 0
        
        parsed_items = self._parse_product_items(product_items) if product_items else []
        
        for item in parsed_items:
            if not isinstance(item, dict):
                continue
            
            retailer_id = item.get("product_retailer_id", "")
            quantity = item.get("quantity", 1)
            name = item.get("name", "Produto")
            item_value = 0
            
            if "item_price" in item:
                item_value = item["item_price"]
            elif "price" in item:
                price = item["price"]
                if price < 100:
                    item_value = int(price * 100)
                else:
                    item_value = int(price)
            
            if item_value <= 0:
                continue
            
            final_item = {
                "retailer_id": retailer_id,
                "name": name,
                "amount": {
                    "value": int(item_value),
                    "offset": 100
                },
                "quantity": int(quantity)
            }
            
            items.append(final_item)
            subtotal += int(item_value) * int(quantity)
        
        tax_value = 0
        discount_value = 0
        items_total = subtotal + tax_value - discount_value + shipping_value
        
        # Validar que há pelo menos um item com valor válido
        if not items or items_total <= 0:
            return {"error": "Nenhum item válido encontrado. Verifique se os itens possuem 'item_price' em centavos e 'name'."}
        
        payment_system_name = payment_info.get("paymentSystemName", "cartão")
        card_display = f"{payment_system_name} terminado em {last_four_digits}" if last_four_digits else payment_system_name
        
        reference_id = f"order_{int(time.time())}"
        
        payload = {
            "urns": [urn],
            "channel": channel_uuid,
            "msg": {
                "text": f"Encontramos um {card_display} salvo na sua conta. Deseja usar este cartão para finalizar o pagamento?",
                "interaction_type": "order_details",
                "order_details": {
                    "reference_id": reference_id,
                    "type": "digital-goods",
                    "payment_settings": {
                        "type": "offsite_card_pay",
                        "offsite_card_pay": {
                            "last_four_digits": str(last_four_digits),
                            "credential_id": str(account_id)
                        }
                    },
                    "total_amount": int(items_total),
                    "order": {
                        "items": items,
                        "subtotal": int(subtotal),
                        "tax": {
                            "description": "Impostos",
                            "offset": 100,
                            "value": int(tax_value)
                        },
                        "discount": {
                            "description": "Desconto",
                            "offset": 100,
                            "value": int(discount_value)
                        },
                        "shipping": {
                            "description": "Frete",
                            "offset": 100,
                            "value": int(shipping_value)
                        }
                    }
                }
            }
        }
        
        if project_uuid:
            payload["project"] = project_uuid
        
        headers = {
            "Authorization": f"Bearer {auth_token}",
            "Content-Type": "application/json"
        }
        
        try:
            response = requests.post(
                "https://flows.stg.cloud.weni.ai/api/v2/whatsapp_broadcasts.json",
                json=payload,
                headers=headers
            )
            
            if response.status_code in [200, 201, 202]:
                try:
                    return response.json()
                except:
                    return {"status": "success", "message": "Message sent"}
            else:
                return {"error": f"Failed to send message: {response.status_code}", "details": response.text}
        except requests.exceptions.RequestException as e:
            return {"error": f"Connection error: {str(e)}"}
        except Exception as e:
            return {"error": f"Unexpected error: {str(e)}"}
