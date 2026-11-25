"""Choose a tool from the explicit request and capture its observable input and output."""

from copy import deepcopy
from hashlib import sha256
import json

from .capabilities import calculate, calculator_expression


CALCULATOR = {
    "name": "calculator",
    "command": "/calc",
    "permission": "local read",
    "schema": {
        "type": "object",
        "required": ["expression"],
        "properties": {"expression": {"type": "string", "maxLength": 160}},
    },
}
# Bump when selection, tool semantics or permission behavior changes without a schema change.
CATALOG_VERSION = 1


def catalog(tools):
    descriptors = [CALCULATOR]
    if tools:
        descriptors.extend(t.describe() for t in tools.registry.values())
    serialized = json.dumps(
        [CATALOG_VERSION, sorted(descriptors, key=lambda x: x["name"])],
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(serialized.encode()).hexdigest()[:20], [
        t["name"] for t in descriptors
    ]


def plan(prompt, mode, tools, fixture=None):
    revision, names = catalog(tools)
    decision = {
        "mode": mode,
        "catalog_revision": revision,
        "available": names,
        "tool": None,
        "input": {},
        "match": None,
        "permission": None,
        "execution": "model",
        "output": None,
    }
    if fixture and fixture["policy"] == "exact" and fixture.get("capture") is not None:
        captured = fixture["capture"]
        if captured:
            for key in ("tool", "input", "match", "permission"):
                decision[key] = deepcopy(captured.get(key))
            decision["original_catalog_revision"] = captured.get("catalog_revision")
        decision["reason"] = "Replaying the recorded selection."
        return decision
    command = prompt.startswith("/")
    if mode == "off" or (mode == "commands" and not command):
        decision["reason"] = (
            "Tool use is disabled."
            if mode == "off"
            else "Command-only mode requires a slash command."
        )
        return decision
    expression = calculator_expression(prompt)
    if expression is not None:
        decision.update(
            tool="calculator", input={"expression": expression}, permission="local read"
        )
    elif tools:
        invocation = tools.parse(prompt)
        if invocation:
            tool, args = invocation
            decision.update(tool=tool.name, input=args, permission=tool.permission)
    if decision["tool"]:
        decision.update(
            match="command" if command else "natural",
            reason=f"Selected {decision['tool']} from a {'slash command' if command else 'natural-language request'}.",
        )
    else:
        decision["reason"] = "No registered tool matched the request."
    return decision


def result_error(name, message):
    return {
        "tool": name,
        "status": "error",
        "error": message,
        "response": message,
        "context": [],
        "events": [{"stage": "Tool error", "detail": message}],
    }


def execute(decision, tools, *, laboratory=False, fixture=None):
    name, args = decision["tool"], decision["input"]
    captured = fixture.get("capture") if fixture else None
    exact = bool(fixture and fixture["policy"] == "exact")
    match = bool(
        captured and captured.get("tool") == name and captured.get("input") == args
    )
    if (
        captured
        and captured.get("legacy_prompt_match")
        and captured.get("tool") == name
    ):
        match = True
    # Exact comparison replay uses its original laboratory result. A chat export result
    # may refer to a real proposal; derive a harmless preview when using that chat as input.
    preserved = bool(captured and captured.get("output") and (exact or match))
    if (
        laboratory
        and preserved
        and (exact or decision["permission"] != "approval required")
    ):
        result = deepcopy(captured["output"])
        decision["execution"] = "replay"
        decision["original_catalog_revision"] = captured.get("catalog_revision")
    elif exact or (
        laboratory
        and fixture
        and fixture["policy"] == "source"
        and decision["permission"] == "public read"
    ):
        result = result_error(
            name,
            "This replay has no matching recorded tool output. Remove the captured input to run a fresh lookup.",
        )
        decision["execution"] = "missing capture"
    elif name == "calculator":
        try:
            answer = calculate(args["expression"])
            result = {
                "tool": name,
                "status": "completed",
                "response": answer,
                "context": [],
                "events": [
                    {
                        "stage": "Calculator",
                        "detail": f"{args['expression']} = {answer}",
                    }
                ],
            }
        except ValueError as exc:
            result = result_error(name, str(exc))
        decision["execution"] = "local read"
    elif laboratory:
        result = tools.experiment(name, args)
        decision["execution"] = (
            "preview" if decision["permission"] == "approval required" else "live read"
        )
    else:
        try:
            result = tools.run(name, args)
        except ValueError as exc:
            result = result_error(name, str(exc))
        decision["execution"] = (
            "proposal" if decision["permission"] == "approval required" else "live read"
        )
    decision["output"] = {
        key: deepcopy(result[key])
        for key in ("tool", "status", "response", "context", "error", "events")
        if key in result
    }
    return result


def from_trace(run, source_prompt):
    capture = run.get("tool_execution")
    if capture:
        return deepcopy(capture)
    # Legacy public tool runs predate structured input capture. Their saved prompt and
    # route identify a matching read. Legacy model-only comparisons never gain tool calls.
    route = run.get("route")
    if (
        route in {"wiki_search", "wiki_research", "weather", "calculator"}
        and run.get("prompt") == source_prompt
    ):
        output = {
            "tool": route,
            "status": "completed" if run.get("status") == "completed" else "error",
            "response": run.get("response", ""),
            "context": run.get("context", []),
            "error": run.get("error"),
            "events": [],
        }
        return {
            "tool": route,
            "input": {},
            "match": "legacy",
            "permission": "local read" if route == "calculator" else "public read",
            "catalog_revision": None,
            "legacy_prompt_match": True,
            "output": output,
        }
    return None
