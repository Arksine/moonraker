from __future__ import annotations
import json
from tornado.httpclient import AsyncHTTPClient, HTTPRequest, HTTPError
from tornado.httputil import HTTPHeaders
from tornado.escape import url_escape
from typing import Dict, Any, Optional

class HttpClient:
    error = HTTPError
    def __init__(self,
                 type: str = "http",
                 port: int = 7010
                 ) -> None:
        self.client = AsyncHTTPClient()
        assert type in ["http", "https"]
        self.prefix = f"{type}://127.0.0.1:{port}/"
        self.last_response_headers: HTTPHeaders = HTTPHeaders()

    def get_response_headers(self) -> HTTPHeaders:
        return self.last_response_headers

    async def _do_request(self,
                          method: str,
                          endpoint: str,
                          args: Dict[str, Any] = {},
                          headers: Optional[Dict[str, str]] = None
                          ) -> Dict[str, Any]:
        ep = "/".join([url_escape(part, plus=False) for part in
                       endpoint.lstrip("/").split("/")])
        url = self.prefix + ep
        method = method.upper()
        body: Optional[str] = "" if method == "POST" else None
        if args:
            if method in ["GET", "DELETE"]:
                parts = []
                for key, val in args.items():
                    if isinstance(val, list):
                        val = ",".join(val)
                    if val:
                        parts.append(f"{url_escape(key)}={url_escape(val)}")
                    else:
                        parts.append(url_escape(key))
                qs = "&".join(parts)
                url += "?" + qs
            else:
                body = json.dumps(args)
                if headers is None:
                    headers = {}
                headers["Content-Type"] = "application/json"
        request = HTTPRequest(url, method, headers, body=body,
                              request_timeout=2., connect_timeout=2.)
        ret = await self.client.fetch(request)
        self.last_response_headers = HTTPHeaders(ret.headers)
        return json.loads(ret.body)

    async def get(self,
                  endpoint: str,
                  args: Dict[str, Any] = {},
                  headers: Optional[Dict[str, str]] = None
                  ) -> Dict[str, Any]:
        return await self._do_request("GET", endpoint, args, headers)

    async def post(self,
                   endpoint: str,
                   args: Dict[str, Any] = {},
                   headers: Optional[Dict[str, str]] = None,
                   ) -> Dict[str, Any]:
        return await self._do_request("POST", endpoint, args, headers)

    async def delete(self,
                     endpoint: str,
                     args: Dict[str, Any] = {},
                     headers: Optional[Dict[str, str]] = None
                     ) -> Dict[str, Any]:
        return await self._do_request("DELETE", endpoint, args, headers)

    def close(self):
        self.client.close()
