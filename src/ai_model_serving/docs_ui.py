from __future__ import annotations

import json

SCALAR_VERSION = "1.57.5"


def scalar_html(openapi_url: str, title: str) -> str:
    """공통 Scalar API reference shell을 렌더링한다.

    Gateway와 Risk Adapter는 의도적으로 별도 OpenAPI 문서를 노출하지만, 주변 documentation UI는 동일하게 유지한다. 이 helper를 한 곳에 두면 docs UX 변경 시 styling/client drift를 줄일 수 있다.
    """
    return f"""<!doctype html>
<html>
  <head>
    <title>{title}</title>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <style>body {{ margin: 0; }}</style>
  </head>
  <body>
    <div id="api-reference"></div>
    <script src="https://cdn.jsdelivr.net/npm/@scalar/api-reference@{SCALAR_VERSION}"></script>
    <script>
      const OPENAPI_URL = {json.dumps(openapi_url)};
      let lastRawResponse = null;
      let overrideNextWrite = false;

      const originalWriteText = navigator.clipboard.writeText.bind(navigator.clipboard);

      function isOpenApiDocumentRequest(input) {{
        try {{
          const reqUrl = new URL(typeof input === "string" ? input : input.url, window.location.href);
          const specUrl = new URL(OPENAPI_URL, window.location.href);
          return reqUrl.pathname === specUrl.pathname;
        }} catch (_) {{ return false; }}
      }}

      function isStreamingRequest(init) {{
        try {{
          if (!init?.body || typeof init.body !== "string") return false;
          return JSON.parse(init.body)?.stream === true;
        }} catch (_) {{ return false; }}
      }}

      async function captureRawResponse(input, response) {{
        if (isOpenApiDocumentRequest(input)) return;
        try {{
          lastRawResponse = await response.clone().text();
        }} catch (_) {{}}
      }}

      // Preview Copy 버튼 클릭을 가로채서 raw response를 복사하도록 한다.
      // "copy as curl", "copy url" 등은 제외하고 일반 Copy만 대상으로 한다.
      document.addEventListener("click", (e) => {{
        if (!lastRawResponse) return;
        const btn = e.target.closest("button, [role='button']");
        if (!btn) return;
        const label = (btn.getAttribute("aria-label") || btn.title || btn.textContent || "").toLowerCase().trim();
        if (label.includes("copy") && !label.includes("curl") && !label.includes("url")) {{
          overrideNextWrite = true;
          setTimeout(() => {{ overrideNextWrite = false; }}, 300);
        }}
      }}, true);

      navigator.clipboard.writeText = async (text) => {{
        if (overrideNextWrite && lastRawResponse) {{
          overrideNextWrite = false;
          return originalWriteText(lastRawResponse);
        }}
        return originalWriteText(text);
      }};

      Scalar.createApiReference("#api-reference", {{
        url: OPENAPI_URL,
        theme: "default",
        defaultHttpClient: {{ targetKey: "shell", clientKey: "curl" }},
        customFetch: async (input, init) => {{
          if (isStreamingRequest(init)) {{
            lastRawResponse = null;
            return fetch(input, init);
          }}
          const response = await fetch(input, init);
          captureRawResponse(input, response);
          return response;
        }}
      }});
    </script>
  </body>
</html>"""
