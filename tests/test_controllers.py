import pytest
from fastapi import WebSocket, Depends
from fastapi.testclient import TestClient
from vorte import Vorte, Controller, route, success_response
from vorte.core.router import VorteAPIRouter

class SampleController(Controller):
    _vorte_prefix = "/items"
    _vorte_tags = ["Items"]

    @route.get("")
    async def get_all(self):
        return success_response(data=[{"id": 1, "name": "Item 1"}])

    @route.get("/{item_id}")
    async def get_by_id(self, item_id: int):
        return success_response(data={"id": item_id, "name": f"Item {item_id}"})

    @route.post("")
    async def create(self, payload: dict):
        return success_response(data=payload)

    @route.websocket("/ws")
    async def websocket_endpoint(self, websocket: WebSocket):
        await websocket.accept()
        await websocket.send_json({"msg": "WS Connected"})
        data = await websocket.receive_text()
        await websocket.send_text(f"Echo: {data}")
        await websocket.close()


def dummy_dep():
    return "injected_value"

class DICoController(Controller):
    _vorte_prefix = "/di"

    @route.get("")
    async def test_di(self, dep_value: str = Depends(dummy_dep)):
        return success_response(data=dep_value)


def test_controller_registration():
    app = Vorte(auto_load=False)
    router = VorteAPIRouter()
    
    # Register controllers on router
    router.register_controller(SampleController)
    router.register_controller(DICoController)
    
    app.include_router(router)
    client = TestClient(app.fastapi)

    # Test GET List
    resp = client.get("/items")
    assert resp.status_code == 200
    assert resp.json()["data"] == [{"id": 1, "name": "Item 1"}]

    # Test GET Single with path param
    resp = client.get("/items/42")
    assert resp.status_code == 200
    assert resp.json()["data"] == {"id": 42, "name": "Item 42"}

    # Test POST with payload
    resp = client.post("/items", json={"name": "New Item"})
    assert resp.status_code == 200
    assert resp.json()["data"] == {"name": "New Item"}

    # Test Dependency Injection
    resp = client.get("/di")
    assert resp.status_code == 200
    assert resp.json()["data"] == "injected_value"

    # Test Websocket endpoint
    with client.websocket_connect("/items/ws") as websocket:
        data = websocket.receive_json()
        assert data == {"msg": "WS Connected"}
        websocket.send_text("Hello Vorte")
        echo = websocket.receive_text()
        assert echo == "Echo: Hello Vorte"

    # Test router routes metadata (tags, path)
    routes = app.get_routes()
    items_route = next(r for r in routes if r["path"] == "/items")
    assert "Items" in items_route.get("tags", []) or "Items" in [t for route in app.fastapi.routes if route.path == "/items" for t in getattr(route, 'tags', [])]
