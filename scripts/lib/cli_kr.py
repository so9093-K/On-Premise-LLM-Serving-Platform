from __future__ import annotations

import argparse
from typing import Any, Sequence


class KoreanHelpFormatter(argparse.HelpFormatter):
    """argparse 기본 도움말 label을 한국어 중심으로 표시한다.

    명령어 이름, option flag, env var는 원문을 유지하되 사람이 읽는
    ``usage``/``options``/``show this help`` 같은 argparse 기본 문구가
    영어로 회귀하지 않도록 공통 formatter로 고정한다.
    """

    def start_section(self, heading: str | None) -> None:
        translations = {
            "positional arguments": "위치 인자",
            "options": "옵션",
            "optional arguments": "옵션",
            "subcommands": "하위 명령",
            "commands": "하위 명령",
        }
        super().start_section(translations.get(heading or "", heading))

    def add_usage(self, usage: str | None, actions: Sequence[argparse.Action], groups: Sequence[argparse._ArgumentGroup], prefix: str | None = None) -> None:  # type: ignore[name-defined]
        super().add_usage(usage, actions, groups, prefix="사용법: ")


class KoreanArgumentParser(argparse.ArgumentParser):
    """한국어 운영자 UX용 ArgumentParser."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("formatter_class", KoreanHelpFormatter)
        super().__init__(*args, **kwargs)

    def add_argument(self, *args: Any, **kwargs: Any) -> argparse.Action:
        if args and any(arg in {"-h", "--help"} for arg in args):
            kwargs["help"] = "도움말을 표시하고 종료합니다."
        return super().add_argument(*args, **kwargs)

    def add_subparsers(self, *args: Any, **kwargs: Any) -> argparse._SubParsersAction:  # type: ignore[type-arg]
        kwargs.setdefault("title", "하위 명령")
        kwargs.setdefault("metavar", "명령")
        return super().add_subparsers(*args, **kwargs)
