# Backups and restoring

This page is for whoever looks after the server. Commands are shown for the Docker setup in this repository.

## What a backup is

One ZIP file in the server's backup folder. It holds every table of the database as text, every uploaded file, and a manifest listing each of them with its size and fingerprint (SHA-256).

It does **not** hold the encryption key (`ENCRYPTION_KEY` in `.env`). Keep a copy of that key somewhere separate from the backups. Without the same key, protected details in a backup (tax file numbers, bank details, connection passwords) cannot be read after a restore. Everything else restores normally.

## Where backups go

The folder set by `BACKUP_PATH` in `.env` (default `./backups` beside `docker-compose.yml`). Use a folder on a different disk from the database if you can, such as a NAS shared folder. The portal writes there as user id 10001, so allow it once:

```
mkdir -p ./backups && sudo chown 10001 ./backups
```

The portal does not copy backups anywhere else. Copy that folder to a second place with your NAS's own backup or sync tool.

## Making a backup

- In the portal: Settings, Backups, **Make a backup now** (account holder only).
- On a timer: the portal does not run backups by itself. Add a scheduled task on the server that runs, for example every night:

```
docker compose exec -T api python -m app.backup snapshot
```

It prints `created: <file name>` and exits with 0, or `failed: <reason>` and exits with 1, so the scheduler can alert on failure.

## Checking a backup

"Created" only means the file was written. Press **Verify** in the portal to re-read the file and compare it with the size and fingerprint recorded when it was made and with its own manifest. Results:

- **Verified**: every check passed.
- **Failed its checks**: do not rely on this file.
- **Could not be checked**: the file is not in the backup folder (moved, or the folder is not connected). This is not a pass.

Verifying never changes the file or any record.

## Rehearsing a restore

A rehearsal loads a verified backup into a separate scratch database and a temporary folder, exports that copy again, checks it is identical to the backup, and then empties the scratch database. Your live records are never touched.

Set it up once by creating the scratch database:

```
docker compose exec db createdb -U portal portal_rehearsal
```

Then press **Rehearse a restore** on a verified backup. The portal refuses to rehearse into its own database or into any database whose name does not end in `_rehearsal`.

## A real restore

There is no button for this, on purpose: restoring over a running portal would overwrite current records. A real restore goes into a **new, empty** database, and the portal is pointed at it afterwards.

1. Stop the portal's web and API containers: `docker compose stop web api`.
2. Create an empty database and an empty files folder:
   ```
   docker compose exec db createdb -U portal portal_restored
   ```
3. Load the backup (the command refuses a database that is not empty, and refuses the database the portal is currently set to use):
   ```
   docker compose run --rm -v ./restored-files:/data/restored api \
     python -m app.backup restore /data/backups/<file>.zip \
     --target-url postgresql+psycopg://portal:<password>@db:5432/portal_restored \
     --files-dir /data/restored
   ```
   It prints each check with `ok` or `FAIL`.
4. Only if every line says `ok`: point the portal at the restored copy (change the database name in `DATABASE_URL` in `docker-compose.yml` and the files volume to the restored folder), make sure `ENCRYPTION_KEY` is the key that was in use when the backup was made, and start the containers.
5. Keep the old database until you are satisfied. Nothing in these steps deletes it.

Restore with the same version of the portal that made the backup, then upgrade. A backup from a different version is refused with a message saying which version made it.

## What has and has not been tested

Tested automatically: making, verifying (including damaged, swapped, not-a-ZIP and missing files), rehearsing against a real PostgreSQL scratch database with data from every part of the portal, the three server commands (snapshot, verify, restore into an empty database), the refusals above, and tidy-up.

Not tested: the Docker commands on this page (no Docker was available where this was built), the restore command against a separate database server, very large file stores, and a NAS shared folder as the backup folder.
