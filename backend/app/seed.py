"""Command-line setup helpers.

  python -m app.seed create-admin --org "Paneltec" --name "Alex Rowe" --email alex@example.com
  python -m app.seed demo

Passwords are never written into this file. `create-admin` prints a one-time
invitation path (or uses BOOTSTRAP_ADMIN_PASSWORD from the environment), and
`demo` uses DEMO_PASSWORD from the environment or generates a random one.
"""
import argparse
import os
import secrets
import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_engine
from .models import Company, Organisation, User, utcnow
from .security import hash_password, password_policy_error
from .services import create_organisation, issue_auth_token, seed_system_roles


def _get_or_create_org(db: Session, name: str) -> Organisation:
    org = db.scalars(select(Organisation).where(Organisation.name == name)).first()
    if org is None:
        org = create_organisation(db, name)
    else:
        seed_system_roles(db, org.id)
    return org


def create_admin(org_name: str, name: str, email: str) -> None:
    email = email.strip().lower()
    with Session(get_engine()) as db:
        if db.scalars(select(User.id).where(User.email == email)).first():
            sys.exit(f"A user with the email {email} already exists.")
        org = _get_or_create_org(db, org_name)
        user = User(org_id=org.id, email=email, name=name.strip(), role_id="admin", status="invited")
        db.add(user)
        db.flush()
        if org.owner_user_id is None:
            # The first admin of an organisation becomes its account holder and payroll owner.
            org.owner_user_id = user.id
            org.payroll_owner_user_id = user.id
        password = os.environ.get("BOOTSTRAP_ADMIN_PASSWORD")
        if password:
            error = password_policy_error(password)
            if error:
                sys.exit(f"BOOTSTRAP_ADMIN_PASSWORD rejected: {error}")
            user.password_hash = hash_password(password)
            user.status = "active"
            user.must_change_password = True
            user.last_password_change_at = utcnow()
            db.commit()
            print(f"Admin {email} created in '{org.name}'. They must change the password at first sign-in.")
        else:
            _row, token = issue_auth_token(db, user, "invite", created_by=None)
            db.commit()
            print(f"Admin {email} created in '{org.name}'.")
            print("Open this one-time path on the portal address to set a password (valid 72 hours):")
            print(f"  /accept-invite?token={token}")


DEMO_PEOPLE = [
    # (name, email local part, role, company code)
    ("Alex Rowe", "alex.rowe", "admin", "PAN"),
    ("Sam Keating", "sam.keating", "hseq_lead", "PAN"),
    ("Jess Marlow", "jess.marlow", "supervisor", "PAN"),
    ("Tom Okafor", "tom.okafor", "worker", "PAN"),
    ("Priya Nand", "priya.nand", "worker", "PAN"),
    ("Dan Whitby", "dan.whitby", "worker", "VIA"),
    ("Mia Castell", "mia.castell", "supervisor", "VIA"),
    ("Lee Fontaine", "lee.fontaine", "auditor", "PAN"),
    ("Rae Dunmore", "rae.dunmore", "contractor_rep", None),
]


def demo() -> None:
    """Synthetic organisations and people for demonstrations. No real data."""
    password = os.environ.get("DEMO_PASSWORD") or secrets.token_urlsafe(12)
    error = password_policy_error(password)
    if error:
        sys.exit(f"DEMO_PASSWORD rejected: {error}")
    hashed = hash_password(password)
    created = 0
    with Session(get_engine()) as db:
        for org_name, domain in (("Paneltec (demo)", "demo.paneltec.example"), ("Other Org (demo)", "demo.other.example")):
            org = _get_or_create_org(db, org_name)
            companies: dict[str, str] = {}
            for code, cname in (("PAN", "Paneltec Civil"), ("VIA", "Viatec Traffic Solutions")):
                company = db.scalars(select(Company).where(Company.org_id == org.id, Company.code == code)).first()
                if company is None:
                    company = Company(org_id=org.id, name=cname, code=code)
                    db.add(company)
                    db.flush()
                companies[code] = company.id
            for name, local, role, code in DEMO_PEOPLE:
                email = f"{local}@{domain}"
                if db.scalars(select(User.id).where(User.email == email)).first():
                    continue
                user = User(org_id=org.id, email=email, name=name, role_id=role, status="active",
                            password_hash=hashed, company_id=companies.get(code) if code else None,
                            last_password_change_at=utcnow())
                db.add(user)
                db.flush()
                if role == "admin" and org.owner_user_id is None:
                    org.owner_user_id = user.id
                    org.payroll_owner_user_id = user.id
                created += 1
        db.commit()
    print(f"Demo data ready. {created} synthetic users created.")
    print("Sign in as alex.rowe@demo.paneltec.example (admin).")
    if os.environ.get("DEMO_PASSWORD"):
        print("Password: the value of DEMO_PASSWORD.")
    elif created:
        print(f"Generated demo password (shown once): {password}")
    else:
        print("No new users were created, so existing passwords are unchanged.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Safety Portal setup helpers")
    sub = parser.add_subparsers(dest="command", required=True)
    admin = sub.add_parser("create-admin", help="create an organisation's first admin")
    admin.add_argument("--org", required=True)
    admin.add_argument("--name", required=True)
    admin.add_argument("--email", required=True)
    sub.add_parser("demo", help="load synthetic demo organisations and users")
    args = parser.parse_args()
    if args.command == "create-admin":
        create_admin(args.org, args.name, args.email)
    else:
        demo()


if __name__ == "__main__":
    main()
