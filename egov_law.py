from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
import re
from typing import Any, Protocol
from urllib.error import HTTPError as UrllibHTTPError
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE_URL = "https://laws.e-gov.go.jp/api/2"
TIMEOUT = 30
DEFAULT_LIMIT = 10
SUPPORTED_FILE_TYPES = ("xml", "json", "html", "rtf", "docx")
TEXT_FILE_TYPES = {"xml", "json", "html", "rtf"}
BINARY_FILE_TYPES = {"docx"}


class HTTPError(Exception):
    pass


class Response:
    def __init__(
        self,
        *,
        content: bytes,
        status_code: int,
        headers: dict[str, str],
        url: str,
    ) -> None:
        self.content = content
        self.status_code = status_code
        self.headers = headers
        self.url = url

    @property
    def text(self) -> str:
        charset = "utf-8"
        content_type = self.headers.get("Content-Type", "")
        match = re.search(r"charset=([^\s;]+)", content_type, flags=re.IGNORECASE)
        if match:
            charset = match.group(1)
        return self.content.decode(charset, errors="replace")

    def json(self) -> Any:
        return json.loads(self.text)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise HTTPError(f"HTTP {self.status_code}: {self.url}")


class SessionLike(Protocol):
    def get(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: int | float | None = None,
    ) -> Any: ...


class SimpleSession:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self.retryable_statuses = {429, 500, 502, 503, 504}
        self.max_retries = 3
        self.backoff_factor = 0.5

    def get(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: int | float | None = None,
    ) -> Response:
        query = urlencode(params or {})
        full_url = f"{url}?{query}" if query else url
        merged_headers = {**self.headers, **(headers or {})}
        request = Request(full_url, headers=merged_headers, method="GET")

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                with urlopen(request, timeout=timeout) as response:
                    return Response(
                        content=response.read(),
                        status_code=getattr(response, "status", response.getcode()),
                        headers=dict(response.headers.items()),
                        url=response.geturl(),
                    )
            except UrllibHTTPError as exc:
                if exc.code in self.retryable_statuses and attempt < self.max_retries:
                    time.sleep(self.backoff_factor * (2**attempt))
                    last_error = exc
                    continue
                raise HTTPError(f"HTTP {exc.code}: {full_url}") from exc
            except URLError as exc:
                if attempt < self.max_retries:
                    time.sleep(self.backoff_factor * (2**attempt))
                    last_error = exc
                    continue
                raise RuntimeError(f"接続エラー: {exc.reason}") from exc

        raise RuntimeError(f"接続エラー: {last_error}")


def create_session() -> SimpleSession:
    session = SimpleSession()
    session.headers.update({"User-Agent": "egov-law-downloader/0.3"})
    return session


session = create_session()


def sanitize_filename(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    name = re.sub(r"\s+", " ", name).strip()
    return (name or "法令")[:120]


def pick_date_str(law: dict[str, Any]) -> str:
    current_revision_info = law.get("current_revision_info") or {}
    law_info = law.get("law_info") or {}
    candidates = [
        current_revision_info.get("amendment_enforcement_date"),
        current_revision_info.get("amendment_promulgate_date"),
        current_revision_info.get("updated"),
        law_info.get("promulgation_date"),
    ]

    for value in candidates:
        if not value:
            continue
        match = re.match(r"(\d{4})-(\d{2})-(\d{2})", str(value))
        if match:
            return f"{match.group(1)}{match.group(2)}{match.group(3)}"

    return datetime.now().strftime("%Y%m%d")


def extract_laws(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("laws", "results", "items", "data"):
            if isinstance(data.get(key), list):
                return data[key]
    return []


def search_laws(
    keyword: str,
    limit: int = DEFAULT_LIMIT,
    *,
    request_session: SessionLike | None = None,
) -> list[dict[str, Any]]:
    active_session = request_session or session
    response = active_session.get(
        f"{BASE_URL}/laws",
        params={
            "law_title": keyword,
            "limit": limit,
            "repeal_status": "None",
        },
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    return extract_laws(response.json())


def get_law_title(law: dict[str, Any]) -> str:
    law_info = law.get("law_info") or {}
    current_revision_info = law.get("current_revision_info") or {}
    return (
        current_revision_info.get("law_title")
        or law.get("revision_info", {}).get("law_title")
        or law_info.get("law_title")
        or "名称不明"
    )


def get_law_identifier(law: dict[str, Any]) -> str:
    law_info = law.get("law_info") or {}
    current_revision_info = law.get("current_revision_info") or {}
    revision_info = law.get("revision_info") or {}

    for source in (current_revision_info, revision_info, law_info):
        if source.get("law_revision_id"):
            return str(source["law_revision_id"])
    for source in (law_info, current_revision_info, revision_info):
        if source.get("law_id"):
            return str(source["law_id"])
        if source.get("law_num"):
            return str(source["law_num"])
    raise ValueError("law_id、law_num、law_revision_id のいずれも見つかりませんでした。")


def build_output_filename(law: dict[str, Any], file_type: str) -> str:
    return f"{sanitize_filename(get_law_title(law))}_{pick_date_str(law)}.{file_type}"


def display_candidates(laws: list[dict[str, Any]]) -> None:
    print("\n候補一覧")
    print("-" * 80)
    for index, law in enumerate(laws, start=1):
        law_info = law.get("law_info") or {}
        print(f"[{index}] {get_law_title(law)}")
        if law_info.get("law_num"):
            print(f"    法令番号: {law_info['law_num']}")
        print(f"    日付: {pick_date_str(law)}")


def choose_law_interactively(laws: list[dict[str, Any]]) -> dict[str, Any]:
    while True:
        choice = input("\n保存したい法令の番号を入力してください: ").strip()
        if not choice.isdigit():
            print("数字で入力してください。")
            continue

        index = int(choice)
        if 1 <= index <= len(laws):
            return laws[index - 1]
        print("候補の範囲内で入力してください。")


def select_law(laws: list[dict[str, Any]], selection: int | None) -> dict[str, Any]:
    if not laws:
        raise ValueError("候補がありません。")
    if selection is None:
        return choose_law_interactively(laws)
    if 1 <= selection <= len(laws):
        return laws[selection - 1]
    raise ValueError(f"--select は 1 から {len(laws)} の範囲で指定してください。")


def validate_file_type(file_type: str) -> str:
    normalized = file_type.lower()
    if normalized not in SUPPORTED_FILE_TYPES:
        choices = ", ".join(SUPPORTED_FILE_TYPES)
        raise ValueError(f"--file-type は {choices} のいずれかを指定してください。")
    return normalized


def build_request_headers(file_type: str) -> dict[str, str]:
    if file_type == "json":
        return {"Accept": "application/json"}
    if file_type == "xml":
        return {"Accept": "application/xml"}
    return {"Accept": "*/*"}


def download_law_file(
    law_identifier: str,
    file_type: str,
    *,
    request_session: SessionLike | None = None,
    asof: str | None = None,
) -> bytes:
    active_session = request_session or session
    url = f"{BASE_URL}/law_file/{file_type}/{law_identifier}"
    params = {"asof": asof} if asof else None
    response = active_session.get(
        url,
        params=params,
        headers=build_request_headers(file_type),
        timeout=TIMEOUT,
    )
    try:
        response.raise_for_status()
    except HTTPError as exc:
        detail = response.text[:300] if response.text else ""
        raise RuntimeError(f"取得失敗: {url} / {detail}") from exc
    return response.content


def save_law_file(content: bytes, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(content)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="e-Gov法令APIを使って法令ファイルを検索・保存します。"
    )
    parser.add_argument("keyword", nargs="?", help="検索する法令名")
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help=f"候補表示件数。既定値は {DEFAULT_LIMIT} 件です。",
    )
    parser.add_argument(
        "--select",
        type=int,
        help="候補番号を指定して非対話で保存します。",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path.cwd(),
        help="保存先ディレクトリ。既定値はカレントディレクトリです。",
    )
    parser.add_argument(
        "--file-type",
        default="html",
        help="保存するファイル形式。xml/json/html/rtf/docx を指定できます。既定値は html です。",
    )
    parser.add_argument(
        "--asof",
        help="法令の時点を YYYY-MM-DD 形式で指定します。",
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="対話入力を行わずに実行します。--select と組み合わせて使います。",
    )
    return parser.parse_args(argv)


def resolve_keyword(args: argparse.Namespace) -> str:
    if args.keyword:
        return args.keyword.strip()
    if args.non_interactive:
        raise ValueError("非対話モードでは keyword を指定してください。")
    keyword = input("検索したい法令名を入力してください: ").strip()
    if not keyword:
        raise ValueError("法令名が空です。")
    return keyword


def run(args: argparse.Namespace) -> int:
    try:
        keyword = resolve_keyword(args)
        file_type = validate_file_type(args.file_type)
    except ValueError as exc:
        print(exc)
        return 1

    if args.limit < 1:
        print("--limit は 1 以上を指定してください。")
        return 1

    if args.non_interactive and args.select is None:
        print("非対話モードでは --select を指定してください。")
        return 1

    print("e-Gov 法令ファイルダウンローダー")
    print(f"検索語: {keyword}")
    print(f"保存形式: {file_type}")

    try:
        laws = search_laws(keyword, limit=args.limit)
    except HTTPError as exc:
        print(f"検索APIエラー: {exc}")
        return 1
    except Exception as exc:
        print(f"検索に失敗しました: {exc}")
        return 1

    if not laws:
        print("候補が見つかりませんでした。")
        return 0

    display_candidates(laws)

    try:
        selected = select_law(laws, args.select)
        law_identifier = get_law_identifier(selected)
        output_path = args.output_dir / build_output_filename(selected, file_type)
        print("\n法令ファイルをダウンロード中...")
        content = download_law_file(law_identifier, file_type, asof=args.asof)
        save_law_file(content, output_path)
    except Exception as exc:
        print(f"ダウンロードに失敗しました: {exc}")
        return 1

    print(f"保存完了: {output_path}")
    return 0


def main() -> None:
    sys.exit(run(parse_args()))


if __name__ == "__main__":
    main()
