"""What the Cloudflare Worker fetches: the signed rule set of one site.

Public on purpose (a Worker has no user): the unguessable site token in the URL is the address, and the rules
are signed with the site's own secret, so a Worker only ever applies rules that came from us. The rules contain
only the page edits that are already (about to be) public on the site.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession

from searchos.core.crypto import DecryptionError, decrypt_json
from searchos.core.db import get_session
from searchos.deploy import service
from searchos.deploy.cloudflare import find_connection, rules_body
from searchos.deploy.signing import SIGNATURE_HEADER, sign_body
from searchos.models import Site

router = APIRouter(prefix="/edge", tags=["edge"])


@router.get("/rules/{site_token}")
async def edge_rules(site_token: str, session: Annotated[AsyncSession, Depends(get_session)]) -> Response:
    if not 16 <= len(site_token) <= 80:
        raise HTTPException(404, "Unknown site")
    conn = await find_connection(session, site_token)
    site = await session.get(Site, conn.site_id) if conn else None
    if conn is None or site is None or not conn.credentials_encrypted:
        raise HTTPException(404, "Unknown site")
    try:
        secret = str(decrypt_json(conn.credentials_encrypted)["signing_secret"])
    except (DecryptionError, KeyError):
        raise HTTPException(404, "Unknown site") from None
    live = await service.live_deployments(session, site.id)
    body = rules_body(site, conn, live)
    return Response(
        content=body,
        media_type="application/json",
        headers={SIGNATURE_HEADER: sign_body(body, secret), "Cache-Control": "no-store"},
    )
