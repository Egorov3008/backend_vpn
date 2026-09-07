import pytest


@pytest.mark.asyncio
async def test_download_android_app_redirects_without_auth(api_client):
    response = await api_client.get("/api/v1/public/app/android")

    assert response.status_code in (302, 307)
    assert response.headers["location"] == "/static/downloads/dlay-svoih.apk"
