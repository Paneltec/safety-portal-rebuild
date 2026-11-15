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


def sign_in(browser, width: int = 1280, height: int = 900):
    page = browser.new_context(viewport={"width": width, "height": height}).new_page()
    page.goto(BASE + "/login")
    page.get_by_label("Email").fill(HOLDER["email"])
    page.get_by_label("Password").fill(HOLDER["password"])
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


STEPS = [stage1_and_2a, documents]

if __name__ == "__main__":
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
        for step in STEPS:
            step(browser)
        browser.close()
    print("all walkthroughs passed; screenshots in", OUT)
