from flask import Flask

FEED_META = {
    "provider": "VBB (Verkehrsverbund Berlin-Brandenburg)",
    "url": "https://production.gtfsrt.vbb.de/data",
    "entity_types": ["trip_update"],
    # The feed has no VehiclePositions; Carma derives them from TripUpdates.
    "vehicle_positions": "derived",
}


def create_app() -> Flask:
    app = Flask("carma")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/v1/meta")
    def meta() -> dict[str, object]:
        return {"feed": FEED_META}

    return app
