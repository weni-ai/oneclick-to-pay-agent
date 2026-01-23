from weni import Tool
from weni.context import Context
from weni.responses import TextResponse
import requests
import json


class GetDeliveryOptions(Tool):

    def execute(self, context: Context) -> TextResponse:
        product_items = context.parameters.get("product_items", [])
        postal_code = context.parameters.get("postal_code", "")
        delivery_method = context.parameters.get("delivery_method", "").lower()

        base_url = context.credentials.get("BASE_URL", "")
        vtex_appkey = context.credentials.get("VTEX_API_APPKEY", "")
        vtex_apptoken = context.credentials.get("VTEX_API_APPTOKEN", "")

        print(f"Product items: {product_items}")
        
        trade_policy = 1

        # Validar método de entrega
        if delivery_method not in ["delivery", "pickup-in-point"]:
            return TextResponse(data="Método de entrega deve ser 'delivery' ou 'pickup-in-point'")

        try:
            # Sempre processar product_items, independente do tipo
            product_items = self._parse_product_items(product_items)
            
            headers = self._get_headers(vtex_appkey, vtex_apptoken)
            
            orderform_id = self._create_orderform(base_url, headers, trade_policy)
            
            self._add_items(base_url, headers, orderform_id, product_items, trade_policy)
            
            
            # Fazer simulação de carrinho
            simulation_data = {
            "shippingData": {
                "selectedAddresses": [
                    {
                        "addressType": "search",
                        "postalCode": postal_code,
                        "country": "BRA"
                    }
                ]
            },
            "orderFormId": orderform_id
        }
            
            simulation_url = f"{base_url}/api/checkout/pub/orderForms/simulation"
            simulation_response = requests.post(simulation_url, headers=headers, json=simulation_data)
            
            if simulation_response.status_code != 200:
                error_msg = f"Erro na simulação de carrinho: {simulation_response.status_code}"
                try:
                    error_detail = simulation_response.json()
                    error_msg += f" - {json.dumps(error_detail)}"
                except:
                    error_msg += f" - {simulation_response.text}"
                return TextResponse({"error": error_msg})

            simulation_result = simulation_response.json()
            
            # Processar opções de entrega
            delivery_options = self._process_delivery_options(simulation_result, delivery_method)
            
            if not delivery_options:
                return TextResponse({"error": f"Nenhuma opção de {delivery_method} encontrada para o CEP {postal_code} --- simulation_result: {simulation_result} --- product_items: {product_items}"})
            
            return TextResponse(data=json.dumps({
                "delivery_method": delivery_method,
                "postal_code": postal_code,
                "options": delivery_options
            }, ensure_ascii=False))
            
        except Exception as e:
            print(f"ERRO: {str(e)}")
            return TextResponse({"error": f"Erro durante consulta de opções de entrega: {str(e)}"})
    
    def _parse_product_items(self, product_items_str):
        """Parse product items from string format to proper dictionary format"""
        try:
            if isinstance(product_items_str, list):
                print(f"Product items is a list: {product_items_str}")
                # Verificar se é uma lista de strings JSON que precisam ser parseadas
                parsed_items = []
                for item in product_items_str:
                    if isinstance(item, str):
                        # É uma string JSON, precisa fazer parse
                        try:
                            parsed_item = json.loads(item)
                            parsed_items.append(parsed_item)
                        except json.JSONDecodeError as e:
                            print(f"Error parsing item as JSON: {item}, error: {str(e)}")
                            # Se não conseguir fazer parse, tentar usar o item como está
                            parsed_items.append(item)
                    elif isinstance(item, dict):
                        # Já é um dicionário, usar diretamente
                        parsed_items.append(item)
                    else:
                        print(f"Unknown item type: {type(item)}")
                        parsed_items.append(item)
                
                # Se conseguiu parsear pelo menos um item, retornar a lista parseada
                if parsed_items and all(isinstance(item, dict) for item in parsed_items):
                    print(f"Successfully parsed list items: {parsed_items}")
                    return parsed_items
                
                # Se todos os itens já eram dicionários, retornar a lista original
                if all(isinstance(item, dict) for item in product_items_str):
                    print(f"All items already parsed: {product_items_str}")
                    return product_items_str
            
            print(f"Parsing product items string: {product_items_str}")
            
            # Primeiro, tentar fazer parse como JSON string
            try:
                # Se é uma string que parece JSON (começa com [ e contém {)
                if product_items_str.strip().startswith('[') and '{' in product_items_str:
                    # Tentar primeiro como JSON válido
                    try:
                        parsed_items = json.loads(product_items_str)
                        if isinstance(parsed_items, list):
                            print(f"Successfully parsed as JSON: {parsed_items}")
                            return parsed_items
                    except json.JSONDecodeError:
                        # Se falhar, tentar converter aspas simples para duplas
                        try:
                            # Substituir aspas simples por duplas para chaves e valores string
                            json_fixed = product_items_str.replace("'", '"')
                            parsed_items = json.loads(json_fixed)
                            if isinstance(parsed_items, list):
                                print(f"Successfully parsed as JSON (after fixing quotes): {parsed_items}")
                                return parsed_items
                        except json.JSONDecodeError:
                            print("Not a valid JSON string, trying legacy format...")
            except Exception as e:
                print(f"Error during JSON parsing: {str(e)}")
            
            # Se não conseguiu fazer parse como JSON, usar o método antigo
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
                        key = key.strip()
                        value = value.strip()
                        
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
    
    def _create_orderform(self, base_url, headers, trade_policy):
        """Cria um novo OrderForm e retorna seu ID"""
        orderform_url = f"{base_url}/api/checkout/pub/orderForm/?forceNewCart=True&sc={trade_policy}"
        orderform_response = requests.get(orderform_url, headers=headers)
        
        if orderform_response.status_code != 200:
            error_msg = f"Erro ao criar OrderForm: {orderform_response.status_code}"
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
    
    def _add_items(self, base_url, headers, orderform_id, product_items, trade_policy):
        """Adiciona os itens ao carrinho"""
        items_url = f"{base_url}/api/checkout/pub/orderForm/{orderform_id}/items?sc={trade_policy}"
        
        # Converter formato de produto_items para formato VTEX
        order_items = []
        for index, item in enumerate(product_items):
            product_retailer_id = item.get("product_retailer_id", "")
            parts = product_retailer_id.split("#")
            
            # Get retailer_id and seller, with fallback values
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
            error_msg = f"Erro ao adicionar itens: {items_response.status_code}"
            try:
                error_detail = items_response.json()
                error_msg += f" - {json.dumps(error_detail)}"
            except:
                pass
            raise Exception(error_msg)
        
        return items_response.json() 
    
    def _get_headers(self, vtex_appkey, vtex_apptoken):
        """Retorna os headers para as requisições à API VTEX"""
        
        if not vtex_appkey or not vtex_apptoken:
            print("ERRO: Credenciais da API VTEX não encontradas no ambiente.")
            
        return {
            "X-Vtex-Api-Appkey": vtex_appkey,
            "X-Vtex-Api-Apptoken": vtex_apptoken, 
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
    
    def _process_delivery_options(self, simulation_result, delivery_method):
        """Processa as opções de entrega da simulação"""
        delivery_options = []
        
        try:
            logistics_info = simulation_result.get("logisticsInfo", [])
            
            for logistics in logistics_info:
                slas = logistics.get("slas", [])
                
                for sla in slas:
                    sla_delivery_channel = sla.get("deliveryChannel", "")
                    
                    # Filtrar apenas pelo método de entrega desejado
                    if sla_delivery_channel == delivery_method:
                        option = {
                            "id": sla.get("id", ""),
                            "name": sla.get("name", ""),
                            "shippingEstimate": sla.get("shippingEstimate", ""),
                            "price": sla.get("price", 0)
                        }
                        
                        # Para pickup-in-point, adicionar informações da loja
                        if delivery_method == "pickup-in-point":
                            pickup_store_info = sla.get("pickupStoreInfo", {})
                            if pickup_store_info.get("isPickupStore", False):
                                option["store_name"] = pickup_store_info.get("friendlyName", "")
                                
                                address = pickup_store_info.get("address", {})
                                if address:
                                    option["store_address"] = {
                                        "street": address.get("street", ""),
                                        "number": address.get("number", ""),
                                        "neighborhood": address.get("neighborhood", ""),
                                        "city": address.get("city", ""),
                                        "state": address.get("state", ""),
                                        "postalCode": address.get("postalCode", "")
                                    }
                                
                                additional_info = pickup_store_info.get("additionalInfo", "")
                                if additional_info:
                                    option["additional_info"] = additional_info
                        
                        # Evitar duplicatas
                        if not any(opt["id"] == option["id"] for opt in delivery_options):
                            delivery_options.append(option)
            
            # Ordenar por preço (menor primeiro)
            delivery_options.sort(key=lambda x: x["price"])
            
        except Exception as e:
            print(f"Erro ao processar opções de entrega: {str(e)}")
        
        return delivery_options
