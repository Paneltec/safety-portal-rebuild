# Performance check

Measured 2026-11-16 by `backend/scripts/perf_check.py` on a synthetic dataset. Run the script again to refresh this page; nothing here is typed by hand.

## What this is and is not

- One person at a time: each call was made five times in a row and the median is shown. It says nothing about many people using the portal at once.
- The API and the database ran on the same machine, called in-process, so network time and the web server are not included.
- The machine below is a small cloud build machine, not the NAS the portal will run on. Expect different numbers there, and measure again once it is installed.
- Stored files (photos, PDFs) were not part of the dataset, so backup time and size here cover records only.

## Machine

- Intel(R) Xeon(R) Processor @ 2.10GHz, 2 cores, 7 GB memory
- psql (PostgreSQL) 16.15 (Ubuntu 16.15-0ubuntu0.24.04.1); Python 3.13.18; Linux 6.18.44-fc-v470

## Dataset

| Records | Number |
|---|---|
| Workers | 500 |
| Certifications | 1,500 |
| Assets | 300 |
| Service schedules | 300 |
| Fuel fills | 100,000 |
| Form submissions | 20,000 |
| Hazards | 5,000 |
| Sites | 50 |
| Site sign ins | 25,000 |
| Activity entries | 50,000 |

Building it took 11 seconds.

## Timings

| Screen or action | Median | Slowest of 5 | Answer size |
|---|---|---|---|
| Home: dashboard figures | 119 ms | 221 ms | 9 KB |
| Search for "smith" across registers | 71 ms | 74 ms | 1 KB |
| Workers: first page of 50 | 79 ms | 86 ms | 23 KB |
| Workers: search "okafor" | 45 ms | 52 ms | 7 KB |
| Certifications register: first page | 55 ms | 64 ms | 27 KB |
| Certifications: expiry counts | 46 ms | 172 ms | 0 KB |
| Fleet: first page of 50 | 18 ms | 18 ms | 45 KB |
| Service: what is due | 25 ms | 25 ms | 34 KB |
| Fuel: fills, first page of 50 | 38 ms | 45 ms | 36 KB |
| Fuel: fills, page 1,000 of 50 | 95 ms | 109 ms | 36 KB |
| Fuel: report by vehicle, last 30 days | 31 ms | 32 ms | 75 KB |
| Fuel: report by vehicle, last 12 months | 63 ms | 70 ms | 76 KB |
| Fuel: summary tiles | 237 ms | 400 ms | 0 KB |
| Forms: submissions, first page of 50 | 56 ms | 63 ms | 24 KB |
| Hazards: first page of 50 | 17 ms | 19 ms | 27 KB |
| Hazards: search "trench" | 23 ms | 24 ms | 27 KB |
| Visitors: list | 46 ms | 57 ms | 257 KB |
| Activity log: first page of 50 | 31 ms | 41 ms | 15 KB |
| Activity log: file for the last 30 days | 110 ms | 247 ms | 526 KB |
| Audit pack: hazards, site log and forms for 30 days (once) | 190 ms |  | 1 KB |
| Backup: make an archive of all records | 5,218 ms | | 12.3 MB archive, 202,675 records |
| Backup: verify that archive | 311 ms | | verified |

## Limits that were exercised

- An audit pack covering all 25,000 site sign-ins and all 20,000 form submissions with no date range was refused (status 422) with a request to shorten the period, as designed.
- The activity log file is limited to 20,000 entries per file; the 30-day file above is within that.
- Register lists are paged by the server (50 per page here). Three answers are not paged: the visitors list returns at most 500 rows, and the service list and fuel report return one row per schedule or vehicle.

## Reading the numbers

Longer than one second on this machine: Making a backup (5,218 ms). A backup runs in the background after the button is pressed, so the screen does not wait for it.
No tuning was done as a result of this run. Anything listed as slow is a known item, not a fixed one.
