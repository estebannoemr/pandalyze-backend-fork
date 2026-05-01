import pytest

from app.endpoints import challenges


class _FakeResponse:
    def __init__(self, content):
        self.content = content

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.content


def test_google_drive_share_url_is_converted_to_direct_download():
    url = "https://drive.google.com/file/d/abc123/view?usp=sharing"

    normalized = challenges._normalize_csv_download_url(url)

    assert normalized == "https://drive.google.com/uc?export=download&id=abc123"


def test_google_sheets_url_is_converted_to_csv_export():
    url = "https://docs.google.com/spreadsheets/d/sheet123/edit?gid=456#gid=456"

    normalized = challenges._normalize_csv_download_url(url)

    assert normalized == (
        "https://docs.google.com/spreadsheets/d/sheet123/export?format=csv&gid=456"
    )


def test_fetch_csv_from_url_uses_normalized_url(monkeypatch):
    requested_urls = []

    def fake_urlopen(request, timeout):
        requested_urls.append(request.full_url)
        return _FakeResponse(b"a,b\n1,2\n")

    monkeypatch.setattr(challenges, "urlopen", fake_urlopen)

    csv_content = challenges._fetch_csv_from_url(
        "https://drive.google.com/file/d/abc123/view?usp=sharing"
    )

    assert requested_urls == ["https://drive.google.com/uc?export=download&id=abc123"]
    assert csv_content == "a,b\n1,2\n"


def test_fetch_csv_from_url_rejects_html(monkeypatch):
    def fake_urlopen(_request, timeout):
        return _FakeResponse(b"<!doctype html><html><body>login</body></html>")

    monkeypatch.setattr(challenges, "urlopen", fake_urlopen)

    with pytest.raises(ValueError, match="CSV directo"):
        challenges._fetch_csv_from_url("https://example.com/not-a-csv")
