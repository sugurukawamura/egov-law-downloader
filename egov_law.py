from __future__ import annotations

import argparse
import io
import re
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE_URL = "https://laws.e-gov.go.jp/api/2"
TIMEOUT = 30
DEFAULT_LIMIT = 10
PDF_FILE_TYPE_CANDIDATES = [
    "pdf",
    "pdf_h",
    "pdf_yoko",
    "pdf1",
]


def create_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": "egov-law-downloader/0.2"})
    retry = Retry(
        total=3,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
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
    request_session: requests.Session | None = None,
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
        or law_info.get("law_title")
        or "名称不明"
    )


def get_law_identifier(law: dict[str, Any]) -> str:
    law_info = law.get("law_info") or {}
    current_revision_info = law.get("current_revision_info") or {}
    for key in ("law_id", "law_num"):
        if law_info.get(key):
            return str(law_info[key])
        if current_revision_info.get(key):
            return str(current_revision_info[key])
    raise ValueError("law_id または law_num が見つかりませんでした。")


def build_output_filename(law: dict[str, Any]) -> str:
    return f"{sanitize_filename(get_law_title(law))}_{pick_date_str(law)}.pdf"


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


def extract_first_pdf_from_zip(zip_bytes: bytes) -> bytes | None:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zip_file:
        for name in zip_file.namelist():
            if name.lower().endswith(".pdf"):
                return zip_file.read(name)
    return None


def try_download_pdf_bytes(
    law_identifier: str,
    *,
    request_session: requests.Session | None = None,
) -> bytes:
    active_session = request_session or session
    attempted_urls: list[str] = []
    last_error: Exception | None = None

    for file_type in PDF_FILE_TYPE_CANDIDATES:
        url = f"{BASE_URL}/law_file/{file_type}/{law_identifier}"
        attempted_urls.append(url)
        try:
            response = active_session.get(url, timeout=TIMEOUT)
            if response.status_code != 200:
                continue

            content_type = (response.headers.get("Content-Type") or "").lower()
            content = response.content

            if content.startswith(b"%PDF") or "application/pdf" in content_type:
                return content

            if "zip" in content_type or content[:2] == b"PK":
                pdf_bytes = extract_first_pdf_from_zip(content)
                if pdf_bytes is not None:
                    return pdf_bytes
        except Exception as exc:
            last_error = exc

    message = "PDF取得に失敗しました。試行URL: " + ", ".join(attempted_urls)
    if last_error is not None:
        raise RuntimeError(f"{message} / 最後のエラー: {last_error}") from last_error
    raise RuntimeError(message)


def save_pdf(pdf_bytes: bytes, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(pdf_bytes)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="e-Gov法令APIを使って法令PDFを検索・保存します。"
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
        help="PDFの保存先ディレクトリ。既定値はカレントディレクトリです。",
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
    except ValueError as exc:
        print(exc)
        return 1

    if args.limit < 1:
        print("--limit は 1 以上を指定してください。")
        return 1

    if args.non_interactive and args.select is None:
        print("非対話モードでは --select を指定してください。")
        return 1

    print("e-Gov 法令PDFダウンローダー")
    print(f"検索語: {keyword}")

    try:
        laws = search_laws(keyword, limit=args.limit)
    except requests.HTTPError as exc:
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
        output_path = args.output_dir / build_output_filename(selected)
        print("\nPDFをダウンロード中...")
        pdf_bytes = try_download_pdf_bytes(law_identifier)
        save_pdf(pdf_bytes, output_path)
    except Exception as exc:
        print(f"ダウンロードに失敗しました: {exc}")
        return 1

    print(f"保存完了: {output_path}")
    return 0


def main() -> None:
    sys.exit(run(parse_args()))


if __name__ == "__main__":
    main()
