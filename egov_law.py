from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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
import webbrowser

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


def serialize_laws(laws: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """ブラウザ UI へ渡しやすい形に、候補データを軽く整形します。"""
    serialized: list[dict[str, Any]] = []
    for index, law in enumerate(laws, start=1):
        law_info = law.get("law_info") or {}
        serialized.append(
            {
                "index": index,
                "title": get_law_title(law),
                "summary": format_law_summary(law, index),
                "law_num": law_info.get("law_num", ""),
                "date": pick_date_str(law),
            }
        )
    return serialized


def build_web_ui_page() -> str:
    """ブラウザで開く 1 ページ完結の UI を返します。"""
    supported_types = json.dumps(SUPPORTED_FILE_TYPES, ensure_ascii=False)
    default_output_dir = html.escape(str(Path.cwd()))
    return f"""<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>e-Gov 法令ダウンローダー</title>
  <style>
    :root {{
      --bg: #f5efe4;
      --panel: #fffdf8;
      --ink: #1d2a33;
      --accent: #007a78;
      --accent-soft: #d8f0ea;
      --line: #d8cdbd;
      --warn: #8b2e00;
      --mono: "SFMono-Regular", "Menlo", monospace;
      --sans: "Hiragino Sans", "Yu Gothic", sans-serif;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: var(--sans);
      color: var(--ink);
      background:
        radial-gradient(circle at top left, #fff7df 0, transparent 32%),
        linear-gradient(160deg, #efe4cf 0%, #f5efe4 55%, #e8efe8 100%);
      min-height: 100vh;
    }}
    .wrap {{
      width: min(1100px, calc(100% - 32px));
      margin: 24px auto 40px;
    }}
    .hero {{
      padding: 24px 28px;
      border: 1px solid var(--line);
      background: rgba(255, 253, 248, 0.92);
      backdrop-filter: blur(8px);
      border-radius: 22px;
      box-shadow: 0 20px 50px rgba(61, 52, 40, 0.08);
    }}
    h1 {{
      margin: 0 0 8px;
      font-size: clamp(28px, 4vw, 44px);
    }}
    .lead {{
      margin: 0;
      line-height: 1.7;
    }}
    .grid {{
      display: grid;
      grid-template-columns: 1.1fr 0.9fr;
      gap: 18px;
      margin-top: 18px;
    }}
    .panel {{
      border: 1px solid var(--line);
      background: rgba(255, 253, 248, 0.95);
      border-radius: 18px;
      padding: 18px;
      box-shadow: 0 12px 30px rgba(61, 52, 40, 0.06);
    }}
    .panel h2 {{
      margin: 0 0 12px;
      font-size: 18px;
    }}
    label {{
      display: block;
      margin-bottom: 8px;
      font-weight: 600;
    }}
    input[type="text"], input[type="number"] {{
      width: 100%;
      padding: 12px 14px;
      border-radius: 12px;
      border: 1px solid #cdbfaa;
      background: #fff;
      font-size: 15px;
    }}
    .row {{
      display: grid;
      grid-template-columns: 1fr 180px;
      gap: 12px;
    }}
    .check-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(100px, 1fr));
      gap: 10px;
    }}
    .pill {{
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 10px 12px;
      border-radius: 999px;
      border: 1px solid #cdbfaa;
      background: #fff;
      font-size: 14px;
    }}
    button {{
      appearance: none;
      border: 0;
      border-radius: 999px;
      padding: 12px 18px;
      font-weight: 700;
      font-size: 14px;
      cursor: pointer;
      background: var(--accent);
      color: white;
    }}
    button.secondary {{
      background: #e7ece8;
      color: var(--ink);
    }}
    button:disabled {{
      opacity: 0.5;
      cursor: wait;
    }}
    .actions {{
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      margin-top: 14px;
    }}
    .results {{
      display: grid;
      gap: 10px;
      max-height: 420px;
      overflow: auto;
      padding-right: 6px;
    }}
    .law-card {{
      border: 1px solid #dccfbf;
      border-radius: 14px;
      background: white;
      padding: 12px 14px;
      display: grid;
      grid-template-columns: 28px 1fr;
      gap: 12px;
    }}
    .law-title {{
      font-weight: 700;
      margin-bottom: 4px;
    }}
    .law-meta {{
      color: #5d6a72;
      font-size: 13px;
      line-height: 1.6;
    }}
    .status {{
      margin-top: 12px;
      min-height: 24px;
      font-weight: 700;
      color: var(--warn);
    }}
    .log {{
      background: #162329;
      color: #d9f8ed;
      border-radius: 14px;
      padding: 14px;
      min-height: 260px;
      white-space: pre-wrap;
      font-family: var(--mono);
      font-size: 13px;
      line-height: 1.7;
      overflow: auto;
    }}
    .hint {{
      margin-top: 10px;
      font-size: 13px;
      color: #5d6a72;
      line-height: 1.6;
    }}
    @media (max-width: 860px) {{
      .grid, .row {{ grid-template-columns: 1fr; }}
      .wrap {{ width: min(100% - 20px, 1100px); }}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <h1>e-Gov 法令ダウンローダー</h1>
      <p class="lead">ブラウザ上で法令を検索して、複数の法令を複数形式で保存できます。下のログには、いま何を実行しているかが順番に表示されます。</p>
    </section>
    <div class="grid">
      <section class="panel">
        <h2>1. 検索条件</h2>
        <div class="row">
          <div>
            <label for="keyword">法令名</label>
            <input id="keyword" type="text" placeholder="例: 民法">
          </div>
          <div>
            <label for="limit">候補件数</label>
            <input id="limit" type="number" min="1" value="{DEFAULT_LIMIT}">
          </div>
        </div>
        <div style="margin-top:12px;">
          <label for="asof">時点 (任意)</label>
          <input id="asof" type="text" placeholder="YYYY-MM-DD">
        </div>
        <div style="margin-top:12px;">
          <label for="outputDir">保存先フォルダ</label>
          <input id="outputDir" type="text" value="{default_output_dir}">
        </div>
        <div style="margin-top:14px;">
          <label>2. ダウンロード形式</label>
          <div class="check-grid" id="fileTypes"></div>
        </div>
        <div class="actions">
          <button id="searchButton">法令を検索</button>
          <button id="downloadButton" class="secondary">選択した法令を保存</button>
        </div>
        <div id="status" class="status"></div>
        <p class="hint">保存したい法令は複数選べます。形式も複数選べます。`pdf` は e-Gov API 非対応なので出していません。</p>
      </section>
      <section class="panel">
        <h2>3. 検索結果</h2>
        <div id="results" class="results"></div>
      </section>
      <section class="panel" style="grid-column: 1 / -1;">
        <h2>4. 実行ログ</h2>
        <div id="log" class="log">アプリを起動しました。法令名を入力して検索してください。</div>
      </section>
    </div>
  </div>
  <script>
    const supportedTypes = {supported_types};
    const state = {{
      laws: [],
      selectedIndexes: new Set(),
    }};

    const fileTypesRoot = document.getElementById("fileTypes");
    const resultsRoot = document.getElementById("results");
    const logRoot = document.getElementById("log");
    const statusRoot = document.getElementById("status");
    const searchButton = document.getElementById("searchButton");
    const downloadButton = document.getElementById("downloadButton");

    function appendLog(message) {{
      logRoot.textContent += "\\n" + message;
      logRoot.scrollTop = logRoot.scrollHeight;
    }}

    function setStatus(message, isError = false) {{
      statusRoot.textContent = message;
      statusRoot.style.color = isError ? "#8b2e00" : "#0b6b63";
    }}

    function selectedFileTypes() {{
      return supportedTypes.filter((name) => {{
        const checkbox = document.getElementById(`file-${{name}}`);
        return checkbox && checkbox.checked;
      }});
    }}

    function renderFileTypes() {{
      fileTypesRoot.innerHTML = "";
      supportedTypes.forEach((name, index) => {{
        const wrapper = document.createElement("label");
        wrapper.className = "pill";
        wrapper.innerHTML = `<input type="checkbox" id="file-${{name}}" ${{index === 2 ? "checked" : ""}}> ${{name.toUpperCase()}}`;
        fileTypesRoot.appendChild(wrapper);
      }});
    }}

    function renderResults() {{
      resultsRoot.innerHTML = "";
      if (!state.laws.length) {{
        resultsRoot.innerHTML = "<div class='hint'>検索するとここに候補が出ます。</div>";
        return;
      }}
      state.laws.forEach((law) => {{
        const card = document.createElement("label");
        card.className = "law-card";
        card.innerHTML = `
          <input type="checkbox" data-index="${{law.index}}" ${{state.selectedIndexes.has(law.index) ? "checked" : ""}}>
          <div>
            <div class="law-title">${{law.title}}</div>
            <div class="law-meta">${{law.summary}}</div>
          </div>
        `;
        const checkbox = card.querySelector("input");
        checkbox.addEventListener("change", () => {{
          if (checkbox.checked) {{
            state.selectedIndexes.add(law.index);
          }} else {{
            state.selectedIndexes.delete(law.index);
          }}
        }});
        resultsRoot.appendChild(card);
      }});
    }}

    async function postJson(url, body) {{
      const response = await fetch(url, {{
        method: "POST",
        headers: {{ "Content-Type": "application/json" }},
        body: JSON.stringify(body),
      }});
      return response.json();
    }}

    searchButton.addEventListener("click", async () => {{
      const keyword = document.getElementById("keyword").value.trim();
      const limit = Number(document.getElementById("limit").value);
      if (!keyword) {{
        setStatus("法令名を入力してください。", true);
        return;
      }}
      if (!Number.isInteger(limit) || limit < 1) {{
        setStatus("候補件数は 1 以上の整数で入力してください。", true);
        return;
      }}

      setStatus("検索中です...");
      searchButton.disabled = true;
      try {{
        appendLog("検索開始: API へ法令候補を問い合わせます。");
        const data = await postJson("/api/search", {{ keyword, limit }});
        if (!data.ok) {{
          setStatus(data.error, true);
          appendLog(`検索失敗: ${{data.error}}`);
          return;
        }}
        state.laws = data.laws;
        state.selectedIndexes = new Set();
        renderResults();
        appendLog(`検索完了: ${{data.laws.length}} 件の候補を表示しました。`);
        setStatus(`検索完了: ${{data.laws.length}} 件`);
      }} catch (error) {{
        setStatus(String(error), true);
        appendLog(`検索失敗: ${{error}}`);
      }} finally {{
        searchButton.disabled = false;
      }}
    }});

    downloadButton.addEventListener("click", async () => {{
      const fileTypes = selectedFileTypes();
      const indexes = Array.from(state.selectedIndexes);
      const outputDir = document.getElementById("outputDir").value.trim();
      const asof = document.getElementById("asof").value.trim();

      if (!indexes.length) {{
        setStatus("保存したい法令を 1 件以上選択してください。", true);
        return;
      }}
      if (!fileTypes.length) {{
        setStatus("保存形式を 1 つ以上選択してください。", true);
        return;
      }}
      if (!outputDir) {{
        setStatus("保存先フォルダを入力してください。", true);
        return;
      }}

      downloadButton.disabled = true;
      setStatus("ダウンロード中です...");
      try {{
        appendLog("保存開始: 選択した法令を順番にダウンロードします。");
        const data = await postJson("/api/download", {{ indexes, file_types: fileTypes, output_dir: outputDir, asof }});
        if (!data.ok) {{
          setStatus(data.error, true);
          appendLog(`保存失敗: ${{data.error}}`);
          return;
        }}
        data.logs.forEach((line) => appendLog(line));
        appendLog(`保存完了: ${{data.saved_paths.length}} ファイルを保存しました。`);
        setStatus(`保存完了: ${{data.saved_paths.length}} ファイル`);
      }} catch (error) {{
        setStatus(String(error), true);
        appendLog(`保存失敗: ${{error}}`);
      }} finally {{
        downloadButton.disabled = false;
      }}
    }});

    renderFileTypes();
    renderResults();
  </script>
</body>
</html>
"""


@dataclass
class WebUIState:
    """ブラウザ UI の現在状態を、サーバー側でまとめて持ちます。"""

    laws: list[dict[str, Any]] = field(default_factory=list)


def build_json_response(payload: dict[str, Any]) -> bytes:
    """ブラウザとのやりとりは JSON に統一します。"""
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def create_web_handler(state: WebUIState) -> type[BaseHTTPRequestHandler]:
    """サーバーに現在の検索結果を持たせた Handler を作ります。"""

    class EgovLawWebHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path != "/":
                self.send_error(404)
                return
            page = build_web_ui_page().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.end_headers()
            self.wfile.write(page)

        def do_POST(self) -> None:
            content_length = int(self.headers.get("Content-Length", "0"))
            raw_body = self.rfile.read(content_length)

            try:
                payload = json.loads(raw_body.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                self._send_json({"ok": False, "error": "JSON を読み取れませんでした。"}, 400)
                return

            if self.path == "/api/search":
                self._handle_search(payload)
                return
            if self.path == "/api/download":
                self._handle_download(payload)
                return

            self._send_json({"ok": False, "error": "未対応の API です。"}, 404)

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _handle_search(self, payload: dict[str, Any]) -> None:
            keyword = str(payload.get("keyword", "")).strip()
            limit = int(payload.get("limit", DEFAULT_LIMIT))

            if not keyword:
                self._send_json({"ok": False, "error": "法令名を入力してください。"}, 400)
                return
            if limit < 1:
                self._send_json({"ok": False, "error": "候補件数は 1 以上にしてください。"}, 400)
                return

            try:
                state.laws = search_laws(keyword, limit=limit)
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, 500)
                return

            self._send_json({"ok": True, "laws": serialize_laws(state.laws)})

        def _handle_download(self, payload: dict[str, Any]) -> None:
            indexes = payload.get("indexes") or []
            file_types = payload.get("file_types") or []
            output_dir = Path(str(payload.get("output_dir", "")).strip() or str(Path.cwd()))
            asof = str(payload.get("asof", "")).strip() or None

            try:
                selected_laws = select_laws(state.laws, [int(index) for index in indexes])
                normalized_types = validate_file_types([str(file_type) for file_type in file_types])
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, 400)
                return

            logs: list[str] = []
            try:
                saved_paths = download_selected_laws(
                    selected_laws,
                    normalized_types,
                    output_dir,
                    asof=asof,
                    reporter=logs.append,
                )
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc), "logs": logs}, 500)
                return

            self._send_json(
                {
                    "ok": True,
                    "logs": logs,
                    "saved_paths": [str(path) for path in saved_paths],
                }
            )

        def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
            body = build_json_response(payload)
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return EgovLawWebHandler


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


def launch_gui() -> int:
    """ブラウザで使うローカル UI サーバーを起動します。"""
    state = WebUIState()
    handler = create_web_handler(state)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    host, port = server.server_address
    url = f"http://{host}:{port}/"

    print("ブラウザ UI を起動しました。")
    print(f"URL: {url}")
    print("終了するときは Ctrl+C を押してください。")

    # ブラウザを自動で開ける環境ではそのまま開きます。
    try:
        webbrowser.open(url)
    except Exception:
        pass

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nUI サーバーを停止します。")
    finally:
        server.server_close()
    return 0


def main(argv: list[str] | None = None) -> None:
    raw_args = sys.argv[1:] if argv is None else argv
    args = parse_args(raw_args)

    # 既定動作は CLI にして、GUI が必要なときだけ明示的に起動します。
    if args.gui:
        sys.exit(launch_gui())

    sys.exit(run_cli(args))


if __name__ == "__main__":
    main()
