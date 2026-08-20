"""
Product -> Order -> OrderItem CRUD API.

Single Lambda behind API Gateway (proxy integration) backed by one DynamoDB
table using a single-table design:

    Product    PK = PRODUCT#<productId>    SK = #META
    Order      PK = PRODUCT#<productId>    SK = ORDER#<orderId>
    OrderItem  PK = PRODUCT#<productId>    SK = ITEM#<orderId>#<itemId>

This is a test-target API for the PerfSage TestGen / Executor agents.
Higher rate limit (500 rps) for performance testing at scale.
"""
import datetime
import decimal
import json
import os
import re
import uuid

import boto3
from boto3.dynamodb.conditions import Key

TABLE_NAME = os.environ.get("TABLE_NAME", "")
if not TABLE_NAME:
    raise RuntimeError("TABLE_NAME environment variable is required")

_dynamodb = boto3.resource("dynamodb")
_table = _dynamodb.Table(TABLE_NAME)


class _DecimalEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, decimal.Decimal):
            return int(o) if o % 1 == 0 else float(o)
        return super().default(o)


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _new_id():
    return uuid.uuid4().hex[:12]


def _response(status, body):
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body, cls=_DecimalEncoder),
    }


def _parse_body(event):
    body = event.get("body") or "{}"
    if isinstance(body, str):
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return {}
    return body


def handler(event, context):
    method = event.get("httpMethod", "GET")
    path = event.get("path", "/")

    # Route: /health
    if path == "/health":
        return _response(200, {"status": "ok", "service": "ecommerce-api"})

    # Route: /products
    if path == "/products" and method == "POST":
        return _create_product(event)
    if path == "/products" and method == "GET":
        return _list_products()

    # Route: /products/{productId}
    m = re.match(r"^/products/([^/]+)$", path)
    if m:
        product_id = m.group(1)
        if method == "GET":
            return _get_product(product_id)
        if method == "PUT":
            return _update_product(product_id, event)
        if method == "DELETE":
            return _delete_product(product_id)

    # Route: /products/{productId}/orders
    m = re.match(r"^/products/([^/]+)/orders$", path)
    if m:
        product_id = m.group(1)
        if method == "POST":
            return _create_order(product_id, event)
        if method == "GET":
            return _list_orders(product_id)

    # Route: /products/{productId}/orders/{orderId}
    m = re.match(r"^/products/([^/]+)/orders/([^/]+)$", path)
    if m:
        product_id, order_id = m.group(1), m.group(2)
        if method == "GET":
            return _get_order(product_id, order_id)
        if method == "PUT":
            return _update_order(product_id, order_id, event)
        if method == "DELETE":
            return _delete_order(product_id, order_id)

    # Route: /products/{productId}/orders/{orderId}/items
    m = re.match(r"^/products/([^/]+)/orders/([^/]+)/items$", path)
    if m:
        product_id, order_id = m.group(1), m.group(2)
        if method == "POST":
            return _create_item(product_id, order_id, event)
        if method == "GET":
            return _list_items(product_id, order_id)

    # Route: /products/{productId}/orders/{orderId}/items/{itemId}
    m = re.match(r"^/products/([^/]+)/orders/([^/]+)/items/([^/]+)$", path)
    if m:
        product_id, order_id, item_id = m.group(1), m.group(2), m.group(3)
        if method == "GET":
            return _get_item(product_id, order_id, item_id)
        if method == "PUT":
            return _update_item(product_id, order_id, item_id, event)
        if method == "DELETE":
            return _delete_item(product_id, order_id, item_id)

    return _response(404, {"message": f"Not found: {method} {path}"})


# ── Products ─────────────────────────────────────────────────────────────

def _create_product(event):
    body = _parse_body(event)
    name = body.get("name", "").strip()
    if not name:
        return _response(400, {"message": "'name' is required"})

    product_id = _new_id()
    item = {
        "PK": f"PRODUCT#{product_id}",
        "SK": "#META",
        "entity": "product",
        "productId": product_id,
        "name": name,
        "category": body.get("category", ""),
        "price": decimal.Decimal(str(body.get("price", 0))),
        "createdAt": _now(),
        "updatedAt": _now(),
    }
    _table.put_item(Item=item)
    return _response(201, {k: v for k, v in item.items() if not k.startswith("PK") and k != "SK"})


def _list_products():
    resp = _table.scan(
        FilterExpression="entity = :e",
        ExpressionAttributeValues={":e": "product"},
    )
    items = [{k: v for k, v in i.items() if not k.startswith("PK") and k != "SK"} for i in resp.get("Items", [])]
    return _response(200, {"count": len(items), "items": items})


def _get_product(product_id):
    resp = _table.get_item(Key={"PK": f"PRODUCT#{product_id}", "SK": "#META"})
    item = resp.get("Item")
    if not item:
        return _response(404, {"message": f"Product '{product_id}' not found"})
    return _response(200, {k: v for k, v in item.items() if not k.startswith("PK") and k != "SK"})


def _update_product(product_id, event):
    existing = _table.get_item(Key={"PK": f"PRODUCT#{product_id}", "SK": "#META"}).get("Item")
    if not existing:
        return _response(404, {"message": f"Product '{product_id}' not found"})

    body = _parse_body(event)
    name = body.get("name", existing.get("name", ""))
    _table.update_item(
        Key={"PK": f"PRODUCT#{product_id}", "SK": "#META"},
        UpdateExpression="SET #n = :n, category = :c, price = :p, updatedAt = :u",
        ExpressionAttributeNames={"#n": "name"},
        ExpressionAttributeValues={
            ":n": name,
            ":c": body.get("category", existing.get("category", "")),
            ":p": decimal.Decimal(str(body.get("price", existing.get("price", 0)))),
            ":u": _now(),
        },
    )
    return _response(200, {"productId": product_id, "name": name, "updatedAt": _now()})


def _delete_product(product_id):
    existing = _table.get_item(Key={"PK": f"PRODUCT#{product_id}", "SK": "#META"}).get("Item")
    if not existing:
        return _response(404, {"message": f"Product '{product_id}' not found"})

    # Cascade delete orders and items
    resp = _table.query(KeyConditionExpression=Key("PK").eq(f"PRODUCT#{product_id}"))
    with _table.batch_writer() as batch:
        for item in resp.get("Items", []):
            batch.delete_item(Key={"PK": item["PK"], "SK": item["SK"]})
    return _response(200, {"message": f"Product '{product_id}' deleted"})


# ── Orders ───────────────────────────────────────────────────────────────

def _create_order(product_id, event):
    existing = _table.get_item(Key={"PK": f"PRODUCT#{product_id}", "SK": "#META"}).get("Item")
    if not existing:
        return _response(404, {"message": f"Product '{product_id}' not found"})

    body = _parse_body(event)
    order_id = _new_id()
    item = {
        "PK": f"PRODUCT#{product_id}",
        "SK": f"ORDER#{order_id}",
        "entity": "order",
        "productId": product_id,
        "orderId": order_id,
        "customerName": body.get("customerName", ""),
        "quantity": body.get("quantity", 1),
        "status": body.get("status", "pending"),
        "createdAt": _now(),
        "updatedAt": _now(),
    }
    _table.put_item(Item=item)
    return _response(201, {k: v for k, v in item.items() if not k.startswith("PK") and k != "SK"})


def _list_orders(product_id):
    resp = _table.query(
        KeyConditionExpression=Key("PK").eq(f"PRODUCT#{product_id}") & Key("SK").begins_with("ORDER#"),
    )
    items = [{k: v for k, v in i.items() if not k.startswith("PK") and k != "SK"} for i in resp.get("Items", [])]
    return _response(200, {"count": len(items), "items": items})


def _get_order(product_id, order_id):
    resp = _table.get_item(Key={"PK": f"PRODUCT#{product_id}", "SK": f"ORDER#{order_id}"})
    item = resp.get("Item")
    if not item:
        return _response(404, {"message": f"Order '{order_id}' not found"})
    return _response(200, {k: v for k, v in item.items() if not k.startswith("PK") and k != "SK"})


def _update_order(product_id, order_id, event):
    existing = _table.get_item(Key={"PK": f"PRODUCT#{product_id}", "SK": f"ORDER#{order_id}"}).get("Item")
    if not existing:
        return _response(404, {"message": f"Order '{order_id}' not found"})

    body = _parse_body(event)
    _table.update_item(
        Key={"PK": f"PRODUCT#{product_id}", "SK": f"ORDER#{order_id}"},
        UpdateExpression="SET customerName = :cn, quantity = :q, #st = :s, updatedAt = :u",
        ExpressionAttributeNames={"#st": "status"},
        ExpressionAttributeValues={
            ":cn": body.get("customerName", existing.get("customerName", "")),
            ":q": body.get("quantity", existing.get("quantity", 1)),
            ":s": body.get("status", existing.get("status", "pending")),
            ":u": _now(),
        },
    )
    return _response(200, {"orderId": order_id, "status": body.get("status", "pending"), "updatedAt": _now()})


def _delete_order(product_id, order_id):
    existing = _table.get_item(Key={"PK": f"PRODUCT#{product_id}", "SK": f"ORDER#{order_id}"}).get("Item")
    if not existing:
        return _response(404, {"message": f"Order '{order_id}' not found"})

    # Cascade delete items
    resp = _table.query(
        KeyConditionExpression=Key("PK").eq(f"PRODUCT#{product_id}") & Key("SK").begins_with(f"ITEM#{order_id}#"),
    )
    with _table.batch_writer() as batch:
        for item in resp.get("Items", []):
            batch.delete_item(Key={"PK": item["PK"], "SK": item["SK"]})
        batch.delete_item(Key={"PK": f"PRODUCT#{product_id}", "SK": f"ORDER#{order_id}"})
    return _response(200, {"message": f"Order '{order_id}' deleted"})


# ── Order Items ──────────────────────────────────────────────────────────

def _create_item(product_id, order_id, event):
    existing = _table.get_item(Key={"PK": f"PRODUCT#{product_id}", "SK": f"ORDER#{order_id}"}).get("Item")
    if not existing:
        return _response(404, {"message": f"Order '{order_id}' not found"})

    body = _parse_body(event)
    item_id = _new_id()
    item = {
        "PK": f"PRODUCT#{product_id}",
        "SK": f"ITEM#{order_id}#{item_id}",
        "entity": "orderItem",
        "productId": product_id,
        "orderId": order_id,
        "itemId": item_id,
        "sku": body.get("sku", ""),
        "quantity": body.get("quantity", 1),
        "unitPrice": decimal.Decimal(str(body.get("unitPrice", 0))),
        "createdAt": _now(),
        "updatedAt": _now(),
    }
    _table.put_item(Item=item)
    return _response(201, {k: v for k, v in item.items() if not k.startswith("PK") and k != "SK"})


def _list_items(product_id, order_id):
    resp = _table.query(
        KeyConditionExpression=Key("PK").eq(f"PRODUCT#{product_id}") & Key("SK").begins_with(f"ITEM#{order_id}#"),
    )
    items = [{k: v for k, v in i.items() if not k.startswith("PK") and k != "SK"} for i in resp.get("Items", [])]
    return _response(200, {"count": len(items), "items": items})


def _get_item(product_id, order_id, item_id):
    resp = _table.get_item(Key={"PK": f"PRODUCT#{product_id}", "SK": f"ITEM#{order_id}#{item_id}"})
    item = resp.get("Item")
    if not item:
        return _response(404, {"message": f"Item '{item_id}' not found"})
    return _response(200, {k: v for k, v in item.items() if not k.startswith("PK") and k != "SK"})


def _update_item(product_id, order_id, item_id, event):
    existing = _table.get_item(Key={"PK": f"PRODUCT#{product_id}", "SK": f"ITEM#{order_id}#{item_id}"}).get("Item")
    if not existing:
        return _response(404, {"message": f"Item '{item_id}' not found"})

    body = _parse_body(event)
    _table.update_item(
        Key={"PK": f"PRODUCT#{product_id}", "SK": f"ITEM#{order_id}#{item_id}"},
        UpdateExpression="SET sku = :s, quantity = :q, unitPrice = :p, updatedAt = :u",
        ExpressionAttributeValues={
            ":s": body.get("sku", existing.get("sku", "")),
            ":q": body.get("quantity", existing.get("quantity", 1)),
            ":p": decimal.Decimal(str(body.get("unitPrice", existing.get("unitPrice", 0)))),
            ":u": _now(),
        },
    )
    return _response(200, {"itemId": item_id, "updatedAt": _now()})


def _delete_item(product_id, order_id, item_id):
    existing = _table.get_item(Key={"PK": f"PRODUCT#{product_id}", "SK": f"ITEM#{order_id}#{item_id}"}).get("Item")
    if not existing:
        return _response(404, {"message": f"Item '{item_id}' not found"})

    _table.delete_item(Key={"PK": f"PRODUCT#{product_id}", "SK": f"ITEM#{order_id}#{item_id}"})
    return _response(200, {"message": f"Item '{item_id}' deleted"})
