from __future__ import annotations

import json

SCALAR_CONFIG = json.dumps({
    "theme": "default",
    "defaultHttpClient": {"targetKey": "shell", "clientKey": "curl"},
})


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
    <script>
      if (!window.isSecureContext && !navigator.clipboard) {{
        navigator.clipboard = {{
          writeText: function(text) {{
            return new Promise(function(resolve, reject) {{
              try {{
                var el = document.createElement('textarea');
                el.value = text;
                el.style.position = 'fixed';
                el.style.opacity = '0';
                document.body.appendChild(el);
                el.focus();
                el.select();
                var ok = document.execCommand('copy');
                document.body.removeChild(el);
                ok ? resolve() : reject(new Error('execCommand failed'));
              }} catch (e) {{ reject(e); }}
            }});
          }}
        }};
      }}
    </script>
    <script
      id="api-reference"
      data-url="{openapi_url}"
      data-configuration='{SCALAR_CONFIG}'
    ></script>
    <script src="https://cdn.jsdelivr.net/npm/@scalar/api-reference@1.59.3"></script>
  </body>
</html>"""
