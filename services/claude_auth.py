"""
OAuth PKCE login flow for Claude Code.
Implements the same flow as `claude auth login` but controllable from Python.
"""
import asyncio
import base64
import hashlib
import json
import logging
import os
import secrets
from pathlib import Path

import aiohttp

logger = logging.getLogger(__name__)

CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
AUTHORIZE_URL = "https://claude.com/cai/oauth/authorize"
TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
CREATE_KEY_URL = "https://api.anthropic.com/api/oauth/claude_cli/create_api_key"
REDIRECT_URI = "https://platform.claude.com/oauth/code/callback"
SCOPES = "org:create_api_key user:profile user:inference user:sessions:claude_code user:mcp_servers user:file_upload"

# Persistent state for the current login flow
_current_flow = None  # {"code_verifier": ..., "state": ...}


def _generate_pkce():
    """Generate PKCE code_verifier and code_challenge."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    return verifier, challenge


def start_oauth_flow() -> dict:
    """Generate OAuth URL with PKCE. Returns {"url": "..."}."""
    global _current_flow

    verifier, challenge = _generate_pkce()
    state = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()

    _current_flow = {"code_verifier": verifier, "state": state}

    from urllib.parse import quote
    scope_encoded = quote(SCOPES, safe='').replace("%20", "+")
    url = (
        f"{AUTHORIZE_URL}"
        f"?code=true"
        f"&client_id={CLIENT_ID}"
        f"&response_type=code"
        f"&redirect_uri={quote(REDIRECT_URI, safe='')}"
        f"&scope={scope_encoded}"
        f"&code_challenge={challenge}"
        f"&code_challenge_method=S256"
        f"&state={state}"
        f"&prompt=consent"
    )
    return {"url": url}


async def exchange_code(code: str) -> dict:
    """Exchange authorization code for token, then create API key."""
    global _current_flow

    if not _current_flow:
        return {"error": "没有正在进行的登录流程，请先点击登录"}

    verifier = _current_flow["code_verifier"]
    state = _current_flow["state"]

    # The callback page displays "code#state" as a single string
    # Split it if the user pasted the combined value
    raw = code.strip()
    if "#" in raw:
        code_part, state_part = raw.split("#", 1)
    else:
        code_part = raw
        state_part = state

    try:
        async with aiohttp.ClientSession() as session:
            # Step 1: Exchange code for access token
            token_data = {
                "grant_type": "authorization_code",
                "client_id": CLIENT_ID,
                "code": code_part,
                "redirect_uri": REDIRECT_URI,
                "code_verifier": verifier,
                "state": state_part,
            }
            logger.info("Token exchange request: url=%s, data=%s", TOKEN_URL,
                        {**token_data, "code": token_data["code"][:20] + "...", "code_verifier": verifier[:20] + "..."})
            async with session.post(TOKEN_URL, json=token_data) as resp:
                body = await resp.text()
                logger.info("Token exchange response: status=%d, body=%s", resp.status, body[:500])
                if resp.status != 200:
                    return {"error": f"Token 交换失败 ({resp.status}): {body[:200]}"}
                token_resp = json.loads(body)

            access_token = token_resp.get("access_token")
            refresh_token = token_resp.get("refresh_token")
            expires_in = token_resp.get("expires_in", 3600)
            if not access_token:
                return {"error": f"未获取到 access_token: {json.dumps(token_resp)[:200]}"}

            # Step 2: Save OAuth token to where Claude Code CLI reads it
            _save_oauth_token(access_token)
            _current_flow = None
            return {"success": True}

    except aiohttp.ClientError as e:
        return {"error": f"网络请求失败: {str(e)[:200]}"}
    except Exception as e:
        return {"error": f"登录失败: {str(e)[:200]}"}


def _save_oauth_token(token: str):
    """Save OAuth token to Claude Code CLI's well-known file path.

    Claude Code CLI reads OAuth tokens from:
    - File descriptor via CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR env var
    - Well-known path: ~/.claude/remote/.oauth_token
    """
    # Write to well-known path
    home = os.path.expanduser("~")
    remote_dir = os.path.join(home, ".claude", "remote")
    os.makedirs(remote_dir, mode=0o700, exist_ok=True)

    token_path = os.path.join(remote_dir, ".oauth_token")
    with open(token_path, "w") as f:
        f.write(token)
    os.chmod(token_path, 0o600)

    logger.info("OAuth token saved to %s", token_path)


def is_logged_in() -> bool:
    """Check if OAuth token file exists."""
    home = os.path.expanduser("~")
    token_path = os.path.join(home, ".claude", "remote", ".oauth_token")
    return os.path.exists(token_path) and os.path.getsize(token_path) > 0
