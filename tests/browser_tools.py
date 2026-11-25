"""Exercise chat commands, reviewed file approvals, and the optional live public-source flow."""
import argparse
from pathlib import Path

from playwright.sync_api import expect, sync_playwright
from browser_smoke import check_layout, test_server


def verify(page, url, live, screenshots):
    faults=[]
    page.on("pageerror",lambda error:faults.append(str(error)))
    page.on("console",lambda msg:faults.append(msg.text) if msg.type in ("error","warning") else None)
    page.goto(url)
    prompt=page.get_by_role("textbox",name="Message Ember")
    prompt.fill("Draft a note titled Field record with the text A local fox observation.")
    prompt.press("Enter")
    expect(page.locator(".from-ember .message-body")).to_contain_text("Review in Tools")
    page.get_by_role("link",name="Tools",exact=True).first.click()
    expect(page.get_by_role("heading",name="Approvals")).to_be_visible()
    state=page.request.get(url+"/api/tools").json()
    destination=Path(state["directory"])
    assert not destination.exists()
    expect(page.locator(".tools-proposal pre")).to_contain_text("A local fox observation.")
    page.get_by_role("button",name="Approve export",exact=True).click()
    expect(page.get_by_text("No exports awaiting approval.")).to_be_visible()
    files=list(destination.glob("*.txt"))
    assert len(files)==1 and "fox observation" in files[0].read_text("utf8")
    page.get_by_label("File type").select_option("export_event")
    page.get_by_label("Title",exact=True).fill("Field trip")
    page.get_by_label("Event date").fill("2027-05-17")
    page.get_by_role("button",name="Prepare export").click()
    expect(page.locator(".tools-proposal pre")).to_contain_text("DTSTART;VALUE=DATE:20270517")
    if screenshots:
        screenshots.mkdir(exist_ok=True,parents=True)
        expect(page.locator("#toast")).to_be_hidden(timeout=8000)
        page.evaluate("window.scrollTo(0, 0)")
        page.screenshot(path=screenshots/"tools-approval.png",full_page=True)
    page.get_by_role("button",name="Decline",exact=True).click()
    expect(page.get_by_text("No exports awaiting approval.")).to_be_visible()
    assert not list(destination.glob("*.ics"))
    if live:
        page.get_by_label("Wikipedia query").fill("red fox")
        page.get_by_label("Lookup method").select_option("wiki_research")
        page.get_by_role("button",name="Look up",exact=True).click()
        hit=page.locator(".tools-hit a").first
        expect(hit).to_have_text("Red fox",timeout=25000)
        assert hit.get_attribute("href").startswith("https://en.wikipedia.org/?curid=")
        expect(page.locator(".tools-hit").first).to_contain_text("Vulpes vulpes")
        page.get_by_label("Lookup method").select_option("weather")
        page.get_by_label("Place name").fill("Berlin, DE")
        page.get_by_role("button",name="Look up",exact=True).click()
        expect(page.locator(".tools-hit").first).to_contain_text("Current temperature",timeout=25000)
        assert page.locator(".tools-hit").first.inner_text().count("Low ")==3
        if screenshots:
            expect(page.locator("#toast")).to_be_hidden(timeout=8000)
            page.evaluate("window.scrollTo(0, 0)")
            page.screenshot(path=screenshots/"tools-live-weather.png",full_page=True)
        page.get_by_role("link",name="Companion",exact=True).click()
        prompt.fill("Research Wikipedia for red fox");prompt.press("Enter")
        expect(page.locator(".from-ember .message-body").last).to_contain_text("Vulpes vulpes",timeout=25000)
        run=page.request.get(url+"/api/runs").json()["runs"][0]
        page.goto(url+"/#laboratory/trace/"+run["id"])
        page.get_by_text("Retrieved references (1)",exact=True).click()
        expect(page.locator(".lab-references a")).to_contain_text("Red fox")
    page.goto(url+"/#tools")
    page.set_viewport_size({"width":390,"height":844})
    expect(page.get_by_role("heading",name="Public lookup")).to_be_visible()
    check_layout(page)
    if screenshots:page.screenshot(path=screenshots/"tools-mobile.png")
    assert not faults,faults
    print("Chat command, approvals, local file content, audit, responsive layout, and requested public reads passed.")


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser",help="Chromium-family browser executable")
    parser.add_argument("--live",action="store_true",help="Query Wikipedia for red fox and Open-Meteo for Berlin.")
    parser.add_argument("--screenshots",type=Path)
    options=parser.parse_args()
    with test_server() as url,sync_playwright() as pw:
        browser=pw.chromium.launch(executable_path=options.browser)
        try:
            verify(browser.new_page(viewport={"width":1440,"height":1000}),url,options.live,options.screenshots)
        finally:
            browser.close()

