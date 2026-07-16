from carma.entrypoints.api import create_app


def test_health_returns_ok() -> None:
    client = create_app().test_client()

    response = client.get("/health")

    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


def test_meta_describes_the_feed() -> None:
    client = create_app().test_client()

    response = client.get("/api/v1/meta")

    assert response.status_code == 200
    feed = response.get_json()["feed"]
    assert feed["provider"].startswith("VBB")
    assert feed["vehicle_positions"] == "derived"
