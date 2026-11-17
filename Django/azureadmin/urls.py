"""Root URL config: static frontends at / and /groups, API under /api/."""
from pathlib import Path

from django.http import HttpResponse
from django.urls import include, path

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


def _page(name: str):
    def view(request):
        html = (FRONTEND_DIR / name).read_text(encoding="utf-8")
        return HttpResponse(html, content_type="text/html; charset=utf-8")
    return view


urlpatterns = [
    path("", _page("index.html")),
    path("index.html", _page("index.html")),
    path("groups", _page("groups.html")),
    path("groups.html", _page("groups.html")),
    path("api/", include("api.urls")),
]
