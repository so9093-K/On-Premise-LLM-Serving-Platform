from __future__ import annotations

import json

SCALAR_CONFIG = json.dumps({
    "theme": "default",
    # classic 레이아웃은 본문을 한 컬럼으로 넓게 쓴다. 이 문서는 설정에서 생성한 표와
    # 코드 예시가 많아서, 기본(modern) 3컬럼에서는 표가 세로로 터지며 계단처럼 찌그러진다.
    "layout": "classic",
    # Scalar가 헤더와 본문 양쪽에 다운로드 버튼을 그려서 첫 화면에 두 번 나온다.
    # OpenAPI 문서는 /openapi.json에서 그대로 받을 수 있으므로 버튼은 하나도 필요 없다.
    "hideDownloadButton": True,
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
      if (!window.isSecureContext) {{
        var _clip = {{
          writeText: function(text) {{
            return new Promise(function(resolve, reject) {{
              try {{
                var el = document.createElement('textarea');
                el.value = text;
                el.setAttribute('readonly', '');
                el.style.cssText = 'position:fixed;top:-9999px;left:-9999px;opacity:0;';
                document.body.appendChild(el);
                el.focus();
                el.setSelectionRange(0, el.value.length);
                var ok = document.execCommand('copy');
                document.body.removeChild(el);
                ok ? resolve() : reject(new Error('execCommand failed'));
              }} catch (e) {{ reject(e); }}
            }});
          }}
        }};
        try {{
          Object.defineProperty(navigator, 'clipboard', {{configurable: true, value: _clip}});
        }} catch(e) {{}}
        if (!navigator.clipboard) {{
          try {{
            Object.defineProperty(Navigator.prototype, 'clipboard', {{
              configurable: true,
              get: function() {{ return _clip; }}
            }});
          }} catch(e) {{}}
        }}
        var _exec0 = document.execCommand.bind(document);
        document.execCommand = function(cmd) {{
          if (cmd === 'copy') {{
            var ts = document.querySelectorAll('body > textarea');
            if (ts.length) ts[ts.length - 1].focus();
          }}
          return _exec0.apply(document, arguments);
        }};
      }}
    </script>
    <script
      id="api-reference"
      data-url="{openapi_url}"
      data-configuration='{SCALAR_CONFIG}'
    ></script>
    <script
      src="https://cdn.jsdelivr.net/npm/@scalar/api-reference@1.60.0"
      integrity="sha384-4BdmZQQTc462+ocGPo+GP3Hi/eQjMQTmNkSU9J5w3FD6hGUEmU2PqNRnbklONt4R"
      crossorigin="anonymous"
    ></script>
  </body>
</html>"""
