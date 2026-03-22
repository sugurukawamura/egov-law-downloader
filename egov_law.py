"""
e-Gov 法令検索: 法令名で候補検索 → 選択 → PDF（横一段）を保存する最小スクリプト

前提:
- Python 3.10 以上推奨
- requests が必要
    pip install requests

注意:
- e-Gov API v2 の laws / law_file を使う想定です。
- PDF取得時、e-Gov側の仕様により ZIP で返る場合があるため、その両方に対応しています。
- PDFの file_type は環境差がある可能性があるため、候補を複数試すようにしています。
"""

from __future__ import annotations

import io
import os
import re
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

BASE_URL = "https://laws.e-gov.go.jp/api/2"
TIMEOUT = 30

# PDF（横一段）に相当しそうな file_type 候補を順番に試す
# e-Gov 側の実際の定義によっては、ここを1つ調整すれば動くようにしています。
PDF_FILE_TYPE_CANDIDATES = [
    "pdf",          # まずはこれを試す
    "pdf_h",        # 横書きの略称を仮定
    "pdf_yoko",     # 念のため
    "pdf1",         # 念のため
]

session = requests.Session()
session.headers.update({
    "User-Agent": "egov-law-downloader/0.1"
})


def sanitize_filename(name: str) -> str:
    """Windowsで使えない文字を _ に置換"""
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    name = re.sub(r"\s+", " ", name).strip()
    # 長すぎると扱いづらいので適度に切る
    return name[:120]


def pick_date_str(law: dict[str, Any]) -> str:
    """
    命名に使う日付を決める
    優先順位:
    1. current_revision_info.amendment_enforcement_date
    2. current_revision_info.amendment_promulgate_date
    3. current_revision_info.updated
    4. law_info.promulgation_date
    """
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

        # "2026-04-01" or "2026-04-01T12:34:56+09:00" を想定
        m = re.match(r"(\d{4})-(\d{2})-(\d{2})", str(value))
        if m:
            return f"{m.group(1)}{m.group(2)}{m.group(3)}"

    return datetime.now().strftime("%Y%m%d")


def search_laws(keyword: str, limit: int = 10) -> list[dict[str, Any]]:
    """
    法令名で候補検索
    公式 ReDoc の説明にある laws API / law_title を使う想定
    """
    url = f"{BASE_URL}/laws"
    params = {
        "law_title": keyword,
        "limit": limit,
        "repeal_status": "None",  # 施行中を優先
    }

    resp = session.get(url, params=params, timeout=TIMEOUT)
    resp.raise_for_status()
    data = resp.json()

    # レスポンス形は今後変わる可能性があるため、よくありそうな形に広めに対応
    if isinstance(data, list):
        return data

    for key in ["laws", "results", "items", "data"]:
        if isinstance(data.get(key), list):
            return data[key]

    return []


def display_candidates(laws: list[dict[str, Any]]) -> None:
    """候補一覧を見やすく表示"""
    print("\n候補一覧")
    print("-" * 80)

    for i, law in enumerate(laws, start=1):
        law_info = law.get("law_info") or {}
        current_revision_info = law.get("current_revision_info") or {}

        title = (
            current_revision_info.get("law_title")
            or law_info.get("law_title")
            or "名称不明"
        )
        law_num = law_info.get("law_num", "")
        date_str = pick_date_str(law)

        print(f"[{i}] {title}")
        if law_num:
            print(f"    法令番号: {law_num}")
        print(f"    日付: {date_str}")


def choose_law(laws: list[dict[str, Any]]) -> dict[str, Any]:
    """番号選択"""
    while True:
        choice = input("\n保存したい法令の番号を入力してください: ").strip()
        if not choice.isdigit():
            print("数字で入力してください。")
            continue

        idx = int(choice)
        if 1 <= idx <= len(laws):
            return laws[idx - 1]

        print("候補の範囲内で入力してください。")


def build_output_filename(law: dict[str, Any]) -> str:
    """法令名_YYYYMMDD.pdf を作る"""
    law_info = law.get("law_info") or {}
    current_revision_info = law.get("current_revision_info") or {}

    title = (
        current_revision_info.get("law_title")
        or law_info.get("law_title")
        or "法令"
    )
    safe_title = sanitize_filename(title)
    date_str = pick_date_str(law)

    return f"{safe_title}_{date_str}.pdf"


def get_law_identifier(law: dict[str, Any]) -> str:
    """
    path に使うIDを取り出す
    まず law_id を優先し、なければ law_num
    """
    law_info = law.get("law_info") or {}
    current_revision_info = law.get("current_revision_info") or {}

    for key in ["law_id", "law_num"]:
        if law_info.get(key):
            return str(law_info[key])
        if current_revision_info.get(key):
            return str(current_revision_info[key])

    raise ValueError("law_id / law_num が見つかりませんでした。")


def try_download_pdf_bytes(law_identifier: str) -> bytes:
    """
    PDF取得を試す。
    e-Govの仕様上、PDFが ZIP で返るケースもあるため両対応。
    """
    last_error: Exception | None = None

    for file_type in PDF_FILE_TYPE_CANDIDATES:
        url = f"{BASE_URL}/law_file/{file_type}/{law_identifier}"

        try:
            resp = session.get(url, timeout=TIMEOUT)
            if resp.status_code != 200:
                continue

            content_type = (resp.headers.get("Content-Type") or "").lower()
            content = resp.content

            # そのまま PDF の場合
            if content.startswith(b"%PDF") or "application/pdf" in content_type:
                return content

            # ZIP の場合
            if "zip" in content_type or content[:2] == b"PK":
                pdf_bytes = extract_first_pdf_from_zip(content)
                if pdf_bytes is not None:
                    return pdf_bytes

        except Exception as e:
            last_error = e
            continue

    if last_error:
        raise RuntimeError(f"PDF取得に失敗しました: {last_error}") from last_error

    raise RuntimeError(
        "PDF取得に失敗しました。file_type の候補が環境に合っていない可能性があります。"
    )


def extract_first_pdf_from_zip(zip_bytes: bytes) -> bytes | None:
    """ZIPの中から最初のPDFを取り出す"""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for name in zf.namelist():
            if name.lower().endswith(".pdf"):
                return zf.read(name)
    return None


def save_pdf(pdf_bytes: bytes, output_path: Path) -> None:
    """PDF保存"""
    output_path.write_bytes(pdf_bytes)


def main() -> None:
    print("e-Gov 法令PDFダウンローダー（最小版）")
    print("法令名で検索し、候補から選んで PDF を保存します。")
    print()

    keyword = input("検索したい法令名を入力してください: ").strip()
    if not keyword:
        print("法令名が空です。")
        sys.exit(1)

    try:
        laws = search_laws(keyword, limit=10)
    except requests.HTTPError as e:
        print(f"検索APIエラー: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"検索に失敗しました: {e}")
        sys.exit(1)

    if not laws:
        print("候補が見つかりませんでした。検索語を変えてみてください。")
        sys.exit(0)

    display_candidates(laws)
    selected = choose_law(laws)

    try:
        law_identifier = get_law_identifier(selected)
        filename = build_output_filename(selected)
        output_path = Path.cwd() / filename

        print("\nPDFをダウンロード中...")
        pdf_bytes = try_download_pdf_bytes(law_identifier)
        save_pdf(pdf_bytes, output_path)

        print(f"保存完了: {output_path}")
    except Exception as e:
        print(f"ダウンロードに失敗しました: {e}")
        print("\n対処候補:")
        print("1. PDF_FILE_TYPE_CANDIDATES の先頭を 'pdf' 以外に変える")
        print("2. laws API のレスポンス項目名が異なる場合は get_law_identifier / display_candidates を調整する")
        sys.exit(1)


if __name__ == "__main__":
    main()
