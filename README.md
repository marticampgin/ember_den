# Ember Den

Ember is a little fox with a local language model, a memory, and a workbench for experimenting with both. Type in the clearing to wake Ember, send a message to see the scratchpad come out, and open **Inspect run** beneath an answer to see how it was produced.

## Start on Windows

Double-click **Start Ember Den.cmd**. It starts the local service, opens the clearing in your browser, and reuses Ollama if it is already running. Keep the console window open during use. Ctrl+C stops the Ember Den server.

The application uses Python 3.11 or later, SQLite with FTS5, and a local Ollama installation with at least one chat model. The standard Python distribution for Windows includes the required SQLite support. The app has no pip or npm runtime dependencies. All interface assets ship in this repository.

To run from a terminal with Ollama already running, use the following command and open [the clearing](http://127.0.0.1:8765).

```powershell
python -m den.server
```

Use `ollama list` to check the installed models. If starting on a new machine with an empty inventory, `ollama pull qwen3.5:2b` installs the default model. The app reads the inventory at runtime. Select a different installed model under **Settings**. The model service is expected on loopback port 11434. For an independent workspace, use `python -m den.server --port 8766 --data-dir ".ember-den/sandbox"`.

## Try a complete experiment

1. Under **Memories**, add a preference or import a UTF-8 Markdown or plain-text file up to 120 KB. The app copies only the file you select.
2. Ask Ember a specific question about the file. **Inspect run** opens the model configuration, retrieved passages with filenames and line numbers, recorded events, and timings.
3. Open the trace in the **Workbench**. Select two models or change one setting, such as temperature. The workbench carries over the captured prompt, history, and references.
4. Expand **Expected text check** and enter a phrase from a known answer. Save the test case or run the comparison. Both configurations run sequentially, and **History** retains their outputs. Each result can be replayed.

For a model-independent check, enter `/calc (19 + 7) * 3`. The deterministic calculator returns `78`, and its trace identifies the arithmetic route.

## Engineering map

| Component | Implementation and design choice |
| --- | --- |
| Browser | Native ES modules and a same-origin API. The UI runs directly from the Python service. The animated scene consumes observable chat state and respects reduced-motion preferences. |
| Model adapter | `den/provider.py` defines the inventory and streaming contract. Its Ollama implementation discovers local chat models, streams visible text and reads runtime counters. An alternative provider can implement this contract and be injected into `Application`. |
| Orchestration | `den/engine.py` selects a route, assembles context, serializes inference, and emits newline-delimited JSON events. The queue is shared by chat and the lab so the two views can coexist on one GPU. |
| Knowledge | `den/documents.py` stores named source text and indexes line-addressed chunks using SQLite FTS5 and BM25. `den/capabilities.py` handles lexical memory matching and the numeric calculator. Explicit imports establish the document boundary. Retrieved text enters the prompt as untrusted reference data. |
| Persistence | `den/store.py` manages conversations, messages, settings and traces using SQLite transactions. A run retains its actual context and parameters. Partial answers are checkpointed so interrupted work can be inspected. |
| Laboratory | `den/laboratory.py` stores experiment inputs, per-arm results and reusable cases. A run records the model digest alongside the name. A replay uses the model currently installed under that name and surfaces digest drift. |
| HTTP and launch | `den/server.py` serves static assets and validates requests on loopback. `den/launcher.py` provides the Windows startup flow. |

## Measurements and limitations

Time to first token measures wall time from receipt of a request to the first **visible** answer text. It includes queueing, loading and prefill. Trace event timestamps are elapsed time from request start. Generation throughput uses the runtime's generated-token count and evaluation duration. Models with internal thinking can report more generated tokens than appear in the answer. Resident VRAM is a post-generation reading from Ollama. Missing counters display as unavailable. [Ollama chat counters](https://docs.ollama.com/api/chat) · [Running models](https://docs.ollama.com/api/ps)

On the development RTX 4070, both `qwen3.5:2b` and `LiquidAI/lfm2.5-2.6b:latest` answered a question about the callsign in an imported field note. Both passed the same expected-text check using the captured source excerpt. One warm run with a 4,096-token context measured:

| Model | First visible token | End to end | Generated tokens/s | Resident VRAM |
| --- | ---: | ---: | ---: | ---: |
| Qwen 3.5 2B | 58 ms | 384 ms | 145.7 | 2.19 GiB |
| LiquidAI LFM2.5 2.6B | 735 ms | 877 ms | 196.5 | 1.69 GiB |

These figures describe one short prompt with loaded models. They illustrate why time to visible text and throughput are measured independently. The installed Qwen 3.5 9B runner failed GPU initialization during verification. The smaller Qwen is the default when present.

Retrieval currently favors lexical overlap. Questions that paraphrase every distinctive term can miss their sources. Context budgeting uses a character estimate before Ollama tokenizes the request. Expected-text matching checks a phrase and leaves broader answer quality for human review. Voice, web retrieval, external app actions and semantic retrieval are future experiments.

## Personal data

On Windows the database is `%LOCALAPPDATA%\Ember Den\ember.sqlite3`. Chats, memories, imported text, settings, and experiment snapshots are local plaintext data. Common credential patterns are masked in diagnostics, while personal transcripts retain the original text. Removing a document or memory ends its use in future retrieval. Past conversations and saved experiments keep their own captured copies. Back up the data directory with the server stopped for a consistent copy.

## Verification

Run the deterministic storage, orchestration, provider, and HTTP tests with the standard library.

```powershell
python -m unittest discover -s tests -v
```

The optional browser regression creates and removes its own temporary data directory. It exercises rendered desktop and mobile views, the animated fox, conversation reload, document import, and reduced motion. With `--live`, it also verifies a real grounded reply and replays it in the lab. Install the Playwright Python package in a development environment to run it.

```powershell
python -m pip install playwright
python tests/browser_smoke.py --live --browser "C:\Program Files\Google\Chrome\Application\chrome.exe"
```

Use `--screenshots PATH` to save the rendered states. With Playwright's Chromium installed, the `--browser` argument can be omitted.
