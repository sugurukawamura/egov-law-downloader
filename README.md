# egov-law-downloader

e-Gov法令API Version 2 を使って、法令を検索し、本文ファイルを保存するツールです。

いちばん使いやすい形として、今は macOS 用のネイティブアプリを用意しています。
検索結果から複数の法令を選び、`xml` `json` `html` `rtf` `docx` の複数形式でまとめて保存できます。

## まず使う方法

このリポジトリには、ダブルクリック起動できる `.app` を作るスクリプトがあります。

```bash
./scripts/build_macos_app.sh
```

ビルドが終わると、次の場所にアプリができます。

```text
dist/EgovLawDownloader.app
```

あとは Finder で `dist/EgovLawDownloader.app` をダブルクリックすれば起動できます。

## アプリでできること

- 法令名で検索
- 候補を複数選択
- 保存形式を複数選択
- 保存先フォルダを選択
- 実行ログで、今どの処理をしているか確認

## 画面の流れ

1. 法令名を入力
2. `法令を検索` を押す
3. 候補一覧から保存したい法令を複数選択
4. 保存形式をチェック
5. 保存先を選ぶ
6. `選択した法令を保存` を押す
7. 下のログで進み具合を確認

## macOS アプリの中身

アプリ本体のソースは次のファイルです。

- [`macos/EgovLawDownloader.m`](/Users/skawamura/egov-law-downloader/macos/EgovLawDownloader.m)
- [`macos/Resources/index.html`](/Users/skawamura/egov-law-downloader/macos/Resources/index.html)

ビルド用スクリプトは次のファイルです。

- [`scripts/build_macos_app.sh`](/Users/skawamura/egov-law-downloader/scripts/build_macos_app.sh)

このスクリプトは、Objective-C + AppKit/WebKit のソースから `.app` を組み立てます。
Xcode プロジェクトは使っていません。

## Python 版について

以前の Python スクリプトも残しています。

- [`egov_law.py`](/Users/skawamura/egov-law-downloader/egov_law.py)

こちらは CLI や検証用として使えますが、普段使いは macOS アプリの方を想定しています。

## 動作要件

- macOS
- Apple の Command Line Tools または Xcode
- Swift 6 系が使える環境

確認例:

```bash
xcode-select -p
swift --version
```

## 保存ファイル名

保存ファイル名は次の形式です。

```text
法令名_YYYYMMDD.拡張子
```

例:

```text
民法_18960427.html
民法_18960427.json
```

日付は API レスポンスの中から、次の優先順で選ばれます。

1. 改正施行日
2. 改正公布日
3. 更新日
4. 公布日
5. どれも無い場合は実行日

## 初心者向けの補足

コードには「この関数は何をしているか」が分かるように、要所にコメントを入れています。
特にアプリ版は、検索・一覧表示・ダウンロード・保存の流れが追いやすいように分けています。

## テスト

Python 側の補助ロジックのテスト:

```bash
python -m unittest discover -s tests -q
```

macOS アプリのビルド確認:

```bash
./scripts/build_macos_app.sh
```

## 注意点

- `pdf` は e-Gov API の対応形式ではないため、このツールでは出していません
- API のレスポンス仕様が変わると調整が必要です
- macOS ネイティブアプリは Mac 専用です

## ライセンス

MIT License
