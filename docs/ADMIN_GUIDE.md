# Guide for the account holder and administrators

Written for the person who runs the portal day to day. Menu names are shown in **bold** as they appear on screen. This describes the rebuild as it stands; anything not mentioned here is not built.

## First things

- **The portal holds made-up data only** until you decide otherwise. It is separate from the existing Paneltec portal and does not read from it or write to it.
- **Nothing is sent.** Emails and texts are prepared and kept in **Settings > Outbox**. They only go out if the server's safe mode lock is turned off, a real Microsoft 365 or TextMagic connection is set up and tested, and safe mode under **Settings > Connections** is turned off as well.
- **Pay is a test workbench.** It works out pay from timesheets so the numbers can be compared with your payroll provider. It never pays anyone, lodges anything with the Tax Office, or sends a payslip.

## Home

**Home** shows what needs attention: expired or expiring certificates, safety records waiting for review, renewals to review, services due, fuel fills to check, messages that failed, and so on. Each number says what it counts (open **What each figure counts**) and opens the screen that lists the same records. A dash means the number could not be worked out just now; it does not mean zero.

The **search box** at the top finds workers, sites, suppliers, fleet, library files, safety records and users by name or title. It does not read inside files.

## People and access

- **Settings > Users**: invite people, change their role, switch a login off. Invitation links are shown to you to pass on by hand while sending is locked.
- **Settings > Roles & permissions**: what each role can see and do. A person can also be given an exception of their own.
- **Compliance > Workers** and **Certifications**: worker records, licences and tickets with expiry dates. A certificate with no expiry date recorded is shown as such, not as valid.
- **Settings > Phone modules**: what each kind of person sees in the phone layout. This tidies their menu only; it does not give or remove access.
- **Settings > Active sessions**: who is signed in, and a way to sign them out.

## Safety records

Under **Capture**: SWMS, pre-starts, site diary, incidents and hazards, inspections, risk assessments, SSRA, and **Forms** (your own forms, built in the form editor). Records move from draft to submitted to approved; a submitted record cannot be edited without it being recorded.

## Sites, suppliers and jobs

- **Sites**: each site has a QR sign. Workers and visitors sign on by scanning it. **Visitors** lists who is on site.
- **Suppliers** and **Renewals**: ask a supplier for insurance documents through a link; what they upload counts only after you accept it.
- **Daily jobs**: give a worker a job for the day; they accept or decline it on their phone.

## Fleet, service and fuel

- **Fleet and equipment**: vehicles, plant and equipment with meters, QR labels and documents.
- Service schedules by days, kilometres or hours, with a list of what is due.
- **Fuel**: fuel fills imported from a file, the weekly diesel rate, reports, and a list of fills that look wrong.
- Navixy, SmartFill and FuelCheck connections exist but have only been tested against stand-ins, never the real services.

## Leave, timesheets and pay (test only)

- Workers ask for leave and fill in timesheets on their phone. **Leave** and **Pay > Timesheets** are where the office approves them.
- Pay screens are only visible to the account holder and to administrators the account holder names.
- The tax table has not been checked against the Tax Office's published schedule. Do not pay anyone from these numbers.

## Records for an auditor

- **Compliance > Audit packs**: make a ZIP of chosen registers for a period. It holds the records and a list of what is inside with a fingerprint for each file. Attached files and PDFs are not included.
- **Settings > Activity log**: every change the portal recorded, with a spreadsheet download. It cannot be edited.

## Looking after the installation (account holder only)

- **Settings > Backups**: make a backup, **Verify** it, and **Rehearse a restore**. A backup is only trustworthy once verified. The portal does not make backups by itself: see `docs/BACKUP_RESTORE.md` for the timer to set up and for what to keep safe (the encryption key).
- **Settings > System check**: whether the database, file storage, encryption key and backups are in order.

## Known gaps

See `docs/PARITY_REPORT.md` for the full list. The main ones: no real provider has been connected; the Docker installation has not been run; the phone layout has not been tried on real phones; no in-app manual; no AI features; no Dropbox; no bulk import of old pre-starts.
