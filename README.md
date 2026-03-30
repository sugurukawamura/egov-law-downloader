# egov-law-downloader

e-Gov法令API Version 2 を使って法令を検索し、ブラウザから保存する小さなUIです。

## Files

- `web/index.html`
- `lawapi-v2.yaml`

## できること

- 法令名で検索
- 検索結果から対象法令を複数選択
- `XML / JSON / HTML / RTF / DOCX / PDF` を選んで保存
- 取得状況とエラー内容を画面内ログで確認

## 使い方

1. ブラウザで `web/index.html` を開く
2. 法令名を入力して検索する
3. 保存したい法令を選ぶ
4. 保存形式を選ぶ
5. 必要なら基準日を `YYYY-MM-DD` 形式で入力する
6. ダウンロードを実行する

## 保存形式

`law_file` エンドポイントの `file_type` で使えるのは次の5種類です。

- `xml`
- `json`
- `html`
- `rtf`
- `docx`

`pdf` は `law_file` ではなく、`law_data` で添付ファイル一覧を取り、`attachment` から公式 PDF を取得します。
添付 PDF が存在しない法令では、取得できない旨のエラーを表示します。

## 備考

- これは e-Gov の公式UIではありません
- 実際の取得可否やレスポンス内容は e-Gov API の仕様に従います
- 法令データの利用条件は e-Gov 側の案内を確認してください

## License

MIT License
