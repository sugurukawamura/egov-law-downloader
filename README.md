# egov-law-downloader

e-Gov法令API Version 2 を利用して、法令名で検索した法令本文ファイルをダウンロードするPythonスクリプトです。

公式API仕様に合わせて、`xml`、`json`、`html`、`rtf`、`docx` の取得に対応しています。

## 特徴

- 法令名を使ってe-Gov法令APIから法令を検索
- 候補一覧から対象法令を選択
- 公式APIで取得可能な法令本文ファイルを保存
- 対話実行と非対話実行の両方に対応
- 保存先ディレクトリを指定可能
- 一時的な通信エラーに備えた簡単なリトライ付き

## 動作要件

- Python 3.10以上

## インストール

追加の依存パッケージは不要です。

```bash
python -m unittest discover -s tests -q
```

## 使い方

対話形式で使う場合:

```bash
python egov_law.py
```

実行後の流れ:

1. 検索したい法令名を入力
2. 表示された候補一覧から番号を選択
3. 指定形式の法令ファイルを保存

非対話で使う場合:

```bash
python egov_law.py "民法" --non-interactive --select 1 --file-type html --output-dir downloads
```

## 主なオプション

- `--limit`: 候補表示件数
- `--select`: 保存する候補番号
- `--output-dir`: 保存先ディレクトリ
- `--file-type`: 保存形式。`xml`、`json`、`html`、`rtf`、`docx` を指定可能
- `--asof`: 法令の時点を `YYYY-MM-DD` 形式で指定
- `--non-interactive`: 対話入力なしで実行

## 保存されるファイル

保存ファイル名は次の形式です。

```text
法令名_YYYYMMDD.拡張子
```

例:

```text
民法_20251001.html
```

日付は、APIレスポンスに含まれる改正施行日・改正公布日・更新日・公布日などを優先順で使って決定されます。

## 注意点

- e-Gov API Version 2 の公式 `law_file` で取得できる形式のみ対応しています
- `pdf` は公式APIの対応形式ではないため、このツールでは取得対象外です
- レスポンス仕様変更があった場合は調整が必要です

## テスト

基本的な単体テストを `unittest` で用意しています。

```bash
python -m unittest discover -s tests -q
```

## ライセンス

MIT License
