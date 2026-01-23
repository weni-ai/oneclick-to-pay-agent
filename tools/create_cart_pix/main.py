from weni import Tool
from weni.context import Context
from weni.responses import TextResponse
import requests
import json
import re
import uuid
import ast
from weni.events import Event


class CreateCart(Tool):
    def send_button_order_form(self, auth_token, order_form_id, channel_uuid, contact_urn):
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

        print("Gallery Order Form: ", payload)

        response = requests.post(url, headers=headers, json=payload)
        status_code = response.status_code
        if status_code == 200:
            print("cart sent successfully to Gallery")
        else:
            print("Error to send cart to Gallery. Response: ", response.json())


    def _parse_product_items(self, product_items_str):
        """Parse product items from string format to proper dictionary format"""
        try:
            if isinstance(product_items_str, list):
                print(f"Product items is a list: {product_items_str}")
                parsed_items = []
                for item in product_items_str:
                    if isinstance(item, str):
                        try:
                            parsed_item = json.loads(item)
                            parsed_items.append(parsed_item)
                        except json.JSONDecodeError as e:
                            print(f"Error parsing item as JSON: {item}, error: {str(e)}")
                            parsed_items.append(item)
                    elif isinstance(item, dict):
                        parsed_items.append(item)
                    else:
                        print(f"Unknown item type: {type(item)}")
                        parsed_items.append(item)
                
                if parsed_items and all(isinstance(item, dict) for item in parsed_items):
                    print(f"Successfully parsed list items: {parsed_items}")
                    return parsed_items
                
                if all(isinstance(item, dict) for item in product_items_str):
                    print(f"All items already parsed: {product_items_str}")
                    return product_items_str
            
            print(f"Parsing product items string: {product_items_str}")
            
            # Tentar parsear como JSON primeiro
            try:
                if product_items_str.strip().startswith('[') and '{' in product_items_str:
                    try:
                        parsed_items = json.loads(product_items_str)
                        if isinstance(parsed_items, list):
                            print(f"Successfully parsed as JSON: {parsed_items}")
                            return parsed_items
                    except json.JSONDecodeError:
                        try:
                            json_fixed = product_items_str.replace("'", '"')
                            parsed_items = json.loads(json_fixed)
                            if isinstance(parsed_items, list):
                                print(f"Successfully parsed as JSON (after fixing quotes): {parsed_items}")
                                return parsed_items
                        except json.JSONDecodeError:
                            print("Not a valid JSON string, trying Python literal eval...")
            except Exception as e:
                print(f"Error during JSON parsing: {str(e)}")
            
            # Tentar parsear como representação Python (com aspas simples)
            # Isso lida com casos como: "{'key': 'value'}, {'key2': 'value2'}" (sem colchetes)
            if '{' in product_items_str and ("product_retailer_id" in product_items_str or "'" in product_items_str):
                try:
                    # Se parece uma lista de dicionários Python, tentar adicionar colchetes se necessário
                    items_str_clean = product_items_str.strip()
                    if not items_str_clean.startswith('['):
                        items_str_clean = '[' + items_str_clean + ']'
                    
                    parsed_items = ast.literal_eval(items_str_clean)
                    if isinstance(parsed_items, list):
                        print(f"Successfully parsed as Python literal: {parsed_items}")
                        return parsed_items
                except (ValueError, SyntaxError) as e:
                    print(f"Failed to parse as Python literal: {str(e)}")
                    print("Trying legacy format...")
            
            # Fallback: tentar parsear manualmente (formato legado)
            items_str = product_items_str.strip('[]').strip()
            if not items_str:
                return []
            
            items = []
            for item_str in items_str.split('}, {'):
                item_str = item_str.strip('{}').strip()
                
                item_dict = {}
                for pair in item_str.split(','):
                    pair = pair.strip()
                    if '=' in pair:
                        key, value = pair.split('=', 1)
                        key = key.strip().strip("'\"")
                        value = value.strip().strip("'\"")
                        
                        if value.isdigit():
                            value = int(value)
                        elif value.replace('.', '').isdigit():
                            value = float(value)
                        
                        item_dict[key] = value
                
                if item_dict:
                    items.append(item_dict)
            
            return items
                
        except Exception as e:
            print(f"Error parsing product items: {str(e)}")
            return []
    
    def execute(self, context: Context) -> TextResponse:
        product_items = context.parameters.get("product_items", [])
        postal_code = context.parameters.get("postal_code", "")
        document = context.parameters.get("document", "")
        number_house = context.parameters.get("number_house", "")
        email = context.parameters.get("email", "")
        delivery_method = context.parameters.get("delivery_method", "")
        sla_id = context.parameters.get("sla_id", "")
        address_complement = context.parameters.get("address_complement","")
        
        api_token = context.credentials.get("API_TOKEN", "")
        base_url = context.credentials.get("BASE_URL", "")
        vtex_appkey = context.credentials.get("VTEX_API_APPKEY", "")
        vtex_apptoken = context.credentials.get("VTEX_API_APPTOKEN", "")

        contact_name = context.parameters.get("contact_name", "")
        urn = context.contact.get("urn", "whatsapp:555191269029")
        phone = urn.replace("whatsapp:", "")
        channel_uuid = context.contact.get("channel_uuid", "")

        auth_token = context.project.get("auth_token", "")
        first_name = contact_name.split(" ")[0]
        if len(contact_name.split(" ")) > 1:
            last_name = contact_name.split(" ")[1]
        else:
            last_name = " "
            
        trade_policy = 1

        try:
            # Sempre processar product_items, independente do tipo
            product_items = self._parse_product_items(product_items)

            headers = self._get_headers(vtex_appkey, vtex_apptoken)
            
            orderform_id = self._create_orderform(base_url, headers, trade_policy)
            
            self._add_profile_data(base_url, headers, orderform_id, email, first_name, last_name, document, phone, trade_policy)
            
            self._add_utm_source(base_url, headers, orderform_id, trade_policy)
            
            self._add_items(base_url, headers, orderform_id, product_items, trade_policy)
            
            self._add_shipping_data(base_url, headers, orderform_id, first_name, last_name, postal_code, number_house, trade_policy, delivery_method, sla_id, address_complement)
            
            total_value = self._get_total_value(base_url, headers, orderform_id, trade_policy)
            
            value, payment_response = self._add_payment_data(base_url, headers, orderform_id, total_value, trade_policy)
            
            transaction_data = self._create_transaction(base_url, headers, orderform_id, value)
            transaction_id = transaction_data.get("id")
            order_id = transaction_data.get("orderGroup")
            merchant_id = transaction_data.get("merchantId")
            merchant_name = transaction_data.get("merchantName")
            self._create_payment_transaction(base_url, headers, transaction_id, order_id, merchant_id, merchant_name, value)
            
            pix_data = self._create_pix_code(base_url, headers, order_id)
            print("Pix Data: ", pix_data)
            self.send_button_order_form(auth_token, orderform_id, channel_uuid, urn)
            Event.register(
                Event(
                    event_name="weni_nexus_data",
                    key="checkout",
                    value_type="string",
                    value=orderform_id,
                    metadata={
                        "agent_name": "Create Cart"
                    }
                )
            )
            
            pix_code, company_name, pix_code_raw = self._extract_pix_info(pix_data)
            if not pix_code or not company_name:
                return TextResponse(data="Erro ao extrair informações do código PIX")
            
            # Validar se a chave PIX é um UUID válido (formato EVP para Meta)
            is_valid_uuid = self._is_valid_uuid(pix_code)
            
            # Se não for UUID válido, retornar mensagem de texto simples sem enviar para Weni Flows
            if not is_valid_uuid:
                text_message = (
                    f"Seu pedido #{order_id}-01 foi criado com sucesso!\n\n"
                    f"Código PIX (copie e cole):\n{pix_code_raw}\n\n"
                    f"Copie o código acima e cole no aplicativo do seu banco para completar o pagamento."
                )
                
                # Retorna o TextResponse diretamente sem chamar Weni Flows
                return TextResponse(data=f"{text_message}\n\nObrigado pela preferência")
            
            # Se o PIX for válido, prossegue com a busca por detalhes do pedido e envio para Weni Flows
            orderform_response = requests.get(f"{base_url}/api/checkout/pub/orderForm/{orderform_id}?sc={trade_policy}", headers=headers)
            order_details = orderform_response.json() if orderform_response.status_code == 200 else {}
            
            # Chama Weni Flows APENAS se o PIX for válido (e usa o formato order_details)
            weni_flows_success = self._send_to_weni_flows(urn, order_id, pix_code, company_name, pix_code_raw, product_items, order_details, api_token, channel_uuid)
            
            if not weni_flows_success:
                print("AVISO: Falha ao enviar para Weni Flows, mas o pedido foi criado com sucesso")
                # Retorna mensagem de erro com código PIX manual
                error_message = (
                    f"Ocorreu um erro ao processar o envio do pagamento, mas seu pedido #{order_id}-01 foi criado com sucesso!\n\n"
                    f"Código PIX (copie e cole):\n{pix_code_raw}\n\n"
                    f"Copie o código acima e cole no aplicativo do seu banco para completar o pagamento.\n\n"
                    f"Obrigado pela preferência"
                )
                return TextResponse(data=error_message)
            
            payment_details = {
                "status": "success",
                "order_id": f"{order_id}-01",
                "transaction_id": transaction_id,
                "payment_method": "pix",
                "order_details": order_details
            }
            print(payment_details)
            
            return TextResponse(data=f"Pix foi enviado para o usuário. Pix data: {pix_data}, payment data: {json.dumps(payment_details)}")
            
        except Exception as e:
            print(f"ERRO: {str(e)}")
            return TextResponse(data=f"Erro durante processamento do carrinho: {str(e)}")
    
    def _get_headers(self, vtex_appkey, vtex_apptoken):
        """Retorna os headers para as requisições à API VTEX"""
        
        if not vtex_appkey or not vtex_apptoken:
            print("ERRO: Credenciais da API VTEX não encontradas no ambiente.")
            
        return {
            "X-Vtex-Api-Appkey": vtex_appkey,
            "X-Vtex-Api-Apptoken": vtex_apptoken, 
            "Content-Type": "application/json"
        }
    
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
    
    def _add_utm_source(self, base_url, headers, orderform_id, trade_policy):
        """Adiciona informações de UTM Source"""
        utm_url = f"{base_url}/api/checkout/pub/orderForm/{orderform_id}/attachments/marketingData?sc={trade_policy}"
        utm_data = {
            "utmSource": "weni-whatsapp"
        }
        
        utm_response = requests.post(utm_url, headers=headers, json=utm_data)
        
        if utm_response.status_code != 200:
            raise Exception(f"Erro ao adicionar UTM: {utm_response.json()}")
        
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
        
        items_data = {
            "orderItems": order_items
        }
        
        print(f"Sending items data: {json.dumps(items_data)}")  # Debug log
        
        items_response = requests.post(items_url, headers=headers, json=items_data)
        
        if items_response.status_code != 200:
            error_msg = f"Erro ao adicionar itens: {items_response.json()}"
            try:
                error_detail = items_response.json()
                error_msg += f" - {json.dumps(error_detail)}"
            except:
                pass
            raise Exception(error_msg)
        
        return items_response.json()
    
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
    
    def _add_shipping_data(self, base_url, headers, orderform_id, first_name, last_name, postal_code, number_house, trade_policy, delivery_method, sla_id,address_complement):
        """Adiciona os dados de entrega"""
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
    
    def _get_total_value(self, base_url, headers, orderform_id, trade_policy):
        """Obtém o valor total atualizado do carrinho"""
        orderform_response = requests.get(f"{base_url}/api/checkout/pub/orderForm/{orderform_id}?sc={trade_policy}", headers=headers)
        
        if orderform_response.status_code != 200:
            raise Exception(f"Erro ao obter valor total: {orderform_response.json()}")
        
        total_value = orderform_response.json().get("value", 0)
        return total_value
    
    def _add_payment_data(self, base_url, headers, orderform_id, total_value, trade_policy):
        """Adiciona os dados de pagamento"""
        payment_url = f"{base_url}/api/checkout/pub/orderForm/{orderform_id}/attachments/paymentData?sc={trade_policy}"
        payment_data = {
            "payments": [
                {
                    "paymentSystem": 125,
                    "paymentSystemName": "Pix",
                    "group": "instantPayment",
                    "installments": 1,
                    "value": total_value,
                    "referenceValue": total_value,
                    "hasDefaultBillingAddress": False
                }
            ]
        }
        
        payment_response = requests.post(payment_url, headers=headers, json=payment_data)
        print("Order Form ID: ", orderform_id)
        if payment_response.status_code != 200:
            raise Exception(f"Erro ao adicionar dados de pagamento: {payment_response.json()}")
        
        value = payment_response.json().get("value", 0)
        return value, payment_response.json()
    
    def _create_transaction(self, base_url, headers, orderform_id, total_value):
        """Cria a transação de pagamento"""
        transaction_url = f"{base_url}/api/checkout/pub/orderForm/{orderform_id}/transaction"
        transaction_data = {
            "referenceId": orderform_id,
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
        
        print(result)
        
        return result
    
    def _create_payment_transaction(self, base_url, headers, transaction_id, order_id, merchant_id, merchant_name, total_value):
        """Cria a transação de pagamento PIX"""
        payment_transaction_url = f"{base_url}/api/payments/transactions/{transaction_id}/payments?orderId={order_id}"
        payment_transaction_data = [
            {
                "paymentSystem": 125,
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
                "currencyCode": "BRL",
                "originalPaymentIndex": 0
            }
        ]
        
        print(payment_transaction_data)
        
        payment_transaction_response = requests.post(payment_transaction_url, headers=headers, json=payment_transaction_data)
        
        print(payment_transaction_response)
        
        if payment_transaction_response.status_code not in [200, 201]:
            raise Exception(f"Erro ao criar transação de pagamento: {payment_transaction_response.json()}")
        
        return {"status": "success", "message": "Transação de pagamento criada"}
    
    def _create_pix_code(self, base_url, headers, order_id):
        """Cria o código PIX e retorna os dados"""
        pix_url = f"{base_url}/api/checkout/pub/gatewayCallback/{order_id}"
        
        pix_response = requests.post(pix_url, headers=headers)
        
        if pix_response.content:
            try:
                pix_data = pix_response.json()
            except:
                
                print("Erro ao decodificar resposta PIX como JSON")
        return pix_data
    
    def _extract_pix_info(self, pix_data):
        """Extrai informações do código PIX e nome da empresa"""       
        if not pix_data:
            return None, None, None
        
        # Inicializar variáveis com valores padrão
        pix_code = None
        company_name = None
        pix_code_raw = None
        
        try:
            if "paymentAuthorizationAppCollection" in pix_data:
                for app in pix_data.get("paymentAuthorizationAppCollection", []):
                    if app.get("appName") == "vtex.pix-payment":
                        app_payload_str = app.get("appPayload", "{}")
                                                
                        try:
                            pix_payload = json.loads(app_payload_str)
                            
                            if "code" in pix_payload:
                                pix_code_raw = pix_payload["code"]
                                print("Código PIX bruto:", pix_code_raw)
                                
                                uuid_match = re.search(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", pix_code_raw)
                                if uuid_match:
                                    pix_code = uuid_match.group(1)
                                else:
                                    bcb_match = re.search(r"br\.gov\.bcb\.pix0136([a-zA-Z0-9-]+)", pix_code_raw)
                                    if bcb_match:
                                        pix_code = bcb_match.group(1)
                                        print("BCB PIX extraído:", pix_code)
                                    else:
                                        print("Nenhum padrão de código PIX encontrado")
                                
                                company_match = re.search(r"BR59(\d*)([A-Za-z0-9\s]+?)(\d{4})", pix_code_raw)
                                if company_match:
                                    company_name = company_match.group(2).strip()
                                    print(f"Nome da empresa extraído: {company_name}")
                                else:
                                    print("Padrão de nome de empresa não encontrado")                                
                                break
                        except json.JSONDecodeError as e:
                            print(f"Erro ao decodificar payload PIX como JSON: {e}")
                            continue
        except Exception as e:
            print(f"Erro na extração do código PIX: {str(e)}")
        
        if pix_code:
            print(pix_code)
        
        return pix_code, company_name, pix_code_raw
    
    def _is_valid_uuid(self, uuid_string):
        """Valida se a string é um UUID válido (formato EVP para Meta)"""
        if not uuid_string:
            return False
        try:
            uuid.UUID(uuid_string)
            return True
        except (ValueError, AttributeError):
            return False
    
    def process_payment_info(self, cart_items, payment_method):
        total = sum(item.get("price", 0) * item.get("quantity", 0) for item in cart_items)
        
        payment_details = {
            "total": total,
            "payment_method": payment_method,
            "items_count": len(cart_items),
            "status": "pending"
        }
        
        print(payment_details)
        
        return payment_details

    def _send_to_weni_flows(self, urn, order_id, pix_code, company_name, pix_code_raw, product_items, order_details, api_token, channel_uuid):
        """Envia os dados do pedido para a API Weni Flows"""
        
        # Validações de parâmetros obrigatórios
        if not api_token:
            print("ERRO: api_token não fornecido")
            return False
        
        if not urn:
            print("ERRO: urn não fornecido")
            return False
        
        if not pix_code:
            print("ERRO: pix_code não fornecido")
            return False
        
        if not company_name:
            print("ERRO: company_name não fornecido")
            return False
        
        weni_url = "https://flows.weni.ai/api/v2/whatsapp_broadcasts.json"
    
        
        items = []
        
        # Verificar se há itens indisponíveis nas mensagens de erro
        unavailable_item_ids = set()
        if order_details and "messages" in order_details:
            for message in order_details.get("messages", []):
                if message.get("status") == "error" and message.get("code") == "ORD027":
                    # Extrair o ID do item das mensagens de erro
                    fields = message.get("fields", {})
                    if "id" in fields:
                        unavailable_item_ids.add(str(fields["id"]))
                        print(f"Item indisponível detectado: {fields.get('name', 'Produto')} (ID: {fields['id']})")
        
        try:
            for item in product_items:
                if not isinstance(item, dict):
                    print(f"Skipping non-dict item: {item}")
                    continue
                
                retailer_id = item.get("product_retailer_id", "")
                parts = retailer_id.split("#")
                item_id = parts[0] if len(parts) > 0 else ""
                
                # Pular itens indisponíveis
                if item_id in unavailable_item_ids:
                    print(f"Pulando item indisponível: {item_id}")
                    continue
                    
                quantity = item.get("quantity", 1)
                name = item.get("name", "Produto")
                item_value = 0
                
                # Buscar nome e preço corretos no order_details
                item_found_in_order = False
                if order_details and "items" in order_details:
                    for order_item in order_details.get("items", []):
                        if str(order_item.get("id", "")) == item_id:
                            # Usar skuName como nome do produto
                            name = order_item.get("skuName", order_item.get("name", "Produto"))
                            item_value = order_item.get("sellingPrice", 0)
                            item_found_in_order = True
                            break
                
                # Se o item não foi encontrado no order_details, pode estar indisponível
                if not item_found_in_order:
                    print(f"Item não encontrado no order_details, pulando: {item_id}")
                    continue
                
                # Fallback para preços do item original se o sellingPrice da VTEX for 0
                # (caso o item foi encontrado mas o preço não está disponível)
                if item_value == 0:
                    if "item_price" in item:
                        # item_price já vem em centavos conforme o formato esperado
                        item_value = item["item_price"]
                        print(f"Usando item_price como fallback para item {item_id}: {item_value}")
                    elif "price" in item:
                        # price pode estar em reais, converter para centavos
                        price = item["price"]
                        # Se o valor for menor que 100, provavelmente está em reais
                        if price < 100:
                            item_value = int(price * 100)
                            print(f"Convertendo price de reais para centavos para item {item_id}: {price} -> {item_value}")
                        else:
                            item_value = int(price)
                            print(f"Usando price como fallback para item {item_id}: {item_value}")
                
                # Validar que o valor do item é válido antes de adicionar
                if item_value <= 0:
                    print(f"AVISO: Item {item_id} tem valor inválido ({item_value}), pulando item")
                    continue
                
                final_item = {
                    "retailer_id": retailer_id,
                    "name": name,
                    "amount": {
                        "value": int(item_value),  # Valor em centavos
                        "offset": 100
                    },
                    "quantity": quantity
                }
                
                items.append(final_item)
                   
        except Exception as e:
            print(f"ERRO ao processar itens: {str(e)}")
            raise e
        
        # Verificar se há itens disponíveis para processar
        if not items:
            error_msg = "Nenhum item disponível para processamento. "
            if unavailable_item_ids:
                error_msg += f"Itens indisponíveis: {', '.join(unavailable_item_ids)}"
            else:
                error_msg += "Todos os itens foram filtrados."
            print(error_msg)
            raise Exception(error_msg)
        
        items_total = 0
        tax_value = 0
        shipping_value = 0
        discount_value = 0
        final_total = 0
        
        if order_details and "totals" in order_details:
            totalizers = order_details.get("totals", [])
            
            if isinstance(totalizers, list):
                for totalizer in totalizers:
                    totalizer_id = totalizer.get("id", "")
                    totalizer_value = totalizer.get("value", 0)
                    
                    if totalizer_id == "Items":
                        items_total = int(totalizer_value)  # Valor já em centavos
                    elif totalizer_id == "Shipping":
                        shipping_value = int(totalizer_value)  # Valor já em centavos
                    elif totalizer_id == "Discounts":
                        discount_value = int(abs(totalizer_value))  # Desconto é negativo, então pegamos o valor absoluto
                    elif totalizer_id == "Tax":
                        tax_value = int(totalizer_value)  # Valor já em centavos
            
            final_total = int(order_details.get("value", 0))
        else:
            items_total = sum(item["amount"]["value"] * item["quantity"] for item in items)
            final_total = items_total
        
        calculated_items_sum = sum(item["amount"]["value"] * item["quantity"] for item in items)
        
        # Se temos dados dos totals da VTEX, usar sempre esses valores para garantir consistência
        if order_details and "totals" in order_details and items_total > 0:
            # Os valores dos totals da VTEX são sempre os corretos
            print(f"Usando valores dos totals da VTEX: Items={items_total}, Desconto={discount_value}, Final={final_total}")
        else:
            # Fallback: calcular com base nos itens disponíveis
            print(f"Fallback: Calculando com base nos itens disponíveis: {calculated_items_sum}")
            items_total = calculated_items_sum
            final_total = items_total - discount_value + shipping_value + tax_value
        
        body = {
            "urns": [
                urn
            ],
            "channel": channel_uuid,
            "msg": {
                "text": f"Seu pedido #{order_id}-01 foi criado com sucesso! Copie o código PIX abaixo para completar o pagamento.",
                "footer": "Obrigado pela preferência",
                "interaction_type": "order_details",
                "order_details": {
                    "reference_id": f"{order_id}-01",
                    "payment_settings": {
                        "type": "digital-goods",
                        "pix_config": {
                            "key": pix_code,
                            "key_type": "EVP",
                            "merchant_name": company_name,
                            "code": pix_code_raw
                        }
                    },
                    "total_amount": final_total,
                    "order": {
                        "items": items,
                        "subtotal": items_total,
                        "tax": {
                            "description": "Impostos",
                            "offset": 100,
                            "value": tax_value
                        },
                        "discount": {
                            "description": "Desconto",
                            "offset": 100,
                            "value": discount_value
                        },
                        "shipping": {
                            "description": "Frete",
                            "offset": 100,
                            "value": shipping_value
                        }
                    }
                }
            }
        }
       
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Token {api_token}"
        }
        
        try:
            response = requests.post(weni_url, headers=headers, json=body)
            
            if response.status_code in [200, 201, 202]:
                try:
                    response_data = response.json()
                    print(f"Resposta recebida: {response_data}")
                except (ValueError, json.JSONDecodeError):
                    print(f"Resposta recebida (não-JSON): {response.text}")
                return True
            else:
                print(f"Status: {response.status_code}")
                print(f"Payload enviado: {json.dumps(body, ensure_ascii=False)}")
                try:
                    error_data = response.json()
                    print(f"Resposta recebida (JSON): {error_data}")
                except (ValueError, json.JSONDecodeError):
                    print(f"Resposta recebida (texto): {response.text}")
                return False
        except requests.exceptions.RequestException as e:
            print(f"ERRO de conexão ao enviar dados para a API Weni: {str(e)}")
            print(f"Payload que seria enviado: {json.dumps(body, ensure_ascii=False)}")
            return False
        except Exception as e:
            print(f"ERRO inesperado ao enviar dados para a API Weni: {str(e)}")
            print(f"Payload que seria enviado: {json.dumps(body, ensure_ascii=False)}")
            return False
