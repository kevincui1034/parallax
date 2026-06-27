"""Test: POST /v1/manuals with no API keys → job completes as PARTIAL with fallback frames.

When GMI_API_KEY and GMI_MAAS_API_KEY are not configured, the pipeline should:
- Use default part analysis (since VLM is unavailable)
- Use placeholder frames (since video generation is blocked)
- Still produce a working visual manual artifact (manual.json + HTML + PDF)
"""

import asyncio
import io
import time

import httpx
import pytest
from httpx import ASGITransport

from app.main import app


@pytest.mark.asyncio
async def test_manual_blocked():
    """When no API keys are configured, job should complete as PARTIAL with fallback frames."""
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        fake_image = io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

        resp = await client.post(
            "/v1/manuals",
            files={"images": ("test.jpg", fake_image, "image/jpeg")},
        )

        assert resp.status_code == 202
        data = resp.json()
        assert "job_id" in data
        assert data["status"] == "queued"

        job_id = data["job_id"]

        # Poll until job is done
        max_wait = 30
        start = time.time()
        final_status = None
        job_data = None

        while time.time() - start < max_wait:
            await asyncio.sleep(0.5)
            job_resp = await client.get(f"/v1/manuals/{job_id}")
            assert job_resp.status_code == 200
            job_data = job_resp.json()
            final_status = job_data["status"]

            if final_status in ("completed", "partial", "blocked"):
                break

        assert final_status in ("completed", "partial", "blocked"), f"Job stuck in {final_status}"

        # Should have parts (from default/fallback analysis)
        assert len(job_data.get("parts", [])) > 0

        # Should have fallback frames in at least one mode
        explode = job_data.get("explode", {})
        turntable = job_data.get("turntable", {})
        assert len(explode.get("frames", [])) > 0 or len(turntable.get("frames", [])) > 0

        # Should have warnings
        assert len(job_data.get("warnings", [])) > 0

        # Should have non-claims
        assert len(job_data.get("non_claims", [])) > 0

        # Should have manual_json
        assert job_data.get("manual_json", {}) != {}

        # Should have artifact_dir
        assert job_data.get("artifact_dir", "") != ""


@pytest.mark.asyncio
async def test_manual_artifacts():
    """Test artifact endpoints return correct content."""
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        fake_image = io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

        resp = await client.post(
            "/v1/manuals",
            files={"images": ("test.jpg", fake_image, "image/jpeg")},
        )
        assert resp.status_code == 202
        job_id = resp.json()["job_id"]

        # Wait for completion
        max_wait = 30
        start = time.time()
        job_data = None

        while time.time() - start < max_wait:
            await asyncio.sleep(0.5)
            job_resp = await client.get(f"/v1/manuals/{job_id}")
            job_data = job_resp.json()
            if job_data["status"] in ("completed", "partial", "blocked"):
                break

        assert job_data is not None
        assert job_data["status"] in ("completed", "partial", "blocked")

        # Test manual.json endpoint
        json_resp = await client.get(f"/v1/manuals/{job_id}/artifacts/manual.json")
        assert json_resp.status_code == 200
        manual = json_resp.json()
        assert manual["job_id"] == job_id
        assert len(manual.get("parts", [])) > 0

        # Test index.html endpoint
        html_resp = await client.get(f"/v1/manuals/{job_id}/artifacts/index.html")
        assert html_resp.status_code == 200
        assert "Visual Manual" in html_resp.text

        # Test part card endpoint
        parts = manual.get("parts", [])
        if parts:
            part_id = parts[0]["id"]
            part_resp = await client.get(f"/v1/manuals/{job_id}/parts/{part_id}")
            assert part_resp.status_code == 200
            assert part_resp.json()["id"] == part_id

        # Test export endpoint
        export_resp = await client.post(f"/v1/manuals/{job_id}/export")
        assert export_resp.status_code == 200
        export_data = export_resp.json()
        assert "pdf_url" in export_data
        assert "html_url" in export_data
        assert "json_url" in export_data

        # Test stub page loads
        stub_resp = await client.get("/stub")
        assert stub_resp.status_code == 200
        assert "Agent Visual Manual" in stub_resp.text
