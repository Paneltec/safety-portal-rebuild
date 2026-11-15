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
    pg.locator("canvas").scroll_into_view_if_needed()  # the drawing must land on the canvas, not above it
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


def workers(browser) -> None:
    from datetime import date, timedelta

    pg = sign_in(browser)
    errors: list[str] = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    past = (date.today() - timedelta(days=10)).isoformat()
    future = (date.today() + timedelta(days=400)).isoformat()

    # Add a worker and fill in details.
    pg.get_by_role("link", name="Workers", exact=True).click()
    expect(pg.get_by_text("No workers yet. Add the first one to get started.")).to_be_visible()
    pg.get_by_role("button", name="Add worker").click()
    dialog = pg.get_by_role("dialog")
    dialog.get_by_label("First name").fill("Nina")
    dialog.get_by_label("Last name").fill("Example")
    dialog.get_by_label("Position (optional)").fill("Traffic controller")
    dialog.get_by_role("button", name="Add worker").click()
    pg.wait_for_url("**/app/workers/*")
    expect(pg.get_by_role("heading", name="Nina Example")).to_be_visible()
    pg.get_by_role("button", name="Edit details").click()
    dialog.get_by_label("Suburb").fill("Invermay")
    dialog.get_by_label("Emergency contact name").fill("Sam Example")
    dialog.get_by_role("button", name="Save").click()
    expect(pg.get_by_text("Details saved.")).to_be_visible()
    expect(pg.get_by_text("Sam Example")).to_be_visible()

    # Protected details: saved encrypted, shown masked, revealed on request.
    protected = pg.locator("section", has=pg.get_by_role("heading", name="Protected details"))
    protected.get_by_role("button", name="Edit").click()
    protected.get_by_label("Tax file number").fill("123 456 782")
    protected.get_by_role("button", name="Save").click()
    expect(protected.get_by_text("•••••• 782")).to_be_visible()
    assert protected.get_by_text("123456782").count() == 0
    protected.get_by_role("button", name="Show full values").click()
    expect(protected.get_by_text("123456782")).to_be_visible()
    protected.get_by_role("button", name="Hide").click()

    # Link the existing login.
    pg.get_by_role("button", name="Link existing login").click()
    dialog.get_by_label("Login").select_option(label="Nina Example · nina@e2e.example")
    dialog.get_by_role("button", name="Link login").click()
    expect(pg.get_by_text("Login linked.")).to_be_visible()

    # Certificates: an expired one, renewed; one with no expiry date recorded.
    certs = pg.locator("section", has=pg.get_by_role("heading", name="Certificates", exact=True))
    certs.get_by_role("button", name="Add certificate").click()
    dialog.get_by_label("Type").select_option(label="First Aid")
    dialog.get_by_label("Expiry date").fill(past)
    dialog.get_by_role("button", name="Save").click()
    expect(certs.get_by_text("Expired", exact=True)).to_be_visible()
    certs.get_by_role("button", name="Renew").click()
    dialog.get_by_label("Expiry date").fill(future)
    dialog.get_by_role("button", name="Record renewal").click()
    expect(certs.get_by_text("Valid", exact=True)).to_be_visible()
    assert certs.get_by_text("Expired", exact=True).count() == 0
    with pg.expect_file_chooser() as chooser:
        certs.get_by_role("button", name="Attach file").click()
    chooser.value.set_files({"name": "first-aid.png", "mimeType": "image/png", "buffer": png()})
    expect(certs.get_by_role("button", name="first-aid.png")).to_be_visible()
    certs.get_by_role("button", name="Add certificate").click()
    dialog.get_by_label("Type").select_option(label="White Card (Construction Induction)")
    dialog.get_by_role("button", name="Save").click()
    expect(certs.get_by_text("Expiry not recorded", exact=True)).to_be_visible()
    certs.get_by_label("Show earlier and archived").check()
    expect(certs.get_by_text("Replaced by a renewal")).to_be_visible()
    shot(pg, "12-worker", full=True)

    # The register totals match what it lists.
    pg.get_by_role("link", name="Certifications", exact=True).click()
    expect(pg.get_by_role("button", name="1 Valid")).to_be_visible()
    expect(pg.get_by_role("button", name="1 Expiry not recorded")).to_be_visible()
    expect(pg.get_by_role("button", name="0 Expired")).to_be_visible()
    pg.get_by_role("button", name="1 Expiry not recorded").click()
    expect(pg.get_by_text("Showing 1–1 of 1 matching")).to_be_visible()
    pg.get_by_role("button", name="1 Expiry not recorded").click()

    # Settings: expect Traffic Control from everyone and switch the form gate on.
    pg.get_by_role("button", name="Settings").click()
    dialog.get_by_label("Traffic Control").check()
    dialog.get_by_label("Allow forms to require certificates").check()
    dialog.get_by_role("button", name="Save settings").click()
    expect(pg.get_by_text("Certification settings saved.")).to_be_visible()
    shot(pg, "13-certifications", full=True)
    pg.get_by_role("link", name="Workers", exact=True).click()
    expect(pg.get_by_text("1 missing")).to_be_visible()

    # Require Traffic Control on the pre-start form.
    pg.get_by_role("link", name="Forms", exact=True).click()
    pg.get_by_role("link", name="Edit").click()
    pg.get_by_label("Require certificates for this form").check()
    gate = pg.locator("section", has=pg.get_by_role("heading", name="Certificates needed to fill this in"))
    gate.get_by_label("Traffic Control").check()
    pg.get_by_role("button", name="Save draft").click()
    expect(pg.get_by_text("Draft saved.")).to_be_visible()

    # The worker on a phone: own record only, and the gated form explains what is missing.
    phone = sign_in(browser, 390, 844, "nina@e2e.example", "Nina-Pass-2026")
    phone.on("pageerror", lambda e: errors.append(str(e)))
    phone.get_by_role("button", name="Menu").click()
    phone.get_by_role("link", name="My worker record").click()
    expect(phone.get_by_role("link", name="Nina Example")).to_be_visible()
    phone.get_by_role("button", name="Menu").click()
    phone.get_by_role("link", name="Forms", exact=True).click()
    phone.get_by_role("button", name="Fill in").click()
    expect(phone.get_by_text("Traffic Control: no certificate on record")).to_be_visible()
    phone.get_by_role("button", name="Menu").click()
    phone.get_by_role("link", name="My profile").click()
    mine = phone.locator("section", has=phone.get_by_role("heading", name="My worker record"))
    mine.get_by_label("Mobile").fill("0400 000 123")
    mine.get_by_role("button", name="Save my details").click()
    expect(phone.get_by_text("Your details are saved.")).to_be_visible()
    expect(phone.get_by_text("Expiry not recorded", exact=True)).to_be_visible()
    shot(phone, "14-phone-my-record", full=True)

    # Record the certificate, and the form opens.
    pg.get_by_role("link", name="Workers", exact=True).click()
    pg.get_by_role("link", name="Nina Example").click()
    expect(pg.get_by_text("Missing expected certificates: Traffic Control.")).to_be_visible()
    certs = pg.locator("section", has=pg.get_by_role("heading", name="Certificates", exact=True))
    certs.get_by_role("button", name="Add certificate").click()
    dialog.get_by_label("Type").select_option(label="Traffic Control")
    dialog.get_by_label("Expiry date").fill(future)
    dialog.get_by_role("button", name="Save").click()
    expect(pg.get_by_text("Certificate added.")).to_be_visible()
    phone.get_by_role("button", name="Menu").click()
    phone.get_by_role("link", name="Forms", exact=True).click()
    phone.get_by_role("button", name="Fill in").click()
    phone.wait_for_url("**/forms/submissions/*")
    assert not errors, errors
    print("workers and certifications walkthrough: ok")


def sites_and_visitors(browser) -> None:
    pg = sign_in(browser)
    errors: list[str] = []
    pg.on("pageerror", lambda e: errors.append(str(e)))

    # Add a site and set what people are asked at the gate.
    pg.get_by_role("link", name="Sites", exact=True).click()
    expect(pg.get_by_text("No sites yet. Add the first one to get its sign-in QR code.")).to_be_visible()
    pg.get_by_role("button", name="Add site").click()
    dialog = pg.get_by_role("dialog")
    dialog.get_by_label("Site name").fill("Bridge Rd upgrade")
    dialog.get_by_label("Street address (optional)").fill("Bridge Rd")
    dialog.get_by_label("Suburb").fill("Launceston")
    dialog.get_by_role("button", name="Add site").click()
    pg.wait_for_url("**/app/sites/*")
    expect(pg.get_by_text("Nobody is signed in at this site.")).to_be_visible()
    expect(pg.get_by_role("img", name="QR code for signing in at Bridge Rd upgrade")).to_be_visible()
    pg.get_by_role("button", name="Add a question").click()
    pg.get_by_label("Question 1").fill("Are you fit for work today?")
    pg.get_by_role("button", name="Save sign-in questions").click()
    expect(pg.get_by_text("Sign-in questions saved.")).to_be_visible()
    link = pg.locator(".code-box").inner_text().strip()
    path = link[link.index("/sign-on/"):]
    shot(pg, "15-site", full=True)

    # A visitor at the gate, on a phone, with no login.
    gate = browser.new_context(viewport={"width": 390, "height": 844}).new_page()
    gate.on("pageerror", lambda e: errors.append(str(e)))
    gate.goto(BASE + path)
    expect(gate.get_by_role("heading", name="Bridge Rd upgrade")).to_be_visible()
    gate.get_by_role("button", name="I am a visitor or contractor").click()
    gate.get_by_label("Your full name").fill("Vera Visitor")
    gate.get_by_label("Company (optional)").fill("Acme Surveying")
    gate.get_by_label("Who are you here to see? (optional)").fill("Stephen")
    expect(gate.get_by_role("button", name="Sign in", exact=True)).to_be_disabled()  # rules not accepted yet
    gate.get_by_label("I have read and accept the site rules").check()
    gate.get_by_role("button", name="Sign in", exact=True).click()
    expect(gate.get_by_text("Are you fit for work today?: an answer is needed")).to_be_visible()
    gate.get_by_role("button", name="Yes", exact=True).click()
    gate.get_by_role("button", name="Sign in", exact=True).click()
    expect(gate.get_by_text("you are signed in")).to_be_visible()
    shot(gate, "16-gate-visitor", full=True)
    gate.reload()  # the phone remembers the visit
    expect(gate.get_by_role("button", name="Sign out")).to_be_visible()
    assert gate.goto(BASE + "/sign-on/not-a-real-code") is not None
    expect(gate.get_by_text("This sign-in code is not in use.")).to_be_visible()

    # A worker scans the same code, signs in to the portal and signs on.
    phone = browser.new_context(viewport={"width": 390, "height": 844}).new_page()
    phone.on("pageerror", lambda e: errors.append(str(e)))
    phone.goto(BASE + path)
    phone.get_by_role("link", name="I work for E2E Civil").click()
    phone.get_by_label("Email").fill("nina@e2e.example")
    phone.get_by_label("Password").fill("Nina-Pass-2026")
    phone.get_by_role("button", name="Sign in").click()
    phone.wait_for_url("**/app/site-sign-on/*")
    phone.get_by_role("button", name="Yes", exact=True).click()
    phone.get_by_role("button", name="Sign on").click()
    expect(phone.get_by_text("You are signed on.")).to_be_visible()
    shot(phone, "17-phone-sign-on", full=True)
    phone.get_by_role("button", name="Menu").click()
    phone.get_by_role("link", name="Home").click()
    expect(phone.get_by_text("You are signed on", exact=True)).to_be_visible()

    # The office sees both, signs the visitor out, and the worker signs off from Home.
    pg.reload()
    on_site = pg.locator("section", has=pg.get_by_role("heading", name="On site now"))
    expect(on_site.get_by_role("cell", name="Vera Visitor · Acme Surveying")).to_be_visible()
    expect(on_site.get_by_role("cell", name="Nina Example")).to_be_visible()
    pg.get_by_role("link", name="Visitors", exact=True).click()
    expect(pg.get_by_role("button", name="1 On site now")).to_be_visible()
    expect(pg.get_by_text("Are you fit for work today? Yes")).to_be_visible()
    shot(pg, "18-visitors", full=True)
    pg.get_by_role("button", name="Correct").click()
    dialog.get_by_label("Company").fill("Acme Surveying Pty Ltd")
    dialog.get_by_label("Why is this being corrected?").fill("Full company name")
    dialog.get_by_role("button", name="Save correction").click()
    expect(pg.get_by_text("Correction saved.")).to_be_visible()
    pg.get_by_role("button", name="Sign visitor out").click()
    expect(pg.get_by_text("Vera Visitor signed out.")).to_be_visible()
    expect(pg.get_by_text("No visitors are on site.")).to_be_visible()
    pg.get_by_role("button", name="1 Signed in today").click()
    expect(pg.get_by_text("by the office")).to_be_visible()
    gate.goto(BASE + path)  # the visitor's phone now offers a fresh sign-in
    expect(gate.get_by_role("button", name="I am a visitor or contractor")).to_be_visible()
    phone.get_by_role("button", name="Sign off").click()
    expect(phone.get_by_text("You are signed on", exact=True)).to_have_count(0)
    pg.get_by_role("link", name="Sites", exact=True).click()
    expect(pg.get_by_role("cell", name="Nobody")).to_be_visible()
    assert not errors, errors
    print("sites and visitors walkthrough: ok")


def suppliers_and_renewals(browser) -> None:
    from datetime import date, timedelta

    pg = sign_in(browser)
    errors: list[str] = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    future = (date.today() + timedelta(days=300)).isoformat()
    pdf = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"

    # Add a contractor; the required documents show as missing.
    pg.get_by_role("link", name="Suppliers", exact=True).click()
    expect(pg.get_by_text("No suppliers yet.")).to_be_visible()
    pg.get_by_role("button", name="Add supplier").click()
    dialog = pg.get_by_role("dialog")
    dialog.get_by_label("Business name").fill("Acme Earthworks")
    dialog.get_by_label("ABN (optional)").fill("51 824 753 557")
    dialog.get_by_label("This is a contractor").check()
    dialog.get_by_role("button", name="Add supplier").click()
    expect(dialog.get_by_text("That ABN is not valid. Check the digits.")).to_be_visible()
    dialog.get_by_label("ABN (optional)").fill("51 824 753 556")
    dialog.get_by_role("button", name="Add supplier").click()
    pg.wait_for_url("**/app/suppliers/*")
    expect(pg.get_by_text("Missing required documents: Public liability insurance, Workers compensation insurance.")).to_be_visible()

    # People, a task and a note.
    people = pg.locator("section", has=pg.get_by_role("heading", name="People"))
    people.get_by_label("Add a person").fill("Pat Lee")
    people.get_by_role("button", name="Add").click()
    expect(people.get_by_role("cell", name="Pat Lee")).to_be_visible()
    tasks = pg.locator("section", has=pg.get_by_role("heading", name="Tasks"))
    tasks.get_by_label("Add a task").fill("Check insurance before next job")
    tasks.get_by_role("button", name="Add").click()
    tasks.get_by_role("button", name="Mark done").click()
    expect(tasks.get_by_text("Done", exact=True)).to_be_visible()
    notes = pg.locator("section", has=pg.get_by_role("heading", name="Notes"))
    notes.get_by_label("Add a note").fill("Good work on the Bridge Rd job.")
    notes.get_by_role("button", name="Save note").click()
    expect(notes.get_by_text("Good work on the Bridge Rd job.")).to_be_visible()

    # Ask for both documents through a link.
    requests = pg.locator("section", has=pg.get_by_role("heading", name="Document requests"))
    requests.get_by_role("button", name="Ask for documents").click()
    dialog.get_by_label("Public liability insurance").check()
    dialog.get_by_label("Workers compensation insurance").check()
    dialog.get_by_label("Message shown on their page (optional)").fill("Please send current certificates of currency.")
    dialog.get_by_role("button", name="Create link").click()
    expect(requests.get_by_text("It is shown once: copy it now.")).to_be_visible()
    expect(requests.get_by_text("No email address is recorded for them")).to_be_visible()
    link = requests.locator(".code-box").inner_text().strip()
    path = link[link.index("/renew/"):]

    # The supplier, with no login, uploads on their phone.
    theirs = browser.new_context(viewport={"width": 390, "height": 844}).new_page()
    theirs.on("pageerror", lambda e: errors.append(str(e)))
    theirs.goto(BASE + path)
    expect(theirs.get_by_role("heading", name="Acme Earthworks")).to_be_visible()
    expect(theirs.get_by_text("Please send current certificates of currency.")).to_be_visible()
    theirs.get_by_role("button", name="Send documents").click()
    expect(theirs.get_by_text("Add at least one document before sending")).to_be_visible()
    for label, number in (("Public liability insurance", "PL-2291"), ("Workers compensation insurance", "WC-7730")):
        row = theirs.locator("div.stack", has=theirs.get_by_text(label, exact=True)).last
        row.get_by_label("Policy, licence or certificate number (optional)").fill(number)
        row.get_by_label("Expiry date (optional)").fill(future)
        row.get_by_label(f"Choose a file for {label}").set_input_files({"name": f"{number}.pdf", "mimeType": "application/pdf", "buffer": pdf})
        expect(row.get_by_text("Added", exact=True)).to_be_visible()
    shot(theirs, "19-renew-public", full=True)
    theirs.get_by_role("button", name="Send documents").click()
    expect(theirs.get_by_text("Thank you. Your documents were sent")).to_be_visible()

    # The office reviews: accept one, reject the other with a reason.
    pg.get_by_role("link", name="Renewals", exact=True).click()
    expect(pg.get_by_role("tab", name="Ready to review (1)")).to_be_visible()
    pg.get_by_role("button", name="Review").click()
    pl = dialog.locator("div.stack", has=pg.get_by_text("Public liability insurance", exact=True)).last
    wc = dialog.locator("div.stack", has=pg.get_by_text("Workers compensation insurance", exact=True)).last
    pl.get_by_role("button", name="Accept").click()
    dialog.get_by_role("button", name="Save review").click()
    expect(dialog.get_by_text("Choose Accept or Reject for every file.")).to_be_visible()
    wc.get_by_role("button", name="Reject").click()
    wc.get_by_label("Why is it rejected?").fill("Policy is in a different company name")
    shot(pg, "20-renewal-review", full=True)
    dialog.get_by_role("button", name="Save review").click()
    expect(pg.get_by_text("Review saved.")).to_be_visible()
    pg.get_by_role("tab", name="Partly accepted (1)").click()
    pg.get_by_role("link", name="Acme Earthworks").click()
    expect(pg.get_by_text("Missing required documents: Workers compensation insurance.")).to_be_visible()
    docs = pg.locator("section", has=pg.get_by_role("heading", name="Compliance documents"))
    expect(docs.get_by_role("cell", name="PL-2291", exact=True)).to_be_visible()
    expect(docs.get_by_text("via renewal link")).to_be_visible()
    expect(docs.get_by_text("Valid", exact=True)).to_be_visible()
    shot(pg, "21-supplier", full=True)
    # The used link shows the request is finished and accepts nothing more.
    theirs.reload()
    expect(theirs.get_by_text("This request is finished.")).to_be_visible()
    assert theirs.get_by_role("button", name="Send documents").count() == 0
    assert not errors, errors
    print("suppliers and renewals walkthrough: ok")


def inductions_and_jobs(browser) -> None:
    from datetime import date, timedelta

    pg = sign_in(browser)
    errors: list[str] = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    future = (date.today() + timedelta(days=200)).isoformat()

    # Inductions matrix: record a date, mark one not applicable, add a column.
    pg.get_by_role("link", name="Inductions & training").click()
    cell = pg.get_by_role("button", name="Nina Example, TasWater Induction: nothing recorded")
    expect(cell).to_be_visible()
    cell.click()
    dialog = pg.get_by_role("dialog")
    dialog.get_by_role("button", name="Save").click()
    expect(dialog.get_by_text("Choose the expiry date, or pick another option.")).to_be_visible()
    dialog.get_by_label("Expiry date").fill(future)
    dialog.get_by_role("button", name="Save").click()
    expect(pg.get_by_text("Induction recorded.")).to_be_visible()
    expect(pg.get_by_role("button", name="Nina Example, TasWater Induction: Valid")).to_be_visible()
    pg.get_by_role("button", name="Nina Example, TasRail Induction: nothing recorded").click()
    dialog.get_by_label("Not applicable to this worker").check()
    dialog.get_by_role("button", name="Save").click()
    expect(pg.get_by_role("button", name="Nina Example, TasRail Induction: Not applicable")).to_be_visible()
    pg.get_by_role("button", name="Induction types").click()
    dialog.get_by_label("New induction").fill("Hydro Tasmania induction")
    dialog.get_by_role("button", name="Add").click()
    expect(dialog.get_by_role("cell", name="Hydro Tasmania induction")).to_be_visible()
    dialog.get_by_role("button", name="Done").click()
    expect(pg.get_by_role("columnheader", name="Hydro Tasmania induction")).to_be_visible()
    shot(pg, "22-inductions", full=True)
    # The induction is on the worker's certificate list too: one record, two views.
    pg.get_by_role("link", name="Nina Example").click()
    certs = pg.locator("section", has=pg.get_by_role("heading", name="Certificates", exact=True))
    expect(certs.get_by_role("cell", name="TasWater Induction")).to_be_visible()

    # HR document: add, then open (which is logged).
    hr = pg.locator("section", has=pg.get_by_role("heading", name="HR documents"))
    hr.get_by_label("Choose an HR document").set_input_files({"name": "contract.pdf", "mimeType": "application/pdf", "buffer": b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"})
    expect(hr.get_by_role("cell", name="contract.pdf")).to_be_visible()

    # Training.
    pg.get_by_role("link", name="Inductions & training").click()
    pg.get_by_role("tab", name="Completed training").click()
    pg.get_by_role("button", name="Record training").click()
    dialog.get_by_label("Worker").select_option(label="Nina Example")
    dialog.get_by_label("Course").fill("Manual handling refresher")
    dialog.get_by_role("button", name="Save").click()
    expect(pg.get_by_role("cell", name="Manual handling refresher")).to_be_visible()

    # Issue a job; the check step shows who gets it before anything is saved.
    pg.get_by_role("link", name="Daily jobs").click()
    pg.get_by_role("button", name="Issue a job").click()
    dialog.get_by_label("Nina Example").check()
    dialog.get_by_label("Site").select_option(label="Bridge Rd upgrade")
    dialog.get_by_label("Start time (optional)").fill("06:30")
    dialog.get_by_label("Instructions (optional)").fill("Meet at the depot first.")
    dialog.get_by_label("Daily pre-start").check()
    dialog.get_by_role("button", name="Check").click()
    expect(dialog.get_by_text("Will go to: Nina Example.")).to_be_visible()
    dialog.get_by_role("button", name="Issue 1 job").click()
    expect(pg.get_by_text("1 job issued.")).to_be_visible()
    expect(pg.get_by_text("Waiting for the worker")).to_be_visible()
    shot(pg, "23-daily-jobs", full=True)

    # The worker's phone: Home points at the job; accept, open the linked form, mark done.
    phone = sign_in(browser, 390, 844, "nina@e2e.example", "Nina-Pass-2026")
    phone.on("pageerror", lambda e: errors.append(str(e)))
    expect(phone.get_by_text("Today's job: Bridge Rd upgrade")).to_be_visible()
    phone.get_by_role("link", name="Open my work").click()
    expect(phone.get_by_text("Meet at the depot first.")).to_be_visible()
    shot(phone, "24-phone-my-work", full=True)
    phone.get_by_role("button", name="Accept job").click()
    expect(phone.get_by_text("Job accepted.")).to_be_visible()
    phone.get_by_role("button", name="Daily pre-start").click()
    phone.wait_for_url("**/forms/submissions/*")
    phone.go_back()
    phone.get_by_role("button", name="Mark as done").click()
    expect(phone.get_by_text("Job marked as done.")).to_be_visible()
    expect(phone.get_by_text("You have no job waiting.")).to_be_visible()
    # Their own inductions only, read-only.
    phone.get_by_role("button", name="Menu").click()
    phone.get_by_role("link", name="Inductions & training").click()
    expect(phone.get_by_role("button", name="Nina Example, TasWater Induction: Valid")).to_be_disabled()
    pg.reload()
    expect(pg.get_by_text("Done", exact=True).first).to_be_visible()
    assert not errors, errors
    print("inductions, training and daily jobs walkthrough: ok")


def connections_and_outbox(browser) -> None:
    pg = sign_in(browser)
    errors: list[str] = []
    pg.on("pageerror", lambda e: errors.append(str(e)))

    # The bell: Nina's job was declined? No - but an expiring item or two may exist. It opens and closes.
    pg.get_by_role("button", name="Notifications").click()
    expect(pg.get_by_role("dialog", name="Notifications")).to_be_visible()
    pg.keyboard.press("Escape")

    # Connections: safe mode is locked on by the server in this run; nothing is set up.
    pg.get_by_role("link", name="Connections").click()
    expect(pg.get_by_text("On: nothing is sent")).to_be_visible()
    expect(pg.get_by_text("Safe mode is locked on by the server setting")).to_be_visible()
    simpro = pg.locator("section", has=pg.get_by_role("heading", name="Simpro"))
    expect(simpro.get_by_text("Not set up")).to_be_visible()
    simpro.get_by_role("button", name="Set up").click()
    simpro.get_by_label("Your Simpro address").fill("http://insecure.example")
    simpro.get_by_label("API token").fill("token-for-the-walkthrough")
    simpro.get_by_label("This connection is").select_option("simulator")
    simpro.get_by_role("button", name="Save").click()
    expect(simpro.get_by_text("enter a full https:// address")).to_be_visible()
    simpro.get_by_label("Your Simpro address").fill("https://example.simprosuite.com")
    simpro.get_by_role("button", name="Save").click()
    expect(simpro.get_by_text("Saved.", exact=True)).to_be_visible()
    # The token is never shown again: the box is empty and says a value is saved.
    assert simpro.get_by_label("API token").input_value() == ""
    expect(simpro.get_by_text("A value is saved. It is never shown again.")).to_be_visible()
    simpro.get_by_role("button", name="Test connection").click()
    expect(simpro.get_by_text("Simulator answered. No real system was contacted")).to_be_visible()
    expect(simpro.get_by_text("Simulator", exact=True)).to_be_visible()  # never "Connected"
    assert simpro.get_by_text("Connected", exact=True).count() == 0
    shot(pg, "25-connections", full=True)

    # Outbox: the job text and the earlier invitation are held, each with its reason.
    pg.get_by_role("link", name="Outbox").click()
    expect(pg.get_by_role("tab", name="Sent (0)")).to_be_visible()
    row = pg.get_by_role("row").filter(has_text="Daily job")
    expect(row.get_by_text("Safe mode is on, so nothing is sent.")).to_be_visible()
    row.get_by_role("button", name="Open").click()
    dialog = pg.get_by_role("dialog")
    expect(dialog.get_by_text("Bridge Rd upgrade")).to_be_visible()
    dialog.get_by_role("button", name="Send now").click()
    expect(pg.get_by_text("Still not sent. Safe mode is on")).to_be_visible()
    shot(pg, "26-outbox", full=True)
    assert not errors, errors
    print("connections and outbox walkthrough: ok")


STEPS = [stage1_and_2a, documents, forms, safety_registers, workers, sites_and_visitors, suppliers_and_renewals, inductions_and_jobs, connections_and_outbox]

if __name__ == "__main__":
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
        for step in STEPS:
            step(browser)
        browser.close()
    print("all walkthroughs passed; screenshots in", OUT)
