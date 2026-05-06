"""
Wrapper around Claude Code CLI for making AI calls.
Claude Code reads OAuth tokens from ~/.claude/remote/.oauth_token
"""
import asyncio
import json
import logging
import shutil

logger = logging.getLogger(__name__)

_CLAUDE_BIN = shutil.which("claude")


async def get_status() -> dict:
    """Check connection status."""
    claude_bin = shutil.which("claude")
    if not claude_bin:
        return {"status": "no_cli", "detail": "Claude Code CLI 未安装"}

    from services.claude_auth import is_logged_in
    if not is_logged_in():
        return {"status": "disconnected", "detail": "未登录，请点击登录"}

    # Verify token works with a quick test
    try:
        proc = await asyncio.create_subprocess_exec(
            claude_bin, "-p", "--output-format", "json",
            "--model", "haiku", "--max-turns", "1",
            "--dangerously-skip-permissions",
            "--", "reply with just: ok",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=20)
        if proc.returncode == 0:
            return {"status": "connected", "detail": "Claude 已连接"}
        err = stderr.decode(errors="replace") + stdout.decode(errors="replace")
        if "auth" in err.lower() or "login" in err.lower() or "invalid" in err.lower():
            return {"status": "disconnected", "detail": "登录已过期，请重新登录"}
        return {"status": "connected", "detail": "Claude 已连接（验证中）"}
    except asyncio.TimeoutError:
        # Token file exists, assume OK
        return {"status": "connected", "detail": "Claude 已连接"}
    except Exception as e:
        return {"status": "error", "detail": str(e)[:200]}


async def claude_query(
    prompt: str,
    model: str = "sonnet",
    allowed_tools: str = "Read",
) -> str:
    claude_bin = _find_claude()
    cmd = [
        claude_bin, "-p",
        "--output-format", "text",
        "--model", model,
        "--allowedTools", allowed_tools,
        "--dangerously-skip-permissions",
        "--", prompt,
    ]
    return await _run(cmd)


async def claude_with_skill(
    skill_path: str,
    prompt: str,
    allowed_tools: str = "Bash,Read,Glob,Grep",
    model: str = "sonnet",
) -> str:
    claude_bin = _find_claude()
    # Read skill file and pass via --append-system-prompt
    # (Claude CLI no longer supports --append-system-prompt-file)
    try:
        with open(skill_path, "r", encoding="utf-8") as f:
            skill_content = f.read()
    except Exception as e:
        raise RuntimeError(f"Failed to read skill file {skill_path}: {e}")
    # Use json output format to get the full result field (text format can only return
    # the last turn when Claude does multi-turn agent execution). Buffer 16MB handles
    # large responses.
    cmd = [
        claude_bin, "-p",
        "--output-format", "json",
        "--model", model,
        "--append-system-prompt", skill_content,
        "--dangerously-skip-permissions",
    ]
    if allowed_tools:
        cmd.extend(["--allowedTools", allowed_tools])
    else:
        # No tools: use --disable-slash-commands to prevent any tool invocation attempts
        cmd.extend(["--disallowedTools", "Bash,Read,Write,Edit,Glob,Grep,WebFetch,WebSearch,Task,TodoWrite"])
    cmd.extend(["--", prompt])
    return await _run(cmd)


def _find_claude() -> str:
    if not _CLAUDE_BIN:
        raise RuntimeError("claude CLI not found")
    return _CLAUDE_BIN


async def _run(cmd: list[str], timeout: int = 1200, max_retries: int = 3) -> str:
    """Run Claude CLI with auto-retry on transient failures.

    Retries on: rc!=0, empty output, invalid JSON, or asyncio errors.
    Backoff: 5s, 15s, 30s.
    """
    last_err: Exception = RuntimeError("no attempt made")
    for attempt in range(1, max_retries + 1):
        try:
            # limit=16MB: default asyncio buffer (64KB) silently truncates large Claude outputs
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                limit=2**24,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            out_text = stdout.decode(errors="replace").strip()
            err_text = stderr.decode(errors="replace").strip()

            if proc.returncode != 0:
                # rc!=0: include both stderr and last chunk of stdout (Claude sometimes writes errors to stdout)
                detail = f"stderr={err_text[:500] or '(empty)'} | stdout_tail={out_text[-500:] or '(empty)'}"
                last_err = RuntimeError(f"Claude error (rc={proc.returncode}): {detail}")
                logger.warning("Attempt %d/%d failed: %s", attempt, max_retries, last_err)
            elif not out_text:
                last_err = RuntimeError(f"Claude returned empty output (stderr={err_text[:300]})")
                logger.warning("Attempt %d/%d empty: %s", attempt, max_retries, last_err)
            else:
                # Success — return output
                return _extract_result(out_text)
        except asyncio.TimeoutError:
            last_err = RuntimeError(f"Claude timed out after {timeout}s")
            logger.warning("Attempt %d/%d timeout", attempt, max_retries)
        except Exception as e:
            last_err = e
            logger.warning("Attempt %d/%d exception: %s", attempt, max_retries, e)

        # Backoff before next attempt (skip on last failure)
        if attempt < max_retries:
            delay = min(30, 5 * (2 ** (attempt - 1)))  # 5s, 10s, 20s (capped 30s)
            logger.info("Retrying in %ds...", delay)
            await asyncio.sleep(delay)

    raise last_err


def _extract_result(output: str) -> str:
    """Parse JSON-wrapped claude output, or return raw text."""
    if not output:
        raise RuntimeError("Claude returned empty output")
    # output-format text returns the model's message directly
    # (legacy json format: {"result": "..."})
    if output.startswith("{") and '"result"' in output[:100]:
        try:
            return json.loads(output).get("result", "")
        except json.JSONDecodeError:
            pass
    return output
