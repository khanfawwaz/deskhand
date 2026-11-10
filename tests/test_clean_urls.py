"""The legal pages have clean addresses (/privacy, /terms); the old .html addresses keep working."""
import pytest
from fastapi.testclient import TestClient

from taskpilot.demo import DEMO_DEVICE, DemoConnection, DemoModel, demo_policy
from taskpilot.devices import DeviceHub
from taskpilot.orchestrator import Config, Orchestrator
from taskpilot.server import create_app
from taskpilot.store import Store


@pytest.fixture
def client():
    store, hub, policy = Store(), DeviceHub(), demo_policy()
    store.enroll_device(DEMO_DEVICE, "Demo laptop")
    hub.register(DEMO_DEVICE, DemoConnection(policy))
    orch = Orchestrator(store, hub, DemoModel(), policy, Config(settle_delay_s=0, confirm_timeout_s=10, action_timeout_s=5, ready_timeout_s=1))
    with TestClient(create_app(store, hub, orch, "admin-key"), follow_redirects=False) as c:
        yield c


@pytest.mark.parametrize("slug,words", [("privacy", "privacy policy"), ("terms", "terms")])
def test_clean_address_serves_the_page_without_dot_html(client, slug, words):
    r = client.get(f"/{slug}")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert words in r.text.lower() and "Last updated" in r.text
    assert f'rel="canonical" href="https://deskhand.fawwazkhan.dev/{slug}"' in r.text
    assert r.headers["cache-control"] == "no-cache"
    assert client.get(f"/{slug}/").status_code == 308 and client.get(f"/{slug}/").headers["location"] == f"/{slug}"


@pytest.mark.parametrize("slug", ["privacy", "terms"])
def test_the_old_html_address_still_works(client, slug):
    assert client.get(f"/app/{slug}.html").status_code == 200        # already printed on a store listing: must not break


def test_pages_link_with_clean_addresses_and_load_their_files_from_anywhere(client):
    for slug in ("privacy", "terms"):
        html = client.get(f"/{slug}").text
        assert ".html" not in html                                    # nothing on the page shows a file extension
        assert 'href="/privacy"' in html and 'href="/terms"' in html
        assert 'src="/app/theme.js"' in html and 'href="/app/legal.css"' in html   # absolute: works at /privacy and /app/privacy.html
    assert client.get("/app/legal.css").status_code == 200 and client.get("/app/theme.js").status_code == 200
