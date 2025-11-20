# reader 3

![reader3](reader3.png)

A lightweight, self-hosted EPUB reader that lets you read through EPUB books one chapter at a time. This makes it very easy to copy paste the contents of a chapter to an LLM, to read along. Basically - get epub books (e.g. [Project Gutenberg](https://www.gutenberg.org/) has many), open them up in this reader, copy paste text around to your favorite LLM, and read together and along.

This project was 90% vibe coded just to illustrate how one can very easily [read books together with LLMs](https://x.com/karpathy/status/1990577951671509438). I'm not going to support it in any way, it's provided here as is for other people's inspiration and I don't intend to improve it. Code is ephemeral now and libraries are over, ask your LLM to change it in whatever way you like.

## Usage

The project uses [uv](https://docs.astral.sh/uv/). So for example, download [Dracula EPUB3](https://www.gutenberg.org/ebooks/345) to this directory as `dracula.epub`, then:

```bash
uv run reader3.py dracula.epub
```

This creates the directory `data/dracula_data`, which registers the book to your local library. You can repeat the same command with `.txt` files (e.g., `uv run reader3.py notes.txt`) to convert long-form text into the web reader. We can then run the server:

> Tip: pass `--library /path/to/dir` if you want to store processed books somewhere other than `data/`.

```bash
uv run server.py
```

And visit [localhost:8123](http://localhost:8123/) to see your current Library. You can easily add more books, or delete them from your library by deleting the folder. It's not supposed to be complicated or complex.

### Importing books without the CLI

- Drop `.epub` or `.txt` files into the `books/` directory (or change the folder via `READER_UPLOAD_DIR`). When you refresh the library page, the server automatically ingests any new files into `data/<title>_data/`.
- Alternatively, use the drag-and-drop uploader on the library page; it streams the file to `/upload` and triggers the same ingestion pipeline. TXT imports automatically detect the file encoding via `charset-normalizer`, so UTF-8, GBK, or other encodings render correctly without manual conversion.

## License

MIT
