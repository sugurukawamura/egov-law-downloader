# egov-law-downloader

e-Gov 法令 API を使って法令を検索し、本文や添付ファイルを保存するための小さなブラウザ UI です。

## Files

- `web/index.html`
- `lawapi-v2.yaml`
- `tools/download-official-pdf.ps1`

## できること

- 法令名で検索
- 検索結果から複数法令を選択
- `XML / JSON / HTML / RTF / DOCX` を保存
- 公式HTML本文を印刷ビューで開いて `PDF 印刷` 保存
- 付属 PowerShell スクリプトで `公式 PDF` を保存
- 添付ファイルを `ATTACH ZIP` で一括保存
- ステータスとログを画面内で確認

## PDF の扱い

`law_file` は `file_type=pdf` を提供していません。
このリポジトリでは、法令本文PDFを 2 通りで扱います。

### 1. `PDF 印刷`

ブラウザ UI の `PDF 印刷` は次の流れです。

1. `law_file/html/...` で公式HTML本文を取得
2. 公式本文の見た目に近い印刷ビューを別タブで開く
3. ブラウザの印刷ダイアログから PDF 保存する

### 2. `公式 PDF`

公式サイト本体は、内部API `SelectLawRevisionData.json` と `GetDownloadFilePath.json` を使って公式PDFの実ファイルURLを解決しています。
ブラウザ UI からこの内部APIを直接呼ぶと CORS で失敗するため、付属の PowerShell スクリプト `tools/download-official-pdf.ps1` で取得します。

例:

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\download-official-pdf.ps1 -LawIdOrUniqueId 360CO0000000075 -Layout h1
```

`-LawIdOrUniqueId` には次のどちらでも指定できます。

- `360CO0000000075` のような `law_id`
- `360CO0000000075_20251105_507CO0000000297` のような `law_unique_id`

`-Layout` は次を指定できます。

- `h1`: PDF 横一段
- `v1`: PDF 縦一段
- `v2`: PDF 縦二段
- `v4`: PDF 縦四段

目的に応じて、見た目重視なら `PDF 印刷`、公式ファイルそのものが必要なら `公式 PDF` を使い分けてください。

## 添付ファイルの扱い

`ATTACH ZIP` は `attachment` API を使って添付ファイルを一括取得します。様式、付録、図表ファイルなどをまとめて保存したい場合に使います。

## 使い方

### ブラウザ UI

1. `web/index.html` をブラウザで開く
2. 法令名で検索する
3. 保存したい法令を選ぶ
4. 保存形式を選ぶ
5. 必要なら `YYYY-MM-DD` 形式で時点日を入れる
6. 保存を実行する

### 公式 PDF スクリプト

1. PowerShell を開く
2. リポジトリのルートへ移動する
3. 次のように実行する

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\download-official-pdf.ps1 -LawIdOrUniqueId 360CO0000000075 -Layout h1
```

保存先を明示したい場合:

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\download-official-pdf.ps1 -LawIdOrUniqueId 360CO0000000075 -Layout v2 -OutFile .\downloads\telecom-v2.pdf
```

## 補足

- このリポジトリの MIT License はソースコードのみに適用されます
- 取得した法令データや本文レイアウトの利用条件は e-Gov 側の条件に従います
- ブラウザ UI から内部APIへ直接アクセスできない箇所では、CORS の影響を受けます

## License

MIT License
