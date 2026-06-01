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
    <style>
      body {{
        margin: 0;
      }}
      .docs-toolbar {{
        position: sticky;
        top: 0;
        z-index: 1000;
        display: flex;
        gap: 12px;
        align-items: center;
        padding: 8px 16px;
        border-bottom: 1px solid #E5E7EB;
        background: #FFFFFF;
        font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        font-size: 13px;
        color: #374151;
      }}
      .docs-toolbar button {{
        border: 1px solid #D1D5DB;
        border-radius: 6px;
        background: #F9FAFB;
        padding: 5px 10px;
        cursor: pointer;
      }}
      .docs-toolbar button:disabled {{
        opacity: 0.45;
        cursor: not-allowed;
      }}
      .docs-toolbar .notice {{
        color: #6B7280;
      }}
    </style>
  </head>
  <body>
    <div class="docs-toolbar">
      <button id="copy-raw-response" disabled>Copy raw response</button>
      <span id="copy-raw-status" class="notice">
        긴 응답은 Preview Copy 대신 Raw 또는 Copy raw response를 사용하세요.
      </span>
    </div>

    <div id="api-reference"></div>

    <script src="https://cdn.jsdelivr.net/npm/@scalar/api-reference@{SCALAR_VERSION}"></script>
    <script>
      const OPENAPI_URL = {json.dumps(openapi_url)};
      let lastRawResponse = null;

      const copyButton = document.getElementById("copy-raw-response");
      const statusLabel = document.getElementById("copy-raw-status");

      function normalizeUrl(input) {{
        try {{
          if (typeof input === "string") {{
            return new URL(input, window.location.href);
          }}
          if (input && input.url) {{
            return new URL(input.url, window.location.href);
          }}
        }} catch (_) {{}}
        return null;
      }}

      function isOpenApiDocumentRequest(input) {{
        const requestUrl = normalizeUrl(input);
        const specUrl = new URL(OPENAPI_URL, window.location.href);
        return requestUrl && requestUrl.pathname === specUrl.pathname;
      }}

      function isStreamingRequest(init) {{
        try {{
          if (!init || !init.body || typeof init.body !== "string") {{
            return false;
          }}
          const body = JSON.parse(init.body);
          return body && body.stream === true;
        }} catch (_) {{
          return false;
        }}
      }}

      async function captureRawResponse(input, response) {{
        if (isOpenApiDocumentRequest(input)) {{
          return;
        }}

        try {{
          const cloned = response.clone();
          const text = await cloned.text();

          lastRawResponse = text;
          copyButton.disabled = false;
          statusLabel.textContent = `Raw response captured: HTTP ${{response.status}}`;
        }} catch (_) {{
          statusLabel.textContent = "Raw response capture failed. Raw 탭 또는 Download를 사용하세요.";
        }}
      }}

      copyButton.addEventListener("click", async () => {{
        if (!lastRawResponse) {{
          return;
        }}

        try {{
          await navigator.clipboard.writeText(lastRawResponse);
          statusLabel.textContent = "Raw response copied.";
        }} catch (_) {{
          statusLabel.textContent = "Clipboard copy failed. Raw 탭을 사용하세요.";
        }}
      }});

      Scalar.createApiReference("#api-reference", {{
        url: OPENAPI_URL,
        theme: "default",
        defaultHttpClient: {{
          targetKey: "shell",
          clientKey: "curl"
        }},
        customFetch: async (input, init) => {{
          if (isStreamingRequest(init)) {{
            lastRawResponse = null;
            copyButton.disabled = true;
            statusLabel.textContent = "Streaming 응답은 Copy raw response를 지원하지 않습니다. Raw 탭을 사용하세요.";
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
