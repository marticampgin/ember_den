"""Repeat the local hardware probes with synthetic inputs in a disposable database."""

import argparse
import json
import platform
import tempfile
import time
from pathlib import Path

from .cancel import Cancellation
from .documents import Documents
from .engine import Engine, choose_model
from .provider import Ollama, ProviderError
from .store import Store, utcnow


DISTRACTORS = (
    "The greenhouse team waters the tomatoes before sunrise.",
    "The commuter service departs platform four at midday.",
    "Spruce lumber was cut and stacked beside the joinery bench.",
    "Paint samples include cobalt and cadmium yellow.",
    "The choir director moved rehearsal to the auditorium.",
    "Invoices for rented school equipment are paid on Friday.",
    "Hikers checked the fabric on the tents after the rain.",
    "A hazelnut cake was served with coffee.",
    "The laboratory technician adjusted the camera shutter.",
    "The orchestra uses a bassoon in the third movement.",
)
TARGETS = (
    ("field-guide.txt", "If a commuter misplaces a pouch on Metro Line 8, visit the lost-property kiosk at Alder Square.",
     "Where should I collect my missing wallet from the subway?"),
    ("packing.txt", "Spare spectacles are stored in the emerald suitcase under the bed.",
     "Which luggage contains the extra eyeglasses?"),
    ("maintenance.txt", "The bicycle chain must be lubricated with graphite oil after wet journeys.",
     "What lubricant is recommended for a bike drivetrain following rain?"),
)
PROMPTS = (
    "The code name of my telescope is Amber Finch. Acknowledge in two words.",
    "What nickname did I give my telescope? Use only the nickname.",
    "It has a 410 mm aperture. Acknowledge in two words.",
    "What is its aperture? Use only the number and unit.",
    "Which nickname was I using? Just the two words.",
    "What is its nickname and aperture together? Give one short sentence.",
)


def summary(run):
    return {key:run.get(key) for key in ("model","model_digest","status","error","response","metrics")}


def model_matrix(engine, names, config):
    results = []
    prompt = "Reply with only the digit equal to four plus three."
    for name in names:
        result = engine.run_config(prompt, {**config, "model": name})
        results.append({**summary(result), "device":config["device"],
                        "exact_digit":result["response"].strip().rstrip(".") == "7"})
        if result["status"] == "error" and "GPU" in (result["error"] or "") and config["device"] != "cpu":
            retry = engine.run_config(prompt, {**config, "model":name, "device":"cpu", "context_length":2048})
            results.append({**summary(retry), "device":"cpu", "context_length":2048,
                            "exact_digit":retry["response"].strip().rstrip(".") == "7"})
    return results


def lookup_benchmark(documents, provider, embedding, device):
    for i in range(160):
        documents.add(f"journal-{i:03}.txt", f"{DISTRACTORS[i%10]} Reference number {i+1}.")
    for name, content, _ in TARGETS:
        documents.add(name,content)
    results = []
    for mode in ("keyword","semantic","hybrid"):
        for name, _, query in TARGETS:
            start = time.monotonic()
            if mode == "keyword":
                hits, meta = documents.search(query), {}
            else:
                if not embedding:
                    results.append({"mode":mode, "error":"No matching local embedding model."})
                    break
                hits, meta = documents.ranked(query,mode,provider,embedding,Cancellation(),device)
            names = [item["name"] for item in hits]
            results.append({"mode":mode,"query":query,"expected":name,"results":names,"hit_at_3":name in names,
                            "ms":round((time.monotonic()-start)*1000,2),**meta})
    return {"documents":len(documents.list()),"results":results}


def conversation_benchmark(engine, config):
    turns, conversation = [], None
    for prompt in PROMPTS * 2:
        iterator = engine.generate(prompt, config, conversation_id=conversation)
        first = next(iterator)
        for _ in iterator:
            pass
        conversation = first["conversation_id"]
        run = engine.store.run(first["run_id"])
        turns.append({**summary(run),"prompt":prompt,"history_messages":len(run["history"])})
        if run["status"] != "completed":
            break
    return turns


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=("all","models","retrieval","conversation","workflow"), default="all")
    parser.add_argument("--model", help="Select one installed chat model; the matrix otherwise checks all of them.")
    parser.add_argument("--embedding-model", help="Select an installed embedding model for the retrieval probes.")
    parser.add_argument("--second-model", help="Reviewer for the workflow comparison.")
    parser.add_argument("--device", choices=("auto","cpu"), default="auto")
    parser.add_argument("--context", type=int, choices=(1024,2048,4096,8192,16384), default=4096)
    parser.add_argument("--report", type=Path, help="Write a new JSON file. The default prints JSON.")
    args = parser.parse_args()
    if args.report and args.report.exists():
        parser.error("Choose a new report path.")
    try:
        provider = Ollama()
        inventory = provider.inventory()
        names = [m["name"] for m in inventory]
        primary = args.model or choose_model(inventory)
        if args.suite != "retrieval" and primary not in names:
            parser.error("Choose an installed chat model.")
        embeddings = provider.embedding_inventory()
        default_embed = next((m for m in embeddings if m["name"] == "qwen3-embedding:0.6b"),
                             embeddings[0] if embeddings else None)
        embedding = next((m for m in embeddings if m["name"] == args.embedding_model), None) if args.embedding_model else default_embed
        if args.embedding_model and not embedding:
            parser.error("The selected embedding model is unavailable.")
        with provider.request("/api/version") as response:
            version = json.load(response).get("version")
        config = {"model":primary,"temperature":0,"seed":13,"retrieval_mode":"off",
                  "device":args.device,"context_length":args.context}
        report = {"started_at":utcnow(),"system":platform.platform(),"python":platform.python_version(),
                  "ollama":version,"inventory":inventory,"config":config}
        with tempfile.TemporaryDirectory(prefix="ember-benchmark-") as directory:
            path = Path(directory)/"bench.sqlite"
            docs = Documents(path)
            engine = Engine(Store(path),provider,docs)
            if args.suite in ("all","retrieval"):
                report["retrieval"] = lookup_benchmark(docs,provider,embedding,args.device)
            if args.suite in ("all","conversation"):
                report["conversation"] = conversation_benchmark(engine,config)
            if args.suite in ("all","models"):
                report["models"] = model_matrix(engine,[primary] if args.model else names,config)
            if args.suite in ("all","workflow"):
                reviewer = args.second_model or next((n for n in names if n != primary),primary)
                if reviewer not in names:
                    parser.error("The selected reviewer is unavailable.")
                prompt = "Compare the two numbers 5 and 7. Return just the larger number."
                report["workflow"] = []
                for workflow in ("direct","revise"):
                    run = engine.run_config(prompt,{**config,"workflow":workflow,"reviewer_model":reviewer})
                    report["workflow"].append({**summary(run),"workflow":workflow,"passes":run["workflow_passes"]})
        report["finished_at"] = utcnow()
        data = json.dumps(report,ensure_ascii=False,indent=2)
        if args.report:
            with args.report.open("x",encoding="utf-8") as handle:
                handle.write(data+"\n")
            print(args.report.resolve())
        else:
            print(data)
    except (OSError,ProviderError) as exc:
        parser.exit(1,f"{exc}\n")


if __name__ == "__main__":
    main()
