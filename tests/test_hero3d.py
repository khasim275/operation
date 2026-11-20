from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1] / "hero3d"


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()
        self.links = []
        self.buttons = []
        self.images = []
        self.service_links = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if "id" in attributes:
            self.ids.add(attributes["id"])
        if tag == "a":
            self.links.append(attributes)
            if "data-service" in attributes:
                self.service_links.append(attributes)
        elif tag == "button":
            self.buttons.append(attributes)
        elif tag == "img":
            self.images.append(attributes)


def load_page():
    page = (ROOT / "index.html").read_text(encoding="utf-8")
    parser = PageParser()
    parser.feed(page)
    return page, parser


def test_internal_navigation_and_actions_have_handlers():
    page, parsed = load_page()
    assert parsed.links
    for link in parsed.links:
        href = link.get("href", "")
        if href.startswith("#"):
            assert href[1:] in parsed.ids
    assert "document.querySelectorAll(\"[data-whatsapp]\")" in page
    assert "document.querySelectorAll(\"[data-call]\")" in page
    assert 'skipIntro.addEventListener("click"' in page
    assert len(parsed.service_links) == 8
    assert "document.querySelectorAll(\"[data-service]\")" in page


def test_fallbacks_accessibility_and_deterministic_render_hook():
    page, parsed = load_page()
    assert any(image.get("src") == "./posters/fallback.svg" for image in parsed.images)
    assert all((ROOT / "posters" / f"shot-{index:02}.svg").is_file() for index in range(1, 7))
    assert "prefers-reduced-motion: reduce" in page
    assert ":focus-visible" in page
    assert "window.renderFrame = (seconds)" in page
    assert "https://cdn.jsdelivr.net/npm/three@0.169.0/" in page
    assert len(parsed.buttons) == 1
