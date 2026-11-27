from app.templating import ASSET_VERSION


def test_pwa_routes_public(client):
    sw = client.get("/sw.js")
    assert sw.status_code == 200 and "javascript" in sw.headers["content-type"]
    assert ASSET_VERSION in sw.text and "__ASSET_VERSION__" not in sw.text
    m = client.get("/manifest.webmanifest").json()
    assert m["display"] == "standalone" and any(i.get("purpose") == "maskable" for i in m["icons"])
    assert "offline" in client.get("/offline").text.lower()
