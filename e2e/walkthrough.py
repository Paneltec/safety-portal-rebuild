"""Browser walkthrough of every built screen, on a fresh database. Run through e2e/run.sh."""
import struct
import sys
import zlib
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

BASE = "http://localhost:4173"
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/e2e-shots")
OUT.mkdir(parents=True, exist_ok=True)
HOLDER = {"email": "holder@e2e.example", "password": "Holder-Pass-2026"}


def png(width: int = 120, height: int = 60) -> bytes:
    """A small valid PNG, built here so no binary fixture is needed."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + bytes([194, 65, 12]) * width for _ in range(height))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def shot(page, name: str, full: bool = False) -> None:
    page.screenshot(path=str(OUT / f"{name}.png"), full_page=full)


def stage1_and_2a(browser) -> None:
    errors: list[str] = []
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    pg = ctx.new_page()
    pg.on("pageerror", lambda e: errors.append(str(e)))

    # First-run setup: the first person becomes the account holder.
    pg.goto(BASE + "/")
    pg.wait_for_url("**/setup")
    pg.get_by_label("Setup key").fill("e2e-setup-key")
    pg.get_by_label("Organisation name").fill("E2E Civil")
    pg.get_by_label("Your name").fill("Stephen")
    pg.get_by_label("Your email").fill(HOLDER["email"])
    pg.get_by_label("Password", exact=False).first.fill(HOLDER["password"])
    pg.get_by_label("Repeat password").fill(HOLDER["password"])
    shot(pg, "01-setup")
    pg.get_by_role("button", name="Create organisation and sign in").click()
    pg.wait_for_url("**/app/home")
    expect(pg.get_by_role("heading", name="Welcome, Stephen")).to_be_visible()

    # Organisation settings, logo and companies.
    pg.get_by_role("link", name="Organisation", exact=True).click()
    pg.get_by_label("Suburb").fill("Launceston")
    pg.get_by_label("Public portal address").fill("https://portal.e2e.example")
    pg.get_by_role("button", name="Save details").click()
    expect(pg.get_by_text("Organisation settings saved.")).to_be_visible()
    expect(pg.get_by_text("is different from the address you are using now")).to_be_visible()
    pg.locator("input[type=file]").set_input_files({"name": "logo.png", "mimeType": "image/png", "buffer": png()})
    expect(pg.get_by_text("Logo saved.")).to_be_visible()
    expect(pg.locator("nav img.brand-img")).to_be_visible()
    pg.get_by_role("button", name="Add company").click()
    pg.get_by_label("Name").last.fill("Paneltec Civil")
    pg.get_by_label("Short code").last.fill("PAN")
    pg.get_by_role("button", name="Save companies").click()
    expect(pg.get_by_text("Companies saved.")).to_be_visible()
    expect(pg.get_by_text("Stephen (you)")).to_be_visible()
    shot(pg, "02-organisation", full=True)

    # Users: add a person, invite them, change one permission.
    pg.get_by_role("link", name="Users", exact=True).click()
    pg.get_by_role("button", name="Add user").click()
    dialog = pg.get_by_role("dialog")
    dialog.get_by_label("Full name").fill("Nina Example")
    dialog.get_by_label("Email").fill("nina@e2e.example")
    dialog.get_by_role("button", name="Add user").click()
    expect(pg.get_by_role("heading", name="Nina Example")).to_be_visible()
    pg.get_by_role("button", name="Create invitation link").click()
    link = pg.get_by_test_id("one-time-link").inner_text()
    assert "/accept-invite?token=" in link
    pg.get_by_role("button", name="Plant & Vehicles, View: not in role. Activate to change.").click()
    pg.get_by_role("button", name="Save permissions").click()
    expect(pg.get_by_text("Permissions saved.")).to_be_visible()
    shot(pg, "03-user-detail", full=True)
    pg.get_by_role("link", name="Roles & permissions").click()
    expect(pg.get_by_role("heading", name="Admin", exact=True)).to_be_visible()
    pg.get_by_role("link", name="Active sessions").click()
    expect(pg.get_by_text("This session")).to_be_visible()

    # The invited person sets a password on a phone-sized screen and sees only what a worker may.
    phone = browser.new_context(viewport={"width": 390, "height": 844}).new_page()
    phone.on("pageerror", lambda e: errors.append(str(e)))
    phone.goto(BASE + link[link.index("/accept-invite"):])
    phone.get_by_label("New password").first.fill("Nina-Pass-2026")
    phone.get_by_label("Repeat new password").fill("Nina-Pass-2026")
    phone.get_by_role("button", name="Save password and sign in").click()
    phone.wait_for_url("**/app/home")
    expect(phone.get_by_role("heading", name="Welcome, Nina")).to_be_visible()
    assert phone.get_by_role("link", name="Users", exact=True).count() == 0
    phone.goto(BASE + "/app/settings/users")
    expect(phone.get_by_role("alert")).to_contain_text("do not have permission")
    shot(phone, "04-phone-denied")

    # Sign out; the sign-in page now carries the organisation's name and logo.
    pg.get_by_role("button", name="Sign out").click()
    pg.wait_for_url("**/login")
    expect(pg.get_by_role("heading", name="E2E Civil")).to_be_visible()
    expect(pg.locator(".auth-card img.brand-img")).to_be_visible()
    pg.get_by_label("Email").fill(HOLDER["email"])
    pg.get_by_label("Password").fill("wrong-wrong-1")
    pg.get_by_role("button", name="Sign in").click()
    expect(pg.get_by_role("alert")).to_contain_text("incorrect")
    shot(pg, "05-login")
    assert not errors, errors
    print("stage 1 + 2a walkthrough: ok")


def sign_in(browser, width: int = 1280, height: int = 900, email: str = HOLDER["email"], password: str = HOLDER["password"]):
    page = browser.new_context(viewport={"width": width, "height": height}).new_page()
    page.goto(BASE + "/login")
    page.get_by_label("Email").fill(email)
    page.get_by_label("Password").fill(password)
    page.get_by_role("button", name="Sign in").click()
    page.wait_for_url("**/app/home")
    return page


def documents(browser) -> None:
    pg = sign_in(browser)
    errors: list[str] = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.get_by_role("link", name="Document library").click()
    expect(pg.get_by_text("No folders yet")).to_be_visible()
    pg.get_by_role("button", name="New folder").click()
    pg.get_by_role("dialog").get_by_label("Folder name").fill("Policies")
    pg.get_by_role("dialog").get_by_role("button", name="Save").click()
    pg.get_by_role("link", name="Policies").click()
    expect(pg.get_by_text("No files in this folder yet.")).to_be_visible()
    pdf = b"%PDF-1.4\n" + b"synthetic policy " * 40
    pg.locator("input[type=file][multiple]").set_input_files([
        {"name": "Safety policy.pdf", "mimeType": "application/pdf", "buffer": pdf},
        {"name": "script.html", "mimeType": "text/html", "buffer": b"<html></html>"},
    ])
    expect(pg.get_by_text("1 file uploaded, 1 not accepted.")).to_be_visible()
    expect(pg.get_by_text("script.html (This file type is not accepted)")).to_be_visible()
    expect(pg.get_by_role("button", name="Safety policy.pdf")).to_be_visible()
    # Download returns the same bytes that were uploaded.
    with pg.expect_download() as download:
        pg.get_by_role("button", name="Download").click()
    assert Path(download.value.path()).read_bytes() == pdf
    pg.get_by_role("button", name="Manage").click()
    dialog = pg.get_by_role("dialog")
    dialog.get_by_label("File name").fill("WHS policy.pdf")
    dialog.get_by_label("Tags, separated by commas").fill("policy, 2026")
    dialog.get_by_label("Expiry date (optional)").fill("2020-01-31")
    dialog.get_by_role("button", name="Save details").click()
    expect(pg.get_by_text("File details saved.")).to_be_visible()
    expect(pg.get_by_role("button", name="WHS policy.pdf")).to_be_visible()
    expect(pg.get_by_text("Expired", exact=True)).to_be_visible()
    shot(pg, "06-documents")
    pg.get_by_label("Search this folder and its subfolders").fill("2026")
    expect(pg.get_by_text("1 found")).to_be_visible()
    pg.get_by_label("Search this folder and its subfolders").fill("")
    pg.get_by_role("button", name="Manage").click()
    pg.get_by_role("dialog").get_by_role("button", name="Archive").click()
    expect(pg.get_by_text("No files in this folder yet.")).to_be_visible()
    pg.get_by_label("Show archived").check()
    expect(pg.get_by_text("Archived", exact=True)).to_be_visible()
    # A folder that still holds a file cannot be deleted.
    pg.get_by_role("button", name="Delete folder").click()
    expect(pg.get_by_role("alert")).to_contain_text("not empty")
    assert not errors, errors
    print("document library walkthrough: ok")


def forms(browser) -> None:
    pg = sign_in(browser)
    errors: list[str] = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.get_by_role("link", name="Forms", exact=True).click()
    expect(pg.get_by_text("No forms yet")).to_be_visible()
    pg.get_by_role("button", name="New form").click()
    pg.wait_for_url("**/edit")
    pg.get_by_label("Form name").fill("Daily pre-start")
    pg.locator("label.field", has_text="Type").locator("select").first.select_option("pre_start")

    def add(kind: str, label: str, required: bool = False, choices: str | None = None) -> None:
        pg.get_by_label("Add a question").select_option(kind)
        pg.get_by_role("button", name="Add", exact=True).click()
        pg.get_by_label("Question", exact=True).last.fill(label)
        if choices:
            pg.get_by_label("Choices, one per line").last.fill(choices)
        if required:
            pg.get_by_label("Required", exact=True).last.check()

    add("text", "Rego", required=True)
    add("compliance", "Tyres and wheel nuts", required=True)
    add("select", "Weather", choices="Fine\nRain")
    add("photo", "Defect photo")
    add("signature", "Operator signature", required=True)
    pg.get_by_role("button", name="Publish", exact=True).click()
    expect(pg.get_by_text("Published as version 1.")).to_be_visible()
    # Limit the form to one role, and check what a named person will see.
    pg.get_by_label("Only the roles, companies and people ticked below").check()
    pg.get_by_role("group", name="Roles").get_by_label("Supervisor").check()
    pg.get_by_role("button", name="Save assignment").click()
    expect(pg.get_by_text("Saved who this is assigned to.")).to_be_visible()
    pg.get_by_label("Check what one person sees").select_option(label="Nina Example")
    expect(pg.get_by_text("No, they do not see this form. It is not assigned to them.")).to_be_visible()
    pg.get_by_role("group", name="Roles").get_by_label("Worker").check()
    pg.get_by_role("button", name="Save assignment").click()
    expect(pg.get_by_text("Saved who this is assigned to.")).to_be_visible()
    pg.get_by_label("Check what one person sees").select_option("")
    pg.get_by_label("Check what one person sees").select_option(label="Nina Example")
    expect(pg.get_by_text("Yes, they see this form. Assigned to the Worker role.")).to_be_visible()
    shot(pg, "07-form-editor", full=True)

    # Fill it in: a failed check needs a note, and the signature is required.
    pg.get_by_role("link", name="← All forms").click()
    pg.get_by_role("button", name="Fill in").click()
    pg.wait_for_url("**/forms/submissions/*")
    pg.get_by_label("Rego").fill("PT-031")
    pg.get_by_role("button", name="Fail").click()
    pg.get_by_role("button", name="Submit", exact=True).click()
    expect(pg.get_by_text("2 answers need attention.")).to_be_visible()
    expect(pg.get_by_text("a failed check needs a note")).to_be_visible()
    pg.get_by_label("What is wrong? (required)").fill("Nail in rear left tyre")
    pg.get_by_label("Weather").select_option("Rain")
    pg.locator("input[type=file]").set_input_files({"name": "tyre.png", "mimeType": "image/png", "buffer": png()})
    expect(pg.get_by_role("button", name="tyre.png", exact=True)).to_be_visible()
    box = pg.locator("canvas").bounding_box()
    pg.mouse.move(box["x"] + 30, box["y"] + 60)
    pg.mouse.down()
    pg.mouse.move(box["x"] + 150, box["y"] + 90, steps=6)
    pg.mouse.move(box["x"] + 260, box["y"] + 50, steps=6)
    pg.mouse.up()
    pg.get_by_label("Name of the person signing").fill("Stephen")
    pg.get_by_role("button", name="Save signature").click()
    expect(pg.get_by_text("Signed by Stephen")).to_be_visible()
    pg.get_by_role("button", name="Save draft").click()
    expect(pg.get_by_text("Draft saved.")).to_be_visible()
    pg.get_by_role("button", name="Submit", exact=True).click()
    expect(pg.get_by_text("Submitted. This record can no longer be changed.")).to_be_visible()
    # The submitted record is read-only and keeps the failure and its note.
    expect(pg.get_by_text("Nail in rear left tyre")).to_be_visible()
    expect(pg.get_by_text("Fail", exact=True)).to_be_visible()
    assert pg.get_by_role("button", name="Submit", exact=True).count() == 0
    shot(pg, "08-form-submitted", full=True)
    # The PDF of the saved record is a real PDF, served only with the session.
    pdf = pg.evaluate("""async () => {
        const id = location.pathname.split('/').pop();
        const token = sessionStorage.getItem('sp.token');
        const r = await fetch(`/api/forms/submissions/${id}/pdf`, { headers: { Authorization: `Bearer ${token}` } });
        const head = new TextDecoder().decode((await r.arrayBuffer()).slice(0, 5));
        const anon = await fetch(`/api/forms/submissions/${id}/pdf`);
        return [r.status, r.headers.get('content-type'), head, anon.status];
    }""")
    assert pdf == [200, "application/pdf", "%PDF-", 401], pdf
    pg.get_by_role("link", name="← Daily pre-start submissions").click()
    expect(pg.get_by_text("Showing 1–1 of 1")).to_be_visible()
    assert not errors, errors
    print("forms walkthrough: ok")


def safety_registers(browser) -> None:
    pg = sign_in(browser)
    errors: list[str] = []
    pg.on("pageerror", lambda e: errors.append(str(e)))

    # The pre-start form submitted earlier landed in the pre-starts register, once.
    pg.get_by_role("link", name="Pre-starts").click()
    expect(pg.get_by_text("Showing 1–1 of 1")).to_be_visible()
    expect(pg.get_by_text("from a form")).to_be_visible()

    # SWMS: draft, submit, approve.
    pg.get_by_role("link", name="SWMS", exact=True).click()
    expect(pg.get_by_text("No swms recorded yet.")).to_be_visible()
    pg.get_by_role("button", name="New SWMS").click()
    pg.wait_for_url("**/r/swms/*")
    pg.get_by_label("SWMS title").fill("Trenching and excavation")
    pg.get_by_label("Job description").fill("Excavate and lay sewer main")
    pg.get_by_label("Prepared by").fill("Stephen")
    pg.get_by_role("button", name="Submit for review").click()
    expect(pg.get_by_text("1 answer needs attention.")).to_be_visible()  # the job steps table is required
    pg.get_by_role("button", name="Add a row").click()
    pg.get_by_role("textbox", name="Job step", exact=True).fill("Excavate trench")
    pg.get_by_role("textbox", name="Hazards", exact=True).fill("Trench collapse")
    pg.get_by_role("textbox", name="Controls", exact=True).fill("Shoring and edge protection")
    pg.get_by_role("button", name="Submit for review").click()
    expect(pg.get_by_text("Submitted for review.")).to_be_visible()
    pg.get_by_role("button", name="Approve").click()
    expect(pg.get_by_text("Approved.", exact=True)).to_be_visible()
    expect(pg.get_by_text("Nobody has acknowledged this version yet.")).to_be_visible()
    shot(pg, "09-swms", full=True)

    # Hazard: report, then close with a resolution note.
    pg.get_by_role("link", name="Incidents & hazards").click()
    pg.get_by_role("link", name="Hazards and near misses").click()
    pg.get_by_role("button", name="Report a hazard").click()
    pg.get_by_role("button", name="Save report").click()
    expect(pg.get_by_text("4 answers need attention.")).to_be_visible()
    pg.get_by_label("What is the hazard?").fill("Open trench unfenced")
    pg.get_by_label("Type").select_option("Hazard")
    pg.get_by_label("Describe it").fill("Trench beside the footpath has no barrier")
    pg.get_by_label("Severity").select_option("High")
    pg.get_by_role("button", name="Save report").click()
    pg.wait_for_url("**/r/hazards/*")
    expect(pg.get_by_role("heading", name="Open trench unfenced")).to_be_visible()
    pg.get_by_role("button", name="Close", exact=True).click()
    pg.get_by_role("dialog").get_by_label("How was it resolved?").fill("Barrier fence installed")
    pg.get_by_role("dialog").get_by_role("button", name="Close", exact=True).click()
    expect(pg.get_by_text("Closed. The original report is unchanged.")).to_be_visible()
    expect(pg.get_by_role("cell", name="Barrier fence installed")).to_be_visible()
    shot(pg, "10-hazard", full=True)

    # A worker on a phone sees the approved SWMS and acknowledges it.
    phone = sign_in(browser, 390, 844, "nina@e2e.example", "Nina-Pass-2026")
    phone.on("pageerror", lambda e: errors.append(str(e)))
    phone.get_by_role("button", name="Menu").click()
    phone.get_by_role("link", name="SWMS", exact=True).click()
    phone.get_by_role("link", name="Trenching and excavation").click()
    assert phone.get_by_role("button", name="Submit for review").count() == 0
    phone.get_by_role("button", name="I have read and understood this SWMS").click()
    expect(phone.get_by_text("You acknowledged version 1")).to_be_visible()
    shot(phone, "11-phone-swms-ack", full=True)
    pg.reload()
    pg.get_by_role("link", name="SWMS", exact=True).click()
    pg.get_by_role("link", name="Trenching and excavation").click()
    expect(pg.get_by_role("cell", name="Nina Example").first).to_be_visible()
    assert not errors, errors
    print("safety registers walkthrough: ok")


STEPS = [stage1_and_2a, documents, forms, safety_registers]

if __name__ == "__main__":
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
        for step in STEPS:
            step(browser)
        browser.close()
    print("all walkthroughs passed; screenshots in", OUT)
