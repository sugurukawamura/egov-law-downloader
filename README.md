# egov-law-downloader

Small browser UI for searching e-Gov laws and saving official law files.

## Files

- `web/index.html`
- `lawapi-v2.yaml`

## Features

- Search laws by title
- Select one or more laws from the result list
- Save `XML / JSON / HTML / RTF / DOCX`
- Open a print-ready `PDF` view from the official HTML law body
- Download attached files as `ATTACH ZIP`
- Show status and detailed logs in the page

## PDF behavior

`law_file` does not provide `pdf` as a valid `file_type`.

This app handles `PDF` by:

1. Fetching the official `HTML` law body from `law_file/html/...`
2. Opening a print-ready browser tab that keeps the official page layout as much as possible
3. Letting the browser print dialog save the law body as PDF

This is intended to be close to the official site PDF output for the law body itself.

## Attachment behavior

`ATTACH ZIP` downloads all attached files for the selected law through the `attachment` API.
This is useful for forms, appendix files, and similar attachments.

## Usage

1. Open `web/index.html` in a browser
2. Search for a law
3. Select the laws you want
4. Select one or more formats
5. Optionally enter an as-of date in `YYYY-MM-DD`
6. Save the selected outputs

## Notes

- The repository MIT License applies to source code only
- Retrieved law data and generated outputs follow e-Gov data terms
- Browser CORS restrictions may affect direct API access in some environments

## License

MIT License
