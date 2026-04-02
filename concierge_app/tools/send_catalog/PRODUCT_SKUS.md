# Parâmetro product_skus

A tool **Send Catalog** recebe os produtos agrupados por categoria no parâmetro `product_skus`.

## Formato

```json
[
  { "category_name": "Nome da categoria", "skus_ids": ["id1", "id2"] },
  { "category_name": "Outra categoria", "skus_ids": ["id3"] }
]
```

## Campos

### category_name (obrigatório)

- **Tipo:** string  
- **Uso:** Nome curto da categoria que será exibido no catálogo (ex.: "Camisas Polo", "Bermudas", "Ofertas até R$150").  
- **Quem preenche:** O agente, com base nos resultados da Search Reserva Products e no que o usuário pediu.  
- **Recomendações:** Use português; seja breve e claro; pode refletir tipo de produto, tema ou faixa de preço.

### skus_ids (obrigatório)

- **Tipo:** array de strings (IDs de SKU).  
- **Uso:** Lista de `sku_id` dos produtos que pertencem a essa categoria.  
- **Origem:** Valores `sku_id` retornados pela tool **Search Reserva Products**.  
- **Regras:** Cada `sku_id` deve aparecer em no máximo uma categoria; não repita o mesmo ID em categorias diferentes.

## Como o agente deve montar product_skus

1. Chamar **Search Reserva Products** e obter a lista de produtos (com `sku_id`, `sku_name`, preços, etc.).  
2. Agrupar os SKUs selecionados por tipo, tema ou critério combinado (ex.: "camisas" em uma categoria, "bermudas" em outra).  
3. Para cada grupo, definir um `category_name` em português e listar os `skus_ids` desse grupo.  
4. Enviar na **Send Catalog** o array `product_skus` com esses objetos.

## Exemplo

Usuário pede: "Quero ver camisas azuis e bermudas."

- Search retorna, entre outros: camisas com `sku_id` 574267, 516832; bermudas com 586756, 578263.  
- Agente monta:

```json
[
  { "category_name": "Camisas azuis", "skus_ids": ["574267", "516832"] },
  { "category_name": "Bermudas", "skus_ids": ["586756", "578263"] }
]
```

- Envia esse `product_skus` para a Send Catalog junto com `simple_message` e demais parâmetros.

## Limite de produtos

A tool aplica um limite total de 10 produtos no catálogo. Categorias com muitos SKUs são processadas em ordem até atingir esse limite.
