"""Integration tests for /api/v1/health verifying live PostgreSQL, Qdrant, and Redis."""

from httpx import ASGITransport, AsyncClient

from app.main import app


async def test_health_endpoint_all_services_ok() -> None:
    """Verify /api/v1/health returns 200 OK and connects to Postgres, Qdrant, and Redis."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/v1/health")

    assert response.status_code == 200
    assert "x-request-id" in response.headers

    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["app_name"] == "FinSight AI"

    services = payload["services"]
    for service_name in ("postgres", "qdrant", "redis"):
        assert service_name in services, f"Missing {service_name} in health response"
        assert services[service_name]["status"] == "ok", (
            f"Service {service_name} failed health check: {services[service_name]}"
        )
        assert services[service_name]["latency_ms"] >= 0.0

