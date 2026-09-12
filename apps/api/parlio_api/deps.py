from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status

from parlio_api.settings import Settings, get_settings
from parlio_api.store import Store


def get_store(request: Request) -> Store:
    store: Store = request.app.state.store
    return store


def require_worker_key(
    x_worker_key: Annotated[str | None, Header()] = None,
    settings: Annotated[Settings, Depends(get_settings)] = None,  # type: ignore[assignment]
) -> None:
    if not x_worker_key or not secrets.compare_digest(x_worker_key, settings.worker_api_key):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid worker key")


StoreDep = Annotated[Store, Depends(get_store)]
