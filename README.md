# Ember Den

Ember is a little fox with a local language model, a memory, and a workbench for experimenting with both. Type in the clearing to wake Ember, send a message to see the scratchpad come out, and open **Inspect run** beneath an answer to see how it was produced.

## Start on Windows

Double-click **Start Ember Den.cmd**. It starts the local service, opens the clearing in your browser, and reuses Ollama if it is already running. Keep the console window open during use. Ctrl+C stops the Ember Den server.

The default setup uses Python 3.11 or later, SQLite with FTS5, and Ollama with an installed chat model. Core chat runs on the Python standard library. Install `pypdf` for PDF import. All interface assets ship in the repository. Local voice uses the installed English Windows desktop recognizer and voices.

To run from a terminal with Ollama already running, use the following command and open [the clearing](http://127.0.0.1:8765).

```powershell
python -m den.server
```

Use `ollama list` to check the installed models. If starting on a new machine with an empty inventory, `ollama pull qwen3.5:2b` installs the default model. The app reads the inventory at runtime. Select a different installed model under **Settings**. The model service is expected on loopback port 11434. For an independent workspace, use `python -m den.server --port 8766 --data-dir ".ember-den/sandbox"`.

The second adapter connects to a local server that implements the OpenAI-compatible models and streaming chat endpoints, such as llama.cpp. The default discovery mode checks Ollama and loopback port 8080. For LM Studio on port 1234, start Ember Den with `python -m den.server --provider auto --provider-port 1234`. Choose a `local-api/` model in Settings or in either comparison arm. Use `--provider ollama` or `--provider openai-compatible` to select one transport exclusively. The adapter connects to the local computer and needs no cloud API key.

## Try a complete experiment

1. Under **Memories**, add a preference or import a UTF-8 text or Markdown file up to 120 KB. Word documents and text-based PDFs can be up to 5 MB with up to 120,000 extracted characters. The library retains the extracted text. PDF page markers and passage line numbers let you find the evidence again.
2. Ask Ember a specific question about the file. **Inspect run** opens the model configuration, retrieved passages with filenames and line numbers, recorded events, and timings.
3. Open the trace in the **Workbench**. Select two models or change one setting, such as temperature. The workbench carries over the captured prompt, history, and references.
4. Choose a check for a phrase, exact answer, valid JSON, an expected retrieved filename, or a completed tool call. Save the test case or run the comparison. Both configurations run sequentially. Vote on the responses or choose a local model to review them. In **Test cases**, choose **Add starter cases** to try six examples with synthetic inputs and your installed model. **Run saved suite** executes the cases, and **History** retains each comparison.

For a model-independent check, enter `/calc (19 + 7) * 3`. The deterministic calculator returns `78`, and its trace identifies the arithmetic route.

## Use the companion

- **Speak and listen.** Choose the microphone to dictate and review a message. Choose **Start hands-free** to send after a pause and listen again after each spoken reply. The microphone closes during playback. Voice Settings lets you select a microphone, recognizer and voice. Windows speech, Vosk recognition and Piper synthesis run locally on the CPU. [Offline speech setup](docs/speech-models.md)
- **Teach a preference.** Choose **Feedback** on a reply and explicitly save a note to memory. Saved preferences remain editable under **Memories**. Settings can include saved notes, past conversation excerpts, or both as reference material.
- **Find evidence.** Choose keyword, semantic, or hybrid document search in Settings or either lab configuration. Semantic search needs a local embedding model. The first semantic query builds the index in batches. Later queries reuse vectors keyed to the model revision. A fallback to keyword search appears in the execution trace.
- **Use public sources.** Try `/search open source speech recognition` for the general web, `/news headlines` for dated reporting, “research Wikipedia for red fox” for an encyclopedia entry, or “weather in Berlin” for a forecast. **Tools** shows the source, required inputs and recent outcomes. Web queries use the anonymous Mwmbl search API by default. News queries filter publisher feeds locally. For your own search service, set `EMBER_SEARXNG_PORT` to its loopback port before launching Ember and enable JSON results in SearXNG. [Mwmbl search](https://developer.mwmbl.org/) · [SearXNG search API](https://docs.searxng.org/dev/search_api.html) · [Open-Meteo forecasts](https://open-meteo.com/en/docs)
- **Approve an action.** Try `/note Packing list | Bring a headlamp` or `/event 2026-12-12 | Trail walk`. Inspect the pending proposal in **Tools**. Approval writes a text note or calendar file in the workspace's exports directory. Open the ICS file in your calendar application to import it. Declining leaves the proposal in history and creates no file.
- **Tune the workspace.** Save or restore named inference profiles, edit Ember's system prompt, choose a fox coat, and set daylight, sunset, moonlight or a landscape that follows the local clock.

## Choose an execution path

The default sends a general question to one local model. For an experiment, compare that baseline with adaptive routing rules, a small local classifier that chooses a specialist, or a draft followed by a revision pass. Choose each model's role under Advanced settings. The engine serializes inference for limited VRAM, validates classifier output, and records pass identities, digests, measurements and fallback decisions. Selecting CPU mode in the native Ollama adapter is useful for measuring GPU contention.

**Long conversations** can keep just the recent messages or add short, attributed excerpts from older turns. Inspect a trace to see how much history fit the input budget. **Tool selection** lets each comparison arm respond to natural requests and commands, require slash commands, or turn tools off. In the workbench an export produces a preview. Replays reuse the captured tool result, including the original response from a web service.

**Performance** summarizes measured latency and outcomes for the most recent 200 runs. **Models** lists available models and metadata exposed by their provider. **Strategy table** is a separate Kuhn poker experiment with simulated chips, a rule-based opponent, and a local model constrained to legal moves. The game owns the cards and payoff arithmetic.

## Engineering map

| Component | Implementation and design choice |
| --- | --- |
| Browser | Native ES modules and a same-origin API. The UI runs directly from the Python service. The animated scene consumes observable chat state and respects reduced-motion preferences. |
| Model adapter | `den/provider.py` defines the inventory and streaming contract for Ollama. `den/local_api.py` implements model discovery, streaming and embeddings for an independent loopback server. Both work in either comparison arm. |
| Orchestration | `den/engine.py` selects a route, assembles context, serializes inference, and emits newline-delimited JSON events. The queue is shared by chat and the lab so the two views can coexist on one GPU. |
| Knowledge | `den/documents.py` combines SQLite FTS5/BM25 and locally computed embeddings. Hybrid search merges the two rankings. `den/episodes.py` retrieves relevant earlier chat excerpts. Retrieved text enters the prompt as untrusted data. |
| Persistence | `den/store.py` manages conversations, messages, settings and traces using SQLite transactions. A run retains its actual context and parameters. Partial answers are checkpointed so interrupted work can be inspected. |
| Laboratory | `den/laboratory.py` stores runnable cases, arm settings, results and per-pass model revisions. Lab runs can retrieve live sources separately, and replays reuse each arm's captured references. `den/evaluation.py` handles suites, profiles, deterministic checks and reviews. |
| Voice and tools | `den/voice.py` adapts Windows speech to a local PCM audio interface. `den/tools.py` registers capabilities, validates inputs and tracks proposals and outcomes. Approval is enforced by application code. |
| HTTP and launch | `den/server.py` serves static assets and validates requests on loopback. `den/launcher.py` provides the Windows startup flow. |

## Measurements and limitations

Time to first token measures wall time from receipt of a request to the first **visible** answer text. It includes queueing, loading and prefill. Trace event timestamps are elapsed time from request start. Generation throughput uses the runtime's generated-token count and evaluation duration. Models with internal thinking can report more generated tokens than appear in the answer. Resident VRAM is a post-generation reading from Ollama. Missing counters display as unavailable. [Ollama chat counters](https://docs.ollama.com/api/chat) · [Running models](https://docs.ollama.com/api/ps)

On the development RTX 4070, both `qwen3.5:2b` and `LiquidAI/lfm2.5-2.6b:latest` answered a question about the callsign in an imported field note. Both passed the same expected-text check using the captured source excerpt. One warm run with a 4,096-token context measured:

| Model | First visible token | End to end | Generated tokens/s | Resident VRAM |
| --- | ---: | ---: | ---: | ---: |
| Qwen 3.5 2B | 58 ms | 384 ms | 145.7 | 2.19 GiB |
| LiquidAI LFM2.5 2.6B | 735 ms | 877 ms | 196.5 | 1.69 GiB |

These figures describe one short prompt with loaded models. On a separate sequential test all seven installed chat models, including Qwen 3.5 9B, produced visible answers at a 4,096-token context. Five returned exactly the requested digit. The 0.5B Qwen returned an incorrect digit and Mistral added words. A 9B GPU initialization failure seen in the first build did not recur in the sequential run. The default remains the smaller Qwen for faster startup.

On three paraphrased queries over 163 synthetic documents, the expected source appeared in the top three for 0/3 keyword searches, 3/3 semantic searches and 3/3 hybrid searches. The same queries were used to tune the fusion weights. A 12-turn chat recovered two facts on the final turn. The model router returned legal labels on two prompts and chose the general path for both. On short hands-free replies, the time from the last voiced sample to playback was 2.5 seconds with Windows speech and 3.0 seconds with Vosk/Piper, including the pause used to detect the end of the turn. Physical microphone accuracy, arbitrary document collections and extended uptime require separate measurement.

The Qwen chat and embedding model cards identify Apache 2.0. The LiquidAI comparison model uses the LFM Open License. Weights are installed separately. Compare the digest and quantization reported for the actual local model when repeating an experiment. [Qwen 3.5 2B](https://huggingface.co/Qwen/Qwen3.5-2B) · [Qwen3 Embedding 0.6B](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B) · [LiquidAI LFM2.5 2.6B](https://huggingface.co/LiquidAI/LFM2.5-2.6B)

## Personal data

On Windows the database is `%LOCALAPPDATA%\Ember Den\ember.sqlite3`. Chats, memories, imported text, settings, and experiment snapshots are local plaintext data. Common credential patterns are masked in diagnostics, while personal transcripts retain the original text. Removing a document or saved memory removes it from that source's index. Prior conversations can still mention it and supply an excerpt until you delete the conversation. Saved experiments retain their captured inputs. Back up the data directory with the server stopped for a consistent copy.

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

### Repeat the acceptance runs

Run the following from the repository root on the Windows development machine. Use a new output filename for each hardware benchmark.

```powershell
$env:EMBER_TEST_WINDOWS_SPEECH = "1"
$env:EMBER_TEST_SPEECH_MODELS = "1"
python -m unittest discover -s tests -v
python -m playwright install chromium
python tests/browser_smoke.py --live
python tests/browser_voice.py
python tests/browser_voice.py --speech-models
python tests/browser_tools.py --live
python tests/browser_lab.py
python tests/browser_strategy.py --live --model qwen3.5:2b
python -m den.benchmark --suite all --report "$env:TEMP/ember-hardware-run.json"
```

The voice test uses a synthetic audio device. Enable the optional speech-model tests after installing Vosk and Piper. The live tool test contacts Wikipedia, Open-Meteo, publisher news feeds and a general-web search service. The laboratory browser test uses the development machine's `qwen3.5:2b` model and PDF support. To exercise two independent inference runtimes, run `python tests/browser_provider.py --llama-server "path/to/llama-server.exe" --model "path/to/chat.gguf"`. The benchmark runner accepts `--suite models`, `retrieval`, `conversation`, or `workflow` for a shorter run. [Verification notes](docs/verification.md)
