"""Live acceptance test of profiles, regression suites, reviews, and the responsive laboratory."""
import argparse
import re
import sys
from pathlib import Path
from playwright.sync_api import expect, sync_playwright
from browser_smoke import ROOT, capture, test_server
sys.path.insert(0,str(ROOT))
from test_importer import small_pdf


def run(page, url, screenshots):
    errors=[]
    page.on("pageerror",lambda error:errors.append(str(error)))
    page.goto(url+"/#memories")
    page.locator("ember-library input[type=file]").set_input_files({
        "name":"station.pdf","mimeType":"application/pdf","buffer":small_pdf()})
    page.get_by_role("button",name="station.pdf",exact=True).click()
    expect(page.get_by_role("dialog")).to_contain_text("Rowan-27")
    page.get_by_role("button",name="Close dialog").click()
    page.get_by_role("link",name="Settings",exact=True).click()
    page.get_by_label("Fox coat").select_option("silver")
    page.locator('select[name="landscape"]').select_option("night")
    page.locator('#temperature').fill("0.25")
    page.get_by_role("button",name="Profiles",exact=True).click()
    dialog=page.get_by_role("dialog")
    dialog.get_by_label("New profile name").fill("Precise")
    dialog.get_by_role("button",name="Save current configuration").click()
    dialog.get_by_role("button",name="Load",exact=True).click()
    expect(dialog).to_have_count(0)
    expect(page.locator("#temperature")).to_have_value("0.25")
    expect(page.locator(".voice-settings")).to_be_visible()
    assert page.request.get(url+"/api/settings").json()["temperature"]==.25
    page.get_by_role("link",name="Companion",exact=True).click()
    expect(page.locator("ember-scene canvas")).to_have_css("filter",re.compile("grayscale"))
    page.get_by_role("link",name="Laboratory",exact=True).click()
    page.get_by_label("Test prompt").fill("What code is in station.pdf? Include the code only.")
    page.locator('select[name="model1"]').select_option("qwen3.5:2b")
    page.locator('[name="live_retrieval"]').check()
    page.locator(".lab-check summary").click()
    page.get_by_label("Evaluation method").select_option("source")
    page.get_by_role("textbox", name="Expected filename", exact=True).fill("station.pdf")
    page.get_by_role("button",name="Save test case").click()
    page.get_by_label("Case name").fill("Find the station note")
    page.get_by_role("button",name="Save case",exact=True).click()
    expect(page.get_by_role("dialog")).to_have_count(0)
    page.get_by_role("button",name="Test cases",exact=True).click()
    page.get_by_role("button",name="Run saved suite").click()
    page.get_by_role("button",name="Find the station note",exact=True).first.wait_for(timeout=180000)
    page.locator("[data-experiment]").first.click(timeout=180000)
    expect(page.locator(".lab-result [data-pass=true]")).to_have_count(2)
    page.get_by_role("button",name="Prefer A",exact=True).click()
    expect(page.get_by_text("Recorded preference · A")).to_be_visible()
    page.get_by_text("Local model assessment",exact=True).click()
    page.get_by_role("button",name="Review answers").click()
    expect(page.get_by_role("button",name="Review answers")).to_be_enabled(timeout=180000)
    record=page.request.get(url+"/api/lab/experiments").json()["experiments"][0]
    detail=page.request.get(url+"/api/lab/experiments/"+record["id"]).json()
    assert detail["judgment"]["assessment"] and detail["preference"]=="A"
    page.get_by_role("button",name="Performance",exact=True).click()
    expect(page.get_by_role("heading",name="Runtime performance")).to_be_visible()
    expect(page.locator(".lab-body")).to_contain_text("Median first token")
    capture(page,screenshots,"performance")
    page.set_viewport_size({"width":390,"height":844})
    page.get_by_role("button",name="Models",exact=True).click()
    expect(page.get_by_role("heading",name="Embedding models")).to_be_visible()
    capture(page,screenshots,"inventory-mobile")
    assert not errors,errors


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser")
    parser.add_argument("--screenshots",type=Path)
    args=parser.parse_args()
    with test_server() as origin,sync_playwright() as pw:
        browser=pw.chromium.launch(executable_path=args.browser)
        try:
            run(browser.new_page(viewport={"width":1440,"height":1000}),origin,args.screenshots)
        finally:
            browser.close()
    print("Live laboratory acceptance passed.")
