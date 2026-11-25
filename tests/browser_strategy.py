"""Test the Strategy table through the UI; --live also plays both seats against a real local model."""

import argparse
from contextlib import contextmanager
from pathlib import Path
import re
import subprocess
import sys
import tempfile

from playwright.sync_api import expect, sync_playwright

from browser_smoke import ROOT, check_layout


@contextmanager
def serve(directory):
    process = subprocess.Popen(
        [
            sys.executable,
            "-u",
            "-m",
            "den.server",
            "--port",
            "0",
            "--data-dir",
            str(directory),
        ],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        startup = process.stdout.readline()
        url = re.search(r"http://127\.0\.0\.1:\d+", startup)
        if not url:
            raise RuntimeError("Could not start Ember Den: " + startup)
        yield url.group()
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        process.stdout.close()


def open_table(page, url):
    page.goto(url)
    menu = page.get_by_role("button", name="Open navigation")
    if menu.is_visible():
        menu.click()
    page.get_by_role("link", name="Laboratory", exact=True).click()
    page.get_by_role("button", name="Strategy table", exact=True).click()
    expect(page.get_by_role("heading", name="Kuhn poker")).to_be_visible()


def snapshot(page, folder, name, *, show_board=True):
    check_layout(page)
    board = page.locator(".strategy-board")
    if show_board and page.viewport_size["width"] <= 480:
        board.evaluate("(element) => element.scrollIntoView({block: 'center'})")
    bounds = board.bounding_box()
    assert (
        bounds
        and bounds["x"] >= 0
        and bounds["x"] + bounds["width"] <= page.viewport_size["width"] + 1
    )
    if folder:
        folder.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=folder / (name + ".png"))


def private_hand(page, hand):
    assert (
        hand["winner"] is None
        and hand["opponent_card"] is None
        and hand["decisions"] == []
    ), hand
    assert hand["user_card"] in {"J", "Q", "K"}
    assert hand["turn"] == "you" and hand["legal_moves"]
    opponent = page.locator(".strategy-card.face-down")
    expect(opponent).to_have_count(1)
    expect(opponent).to_have_text("")
    expect(opponent).to_have_attribute("aria-label", "Opponent hidden")
    expect(page.locator(".strategy-card").first).to_have_text(hand["user_card"])
    expect(page.locator(".strategy-turn")).to_have_text("Your move")
    expect(page.locator(".strategy-decision")).to_have_count(0)


def deal(page, url, policy, first, model):
    page.get_by_label("Opponent policy").select_option(policy)
    if policy == "model":
        page.get_by_label("Opponent model").select_option(model)
    with page.expect_response(
        lambda r: r.url == url + "/api/strategy/hands" and r.request.method == "POST",
        timeout=180000,
    ) as response:
        page.get_by_role("button", name="Deal hand").click()
    assert response.value.status == 201, response.value.text()
    hand = response.value.json()
    assert hand["policy"] == policy and hand["first_seat"] == first
    assert hand["model"] == (model if policy == "model" else "")
    private_hand(page, hand)
    return hand


def finish(page, url, hand):
    # Bet when possible and call a bet. This works for every random deal and gives
    # the opponent a decision in either seat, including a first-seat opponent's opening move.
    for _ in range(2):
        if hand["winner"]:
            break
        action = "Bet 1 chip" if "bet" in hand["legal_moves"] else "Call 1 chip"
        moves_url = url + "/api/strategy/hands/" + hand["id"] + "/moves"
        with page.expect_response(
            lambda r: r.url == moves_url and r.request.method == "POST", timeout=180000
        ) as response:
            page.get_by_role("button", name=action, exact=True).click()
        assert response.value.status == 200, response.value.text()
        hand = response.value.json()
        if hand["winner"] is None:
            private_hand(page, hand)
    assert hand["winner"] in {"you", "opponent"} and abs(hand["net_chips"]) in {1, 2}, (
        hand
    )
    assert hand["opponent_card"] in {"J", "Q", "K"} - {hand["user_card"]}
    assert hand["decisions"] and all(d["move"] in d["legal"] for d in hand["decisions"])
    expect(page.locator(".strategy-result")).to_contain_text(
        "You won" if hand["winner"] == "you" else "Opponent won"
    )
    expect(page.locator(".strategy-card.face-down")).to_have_count(0)
    expect(page.locator(".strategy-card").last).to_have_text(hand["opponent_card"])
    if hand["policy"] == "model":
        # A provider that reports token usage is required for this optional proof of real inference.
        assert any(
            (d.get("metrics", {}).get("output_tokens") or 0) > 0
            for d in hand["decisions"]
        ), hand
    return hand


def review(page, hand, index):
    page.locator(".strategy-log").nth(index).click()
    dialog = page.get_by_role("dialog", name="Hand review")
    expect(dialog).to_contain_text(
        f"Your card {hand['user_card']} · Opponent {hand['opponent_card']}"
    )
    expect(dialog.locator(".strategy-decision")).to_have_count(len(hand["decisions"]))
    for decision in hand["decisions"]:
        expect(dialog).to_contain_text("Opponent chose " + decision["move"])
    dialog.get_by_role("button", name="Close dialog").click()


def check_private_logs(page, url):
    runs = page.request.get(url + "/api/runs").json()["runs"]
    conversations = page.request.get(url + "/api/conversations").json()["conversations"]
    assert not runs and not conversations, (
        "Card-game decisions appeared in regular chat history."
    )


def run(page, data, live, model, screenshots):
    faults = []
    page.on("pageerror", lambda err: faults.append(str(err)))
    page.on(
        "console",
        lambda msg: (
            faults.append(msg.text) if msg.type in {"error", "warning"} else None
        ),
    )
    with serve(data) as url:
        open_table(page, url)
        page.get_by_text("Rules and opponent policy", exact=True).click()
        expect(page.locator(".strategy-rules")).to_contain_text(
            "Each player sees one card"
        )
        pending = deal(page, url, "baseline", "you", "")
        snapshot(page, screenshots, "strategy-baseline-hidden")
        check_private_logs(page, url)

    completed = []
    with serve(data) as url:
        open_table(page, url)
        current = page.request.get(url + "/api/strategy/state").json()["hand"]
        assert current == pending, "Active hand changed across server restart."
        private_hand(page, current)
        completed.append(finish(page, url, current))
        completed.append(finish(page, url, deal(page, url, "baseline", "opponent", "")))
        review(page, completed[-1], 0)
        expect(page.locator(".strategy-stat")).to_contain_text("2 hands")
        if live:
            choices = page.request.get(url + "/api/status").json()["models"]
            assert model in {item["name"] for item in choices}, (
                "Install/select an available local model with --model."
            )
            page.set_viewport_size({"width": 320, "height": 740})
            open_table(page, url)
            for first in ("you", "opponent"):
                hand = deal(page, url, "model", first, model)
                snapshot(page, screenshots, "strategy-phone-" + first + "-hidden")
                completed.append(finish(page, url, hand))
                review(page, completed[-1], 0)
                check_layout(page)
        check_private_logs(page, url)

    with serve(data) as url:
        open_table(page, url)
        expect(page.locator(".strategy-log")).to_have_count(len(completed))
        for i, hand in enumerate(reversed(completed)):
            review(page, hand, i)
        state = page.request.get(url + "/api/strategy/state").json()
        assert [r["id"] for r in state["recent"]] == [
            h["id"] for h in reversed(completed)
        ]
        for stats in state["stats"]:
            matching = [
                h
                for h in completed
                if h["policy"] == stats["policy"] and h["model"] == stats["model"]
            ]
            assert stats["hands"] == len(matching) == 2
            assert stats["net_chips"] == sum(h["net_chips"] for h in matching)
        snapshot(page, screenshots, "strategy-history-restored", show_board=False)
        check_private_logs(page, url)
    assert not faults, faults
    for hand in completed:
        fallbacks = sum(bool(d["fallback"]) for d in hand["decisions"])
        print(
            f"{hand['policy']}: {hand['first_seat']} first; {len(hand['decisions'])} decisions, {fallbacks} fallbacks."
        )
    print(
        "Both seats, private active hands, reveal/review, and process-restart persistence passed."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", help="Chromium-family browser executable.")
    parser.add_argument(
        "--live",
        action="store_true",
        help="Also play two phone hands against the specified local model.",
    )
    parser.add_argument(
        "--model", default="qwen3.5:2b", help="Installed model reporting token usage."
    )
    parser.add_argument(
        "--screenshots", type=Path, help="Optional directory for visual evidence."
    )
    args = parser.parse_args()
    with (
        tempfile.TemporaryDirectory(prefix="ember-strategy-") as directory,
        sync_playwright() as playwright,
    ):
        browser = playwright.chromium.launch(executable_path=args.browser)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            run(page, Path(directory), args.live, args.model, args.screenshots)
        finally:
            browser.close()
