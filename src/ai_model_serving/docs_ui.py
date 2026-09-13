from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

# Scalar 기본 스타일은 표 안의 `code`에 word-break: break-word를 건다. 본문 컬럼이
# 1440px 화면에서도 485px밖에 안 되기 때문에(나머지 절반은 요청/응답 예시 칸이고,
# 태그 설명에는 예시가 없어 비어 있다), 식별자가 칸에 안 들어가면 글자 단위로
# 쪼개진다 -- local-main -> "local-/main", embedding_ko_vllm -> "embeddi/ng_ko_v/llm".
#
# 식별자는 중간에서 끊으면 다른 이름처럼 보이므로 줄바꿈을 막고, 그래도 넘치면
# 잘리는 대신 표 안에서 가로로 스크롤되게 한다. layout은 건드리지 않는다
# (classic으로 바꾸면 본문은 넓어지지만 좌측 사이드바가 사라진다).
# 두 번째 문제: Scalar는 섹션을 항상 반반(flex:1 1 0%)으로 나눈다. 오퍼레이션에서는
# 오른쪽이 요청/응답 예시라 그 폭이 필요하지만, 태그 설명 섹션의 오른쪽은 40자짜리
# 엔드포인트 카드뿐이라 492px가 통째로 빈다. 그래서 표와 본문이 절반 폭에 갇힌다.
# 그 칸만 300px로 고정하면 본문이 492 -> 684px가 되고, 오퍼레이션 섹션은 선택자에
# 걸리지 않아 그대로 남는다(실측 확인).
#
# Scalar 내부 클래스에 기대는 선택자다. 버전은 SRI 해시까지 고정돼 있어 조용히
# 바뀌지 않지만, Scalar를 올릴 때는 이 규칙이 아직 맞는지 확인해야 한다.
# 값에 작은따옴표를 쓰면 data-configuration='...' 속성이 깨지므로 쓰지 않는다.
DOCS_CUSTOM_CSS = (
    ".markdown table code{white-space:nowrap;word-break:normal;}"
    ".markdown table{display:block;width:100%;overflow-x:auto;}"
    "@media (min-width:1000px){"
    ".section-column:has(> [class*=endpoints-]),"
    ".section-column:has(> .sticky-cards){flex:0 0 300px;}"
    "}"
)

# 문서 화면의 JS 번들은 저장소에 vendoring 한다. 온프레미스 배포는 외부 egress가
# 없어도 /docs와 /redoc이 떠야 하고, CDN 참조는 air-gap 망에서 빈 화면이 된다.
# 버전과 SRI 해시는 이 표가 단독으로 소유하며, scripts/build/fetch_docs_assets.py가
# 같은 값으로 내려받아 검증한다. 브라우저는 same-origin 응답에도 integrity를 그대로
# 검증하므로 vendoring 파일이 손상되면 실행하지 않는다.
_STATIC_DIR = Path(__file__).resolve().parent / "static"


@dataclass(frozen=True)
class VendoredAsset:
    """문서 화면이 쓰는 self-host JS 번들 하나."""

    filename: str
    source_url: str
    integrity: str

    @property
    def route(self) -> str:
        return f"/static/{self.filename}"

    @property
    def path(self) -> Path:
        return _STATIC_DIR / self.filename


SCALAR_BUNDLE = VendoredAsset(
    filename="scalar-api-reference-1.60.0.js",
    source_url="https://cdn.jsdelivr.net/npm/@scalar/api-reference@1.60.0",
    integrity="sha384-4BdmZQQTc462+ocGPo+GP3Hi/eQjMQTmNkSU9J5w3FD6hGUEmU2PqNRnbklONt4R",
)
# FastAPI 기본 /redoc은 `redoc@2`라는 가변 태그를 SRI 없이 부른다. 다른 모든 외부
# 아티팩트를 digest로 고정하는 이 저장소 기준에 어긋나므로, 태그가 가리키던 실제
# 버전(2.5.4)으로 고정해 같이 vendoring 한다.
REDOC_BUNDLE = VendoredAsset(
    filename="redoc-standalone-2.5.4.js",
    source_url="https://cdn.jsdelivr.net/npm/redoc@2.5.4/bundles/redoc.standalone.js",
    integrity="sha384-w447zOpYfw/1Tv/5AK9NfHTlQIqE3RVR6KY62jCyy9zNDgO64cMwGGP1Fj0zJVf5",
)
VENDORED_ASSETS = (SCALAR_BUNDLE, REDOC_BUNDLE)

# 브라우저는 HTML 문서를 열 때마다 /favicon.ico를 요청한다. 라우트가 없으면
# /docs와 /redoc을 열 때마다 운영 로그에 404가 쌓인다. 외부 URL을 가리킬 수는
# 없으므로(air-gap) 작은 SVG를 인라인으로 들고 직접 서빙한다.
FAVICON_ROUTE = "/favicon.ico"
FAVICON_MEDIA_TYPE = "image/svg+xml"
FAVICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
    '<rect width="32" height="32" rx="7" fill="#1f2933"/>'
    '<circle cx="16" cy="16" r="4.5" fill="#7dd3fc"/>'
    '<circle cx="7" cy="9" r="2.5" fill="#94a3b8"/>'
    '<circle cx="7" cy="23" r="2.5" fill="#94a3b8"/>'
    '<circle cx="25" cy="16" r="2.5" fill="#94a3b8"/>'
    '<g stroke="#94a3b8" stroke-width="1.6">'
    '<line x1="9.2" y1="10.2" x2="13" y2="13.6"/>'
    '<line x1="9.2" y1="21.8" x2="13" y2="18.4"/>'
    '<line x1="20.5" y1="16" x2="22.5" y2="16"/>'
    "</g></svg>"
).encode("utf-8")


SCALAR_CONFIG = json.dumps({
    "theme": "default",
    "customCss": DOCS_CUSTOM_CSS,
    "defaultHttpClient": {"targetKey": "shell", "clientKey": "curl"},
})


def scalar_html(openapi_url: str, title: str, *, bundle_url: str | None = None) -> str:
    """공통 Scalar API reference shell을 렌더링한다.

    Gateway와 Risk Adapter는 의도적으로 별도 OpenAPI 문서를 노출하지만, 주변 documentation UI는 동일하게 유지한다. 이 helper를 한 곳에 두면 docs UX 변경 시 styling/client drift를 줄일 수 있다.
    """
    bundle_url = bundle_url or SCALAR_BUNDLE.route
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
      src="{bundle_url}"
      integrity="{SCALAR_BUNDLE.integrity}"
    ></script>
  </body>
</html>"""
