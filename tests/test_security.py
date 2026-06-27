"""Security tests — no secrets in logs, file type validation, path traversal."""

import io
import time
import asyncio

import httpx
import pytest
from httpx import ASGITransport

from app.main import app


@pytest.mark.asyncio
async def test_no_image_returns_400():
    """POST /v1/manuals with no images should return 400."""
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/v1/manuals", files={})
        assert resp.status_code in (400, 422)  # 422 is FastAPI validation error


@pytest.mark.asyncio
async def test_unsupported_file_type_returns_415():
    """Upload a .txt file should return 415."""
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        fake_file = io.BytesIO(b"not an image")
        resp = await client.post(
            "/v1/manuals",
            files={"images": ("test.txt", fake_file, "text/plain")},
        )
        assert resp.status_code == 415


@pytest.mark.asyncio
async def test_credentials_no_secret_values():
    """GET /credentials should never expose actual key values."""
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/credentials")
        assert resp.status_code == 200
        data = resp.json()
        for cred in data["credentials"]:
            # Should only have name and present, never the value
            assert "name" in cred
            assert "present" in cred
            # Ensure no key value is leaked
            assert cred.get("value") is None or cred.get("value") == ""


@pytest.mark.asyncio
async def test_path_traversal_blocked():
    """GET /v1/manuals/../should not traverse."""
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # FastAPI normalizes paths, so this should just 404
        resp = await client.get("/v1/manuals/..%2Fetc%2Fpasswd")
        assert resp.status_code in (404, 422)


@pytest.mark.asyncio
async def test_mcp_resources_list():
    """MCP resources endpoint should return resource list."""
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/mcp/resources")
        assert resp.status_code == 200
        data = resp.json()
        assert "resources" in data
        assert len(data["resources"]) > 0


@pytest.mark.asyncio
async def test_mcp_tools_list():
    """MCP tools endpoint should return tool list."""
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/mcp/tools")
        assert resp.status_code == 200
        data = resp.json()
        assert "tools" in data
        tool_names = [t["name"] for t in data["tools"]]
        assert "create_visual_manual" in tool_names
        assert "validate_manual_claims" in tool_names


@pytest.mark.asyncio
async def test_mcp_prompts_list():
    """MCP prompts endpoint should return prompt list."""
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/mcp/prompts")
        assert resp.status_code == 200
        data = resp.json()
        assert "prompts" in data
        assert len(data["prompts"]) >= 5


@pytest.mark.asyncio
async def test_validate_endpoint():
    """POST /v1/manuals/{job_id}/validate should return verdict."""
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Create a job first
        fake_image = io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        resp = await client.post(
            "/v1/manuals",
            files={"images": ("test.png", fake_image, "image/png")},
        )
        assert resp.status_code == 202
        job_id = resp.json()["job_id"]

        # Wait for completion
        max_wait = 30
        start = time.time()
        while time.time() - start < max_wait:
            await asyncio.sleep(0.5)
            job_resp = await client.get(f"/v1/manuals/{job_id}")
            job_data = job_resp.json()
            if job_data["status"] in ("completed", "partial", "blocked"):
                break

        # Test validate endpoint
        val_resp = await client.post(f"/v1/manuals/{job_id}/validate")
        assert val_resp.status_code == 200
        val_data = val_resp.json()
        assert "verdict" in val_data
        assert val_data["verdict"] in ("PASS", "WARNING", "FAIL")


@pytest.mark.asyncio
async def test_artifacts_aggregated_endpoint():
    """GET /v1/manuals/{job_id}/artifacts should return all URLs."""
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        fake_image = io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        resp = await client.post(
            "/v1/manuals",
            files={"images": ("test.png", fake_image, "image/png")},
        )
        job_id = resp.json()["job_id"]

        # Wait for completion
        max_wait = 30
        start = time.time()
        while time.time() - start < max_wait:
            await asyncio.sleep(0.5)
            job_resp = await client.get(f"/v1/manuals/{job_id}")
            job_data = job_resp.json()
            if job_data["status"] in ("completed", "partial", "blocked"):
                break

        art_resp = await client.get(f"/v1/manuals/{job_id}/artifacts")
        assert art_resp.status_code == 200
        art_data = art_resp.json()
        assert "manual_json_url" in art_data
        assert "html_url" in art_data
        assert "pdf_url" in art_data
        assert "exploded_frames" in art_data
        assert "turntable_frames" in art_data
