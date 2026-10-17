"""Read-only health checks. Emit only fixed labels/booleans, never exceptions."""
import asyncio
import json
import logging


async def check():
    result = dict(dependencies=False, dpapi=False, reachable=False, discovery=False)
    try:
        from importlib.metadata import version
        from bridge import upstream, catalog
        from credentials import load
        import httpx2
        result['dependencies'] = version('mcp') == '2.3.0'
    except Exception:
        return result
    try:
        credential = load()
        result['dpapi'] = True
    except Exception:
        return result
    try:
        async with httpx2.AsyncClient(trust_env=False, timeout=3, follow_redirects=False) as http:
            response = await http.get(credential.endpoint)
            result['reachable'] = response.status_code in (200, 400, 401, 403, 405, 406)
    except Exception:
        pass
    try:
        async with upstream(credential) as client:
            result['discovery'] = len(await catalog(client)) == 10
    except Exception:
        pass
    return result


if __name__ == '__main__':
    logging.disable(logging.CRITICAL)
    print(json.dumps(asyncio.run(check())))
