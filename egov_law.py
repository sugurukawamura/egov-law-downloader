from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
import re
from typing import Any, Callable, Protocol
from urllib.error import HTTPError as UrllibHTTPError
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE_URL = "https://laws.e-gov.go.jp/api/2"
TIMEOUT = 30
DEFAULT_LIMIT = 10
SUPPORTED_FILE_TYPES = ("xml", "json", "html", "rtf", "docx")


class HTTPError(Exception):
    """API 呼び出し時の HTTP エラーを分かりやすく扱うための例外です。"""


class Response:
    """`requests.Response` の代わりに使う、小さなレスポンス入れ物です。"""

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
    """テスト差し替え用の最小インターフェースです。"""

    def get(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: int | float | None = None,
    ) -> Any: ...


class SimpleSession:
    """標準ライブラリだけで GET リクエストと簡単なリトライを行います。"""

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
    session.headers.update({"User-Agent": "egov-law-downloader/0.4"})
    return session


session = create_session()


def sanitize_filename(name: str) -> str:
    """OS で扱いにくい文字を置き換えて、保存しやすい名前にします。"""
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    name = re.sub(r"\s+", " ", name).strip()
    return (name or "法令")[:120]


def pick_date_str(law: dict[str, Any]) -> str:
    """API レスポンスの中から、ファイル名に使う日付を優先順で選びます。"""
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
    """検索 API のレスポンス差分を吸収して、法令一覧だけを取り出します。"""
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
    """キーワードで法令候補を検索します。"""
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
    """表示用の法令名を、使える項目から順に拾います。"""
    law_info = law.get("law_info") or {}
    current_revision_info = law.get("current_revision_info") or {}
    return (
        current_revision_info.get("law_title")
        or law.get("revision_info", {}).get("law_title")
        or law_info.get("law_title")
        or "名称不明"
    )


def get_law_identifier(law: dict[str, Any]) -> str:
    """ダウンロード API に渡す識別子を探します。"""
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
    """法令名と日付を含む保存ファイル名を作ります。"""
    return f"{sanitize_filename(get_law_title(law))}_{pick_date_str(law)}.{file_type}"


def format_law_summary(law: dict[str, Any], index: int | None = None) -> str:
    """CLI と GUI の両方で使える候補表示文字列を作ります。"""
    law_info = law.get("law_info") or {}
    parts = [get_law_title(law)]
    if law_info.get("law_num"):
        parts.append(f"法令番号: {law_info['law_num']}")
    parts.append(f"日付: {pick_date_str(law)}")
    summary = " | ".join(parts)
    if index is None:
        return summary
    return f"[{index}] {summary}"


def display_candidates(laws: list[dict[str, Any]]) -> None:
    print("\n候補一覧")
    print("-" * 80)
    for index, law in enumerate(laws, start=1):
        print(format_law_summary(law, index))


def parse_selection_text(selection_text: str) -> list[int]:
    """`1,3,5` のような入力を整数リストへ変換します。"""
    values: list[int] = []
    for chunk in selection_text.split(","):
        value = chunk.strip()
        if not value:
            continue
        if not value.isdigit():
            raise ValueError("候補番号は 1,3 のように数字とカンマで入力してください。")
        values.append(int(value))
    if not values:
        raise ValueError("候補番号が空です。")
    return values


def choose_laws_interactively(laws: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """CLI で保存対象の法令を複数選べるようにします。"""
    while True:
        choice = input("\n保存したい法令の番号をカンマ区切りで入力してください: ").strip()
        try:
            selections = parse_selection_text(choice)
            return select_laws(laws, selections)
        except ValueError as exc:
            print(exc)


def select_laws(
    laws: list[dict[str, Any]],
    selections: list[int] | None,
) -> list[dict[str, Any]]:
    """候補番号から実際の法令データを取り出します。"""
    if not laws:
        raise ValueError("候補がありません。")
    if selections is None:
        return choose_laws_interactively(laws)

    chosen: list[dict[str, Any]] = []
    seen: set[int] = set()
    for selection in selections:
        if selection < 1 or selection > len(laws):
            raise ValueError(f"--select は 1 から {len(laws)} の範囲で指定してください。")
        if selection in seen:
            continue
        seen.add(selection)
        chosen.append(laws[selection - 1])
    return chosen


def validate_file_types(file_types: list[str]) -> list[str]:
    """`html,json` や `--file-type html --file-type json` を正規化します。"""
    normalized_types: list[str] = []
    seen: set[str] = set()

    for raw_value in file_types:
        for chunk in raw_value.split(","):
            file_type = chunk.strip().lower()
            if not file_type:
                continue
            if file_type not in SUPPORTED_FILE_TYPES:
                choices = ", ".join(SUPPORTED_FILE_TYPES)
                raise ValueError(f"--file-type は {choices} のいずれかを指定してください。")
            if file_type in seen:
                continue
            seen.add(file_type)
            normalized_types.append(file_type)

    if not normalized_types:
        raise ValueError("少なくとも 1 つの保存形式を指定してください。")
    return normalized_types


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
    """1 件の法令を 1 形式で取得します。"""
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
    """保存先フォルダを作ってからファイルを書き込みます。"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(content)


def download_law_files(
    law: dict[str, Any],
    file_types: list[str],
    output_dir: Path,
    *,
    request_session: SessionLike | None = None,
    asof: str | None = None,
    reporter: Callable[[str], None] | None = None,
) -> list[Path]:
    """1 件の法令を複数形式でダウンロードします。"""
    report = reporter or (lambda _message: None)
    law_identifier = get_law_identifier(law)
    law_title = get_law_title(law)
    saved_paths: list[Path] = []

    for file_type in file_types:
        report(f"ダウンロード中: {law_title} ({file_type})")
        content = download_law_file(
            law_identifier,
            file_type,
            request_session=request_session,
            asof=asof,
        )
        output_path = output_dir / build_output_filename(law, file_type)
        save_law_file(content, output_path)
        saved_paths.append(output_path)
        report(f"保存完了: {output_path}")

    return saved_paths


def download_selected_laws(
    selected_laws: list[dict[str, Any]],
    file_types: list[str],
    output_dir: Path,
    *,
    request_session: SessionLike | None = None,
    asof: str | None = None,
    reporter: Callable[[str], None] | None = None,
) -> list[Path]:
    """複数法令 x 複数形式の保存をまとめて行います。"""
    report = reporter or (lambda _message: None)
    saved_paths: list[Path] = []

    for law in selected_laws:
        report(f"対象法令: {get_law_title(law)}")
        saved_paths.extend(
            download_law_files(
                law,
                file_types,
                output_dir,
                request_session=request_session,
                asof=asof,
                reporter=report,
            )
        )

    return saved_paths


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
        help="保存する候補番号。複数選ぶ場合は 1,3 のように指定します。",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path.cwd(),
        help="保存先ディレクトリ。既定値はカレントディレクトリです。",
    )
    parser.add_argument(
        "--file-type",
        action="append",
        default=["html"],
        help="保存形式。html,json のようなカンマ区切りや複数指定が使えます。",
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
    parser.add_argument(
        "--gui",
        action="store_true",
        help="GUI を起動します。",
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


def run_cli(args: argparse.Namespace) -> int:
    """CLI モードの入口です。処理順が追いやすいように段階ごとに表示します。"""
    try:
        keyword = resolve_keyword(args)
        file_types = validate_file_types(args.file_type)
        selections = parse_selection_text(args.select) if args.select else None
    except ValueError as exc:
        print(exc)
        return 1

    if args.limit < 1:
        print("--limit は 1 以上を指定してください。")
        return 1

    if args.non_interactive and selections is None:
        print("非対話モードでは --select を指定してください。")
        return 1

    print("e-Gov 法令ファイルダウンローダー")
    print("1. 検索条件を確認")
    print(f"   検索語: {keyword}")
    print(f"   保存形式: {', '.join(file_types)}")
    print(f"   保存先: {args.output_dir}")

    try:
        print("2. API に問い合わせて法令候補を取得")
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
        print("3. 保存対象の法令を選択")
        selected_laws = select_laws(laws, selections)
        print("4. 選んだ法令を指定形式でダウンロード")
        saved_paths = download_selected_laws(
            selected_laws,
            file_types,
            args.output_dir,
            asof=args.asof,
            reporter=lambda message: print(f"   {message}"),
        )
    except Exception as exc:
        print(f"ダウンロードに失敗しました: {exc}")
        return 1

    print("5. 保存完了")
    for saved_path in saved_paths:
        print(f"   {saved_path}")
    return 0


class EgovLawApp:
    """初心者でも流れを追いやすいよう、検索と保存を画面で分けた GUI です。"""

    def __init__(self, root: Any) -> None:
        import tkinter as tk
        from tkinter import ttk

        self.root = root
        self.root.title("e-Gov 法令ダウンローダー")
        self.root.geometry("980x720")

        self.laws: list[dict[str, Any]] = []
        self.keyword_var = tk.StringVar()
        self.limit_var = tk.StringVar(value=str(DEFAULT_LIMIT))
        self.asof_var = tk.StringVar()
        self.output_dir_var = tk.StringVar(value=str(Path.cwd()))
        self.file_type_vars = {
            file_type: tk.BooleanVar(value=(file_type == "html"))
            for file_type in SUPPORTED_FILE_TYPES
        }

        root.columnconfigure(0, weight=1)
        root.rowconfigure(3, weight=1)
        root.rowconfigure(5, weight=1)

        search_frame = ttk.LabelFrame(root, text="1. 検索条件")
        search_frame.grid(row=0, column=0, sticky="nsew", padx=12, pady=12)
        search_frame.columnconfigure(1, weight=1)

        ttk.Label(search_frame, text="法令名").grid(row=0, column=0, sticky="w", padx=8, pady=6)
        ttk.Entry(search_frame, textvariable=self.keyword_var).grid(
            row=0, column=1, sticky="ew", padx=8, pady=6
        )
        ttk.Label(search_frame, text="候補件数").grid(row=1, column=0, sticky="w", padx=8, pady=6)
        ttk.Entry(search_frame, textvariable=self.limit_var, width=8).grid(
            row=1, column=1, sticky="w", padx=8, pady=6
        )
        ttk.Label(search_frame, text="時点 (任意)").grid(row=2, column=0, sticky="w", padx=8, pady=6)
        ttk.Entry(search_frame, textvariable=self.asof_var).grid(
            row=2, column=1, sticky="ew", padx=8, pady=6
        )
        ttk.Button(search_frame, text="法令を検索", command=self.search).grid(
            row=0, column=2, rowspan=3, sticky="ns", padx=8, pady=6
        )

        type_frame = ttk.LabelFrame(root, text="2. ダウンロード形式")
        type_frame.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 12))
        for index, file_type in enumerate(SUPPORTED_FILE_TYPES):
            ttk.Checkbutton(
                type_frame,
                text=file_type.upper(),
                variable=self.file_type_vars[file_type],
            ).grid(row=0, column=index, sticky="w", padx=8, pady=8)

        result_frame = ttk.LabelFrame(root, text="3. 検索結果 (複数選択可)")
        result_frame.grid(row=3, column=0, sticky="nsew", padx=12, pady=(0, 12))
        result_frame.columnconfigure(0, weight=1)
        result_frame.rowconfigure(0, weight=1)

        self.result_listbox = tk.Listbox(result_frame, selectmode="extended")
        self.result_listbox.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        result_scrollbar = ttk.Scrollbar(
            result_frame, orient="vertical", command=self.result_listbox.yview
        )
        result_scrollbar.grid(row=0, column=1, sticky="ns", pady=8)
        self.result_listbox.config(yscrollcommand=result_scrollbar.set)

        output_frame = ttk.LabelFrame(root, text="4. 保存先")
        output_frame.grid(row=4, column=0, sticky="ew", padx=12, pady=(0, 12))
        output_frame.columnconfigure(0, weight=1)
        ttk.Entry(output_frame, textvariable=self.output_dir_var).grid(
            row=0, column=0, sticky="ew", padx=8, pady=8
        )
        ttk.Button(output_frame, text="参照...", command=self.choose_output_dir).grid(
            row=0, column=1, padx=8, pady=8
        )
        ttk.Button(output_frame, text="選択した法令を保存", command=self.download).grid(
            row=0, column=2, padx=8, pady=8
        )

        log_frame = ttk.LabelFrame(root, text="5. 実行ログ")
        log_frame.grid(row=5, column=0, sticky="nsew", padx=12, pady=(0, 12))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)

        self.log_text = tk.Text(log_frame, wrap="word", height=12)
        self.log_text.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        log_scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        log_scrollbar.grid(row=0, column=1, sticky="ns", pady=8)
        self.log_text.config(yscrollcommand=log_scrollbar.set)

        self.append_log("アプリを起動しました。法令名を入れて「法令を検索」を押してください。")

    def append_log(self, message: str) -> None:
        """画面下部に、今どの段階なのかを順に表示します。"""
        self.log_text.insert("end", message + "\n")
        self.log_text.see("end")
        self.root.update_idletasks()

    def choose_output_dir(self) -> None:
        from tkinter import filedialog

        selected_dir = filedialog.askdirectory(initialdir=self.output_dir_var.get() or str(Path.cwd()))
        if selected_dir:
            self.output_dir_var.set(selected_dir)
            self.append_log(f"保存先を変更しました: {selected_dir}")

    def get_selected_file_types(self) -> list[str]:
        return [name for name, selected in self.file_type_vars.items() if selected.get()]

    def search(self) -> None:
        from tkinter import messagebox

        keyword = self.keyword_var.get().strip()
        if not keyword:
            messagebox.showerror("入力エラー", "法令名を入力してください。")
            return

        try:
            limit = int(self.limit_var.get())
            if limit < 1:
                raise ValueError
        except ValueError:
            messagebox.showerror("入力エラー", "候補件数は 1 以上の整数で入力してください。")
            return

        self.append_log("検索開始: API へ法令候補を問い合わせます。")
        try:
            self.laws = search_laws(keyword, limit=limit)
        except Exception as exc:
            messagebox.showerror("検索失敗", str(exc))
            self.append_log(f"検索失敗: {exc}")
            return

        self.result_listbox.delete(0, "end")
        for law in self.laws:
            self.result_listbox.insert("end", format_law_summary(law))

        if not self.laws:
            self.append_log("候補が見つかりませんでした。")
            messagebox.showinfo("検索結果", "候補が見つかりませんでした。")
            return

        self.append_log(f"検索完了: {len(self.laws)} 件の候補を表示しました。")

    def download(self) -> None:
        from tkinter import messagebox

        if not self.laws:
            messagebox.showerror("保存エラー", "先に法令を検索してください。")
            return

        selected_indexes = list(self.result_listbox.curselection())
        if not selected_indexes:
            messagebox.showerror("保存エラー", "保存したい法令を 1 件以上選択してください。")
            return

        try:
            file_types = validate_file_types(self.get_selected_file_types())
        except ValueError as exc:
            messagebox.showerror("保存エラー", str(exc))
            return

        output_dir = Path(self.output_dir_var.get() or Path.cwd())
        selected_laws = [self.laws[index] for index in selected_indexes]
        asof = self.asof_var.get().strip() or None

        self.append_log("保存開始: 選択した法令を順番にダウンロードします。")
        try:
            saved_paths = download_selected_laws(
                selected_laws,
                file_types,
                output_dir,
                asof=asof,
                reporter=self.append_log,
            )
        except Exception as exc:
            messagebox.showerror("保存失敗", str(exc))
            self.append_log(f"保存失敗: {exc}")
            return

        self.append_log(f"保存完了: {len(saved_paths)} ファイルを保存しました。")
        messagebox.showinfo(
            "保存完了",
            f"{len(saved_paths)} ファイルを保存しました。\n保存先: {output_dir}",
        )


def launch_gui() -> int:
    """GUI を起動します。環境が対応していない場合はエラーを返します。"""
    try:
        import tkinter as tk
    except ImportError:
        print("この環境では tkinter が使えないため GUI を起動できません。")
        return 1

    root = tk.Tk()
    EgovLawApp(root)
    root.mainloop()
    return 0


def main(argv: list[str] | None = None) -> None:
    raw_args = sys.argv[1:] if argv is None else argv
    args = parse_args(raw_args)

    # 引数なしで起動したときは、初心者でも扱いやすい GUI を既定にします。
    if args.gui or not raw_args:
        sys.exit(launch_gui())

    sys.exit(run_cli(args))


if __name__ == "__main__":
    main()
