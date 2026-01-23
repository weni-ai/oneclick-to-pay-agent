from weni import Tool
from weni.context import Context
from weni.responses import TextResponse
import requests
import json


class ProcessPaymentCard(Tool):
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
    
    def _create_orderform(self, base_url, headers, trade_policy):
        """Cria um novo OrderForm e retorna seu ID"""
        orderform_url = f"{base_url}/api/checkout/pub/orderForm/?forceNewCart=True&sc={trade_policy}"
        orderform_response = requests.get(orderform_url, headers=headers)
        
        if orderform_response.status_code != 200:
            error_msg = f"Erro ao criar OrderForm: {orderform_response.json()}"
            try:
                error_detail = orderform_response.json()
                error_msg += f" - {json.dumps(error_detail)}"
            except:
                pass
            raise Exception(error_msg)
        
        orderform_data = orderform_response.json()
        orderform_id = orderform_data.get("orderFormId")
        
        if not orderform_id:
            raise Exception("Erro: OrderFormId não encontrado na resposta")
        
        return orderform_id
    
    def _add_profile_data(self, base_url, headers, orderform_id, email, first_name, last_name, document, phone, trade_policy):
        """Adiciona os dados do perfil do cliente"""
        profile_url = f"{base_url}/api/checkout/pub/orderForm/{orderform_id}/attachments/clientProfileData?sc={trade_policy}"
        document = document.replace(".", "").replace("-", "")
        if len(document) == 11:
            document_type = "cpf"
        else:
            document_type = "cnpj"
        profile_data = {
            "email": email,
            "firstName": first_name,
            "lastName": last_name,
            "documentType": document_type,
            "document": document,
            "phone": phone
        }
        
        profile_response = requests.post(profile_url, headers=headers, json=profile_data)
        
        if profile_response.status_code != 200:
            raise Exception(f"Erro ao adicionar perfil: {profile_response.json()}")
        
        return profile_response.json()
    
    def _get_address_details(self, base_url, headers, postal_code):
        """Busca os detalhes do endereço a partir do CEP usando a API VTEX"""
        address_url = f"{base_url}/api/checkout/pub/postal-code/BRA/{postal_code}"
        
        try:
            address_response = requests.get(address_url, headers=headers)
            
            if address_response.status_code != 200:
                print(f"Erro ao consultar endereço: {address_response.status_code}")
                return None
                
            return address_response.json()
        except Exception as e:
            print(f"Erro ao consultar detalhes do endereço: {str(e)}")
            return None
    
    def _add_shipping_data(self, base_url, headers, orderform_id, first_name, last_name, postal_code, number_house, trade_policy, delivery_method, sla_id, address_complement):
        """Adiciona os dados de entrega (endereço + SLA)"""
        shipping_url = f"{base_url}/api/checkout/pub/orderForm/{orderform_id}/attachments/shippingData?sc={trade_policy}"
        
        orderform_response = requests.get(f"{base_url}/api/checkout/pub/orderForm/{orderform_id}?sc={trade_policy}", headers=headers)
        if orderform_response.status_code != 200:
            raise Exception(f"Erro ao obter orderForm para configurar entrega: {orderform_response.json()}")
            
        orderform_data = orderform_response.json()
        items_count = len(orderform_data.get("items", []))
        
        logistics_info = []
        for index in range(items_count):
            logistics_info.append({
                "itemIndex": index,
                "selectedDeliveryChannel": delivery_method,
                "selectedSla": sla_id
            })
        
        address_details = self._get_address_details(base_url, headers, postal_code)
        
        if address_details:
            address = {
                "addressType": "residential",
                "receiverName": f"{first_name} {last_name}",
                "isDisposable": True,
                "postalCode": postal_code,
                "city": address_details.get("city", ""),
                "state": address_details.get("state", ""),
                "country": "BRA",
                "street": address_details.get("street", "Rua não encontrada"),
                "number": number_house,
                "neighborhood": address_details.get("neighborhood", "Bairro"),
                "complement": address_complement,
                "reference": address_details.get("reference", None),
                "addressQuery": ""
            }
        
        if address_details.get("street") == "Rua não encontrada":
            return TextResponse({"status": "error", "message": "Endereço não encontrado"})

        shipping_data = {
            "logisticsInfo": logistics_info,
            "clearAddressIfPostalCodeNotFound": False,
            "selectedAddresses": [address]
        }
        
        shipping_response = requests.post(shipping_url, headers=headers, json=shipping_data)
        
        if shipping_response.status_code != 200:
            raise Exception(f"Erro ao adicionar dados de entrega: {shipping_response.json()}")
        
        return shipping_response.json()
    
    def _add_utm_source(self, base_url, headers, orderform_id, trade_policy):
        """Adiciona informações de UTM Source"""
        utm_url = f"{base_url}/api/checkout/pub/orderForm/{orderform_id}/attachments/marketingData?sc={trade_policy}"
        utm_data = {
            "utmSource": "Weni",
            "utmMedium": "WhatsApp",
            "utmCampaign": "Weni-WhatsApp"
        }
        
        utm_response = requests.post(utm_url, headers=headers, json=utm_data)
        
        if utm_response.status_code != 200:
            error_detail = utm_response.json() if utm_response.content else "Sem conteúdo"
            raise Exception(f"Erro ao adicionar UTM (status {utm_response.status_code}): {error_detail}")
        
        return utm_response.json()
    
    def _add_items(self, base_url, headers, orderform_id, product_items, trade_policy):
        """Adiciona os itens ao carrinho"""
        items_url = f"{base_url}/api/checkout/pub/orderForm/{orderform_id}/items?sc={trade_policy}"
        
        order_items = []
        for index, item in enumerate(product_items):
            product_retailer_id = item.get("product_retailer_id", "")
            parts = product_retailer_id.split("#")
            retailer_id = parts[0] if len(parts) > 0 else ""
            seller = parts[1] if len(parts) > 1 else "1"
            
            order_items.append({
                "id": retailer_id,
                "seller": seller,
                "quantity": item.get("quantity", 1),
                "index": index
            })
        
        items_data = {"orderItems": order_items}
        items_response = requests.post(items_url, headers=headers, json=items_data)
        
        if items_response.status_code != 200:
            error_detail = items_response.json() if items_response.content else "Sem conteúdo"
            raise Exception(f"Erro ao adicionar itens (status {items_response.status_code}): {error_detail}")
        
        response_json = items_response.json()
        messages = response_json.get("messages", [])
        error_messages = [msg for msg in messages if msg.get("status") == "error"]
        if error_messages:
            error_text = ", ".join([f"{msg.get('code')}: {msg.get('text')}" for msg in error_messages])
            raise Exception(f"Erro ao adicionar itens: {error_text}")
        
        return response_json
    
    def execute(self, context: Context) -> TextResponse:
        base_url = context.credentials.get("BASE_URL", "")
        vtex_appkey = context.credentials.get("VTEX_API_APPKEY", "")
        vtex_apptoken = context.credentials.get("VTEX_API_APPTOKEN", "")
        account_name = context.credentials.get("ACCOUNT_NAME", "")

        product_items = context.parameters.get("product_items", [])
        email = context.parameters.get("email", "")
        document = context.parameters.get("document", "")
        contact_name = context.parameters.get("contact_name", "")
        postal_code = context.parameters.get("postal_code", "")
        number_house = context.parameters.get("number_house", "")
        address_complement = context.parameters.get("address_complement", "")
        delivery_method = context.parameters.get("delivery_method", "")
        sla_id = context.parameters.get("sla_id", "")
        cvv = context.parameters.get("cvv", "")
       
        trade_policy = 1

        urn = context.contact.get("urn", "")
        phone = urn.replace("whatsapp:", "") if urn else ""
        
        if not email:
            return TextResponse(data=json.dumps({"error": "email is required"}))
        
        first_name = contact_name.split(" ")[0] if contact_name else ""
        last_name = " ".join(contact_name.split(" ")[1:]) if len(contact_name.split(" ")) > 1 else " "
        
        try:
            product_items = self._parse_product_items(product_items)

            headers = self._get_headers(vtex_appkey, vtex_apptoken)
            
            order_form_id = self._create_orderform(base_url, headers, trade_policy)
            
            self._add_items(base_url, headers, order_form_id, product_items, trade_policy)

            self._add_shipping_data(base_url, headers, order_form_id, first_name, last_name, postal_code, number_house, trade_policy, delivery_method, sla_id, address_complement)

            total_value = self._get_total_value(base_url, headers, order_form_id, trade_policy)

            self._add_profile_data(base_url, headers, order_form_id, email, first_name, last_name, document, phone, trade_policy)

            self._add_utm_source(base_url, headers, order_form_id, trade_policy)

            value, payment_response = self._add_payment_data(base_url, headers, order_form_id, total_value, trade_policy)

            transaction_data = self._create_transaction(base_url, headers, order_form_id, value)
            transaction_id = transaction_data.get("id")
            order_id = transaction_data.get("orderId")
            merchant_id = transaction_data.get("merchantId")
            merchant_name = transaction_data.get("merchantName")

            payment_info = self.get_payment_info(base_url, email)
            if not payment_info:
                return TextResponse(data=json.dumps({"error": "Could not find payment information"}))
            card_number = payment_info.get("cardNumber", "")
            order_group = transaction_data.get("orderGroup")
            bin_code = payment_info.get("bin", "")
            address_id = payment_info.get("addressId", "")
            account_id = payment_info.get("accountId", "")
            

            self._create_payment_transaction(account_name, headers, transaction_id, order_id, merchant_id, merchant_name, value, card_number, bin_code, address_id, account_id, cvv, contact_name)

            order_confirmation_response = self._process_order_confirmation(base_url, headers, order_group)
            print("Order Confirmation Response: ", order_confirmation_response)

            return TextResponse(data=json.dumps({
                "status": "success",
                "order_form_id": order_form_id,
                "transaction_id": transaction_id,
                "order_confirmation_response": order_confirmation_response
            }))
            
        except Exception as e:
            return TextResponse(data=json.dumps({"error": f"Erro durante processamento do pagamento: {str(e)}"}))

    def _get_headers(self, vtex_appkey, vtex_apptoken):
        """Retorna os headers para as requisições à API VTEX"""
        
        if not vtex_appkey or not vtex_apptoken:
            print("ERRO: Credenciais da API VTEX não encontradas no ambiente.")
            
        return {
            "X-Vtex-Api-Appkey": vtex_appkey,
            "X-Vtex-Api-Apptoken": vtex_apptoken, 
            "Content-Type": "application/json"
        }

    def _get_total_value(self, base_url, headers, orderform_id, trade_policy):
        """Obtém o valor total atualizado do carrinho"""
        orderform_response = requests.get(f"{base_url}/api/checkout/pub/orderForm/{orderform_id}?sc={trade_policy}", headers=headers)
        
        if orderform_response.status_code != 200:
            raise Exception(f"Erro ao obter valor total: {orderform_response.json()}")
        
        total_value = orderform_response.json().get("value", 0)
        return total_value    
    
    def get_payment_info(self, base_url, email):
        """Fetches payment information from the user's profile"""
        url = f"{base_url}/api/checkout/pub/profiles?email={email}"
        
        response = requests.get(url)
        
        if response.status_code != 200:
            print("Payment Info Response Status Code: ", response.status_code)
            return None
        
        try:
            response_json = response.json()
            
            if "availableAccounts" not in response_json:
                print("Available Accounts not found")
                print("Response JSON: ", response_json)
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
            
            if account.get("availableAddresses"):
                address_id = account.get("availableAddresses")[0]
                payment_info["addressId"] = address_id

            return payment_info
            
        except Exception:
            return {}
    
    def _add_payment_data(self, base_url, headers, orderform_id, total_value, trade_policy):
        """Adiciona os dados de pagamento"""
        payment_url = f"{base_url}/api/checkout/pub/orderForm/{orderform_id}/attachments/paymentData?sc={trade_policy}"
        payment_data = {
            "payments": [
                {
                    "hasDefaultBillingAddress": True,
                    "installmentsInterestRate": None,
                    "referenceValue": total_value,
                    "accountId": None,
                    "value": total_value,
                    "tokenId": None,
                    "paymentSystem": "2",
                    "installments": 1,
                    "isRegexValid": True
                }
            ]
        }
        
        payment_response = requests.post(payment_url, headers=headers, json=payment_data)
        print("Order Form ID: ", orderform_id)
        if payment_response.status_code != 200:
            raise Exception(f"Erro ao adicionar dados de pagamento: {payment_response.json()}")
        
        value = payment_response.json().get("value", 0)
        return value, payment_response.json()

    def _create_transaction(self, base_url, headers, order_form_id, total_value):
        """Cria a transação de pagamento"""
        transaction_url = f"{base_url}/api/checkout/pub/orderForm/{order_form_id}/transaction"
        transaction_data = {
            "referenceId": order_form_id,
            "savePersonalData": False,
            "optinNewsLetter": False,
            "value": total_value,
            "referenceValue": total_value,
            "interestValue": 0
        }

        transaction_response = requests.post(transaction_url, headers=headers, json=transaction_data)
        
        if transaction_response.status_code != 200:
            raise Exception(f"Erro ao criar transação: {transaction_response.json()}")
        
        transaction_json = transaction_response.json()

        result = {
            "id": transaction_json.get("id"),
            "orderGroup": transaction_json.get("orderGroup"),
            "merchantId": transaction_json.get("merchantTransactions", [{}])[0].get("id", ""),
            "merchantName": transaction_json.get("merchantTransactions", [{}])[0].get("merchantName", "")
        }
                
        return result
    

    def _process_order_confirmation(self, base_url, headers, order_group):
        """Processa a confirmação do pedido"""
        order_confirmation_url = f"{base_url}/api/checkout/pub/gatewayCallback/{order_group}"
        order_confirmation_response = requests.post(order_confirmation_url, headers=headers)
        if order_confirmation_response.status_code not in [200, 204]:
            try:
                error_detail = order_confirmation_response.json()
            except:
                error_detail = order_confirmation_response.text
            raise Exception(f"Erro ao processar confirmação do pedido: {error_detail}")
        
        # Para status 204 (No Content), não há corpo na resposta
        if order_confirmation_response.status_code == 204:
            return {"status": "success", "message": "Pedido processado com sucesso"}
        
        try:
            response_json = order_confirmation_response.json()
        except:
            response_json = {"raw_response": order_confirmation_response.text}
        
        return {"status": "success", "message": "Pedido processado com sucesso", "order_confirmation_response": response_json}
    
    def _create_payment_transaction(self, account_name, headers, transaction_id, order_id, merchant_id, merchant_name, total_value, card_number, bin_code, address_id, account_id, cvv, contact_name):
        """Cria a transação de pagamento com Cartão de Crédito"""
        payment_transaction_url = f"https://api.vtexvault.com/api/payments/transactions/{transaction_id}/payments?an={account_name}"
        payment_transaction_data = [
            {
                "paymentSystem": 2,
                "installments": 1,
                "installmentsInterestRate": 0,
                "installmentsValue": total_value,
                "value": total_value,
                "referenceValue": total_value,
                "id": merchant_id,
                "interestRate": 0,
                "installmentValue": total_value,
                "transaction": {
                    "id": transaction_id,
                    "merchantName": merchant_name
                },
                "fields": {
                        "cardNumber": card_number,
                        "cardHolder": contact_name,
                        "accountId": account_id,
                        "validationCode": cvv,
                        "addressId": address_id,
                        "bin": bin_code
                    },
                "currencyCode": "BRL",
                "originalPaymentIndex": 0
            }
        ]
        
        payment_transaction_response = requests.post(payment_transaction_url, headers=headers, json=payment_transaction_data)
        
        if payment_transaction_response.status_code not in [200, 201, 204]:
            print("Payment Transaction Response: ", payment_transaction_response.text)
            print("Payment Transaction Response Status Code: ", payment_transaction_response.status_code)
            try:
                error_detail = payment_transaction_response.json()
            except:
                error_detail = payment_transaction_response.text
            raise Exception(f"Erro ao criar transação de pagamento: {error_detail}")
        
        return {"status": "success", "message": "Transação de pagamento criada"}

    def send_button_order_form(self, auth_token, order_form_id, channel_uuid, contact_urn):
        """Sends order form tracking to Gallery"""
        url = "https://retailsetup.weni.ai/vtex/order-form-tracking/"

        headers = {
            "content-type": "application/json",
            "accept": "application/json",
            "Authorization": f"Bearer {auth_token}"
        }

        payload = {
            "order_form_id": order_form_id,
            "channel_uuid": channel_uuid,
            "contact_urn": contact_urn,
        }

        try:
            response = requests.post(url, headers=headers, json=payload)
        except Exception:
            pass


